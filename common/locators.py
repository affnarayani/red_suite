"""Locator book: resolves locators as primary -> fallbacks -> Gemini vision,
and manages locators/changed_locators.json (add / update / remove).

Locator files live in locators/<name>.txt. Each line is:

    variable_name = <playwright expression> :: primary|fallback

The first line for a variable is the primary; following lines are fallbacks.
Blank lines and lines starting with '#' are ignored. The ":: primary|fallback"
marker is optional and used only for readability.
"""
import json
import re

from .config import CONFIG
from .gemini_vision import GeminiVision
from .paths import CHANGED_LOCATORS_FILE, LOCATORS_DIR


# ---------------------------------------------------------------------------
# changed_locators.json helpers
# ---------------------------------------------------------------------------
def _load_changed():
    if not CHANGED_LOCATORS_FILE.exists():
        return []
    try:
        with open(CHANGED_LOCATORS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, list) else []
    except Exception:
        return []


def _save_changed(entries):
    CHANGED_LOCATORS_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHANGED_LOCATORS_FILE, "w", encoding="utf-8") as f:
        json.dump(entries, f, indent=4, ensure_ascii=False)


def _append_changed_entry(entry):
    """Add or update a single changed-locator entry (written immediately)."""
    entries = _load_changed()
    for i, existing in enumerate(entries):
        if (
            existing.get("locator_file") == entry["locator_file"]
            and existing.get("variable") == entry["variable"]
            and existing.get("broke_locator") == entry["broke_locator"]
        ):
            entries[i] = entry
            _save_changed(entries)
            return
    entries.append(entry)
    _save_changed(entries)


def _read_locator_lines(file_path):
    if not file_path.exists():
        return []
    with open(file_path, "r", encoding="utf-8") as f:
        return f.readlines()


def _expr_from_line(line):
    """Return (variable, expression) for a locator line, else (None, None).

    Only a trailing ':: primary' / ':: fallback' marker is stripped, so '::'
    sequences inside expressions (for example XPath 'ancestor-or-self::') are
    preserved.
    """
    stripped = line.strip()
    if not stripped or stripped.startswith("#"):
        return None, None
    marker = re.search(r"\s*::\s*(primary|fallback)\s*$", stripped, re.IGNORECASE)
    if marker:
        stripped = stripped[: marker.start()].strip()
    if "=" not in stripped:
        return None, None
    var, expr = stripped.split("=", 1)
    var = var.strip()
    expr = expr.strip()
    if not var or not expr:
        return None, None
    return var, expr


def _suggested_in_file(entry):
    """True if the suggested locator expression already exists in the txt file."""
    file_path = LOCATORS_DIR / entry.get("locator_file", "")
    suggested = entry.get("suggested_locator", "")
    if not suggested:
        return False
    for line in _read_locator_lines(file_path):
        var, expr = _expr_from_line(line)
        if expr == suggested:
            return True
    return False


def _append_suggested_as_fallback(entry):
    """Insert the suggested locator directly below the primary line for its variable."""
    file_path = LOCATORS_DIR / entry.get("locator_file", "")
    variable = entry.get("variable")
    suggested = entry.get("suggested_locator")
    if not variable or not suggested:
        return

    # Avoid creating a duplicate fallback line.
    if _suggested_in_file(entry):
        return

    lines = _read_locator_lines(file_path)
    if not lines:
        lines = []

    new_line = f"{variable} = {suggested} :: fallback\n"
    insert_at = None
    for idx, line in enumerate(lines):
        var, _ = _expr_from_line(line)
        if var == variable:
            insert_at = idx + 1  # right below the primary line
            break

    if insert_at is None:
        # Variable not found; append at the end.
        if lines and not lines[-1].endswith("\n"):
            lines[-1] = lines[-1] + "\n"
        lines.append(new_line)
    else:
        lines.insert(insert_at, new_line)

    with open(file_path, "w", encoding="utf-8") as f:
        f.writelines(lines)


def apply_auto_correct():
    """Run the auto-correct pass once at the start of a script run."""
    entries = _load_changed()
    if not entries:
        return

    if CONFIG.get("auto_correct_locator", False):
        for entry in entries:
            _append_suggested_as_fallback(entry)
        _save_changed([])
        print(
            "[INFO] auto_correct_locator ON: applied suggestions to locators and cleared changed_locators.json",
            flush=True,
        )
    else:
        kept = [entry for entry in entries if not _suggested_in_file(entry)]
        if len(kept) != len(entries):
            _save_changed(kept)
            print(
                "[INFO] auto_correct_locator OFF: removed entries already present in locators",
                flush=True,
            )


# ---------------------------------------------------------------------------
# Locator book
# ---------------------------------------------------------------------------
class LocatorBook:
    def __init__(self, name, page, vision=None):
        self.name = name
        self.file_path = LOCATORS_DIR / f"{name}.txt"
        self.page = page
        if isinstance(vision, GeminiVision):
            self.vision = vision
        else:
            self.vision = GeminiVision()
        self._map = self._load()
        self._last_index = None
        self._changed = _load_changed()

    def _load(self):
        if not self.file_path.exists():
            raise FileNotFoundError(f"Locator file not found: {self.file_path}")
        mapping = {}
        for line in _read_locator_lines(self.file_path):
            var, expr = _expr_from_line(line)
            if var is None:
                continue
            mapping.setdefault(var, []).append(expr)
        return mapping

    def _eval(self, expr):
        return eval(expr, {"page": self.page, "re": re, "__builtins__": {}})

    # -- resolution --------------------------------------------------------
    def resolve_quick(self, var):
        """Instant check only (no waiting). Returns a visible Locator or None."""
        for i, expr in enumerate(self._map.get(var, [])):
            try:
                loc = self._eval(expr)
                if loc.count() > 0 and loc.first.is_visible():
                    self._last_index = i
                    return loc.first
            except Exception:
                continue
        return None

    def resolve(self, var, timeout_ms=None):
        """Return a visible Locator (primary first, then fallbacks) or None.

        Uses instant count()/is_visible() checks first, then a short visibility
        wait for dynamic elements. No long timeouts are spent per fallback.
        """
        if timeout_ms is None:
            timeout_ms = CONFIG.get("locator_check_timeout_ms", 1500)

        # Pass 1: instant checks (no waiting).
        loc = self.resolve_quick(var)
        if loc is not None:
            return loc

        # Pass 2: short visibility wait for dynamic elements.
        for i, expr in enumerate(self._map.get(var, [])):
            try:
                loc = self._eval(expr)
                if loc.count() == 0:
                    continue
                loc.first.wait_for(state="visible", timeout=timeout_ms)
                self._last_index = i
                return loc.first
            except Exception:
                continue
        return None

    # -- vision helpers ----------------------------------------------------
    def _vision_locate(self, purpose, action):
        if not self.vision or not self.vision.enabled:
            return None
        try:
            screenshot = self.page.screenshot(full_page=False)
        except Exception:
            return None
        return self.vision.locate(screenshot, purpose, action)

    @staticmethod
    def _wrap_locator(suggested):
        sel = suggested.strip()
        if not sel or sel.startswith("#"):
            return None  # never record raw id selectors
        # Reject anything that is not a plain CSS selector. This prevents a
        # prompt-injected vision response (or a poisoned changed_locators.json)
        # from injecting executable code into the locator txt / eval path.
        if sel.startswith("page.") or "(" in sel or ")" in sel or "__" in sel:
            return None
        # json.dumps produces a safely-escaped Python string literal.
        return f"page.locator({json.dumps(sel)})"

    def _record_from_vision(self, var, result):
        suggested = result.get("suggested_locator")
        wrapped = self._wrap_locator(suggested) if suggested else None
        if not wrapped:
            return
        exprs = self._map.get(var, [])
        primary = exprs[0] if exprs else var
        _append_changed_entry(
            {
                "locator_file": self.file_path.name,
                "variable": var,
                "broke_locator": f"{var} = {primary}",
                "suggested_locator": wrapped,
            }
        )
        self._changed = _load_changed()
        print(
            f"[INFO] Recorded suggested locator for '{var}' in changed_locators.json",
            flush=True,
        )

    def _maybe_recover(self, var):
        """If the primary works again, drop any changed entry for this variable."""
        if not any(entry.get("variable") == var for entry in self._changed):
            return
        remaining = [entry for entry in self._changed if entry.get("variable") != var]
        if len(remaining) != len(self._changed):
            self._changed = remaining
            _save_changed(remaining)
            print(
                f"[INFO] Locator '{var}' works again; removed from changed_locators.json",
                flush=True,
            )

    def _after_resolve(self, var):
        if self._last_index == 0:
            self._maybe_recover(var)

    # -- public actions ----------------------------------------------------
    def click(self, var, purpose):
        loc = self.resolve(var)
        if loc is not None:
            self._after_resolve(var)
            loc.click()
            return True
        result = self._vision_locate(purpose, "click")
        if result and result.get("found"):
            self.page.mouse.click(result["x"], result["y"])
            self._record_from_vision(var, result)
            return True
        print(f"[ERROR] Could not locate/click '{var}' ({purpose})", flush=True)
        raise RuntimeError(f"Locator action failed: click '{var}' ({purpose})")

    def fill(self, var, value, purpose):
        loc = self.resolve(var)
        if loc is not None:
            self._after_resolve(var)
            try:
                loc.click()
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
                self.page.keyboard.insert_text(value)
                return True
            except Exception:
                # Never leak the filled value (prompt/history) in the console.
                print(f"[ERROR] Could not fill '{var}' ({purpose})", flush=True)
                raise RuntimeError(f"Locator action failed: fill '{var}' ({purpose})") from None
        result = self._vision_locate(purpose, "type")
        if result and result.get("found"):
            self.page.mouse.click(result["x"], result["y"])
            try:
                self.page.keyboard.press("Control+A")
                self.page.keyboard.press("Delete")
                self.page.keyboard.insert_text(value)
            except Exception:
                print(f"[ERROR] Could not type into '{var}' ({purpose})", flush=True)
                raise RuntimeError(f"Locator action failed: fill '{var}' ({purpose})") from None
            self._record_from_vision(var, result)
            return True
        print(f"[ERROR] Could not locate/fill '{var}' ({purpose})", flush=True)
        raise RuntimeError(f"Locator action failed: fill '{var}' ({purpose})")

    def type_human(self, var, text, purpose):
        from .browser import human_type

        loc = self.resolve(var)
        if loc is not None:
            self._after_resolve(var)
            loc.click()
            human_type(self.page, text)
            return True
        result = self._vision_locate(purpose, "type")
        if result and result.get("found"):
            self.page.mouse.click(result["x"], result["y"])
            human_type(self.page, text)
            self._record_from_vision(var, result)
            return True
        print(f"[ERROR] Could not locate/type into '{var}' ({purpose})", flush=True)
        raise RuntimeError(f"Locator action failed: type into '{var}' ({purpose})")

    def inner_text(self, var, purpose):
        loc = self.resolve(var)
        if loc is not None:
            self._after_resolve(var)
            return loc.inner_text()
        result = self._vision_locate(purpose, "read")
        if result and result.get("found"):
            self._record_from_vision(var, result)
            return result.get("text", "") or ""
        return ""

    def is_visible(self, var, purpose=None, use_vision=False):
        loc = self.resolve(var)
        if loc is not None:
            self._after_resolve(var)
            return True
        if use_vision and purpose:
            result = self._vision_locate(purpose, "check")
            return bool(result and result.get("found"))
        return False

    def raw(self, var, index=0):
        """Return the unevaluated Locator for the primary/backup expression.

        Useful for generic scans (for example .all()) where the caller needs the
        full multi-match locator instead of a single resolved element.
        """
        exprs = self._map.get(var, [])
        if index >= len(exprs):
            return None
        try:
            return self._eval(exprs[index])
        except Exception:
            return None