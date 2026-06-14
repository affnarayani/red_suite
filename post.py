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
HEADLESS = False

REDDIT_COOKIES_FILE = "reddit_cookies.json.encrypted"
STATUS_JSON_FILE = "status.json"
REDDIT_POST_FILE = "reddit_post.json"

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
    # Regex \n+ ka use karke multiple newlines ko single \n mein convert kiya
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

    # Clean subreddit format (e.g., convert "r/Anxiety" or "Anxiety" to valid URL segment)
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
        human_type(page, post_title)

        # ====================================================
        # DYNAMIC SUBREDDIT FLAIR SELECTION WITH FALLBACK
        # ====================================================
        flair_mapping = {
            "anxiety": "Anxiety Resource",
            "mentalhealth": "Opinion / Thoughts",
            "productivity": "Book",
            "getdisciplined": "💡 Advice"
        }

        current_sub_lower = cleaned_sub_name.lower().strip()

        if current_sub_lower in flair_mapping:
            flair_name = flair_mapping[current_sub_lower]
            print(f"[STEP] Flair required for r/{cleaned_sub_name}. Selecting '{flair_name}'...", flush=True)

            # 1. Click 'Add flair and tags *'
            flair_btn = page.get_by_role('button', name='Add flair and tags *')
            flair_btn.wait_for(state="visible", timeout=20000)
            flair_btn.click()
            custom_random_wait(3, 6)

            # 2. Specific Radio Button dhoondhne aur click karne ki koshish karein
            flair_option = page.get_by_role('radio', name=flair_name)
            try:
                # Pehle short timeout (5s) ke sath check karein agar radio button seedhe mil jaye
                flair_option.wait_for(state="visible", timeout=5000)
                flair_option.click()
                custom_random_wait(3, 6)
            except Exception:
                # Agar radio button nahi mila, toh check karein 'View all flairs' button toh nahi hai
                print(f"[INFO] '{flair_name}' not visible instantly. Checking for 'View all flairs' button...", flush=True)
                view_all_btn = page.get_by_role('button', name='View all flairs')
                
                if view_all_btn.is_visible():
                    print("[STEP] 'View all flairs' button found. Clicking it to expand options...", flush=True)
                    view_all_btn.click()
                    custom_random_wait(3, 6)
                    
                    # Expand hone ke baad fir se radio button par click karne ki koshish karein
                    flair_option.wait_for(state="visible", timeout=15000)
                    flair_option.click()
                    custom_random_wait(3, 6)
                else:
                    # Agar 'View all flairs' bhi nahi mila toh workflow crash na ho, exception raise karein
                    raise RuntimeError(f"Could not find flair radio option '{flair_name}' nor 'View all flairs' button.")

            # 3. Click 'Add' Button
            add_btn = page.get_by_role('button', name='Add', exact=True)
            add_btn.wait_for(state="visible", timeout=20000)
            add_btn.click()
            custom_random_wait(3, 6)
            print(f"[OK] Flair '{flair_name}' applied successfully.", flush=True)
        else:
            print(f"[INFO] No flair setup needed for r/{cleaned_sub_name}. Moving directly to post body.", flush=True)
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

        # 3. Post successfully submit hone par status ko false set karna
        if program_success:
            print(f"[STEP] Resetting content_generated to false in {STATUS_JSON_FILE}...", flush=True)
            status_data["content_generated"] = False
            with status_path.open("w", encoding="utf-8") as sf:
                json.dump(status_data, sf, indent=4)
            print("[OK] status.json updated successfully.", flush=True)

        print("[DONE] Process context terminated cleanly.", flush=True)


if __name__ == "__main__":
    run()