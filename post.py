import os
import sys
import json
import time
import base64
import random
import re
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
STATUS_JSON_FILE = "status.json"
REDDIT_POST_FILE = "reddit_post.json"
POSTED_JSON_FILE = "posted.json"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")


# =========================
# RANDOM WAIT
# =========================
def custom_random_wait(min_sec=15, max_sec=30):
    seconds = random.uniform(min_sec, max_sec)
    print(f"[WAIT] Sleeping for {seconds:.2f} seconds...", flush=True)
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
    print("[STEP] Loading cookies...", flush=True)

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

    print("[OK] Cookies loaded", flush=True)
    return cookies


# =========================
# HUMAN TYPING SIMULATOR
# =========================
def human_type(page, text_to_type: str):
    """
    Simulates human typing character by character.
    Converts multiple consecutive newlines (\n\n, \n\n\n, etc.) into a single \n.
    """
    cleaned_text = re.sub(r'\n+', '\n', text_to_type)
    
    for char in cleaned_text:
        if char == '\n':
            page.keyboard.press("Enter")
        else:
            page.keyboard.type(char)
        time.sleep(random.uniform(0.04, 0.09)) # Human stroke delays


# =========================
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # 1. Check status.json first
    status_path = Path(STATUS_JSON_FILE)
    if not status_path.exists():
        print(f"❌ {STATUS_JSON_FILE} not found. Please create it first.", flush=True)
        sys.exit(1)

    with status_path.open("r", encoding="utf-8") as sf:
        status_data = json.load(sf)

    if not status_data.get("content_generated", False):
        print("Please generate the content first", flush=True)
        sys.exit(0)

    print("[PROCEED] content_generated is true. Proceeding with execution...", flush=True)

    # 2. Read reddit_post.json data
    post_path = Path(REDDIT_POST_FILE)
    if not post_path.exists():
        print(f"❌ Configured file {REDDIT_POST_FILE} not found!", flush=True)
        sys.exit(1)

    with post_path.open("r", encoding="utf-8") as pf:
        post_data = json.load(pf)

    subreddit_name = post_data.get("subreddit", "").strip()
    post_title = post_data.get("title", "").strip()
    post_body = post_data.get("body", "").strip()

    if not subreddit_name or not post_title or not post_body:
        print("❌ Subreddit, title, or body missing in reddit_post.json!", flush=True)
        sys.exit(1)

    # Clean subreddit format
    if subreddit_name.startswith("r/"):
        cleaned_sub_name = subreddit_name[2:]
    elif subreddit_name.startswith("/r/"):
        cleaned_sub_name = subreddit_name[3:]
    else:
        cleaned_sub_name = subreddit_name

    target_subreddit_url = f"https://www.reddit.com/r/{cleaned_sub_name}/"

    # Load Cookies
    cookies = load_cookies(Path(REDDIT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # Playwright Stealth Setup
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    program_success = False

    try:
        browser = pw.chromium.launch(
            headless=HEADLESS,
            args=[
                "--start-maximized",
                "--disable-blink-features=AutomationControlled"
            ]
        )

        context = browser.new_context(
            no_viewport=True,
            user_agent=USER_AGENT
        )

        context.grant_permissions(["clipboard-read", "clipboard-write"])
        print("[STEP] Adding cookies to browser context...", flush=True)
        context.add_cookies(cookies)

        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        # Step 1: Open Target Subreddit directly after login
        print(f"[STEP] Navigating to subreddit URL: {target_subreddit_url}...", flush=True)
        page.goto(target_subreddit_url, wait_until="domcontentloaded")
        print(f"[OK] {target_subreddit_url} opened completely.", flush=True)

        # Step 2: Wait 15-30s and click 'Create Post'
        custom_random_wait(15, 30)
        print("[STEP] Clicking 'Create Post' button...", flush=True)
        create_post_btn = page.get_by_test_id('create-post')
        create_post_btn.wait_for(state="visible", timeout=20000)
        create_post_btn.click()

        # Step 3: Wait 15-30s and focus/type Title
        custom_random_wait(15, 30)
        print("[STEP] Focusing Title field and typing title...", flush=True)
        title_box = page.get_by_role('textbox', name='Title')
        title_box.wait_for(state="visible", timeout=20000)
        title_box.click()
        human_type(page, f"{post_title}")

        # ====================================================
        # CONDITIONAL FLAIR SELECTION WITH KEYBOARD SEQUENCES
        # ====================================================
        print("[STEP] Checking if flair button is available on page...", flush=True)
        flair_btn = page.get_by_role('button', name='Add flair and tags *')
        
        if flair_btn.is_visible():
            print("[INFO] Mandatory flair button found. Initiating interaction sequence...", flush=True)
            flair_btn.click()
            custom_random_wait(3, 6)

            for i in range(1, 4):
                print(f"[STEP] Sending TAB key ({i}/3)...", flush=True)
                page.keyboard.press("Tab")
                custom_random_wait(3, 6)

            print("[STEP] Sending SPACE key...", flush=True)
            page.keyboard.press("Space")
            custom_random_wait(3, 6)

            print("[STEP] Clicking 'Add' button to submit flair...", flush=True)
            add_btn = page.get_by_role('button', name='Add', exact=True)
            add_btn.wait_for(state="visible", timeout=20000)
            add_btn.click()
            custom_random_wait(3, 6)
            print("[OK] Flair interaction sequence completed successfully.", flush=True)
        else:
            print("[INFO] Flair button not found or not visible. Skipping flair selection safely...", flush=True)
        # ====================================================

        # Step 4: Wait 15-30s and focus/type Post Body text field
        custom_random_wait(15, 30)
        print("[STEP] Focusing Post body field and typing body paragraphs...", flush=True)
        body_box = page.get_by_role('textbox', name='Post body text field')
        body_box.wait_for(state="visible", timeout=20000)
        body_box.click()
        human_type(page, post_body)

        # Step 5: Wait 15-30s and click 'Post' button
        custom_random_wait(15, 30)
        print("[STEP] Clicking 'Post' submit button...", flush=True)
        post_btn = page.get_by_role('button', name='Post', exact=True)
        post_btn.wait_for(state="visible", timeout=20000)
        post_btn.click()
        print("[OK] Post submitted successfully!", flush=True)

        # Step 6: Final wait before closing browser
        custom_random_wait(15, 30)
        program_success = True

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation workflow failed:", e, flush=True)
        sys.exit(1)

    finally:
        try:
            browser.close()
            print("[INFO] Browser context closed.", flush=True)
        except:
            pass

        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        # 3. Post successfully submit hone par status aur history update karna
        if program_success:
            # 3a. Update posted.json at the top
            print(f"[STEP] Appending new topic to the top of {POSTED_JSON_FILE}...", flush=True)
            posted_path = Path(POSTED_JSON_FILE)
            existing_history = []
            
            if posted_path.exists():
                try:
                    with posted_path.open("r", encoding="utf-8") as hf:
                        existing_history = json.load(hf)
                        if not isinstance(existing_history, list):
                            existing_history = []
                except Exception:
                    existing_history = []
            
            new_entry = {
                "topic": post_title,
                "subreddit": subreddit_name
            }
            
            # List ke starting (index 0) me insert karne ke liye add kiya
            updated_history = [new_entry] + existing_history
            
            with posted_path.open("w", encoding="utf-8") as hf:
                json.dump(updated_history, hf, indent=4, ensure_ascii=False)
            print(f"[OK] {POSTED_JSON_FILE} successfully updated (New entry added on top).", flush=True)

            # 3b. Reset content_generated to false in status.json
            print(f"[STEP] Resetting content_generated to false in {STATUS_JSON_FILE}...", flush=True)
            status_data["content_generated"] = False
            with status_path.open("w", encoding="utf-8") as sf:
                json.dump(status_data, sf, indent=4)
            print("[OK] status.json updated successfully.", flush=True)

        print("[DONE] Process context terminated cleanly.", flush=True)


if __name__ == "__main__":
    run()