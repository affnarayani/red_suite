import os
import sys
import time
import base64
import random
import re
import json
import requests
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
BANNED_FILE = "banned.txt"

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

    cookies = load_cookies(Path(REDDIT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # Subreddits list load karna
    print(f"[STEP] Reading subreddits from {SUBREDDITS_FILE}...", flush=True)
    subreddits_path = Path(SUBREDDITS_FILE)
    
    if not subreddits_path.exists():
        raise FileNotFoundError(f"❌ Configured file {SUBREDDITS_FILE} not found!")

    with subreddits_path.open("r", encoding="utf-8") as f:
        raw_subreddits = [line.strip() for line in f if line.strip()]

    if not raw_subreddits:
        raise ValueError(f"❌ {SUBREDDITS_FILE} khali hai!")

    # Banned subreddits list jo iss execution me dhoondhe jayenge
    banned_subs_detected = []

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

        # Loop chalakar 1 by 1 har subreddit par jana
        for current_sub in raw_subreddits:
            print(f"\n[LOOP] Processing Subreddit: {current_sub}", flush=True)
            
            clean_sub = current_sub[1:] if current_sub.startswith("/") else current_sub
            target_url = f"https://www.reddit.com/{clean_sub}/rising/?feedViewType=compactView"
            
            print(f"[STEP] Opening Subreddit URL: {target_url}...", flush=True)
            try:
                page.goto(target_url, wait_until="domcontentloaded")
                custom_random_wait(15, 30)

                # ========================================================
                # LINK INTERACTION (WITH SCROLL RETRY FOR SLOW PAGES)
                # ========================================================
                print("[STEP] Searching for the first post link...", flush=True)
                
                post_link = None
                # 5-6 baar scroll karke post link dhoondhne ki koshish karega
                for post_search_attempt in range(6):
                    post_links_by_role = page.locator("shreddit-post").get_by_role('link', name=re.compile(r'.+')).filter(
                        has=page.locator("xpath=./ancestor-or-self::a[contains(@href, '/comments/')]")
                    )
                    
                    if post_links_by_role.first.count() > 0:
                        post_link = post_links_by_role.first
                        break
                        
                    fallback_link = page.locator("a[href*='/comments/']").first
                    if fallback_link.count() > 0:
                        post_link = fallback_link
                        break
                        
                    if post_search_attempt < 5:
                        print(f"[SCROLL] First post not found yet. Scrolling to trigger render (Attempt {post_search_attempt + 1}/5)...", flush=True)
                        page.evaluate("window.scrollBy(0, 300);")
                        time.sleep(random.uniform(1.5, 2.5))

                if post_link is None:
                    print(f"[WARNING] Failed to locate any post link on {current_sub} even after multiple scrolls. Skipping...", flush=True)
                    continue

                # Locator milne ke baad safe wait aur click sequence
                post_link.wait_for(state="visible", timeout=30000)
                post_link.scroll_into_view_if_needed()
                
                print("[STEP] Clicking on the first post link...", flush=True)
                post_link.click()
                custom_random_wait(15, 30)

                full_url = page.url
                clean_url_match = re.match(r'(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)', full_url)
                clean_post_url = clean_url_match.group(1) if clean_url_match else full_url
                print(f"[NAVIGATED URL]: {clean_post_url}", flush=True)

                # ========================================================
                # BAN DETECTION WITH SCROLLING LOOP
                # ========================================================
                is_banned = False
                ban_locator = page.get_by_text("You're currently banned from", exact=False)

                # 5-6 baar scroll karne ka loop (Shuru me + 5 extra scroll attempts)
                for scroll_attempt in range(6):
                    if ban_locator.is_visible():
                        is_banned = True
                        break
                    
                    if scroll_attempt < 5:  # Last loop me faltu scroll na ho
                        print(f"[SCROLL] Ban message not found. Scrolling down (Attempt {scroll_attempt + 1}/5)...", flush=True)
                        page.evaluate("window.scrollBy(0, 400);")  # Thoda niche scroll karega
                        time.sleep(random.uniform(1.5, 2.5))  # Chhota human delay scroll ke baad

                if is_banned:
                    print(f"[ALERT] Banned from {current_sub}! Adding to tracking list.", flush=True)
                    banned_subs_detected.append(current_sub)
                    
                    # banned.txt mein new line par append karna
                    banned_path = Path(BANNED_FILE)
                    with banned_path.open("a", encoding="utf-8") as bf:
                        bf.write(f"{current_sub}\n")
                    print(f"[OK] Appended {current_sub} to {BANNED_FILE}", flush=True)
                else:
                    print(f"[INFO] No ban detected for {current_sub}. Moving to next subreddit.", flush=True)

                # Buffer delay before next subreddit
                custom_random_wait(3, 6)

            except Exception as e:
                print(f"[WARNING] Failed to process {current_sub} due to error: {e}. Skipping...", flush=True)
                continue

        # ========================================================
        # REWRITE SUBREDDITS.TXT (CLEAN REMOVAL)
        # ========================================================
        if banned_subs_detected:
            print("\n[STEP] Cleaning up subreddits.txt...", flush=True)
            # Sirf unhi subreddits ko rkhna jo banned nahi hain
            remaining_subs = [sub for sub in raw_subreddits if sub not in banned_subs_detected]
            
            with subreddits_path.open("w", encoding="utf-8") as f:
                for sub in remaining_subs:
                    f.write(f"{sub}\n")
            print(f"[OK] Removed banned subreddits from {SUBREDDITS_FILE}. No blank lines left.", flush=True)

        print("\n[STEP] All subreddits processed. Initiating final wait...", flush=True)
        custom_random_wait(10, 15)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
        # CAPTURE SCREENSHOT ON ERROR
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
                
                imgbb_key = os.getenv("IMGBBB_API_KEY")
                if imgbb_key:
                    print("[OK] Uploading screenshot to ImgBB...", flush=True)
                    url = f"https://api.imgbb.com/1/upload?expiration=86400&key={imgbb_key}"
                    
                    with open(screenshot_path, "rb") as file:
                        response = requests.post(url, files={"image": file})
                    
                    if response.status_code == 200:
                        res_data = response.json()
                        direct_url = res_data["data"]["display_url"]
                        print("\n" + "="*50, flush=True)
                        print(f"👉 DIRECT SCREENSHOT LINK: {direct_url}", flush=True)
                        print("="*50 + "\n", flush=True)
                    else:
                        print(f"[WARNING] ImgBB Upload Failed Status: {response.status_code}", flush=True)
                else:
                    print("[WARNING] IMGBBB_API_KEY environment variable not found.", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture or upload screenshot: {screenshot_err}", flush=True)
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

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)


if __name__ == "__main__":
    run()