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
COMMENTED_JSON_FILE = "commented.json"

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
# RANDOM WAIT (REQUIRED LOCATOR DELAYS)
# =========================
def custom_random_wait(min_sec=5, max_sec=10):
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
# MAIN
# =========================
def run():
    print("[START] Script started", flush=True)

    # ========================================================
    # STATUS JSON VALIDATION
    # ========================================================
    status_path = Path(STATUS_JSON_FILE)
    if not status_path.exists():
        raise FileNotFoundError(f"❌ Status file {STATUS_JSON_FILE} not found!")

    with status_path.open("r", encoding="utf-8") as sf:
        status_data = json.load(sf)

    post_found = status_data.get("post_to_comment_found")
    comment_gen = status_data.get("comment_generated")

    # Dono values true honi chahiye, nahi toh exit
    if not (post_found is True and comment_gen is True):
        print("Not Ready to Comment Yet!", flush=True)
        sys.exit(0)

    target_url = status_data.get("link_to_post_to_comment")
    comment_to_post = status_data.get("comment")

    if not target_url or not comment_to_post:
        print("[ERROR] URL or Comment text missing in status.json despite flags being True.", flush=True)
        sys.exit(1)

    # Cookies setup
    cookies = load_cookies(Path(REDDIT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

    browser = None
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

        # ========================================================
        # DIRECT NAVIGATION TO TARGET URL
        # ========================================================
        print(f"[STEP] Navigating directly to post URL: {target_url}...", flush=True)
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"[OK] {target_url} opened completely", flush=True)
        
        # Post load hone ka wait
        custom_random_wait(5, 10)

        # URL extraction & duplicate handling
        full_url = page.url
        clean_url_match = re.match(r'(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)', full_url)
        clean_post_url = clean_url_match.group(1) if clean_url_match else full_url

        json_path = Path(COMMENTED_JSON_FILE)
        existing_urls = []
        if json_path.exists():
            try:
                with json_path.open("r", encoding="utf-8") as jf:
                    existing_urls = json.load(jf)
                    if not isinstance(existing_urls, list):
                        existing_urls = []
            except Exception as j_err:
                print(f"[WARNING] Reading history json failed: {j_err}", flush=True)

        if clean_post_url in existing_urls:
            print(f"[CLEAN EXIT] URL already commented in history: {clean_post_url}. Resetting status and exiting.", flush=True)
            
            # 1. status.json को रीसेट करें
            status_data["post_to_comment_found"] = False
            status_data["comment_generated"] = False
            status_data["link_to_post_to_comment"] = ""
            status_data["content_of_post_to_comment"] = ""
            status_data["comment"] = ""
            with status_path.open("w", encoding="utf-8") as sf:
                json.dump(status_data, sf, indent=4)
                
            # 2. सिर्फ ब्राउज़र बंद करें, बाकी काम नीचे वाला finally ब्लॉक अपने आप संभाल लेगा
            browser.close()
            sys.exit(0)

        # =========================
        # COMMENT AUTOMATION (PLAYWRIGHT)
        # =========================
        # 1. Click 'Go to comments' button agar available ho
        print("[STEP] Locating and clicking 'Go to comments' button...", flush=True)
        go_to_comments_btn = page.get_by_role('button', name='Go to comments')
        if go_to_comments_btn.count() > 0:
            go_to_comments_btn.click()
            custom_random_wait(5, 10)

        # 2. Locate empty comment input box
        print("[STEP] Locating comment input field...", flush=True)
        rich_text_editor = page.locator('[data-testid="comment-composer-richtext"], [contenteditable="true"]').first
        rich_text_editor.wait_for(state="visible", timeout=15000)
        rich_text_editor.click()
        custom_random_wait(5, 10)

        # 3. Type comment like a human
        print("[STEP] Typing comment from status.json via native keyboard emulation...", flush=True)
        for char in comment_to_post:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.04, 0.09))
            
        custom_random_wait(5, 10)

        # 4. Click 'Comment' Submit button
        print("[STEP] Clicking 'Comment' submit button...", flush=True)
        submit_btn = page.get_by_role('button', name='Comment', exact=True)
        submit_btn.click()
        print("[OK] Comment posted successfully!", flush=True)
        custom_random_wait(5, 10)

        # =========================
        # UPVOTE AUTOMATION
        # =========================
        print("[STEP] Attempting to upvote the post...", flush=True)
        upvote_btn_testid = page.get_by_test_id('action-row').get_by_role('button', name='Upvote').first
        upvote_btn_aria = page.get_by_role('button', name='Upvote').first

        if upvote_btn_testid.count() > 0 and upvote_btn_testid.is_visible():
            upvote_btn_testid.click()
            print("[OK] Post Upvoted via TestID locator!", flush=True)
        elif upvote_btn_aria.count() > 0 and upvote_btn_aria.is_visible():
            upvote_btn_aria.click()
            print("[OK] Post Upvoted via Aria locator!", flush=True)
        else:
            print("[WARNING] Upvote button could not be located or click state is blocked.", flush=True)
            
        custom_random_wait(5, 10)

        # History update
        if clean_post_url not in existing_urls:
            existing_urls.insert(0, clean_post_url)
        with json_path.open("w", encoding="utf-8") as jf:
            json.dump(existing_urls, jf, indent=4)
        print("[OK] History JSON updated successfully.", flush=True)

        # ========================================================
        # RESET STATUS JSON ON SUCCESSFUL RUN
        # ========================================================
        print(f"[STEP] Resetting workflow keys in {STATUS_JSON_FILE}...", flush=True)
        status_data["post_to_comment_found"] = False
        status_data["comment_generated"] = False
        status_data["link_to_post_to_comment"] = ""
        status_data["content_of_post_to_comment"] = ""
        status_data["comment"] = ""
        
        with status_path.open("w", encoding="utf-8") as sf:
            json.dump(status_data, sf, indent=4)
        print("[OK] status.json reset process complete.", flush=True)

        # Final close delay buffer (15 to 30 seconds as requested)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
        sys.exit(1)

    finally:
        if browser:
            try:
                browser.close()
            except:
                pass
        try:
            pw_cm.__exit__(None, None, None)
        except:
            pass

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)


if __name__ == "__main__":
    run()