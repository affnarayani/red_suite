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
SUBREDDITS_FILE = "subreddits.txt"
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
    # STATUS JSON PRE-CHECK
    # ========================================================
    status_path = Path(STATUS_JSON_FILE)
    status_data = {
        "content_generated": False,
        "post_to_comment_found": False,
        "link_to_post_to_comment": "",
        "content_of_post_to_comment": "",
        "comment_generated": False,
        "comment": ""  # FIXED: 'comment' key ko wapas default mein rakh diya hai
    }

    if status_path.exists():
        try:
            with status_path.open("r", encoding="utf-8") as sf:
                existing_status = json.load(sf)
                if isinstance(existing_status, dict):
                    # FIXED: Agar file mein pehle se galat 'commented' key ho toh use uda do
                    if "commented" in existing_status:
                        del existing_status["commented"]
                    status_data.update(existing_status)
        except Exception as json_err:
            print(f"[WARNING] Reading status.json failed, using defaults: {json_err}", flush=True)

    if status_data.get("post_to_comment_found") is True:
        print("[CLEAN EXIT] New Post to Comment Not Found (post_to_comment_found is already true). Exiting with status 0.", flush=True)
        sys.exit(0)

    cookies = load_cookies(Path(REDDIT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP
    # =========================
    stealth = Stealth()
    pw_cm = stealth.use_sync(sync_playwright())
    pw = pw_cm.__enter__()

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

        # =========================
        # SUBREDDIT SELECTION
        # =========================
        print(f"[STEP] Reading subreddits from {SUBREDDITS_FILE}...", flush=True)
        subreddits_path = Path(SUBREDDITS_FILE)
        
        if not subreddits_path.exists():
            raise FileNotFoundError(f"❌ Configured file {SUBREDDITS_FILE} not found!")

        with subreddits_path.open("r", encoding="utf-8") as f:
            subreddits = [line.strip() for line in f if line.strip()]

        if not subreddits:
            raise ValueError(f"❌ {SUBREDDITS_FILE} khali hai!")

        selected_subreddit = random.choice(subreddits)
        
        if selected_subreddit.startswith("/"):
            selected_subreddit = selected_subreddit[1:]
            
        target_url = f"https://www.reddit.com/{selected_subreddit}/new/?feedViewType=compactView"
        
        print(f"[STEP] Opening chosen Subreddit URL (Compact View): {target_url}...", flush=True)
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"[OK] {target_url} opened completely", flush=True)
        
        custom_random_wait(5, 10)

        # ========================================================
        # LINK INTERACTION (NO SCROLLING)
        # ========================================================
        print("[STEP] Searching for the first post link using get_by_role...", flush=True)
        
        post_links_by_role = page.locator("shreddit-post").get_by_role('link', name=re.compile(r'.+')).filter(
            has=page.locator("xpath=./ancestor-or-self::a[contains(@href, '/comments/')]")
        )
        
        post_link = post_links_by_role.first

        if post_link.count() == 0:
            print("[WARNING] get_by_role failed, falling back to any new comment link...", flush=True)
            post_link = page.locator("a[href*='/comments/']").first

        post_link.wait_for(state="visible", timeout=20000)
        post_link.scroll_into_view_if_needed()
        
        print("[STEP] Clicking on the post link...", flush=True)
        post_link.click()
        print("[OK] Post link clicked successfully!", flush=True)
        
        custom_random_wait(5, 10)

        # ========================================================
        # URL EXTRACTION & CLEANING
        # ========================================================
        full_url = page.url
        clean_url_match = re.match(r'(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)', full_url)
        clean_post_url = clean_url_match.group(1) if clean_url_match else full_url

        print(f"[NAVIGATED URL]: {clean_post_url}", flush=True)

        # >>>>>>> UPDATE HUA BLOCK (SAFE MATCHING) <<<<<<<
        commented_path = Path(COMMENTED_JSON_FILE)
        if commented_path.exists():
            try:
                with commented_path.open("r", encoding="utf-8") as cf:
                    commented_data = json.load(cf)
                
                if isinstance(commented_data, list):
                    # Dono side ke extracted URL aur saved URLs ka trailing slash (/) hata kar check karein
                    current_url_clean = clean_post_url.rstrip('/')
                    commented_urls_clean = [url.rstrip('/') for url in commented_data if isinstance(url, str)]
                    
                    if current_url_clean in commented_urls_clean:
                        print(f"[CLEAN EXIT] URL already exists in commented.json (Slash Matched). Skipping. Exiting 1.", flush=True)
                        browser.close()
                        pw_cm.__enter__().__exit__(None, None, None)
                        sys.exit(1)
            except Exception as e:
                print(f"[WARNING] Reading commented.json failed: {e}", flush=True)
        # >>>>>>> BLOCK END <<<<<<<
        
        print("[PROCEED] Extracting text body...", flush=True)

        # =========================
        # TEXT EXTRACTION
        # =========================
        text_body_locator = page.locator("shreddit-post-text-body [slot='text-body']").first
        post_text = ""
        
        try:
            text_body_locator.wait_for(state="attached", timeout=10000)
            post_text = text_body_locator.inner_text().strip()
        except:
            fallback_body = page.locator("[data-post-click-location='text-body']").first
            if fallback_body.count() > 0:
                post_text = fallback_body.inner_text().strip()

        print(f"[DEBUG] Extracted text length: {len(post_text)} characters", flush=True)

        # ========================================================
        # CHARACTER COUNT VALIDATION & STATUS UPDATE
        # ========================================================
        if len(post_text) <= 150:
            print("[CLEAN EXIT] Post content is 150 characters or less. Skipping this post. Terminating cleanly with status 0.", flush=True)
            browser.close()
            pw_cm.__enter__().__exit__(None, None, None)
            sys.exit(1)

        print("[PROCEED] Eligible post found (> 150 characters). Updating status.json...", flush=True)

        # Update JSON parameters
        status_data["post_to_comment_found"] = True
        status_data["link_to_post_to_comment"] = clean_post_url
        status_data["content_of_post_to_comment"] = post_text
        status_data["content_generated"] = False
        status_data["comment_generated"] = False
        status_data["comment"] = ""  # FIXED: 'comment' key ko wapas append kar diya hai

        with status_path.open("w", encoding="utf-8") as sf:
            json.dump(status_data, sf, indent=4)
            
        print("[OK] status.json has been successfully updated with the comment key!", flush=True)

        # Final close delay buffer as instructed (15 to 30 seconds)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
        sys.exit(1)

    finally:
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