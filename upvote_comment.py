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
from huggingface_hub import InferenceClient
from playwright_stealth import Stealth


# =========================
# CONFIG
# =========================
HEADLESS = True

REDDIT_COOKIES_FILE = "reddit_cookies.json.encrypted"
SUBREDDITS_FILE = "subreddits.txt"
COMMENTED_JSON_FILE = "commented.json"

PBKDF2_ITERATIONS = 200_000

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"


# =========================
# ENV
# =========================
load_dotenv()

DECRYPT_KEY = os.getenv("DECRYPT_KEY")
HF_TOKEN = os.getenv("HF_TOKEN")

if not DECRYPT_KEY:
    raise RuntimeError("DECRYPT_KEY missing")

if not HF_TOKEN:
    raise RuntimeError("HF_TOKEN missing")


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
            
        target_url = f"https://www.reddit.com/{selected_subreddit}"
        
        print(f"[STEP] Opening chosen Subreddit URL: {target_url}...", flush=True)
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"[OK] {target_url} opened completely", flush=True)
        
        # DELAY 1: Subreddit page load aur links render hone ke liye zaroori hai
        custom_random_wait(5, 10)

        # =========================
        # LINK INTERACTION
        # =========================
        print("[STEP] Filtering for pure post links...", flush=True)
        all_comment_links = page.locator("a[href*='/comments/']")
        pure_posts = all_comment_links.filter(
            has_not=page.locator("h1, h2, h3, h4, h5, h6, [class*='heading'], [id*='heading']")
        )
        
        post_link = pure_posts.first
        if post_link.count() == 0:
            post_link = all_comment_links.first

        post_link.wait_for(state="visible", timeout=20000)
        post_link.scroll_into_view_if_needed()
        
        print("[STEP] Clicking on the genuine user post link...", flush=True)
        post_link.click()
        print("[OK] Real post link clicked successfully!", flush=True)
        
        # DELAY 2: Click ke baad naya post page load hone ke liye zaroori hai
        custom_random_wait(5, 10)

        # ========================================================
        # URL EXTRACTION & EXISTING CHECK
        # ========================================================
        full_url = page.url
        clean_url_match = re.match(r'(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)', full_url)
        clean_post_url = clean_url_match.group(1) if clean_url_match else full_url

        print(f"[NAVIGATED URL]: {clean_post_url}", flush=True)

        # Read JSON file to check for duplicate URL
        json_path = Path(COMMENTED_JSON_FILE)
        existing_urls = []
        if json_path.exists():
            try:
                with json_path.open("r", encoding="utf-8") as jf:
                    existing_urls = json.load(jf)
                    if not isinstance(existing_urls, list):
                        existing_urls = []
            except Exception as j_err:
                print(f"[WARNING] Reading json failed: {j_err}", flush=True)

        if clean_post_url in existing_urls:
            print(f"[CLEAN EXIT] URL already commented in history: {clean_post_url}. Exiting with status 0.", flush=True)
            browser.close()
            pw_cm.__enter__().__exit__(None, None, None)
            sys.exit(0)

        print("[PROCEED] Fresh URL detected. Extracting text body...", flush=True)

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

        # =========================
        # CHARACTER COUNT VALIDATION
        # =========================
        if len(post_text) <= 150:
            print("[CLEAN EXIT] Post content is 150 characters or less. Terminating cleanly with status 0.", flush=True)
            browser.close()
            pw_cm.__enter__().__exit__(None, None, None)
            sys.exit(0)

        print("[PROCEED] Post content is greater than 150 characters. Continuing workflow...", flush=True)

        # ========================================================
        # HF AI COMMENT GENERATION (IMPROVED PROMPT & CONSTRAINTS)
        # ========================================================
        print("[STEP] Initializing Hugging Face InferenceClient chat protocol...", flush=True)
        client = InferenceClient(model="meta-llama/Meta-Llama-3-8B-Instruct", token=HF_TOKEN)
        
        system_prompt = (
            "You are an expert self-improvement mentor, author, and mindset coach participating in a community discussion. "
            "Read the provided post and write a highly valuable, authoritative, yet empathetic comment that naturally positions you as an expert in the self-improvement niche.\n\n"
            
            "EXPERT ENGAGEMENT GUIDELINES:\n"
            "1. Speak with Authority & Experience: Use a grounded, mature, and confident tone. Avoid generic AI phrases, but also avoid sounding like a clueless user. Speak as someone who deeply understands human psychology and habits.\n"
            "2. Deliver High Value Instantly: Break down the root cause of the OP's problem in a concise way. Offer one actionable, practical takeaway, perspective, or mental model that they can apply immediately.\n"
            "3. Invite Deeper Reflection: Instead of asking for help, end with a thought-provoking question or a subtle prompt that challenges the OP or readers to think deeper about their growth journey (this naturally triggers profile curiosity).\n"
            "4. Maintain strict logical timeline: Carefully analyze whether the author is asking about a future action, an ongoing issue, or a past event. Match their reality perfectly without assuming unstated facts.\n\n"
            
            "STRICT CONSTRAINTS:\n"
            "1. The entire comment body must be strict MAXIMUM of 600 characters long.\n"
            "2. Do NOT surround the comment or any text with quote marks (neither single nor double inverted commas).\n"
            "3. Do NOT use any asterisk characters (*) anywhere in the entire text. No bolding or italicizing formatting allowed.\n"
            "4. Do NOT include greetings, intro phrases, pre-text, or post-text like 'Here is a response:'. Just output the direct comment text instantly."
        )
        
        user_prompt = f"Subreddit Context: {clean_post_url}\nPost Text Body:\n{post_text}"
        
        try:
            res = client.chat.completions.create(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                max_tokens=300,
                temperature=0.7,
            )
            generated_comment = res.choices[0].message.content.strip().replace('"', '').replace('*', '')
            print(f"[AI GENERATED COMMENT]:\n{generated_comment}\n", flush=True)
        except Exception as hf_err:
            print(f"❌ Error during AI generation layer: {hf_err}", flush=True)
            raise hf_err

        # =========================
        # COMMENT AUTOMATION (PLAYWRIGHT)
        # =========================
        # 1. Click 'Go to comments' button
        print("[STEP] Locating and clicking 'Go to comments' button...", flush=True)
        go_to_comments_btn = page.get_by_role('button', name='Go to comments')
        if go_to_comments_btn.count() > 0:
            go_to_comments_btn.click()
            # DELAY 3: Viewport adjust hone ke liye zaroori hai
            custom_random_wait(5, 10)

        # 2. Locate empty comment input box
        print("[STEP] Locating comment input field...", flush=True)
        rich_text_editor = page.locator('[data-testid="comment-composer-richtext"], [contenteditable="true"]').first
        rich_text_editor.wait_for(state="visible", timeout=15000)
        rich_text_editor.click()
        # DELAY 4: Focus stable hone ke liye zaroori hai
        custom_random_wait(5, 10)

        # 3. Type like a human using page.keyboard (Fluctuation proof)
        print("[STEP] Typing comment into text field via native keyboard emulation...", flush=True)
        for char in generated_comment:
            page.keyboard.type(char)
            time.sleep(random.uniform(0.04, 0.09)) # Human stroke delay
            
        # DELAY 5: Submit button clickable hone ke liye zaroori hai
        custom_random_wait(5, 10)

        # 4. Click 'Comment' Submit button
        print("[STEP] Clicking 'Comment' submit button...", flush=True)
        submit_btn = page.get_by_role('button', name='Comment', exact=True)
        submit_btn.click()
        print("[OK] Comment posted successfully!", flush=True)
        # DELAY 6: Comment process/refresh hone ke liye zaroori hai
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
            
        # DELAY 7: Network sync aur update confirm hone ke liye zaroori hai
        custom_random_wait(5, 10)

        # =========================
        # APPEND TO JSON (PREPEND AT INDEX 0)
        # =========================
        print(f"[STEP] Prepended fresh URL to top of {COMMENTED_JSON_FILE}...", flush=True)
        
        if clean_post_url not in existing_urls:
            existing_urls.insert(0, clean_post_url)

        with json_path.open("w", encoding="utf-8") as jf:
            json.dump(existing_urls, jf, indent=4)
        print("[OK] JSON history updated successfully at top.", flush=True)

        # Final close delay buffer (30-60 seconds)
        custom_random_wait(30, 60)

    except SystemExit:
        # Business logic normal exits (sys.exit(0)) ko direct bypass hone dein
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