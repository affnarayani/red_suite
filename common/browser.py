"""Browser/stealth setup, human-like waits, error screenshot, onlyfiles upload."""
import random
import re
import time

import requests
from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth

from .config import CONFIG
from .paths import ERROR_DIR, SCREENSHOT_FILE


def random_wait(min_sec, max_sec):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


def wait(profile):
    """Sleep for a random duration defined by a named profile in config.json."""
    bounds = CONFIG["waits"][profile]
    random_wait(bounds["min"], bounds["max"])


def human_type(page, text_to_type: str):
    """Simulate human typing character by character (collapses blank lines)."""
    cleaned_text = re.sub(r"\n+", "\n", text_to_type)
    bounds = CONFIG["waits"]["human_type"]
    for char in cleaned_text:
        if char == "\n":
            page.keyboard.press("Enter")
        else:
            page.keyboard.type(char)
        time.sleep(random.uniform(bounds["min"], bounds["max"]))


def start_playwright():
    """Start Playwright through playwright-stealth. Returns (pw, pw_cm)."""
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()
    return pw, pw_cm


def launch_browser(pw):
    return pw.chromium.launch(
        headless=CONFIG["headless"],
        args=[
            "--start-maximized",
            "--disable-blink-features=AutomationControlled",
        ],
    )


def new_context(browser):
    context = browser.new_context(
        no_viewport=True,
        user_agent=CONFIG["user_agent"],
    )
    context.grant_permissions(["clipboard-read", "clipboard-write"])
    return context


def capture_error_screenshot(page):
    """Save a single fixed error screenshot (replaces any existing file)."""
    try:
        ERROR_DIR.mkdir(parents=True, exist_ok=True)
        if SCREENSHOT_FILE.exists():
            SCREENSHOT_FILE.unlink()
        page.screenshot(path=str(SCREENSHOT_FILE), full_page=True)
        print(f"[OK] Error screenshot captured: {SCREENSHOT_FILE}", flush=True)
        return SCREENSHOT_FILE
    except Exception as err:
        print(f"[WARNING] Could not capture error screenshot: {err}", flush=True)
        return None


def upload_to_onlyfiles(screenshot_path):
    """Upload a screenshot to onlyfiles and return the direct link (or None)."""
    onlyfiles = CONFIG["onlyfiles"]
    url = onlyfiles["upload_url"]
    expire = onlyfiles["expire_seconds"]
    try:
        with open(screenshot_path, "rb") as file:
            response = requests.post(url, files={"file": file}, data={"expire": expire})

        if response.status_code == 200:
            res_data = response.json()
            if res_data.get("status"):
                direct_url = res_data["data"]["file"]["url"]["full"]
                hours = expire // 3600
                print(f"[LINK] DIRECT LINK (Expires in {hours} Hours): {direct_url}", flush=True)
                return direct_url
            msg = res_data.get("error", {}).get("message", "Unknown error")
            print(f"[WARNING] Upload Failed: {msg}", flush=True)
            return None
        print(f"[WARNING] Upload Failed: {response.status_code}", flush=True)
        return None
    except Exception as err:
        print(f"[WARNING] onlyfiles upload error: {err}", flush=True)
        return None


def report_error(page):
    """Capture the error screenshot and upload it. Safe to call with page=None."""
    if page is None:
        return
    shot = capture_error_screenshot(page)
    if shot:
        upload_to_onlyfiles(shot)