import os
import sys
import json
import time
import base64
import random
from pathlib import Path
from typing import List, Dict, Any

from dotenv import load_dotenv

from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.exceptions import InvalidTag

from playwright.sync_api import sync_playwright
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

REDDIT_COOKIES_FILE = "reddit_cookies.json.encrypted"
PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"

# Files Configuration
SUBREDDITS_FILE = Path("subreddits.txt")
BANNED_FILE = Path("banned.txt")

SEARCH_URL = "https://www.reddit.com/search/?q=Motivation+%26+Self-Improvement&type=communities"


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"   ⏳ [WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
    time.sleep(seconds)


# =========================
# CRYPTO
# =========================
def _derive_key(password: bytes, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(password)


def _decrypt_payload(payload: Dict[str, Any], password: str) -> bytes:
    salt = base64.b64decode(payload["s"])
    nonce = base64.b64decode(payload["n"])
    ciphertext = base64.b64decode(payload["ct"])

    key = _derive_key(password.encode("utf-8"), salt)
    aesgcm = AESGCM(key)

    try:
        return aesgcm.decrypt(nonce, ciphertext, None)
    except InvalidTag:
        raise RuntimeError("❌ Decryption failed (InvalidTag)")


def load_cookies(file_path: Path) -> List[Dict[str, Any]]:
    print("🔑 [COOKIES] Initializing secure cookie decryption module...", flush=True)

    if not file_path.exists():
        raise FileNotFoundError(f"❌ Encrypted cookies file not found at {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        payload = json.load(f)

    plaintext = _decrypt_payload(payload, DECRYPT_KEY)
    cookies = json.loads(plaintext.decode("utf-8"))

    for c in cookies:
        if "partitionKey" in c and isinstance(c["partitionKey"], dict):
            if "topLevelSite" in c["partitionKey"]:
                c["partitionKey"] = str(c["partitionKey"]["topLevelSite"])
            else:
                del c["partitionKey"]

        if "sameSite" in c:
            val = str(c["sameSite"]).lower()

            if val in ["no_restriction", "none", "unspecified", "null"]:
                c["sameSite"] = "None"
            elif val == "lax":
                c["sameSite"] = "Lax"
            elif val == "strict":
                c["sameSite"] = "Strict"
            else:
                c["sameSite"] = "Lax"

    print(f"✅ [COOKIES] {len(cookies)} session cookies successfully loaded into memory.\n", flush=True)
    return cookies


# =========================
# FILE HELPERS
# =========================
def read_list_from_file(file_path: Path) -> List[str]:
    if not file_path.exists():
        return []
    with file_path.open("r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip()]


def append_to_file(file_path: Path, text: str):
    ensure_newline = False
    if file_path.exists() and file_path.stat().st_size > 0:
        with file_path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            last_char = f.read(1)
            if last_char != b'\n' and last_char != b'\r':
                ensure_newline = True

    with file_path.open("a", encoding="utf-8") as f:
        if ensure_newline:
            f.write("\n")
        f.write(text + "\n")
        
    print(f"📝 [FILE WRITE] '{text}' successfully appended to {file_path.name}.", flush=True)


def parse_stat_value(val_str: str) -> int:
    val_str = val_str.strip().upper()
    if not val_str:
        return 0
    try:
        if 'M' in val_str:
            return int(float(val_str.replace('M', '')) * 1_000_000)
        elif 'K' in val_str:
            return int(float(val_str.replace('K', '')) * 1_000)
        return int(val_str.replace(',', ''))
    except ValueError:
        return 0


# =========================
# ENV
# =========================
load_dotenv()
DECRYPT_KEY = os.getenv("DECRYPT_KEY")
if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing in environmental setup")


# =========================
# MAIN FLOW
# =========================
def run():
    print("🚀 [START] Reddit Target Hunter Script Activated.", flush=True)

    cookies = load_cookies(Path(REDDIT_COOKIES_FILE))
    
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    try:
        print("🌐 [BROWSER] Initializing Chromium browser context...", flush=True)
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"]
        )

        context = browser.new_context(no_viewport=True, user_agent=USER_AGENT)
        context.grant_permissions(["clipboard-read", "clipboard-write"])
        context.add_cookies(cookies)

        page = context.new_page()
        print("✨ [BROWSER] Session initialized completely. Starting target lookup loop.\n", flush=True)
        
        processed_in_current_session = set()

        while True:
            # Refresh local arrays
            subs_list = read_list_from_file(SUBREDDITS_FILE)
            banned_list = read_list_from_file(BANNED_FILE)

            current_count = len(subs_list)
            
            print("=" * 65, flush=True)
            print(f"📊 [STATUS REPORT] Current targets: {current_count} / 5", flush=True)
            print("=" * 65, flush=True)

            if current_count >= 5:
                print("\n🎉 [SUCCESS] Target count of 5 achieved inside subreddits.txt! Stopping cycles.", flush=True)
                break

            print(f"🔍 [SEARCH] Navigating to matrix node: {SEARCH_URL}", flush=True)
            page.goto(SEARCH_URL, wait_until="domcontentloaded")
            page.wait_for_timeout(5000) 

            all_links = page.locator("a").all()
            candidate_subs = []

            for link in all_links:
                href = link.get_attribute("href")
                if href and href.startswith("/r/") and not href.startswith("/r/search"):
                    parts = [p for p in href.split("/") if p]
                    if len(parts) >= 2 and parts[0] == "r":
                        sub_name = parts[1].strip().lower()
                        full_formatted_name = f"r/{sub_name}"
                        
                        if full_formatted_name in subs_list or full_formatted_name in banned_list or full_formatted_name in processed_in_current_session:
                            continue
                        
                        if full_formatted_name not in candidate_subs:
                            candidate_subs.append(full_formatted_name)

            print(f"💡 [INFO] Found {len(candidate_subs)} unique unprocessed subreddits on this scroll.", flush=True)

            if not candidate_subs:
                print("⚠️  [WARNING] Zero new matches here. Scrolling down to trigger dynamic rendering...", flush=True)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                custom_random_wait(3, 5)
                continue 

            # Target allocation
            target_sub = candidate_subs[0]
            processed_in_current_session.add(target_sub)
            
            print(f"\n🎯 [TARGET] >>> Processing Candidate: {target_sub} <<<", flush=True)
            target_sub_url = f"https://www.reddit.com/{target_sub}/"

            # --- OPTIMIZED STEP 1: Direct About Page & Bot Bouncer Audit ---
            about_url = f"{target_sub_url}about/"
            print(f"   🔹 [STEP 1] Direct security check on About page: {about_url}", flush=True)
            page.goto(about_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            # Scroll dynamically for mod widgets
            for chunk in range(1, 4):
                page.evaluate("window.scrollBy(0, 1000);")
                page.wait_for_timeout(1000)

            bot_bouncer_link = page.get_by_role('link', name='Bot Bouncer')
            if bot_bouncer_link.is_visible():
                print(f"      🚨 [ALERT] 'Bot Bouncer' detected! Moving seedha to banned.txt and skipping further checks.", flush=True)
                append_to_file(BANNED_FILE, target_sub)
                continue
            
            print(f"   ✅ [PASS] No Bot Bouncer found. Proceeding to metrics.", flush=True)

            # --- OPTIMIZED STEP 2: Main URL & Weekly Traffic Counts Check ---
            print(f"   🔹 [STEP 2] Returning to landing workspace for metrics: {target_sub_url}", flush=True)
            page.goto(target_sub_url, wait_until="domcontentloaded")
            
            # About page se back aane ka required sync wait
            print(f"      🕒 [SYNC] Holding thread for structural components sync...", flush=True)
            custom_random_wait(15, 30)

            print(f"      📈 [STATS] Fetching community traffic parameters...", flush=True)
            try:
                weekly_users_loc = page.locator('span[slot="weekly-active-users-count"]')
                weekly_contrib_loc = page.locator('span[slot="weekly-contributions-count"]')

                if weekly_users_loc.is_visible() and weekly_contrib_loc.is_visible():
                    users_text = weekly_users_loc.inner_text()
                    contrib_text = weekly_contrib_loc.inner_text()

                    users_count = parse_stat_value(users_text)
                    contrib_count = parse_stat_value(contrib_text)

                    print(f"      📊 [DATA] Users: {users_count} ({users_text}) | Contribs: {contrib_count} ({contrib_text})", flush=True)

                    if users_count <= 5000 or contrib_count <= 1000:
                        print(f"      🛑 [SKIP] Metrics below requirements. Simply ignoring.", flush=True)
                        continue
                else:
                    print(f"      🛑 [SKIP] Analytics elements layout hidden or unpopulated. Simply ignoring.", flush=True)
                    continue

            except Exception as stat_err:
                print(f"      ❌ [ERROR] Metrics parsing exception: {stat_err}. Simply ignoring.", flush=True)
                continue

            print(f"   ✅ [PASS] Traffic volume matches profile constraints.")

            # --- OPTIMIZED STEP 3: Create Post Overlay Checks ---
            print(f"   🔹 [STEP 3] Triggering Post creation workspace overlay evaluation...", flush=True)
            
            create_post_btn = page.get_by_test_id('create-post')
            is_button_valid = create_post_btn.is_visible()
            
            if not is_button_valid:
                print(f"      ⚠️  [FALLBACK] Standard locator failed. Injecting secondary CSS fallback paths...", flush=True)
                create_post_btn = page.locator("a[data-testid='create-post']")
                is_button_valid = create_post_btn.is_visible()

            if not is_button_valid:
                print(f"      🛑 [SKIP] Editor trigger button missing or protected. Simply ignoring.", flush=True)
                continue
                
            print(f"      鼠标 [ACTION] Clicking 'Create Post' element...", flush=True)
            create_post_btn.click()
            
            print(f"      🕒 [SYNC] Holding post-click buffer window active...", flush=True)
            custom_random_wait(15, 30)

            similar_comm_btn = page.get_by_role('button', name='Post in Similar Communities')
            cant_contrib_text = page.get_by_text("You can't contribute in this")

            if similar_comm_btn.is_visible() or cant_contrib_text.is_visible():
                print(f"      🛑 [SKIP] Interface restrictions flagged inside publishing wrapper. Simply ignoring.", flush=True)
                continue

            # --- ALL FILTERS PASSED ---
            print(f"🏆 [QUALIFIED] Candidate matches all constraints perfectly! Recording data...", flush=True)
            append_to_file(SUBREDDITS_FILE, target_sub)

        print("\n🏁 [FINAL] Target limit reached or loop broke safely. Preparing system teardown.", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print(f"\n💥 [CRASH] Execution lifecycle dropped context error: {e}", flush=True)
        sys.exit(1)

    finally:
        print("\n" + "=" * 65, flush=True)
        print("🧼 [CLEANUP] Executing secure interface teardown sequence...", flush=True)
        try:
            browser.close()
            print("   🔒 Browser profiles and dynamic window contexts deleted.", flush=True)
        except:
            pass
        try:
            pw_cm.__exit__(None, None, None)
            print("   🔒 Core automation engine disconnected cleanly.", flush=True)
        except:
            pass
        print("⭐ [DONE] Script engine terminated flawlessly.", flush=True)


if __name__ == "__main__":
    run()