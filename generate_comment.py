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

COOKIES_DIR = Path("cookies")
encrypted_files = list(COOKIES_DIR.glob("*.encrypted"))

if not encrypted_files:
    raise RuntimeError("❌ No .encrypted cookie files found in 'cookies/' folder")

CHATGPT_COOKIES_FILE = random.choice(encrypted_files)
print(f"[OK] Randomly selected cookie file: {CHATGPT_COOKIES_FILE.name}", flush=True)

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
def custom_random_wait(min_sec, max_sec):
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

    # normalize SameSite and PartitionKey
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

    # =========================
    # STATUS CHECK
    # =========================
    status_file = Path("status.json")
    if not status_file.exists():
        print("[ERROR] status.json file nahi mila. Exiting...", flush=True)
        sys.exit(0)
        
    try:
        with status_file.open("r", encoding="utf-8") as f:
            status_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] status.json parse nahi ho paya: {e}. Exiting...", flush=True)
        sys.exit(0)

    post_found = status_data.get("post_to_comment_found", False)
    comment_gen = status_data.get("comment_generated", False)

    # Condition Check (Pylance typo fixed here)
    if post_found is True and comment_gen is False:
        print("[OK] Status check passed (post_to_comment_found is True & comment_generated is False). Proceeding...", flush=True)
    else:
        if post_found is False:
            print("Comment Not Generated Yet!", flush=True)
        elif comment_gen is True:
            print("Comment already generated!", flush=True)
        sys.exit(0)

    # Target content extract karna prompt ke liye
    post_content = status_data.get("content_of_post_to_comment", "")
    if not post_content:
        print("[ERROR] content_of_post_to_comment khali hai. Exiting...", flush=True)
        sys.exit(0)

    cookies = load_cookies(Path(CHATGPT_COOKIES_FILE))
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # =========================
    # STEALTH SETUP & LOGIN
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

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto(
            "https://chatgpt.com/",
            wait_until="load"
        )
        print("[OK] URL opened successfully (Logged In)", flush=True)

        # 15 to 30 seconds random wait after page load
        custom_random_wait(30, 60)

        # ============================================
        # CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
        # ============================================
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = page.get_by_role('button', name=list(map(lambda x: x.compile(r'.*Free, open'), [__import__('re')]))[0])
        
        if profile_button.count() > 0:
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{profile_button.first.get_attribute('aria-label') or 'User Account'}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        # =========================
        # AUTOMATION FLOW
        # =========================
        print("[STEP] Locating chat textbox...", flush=True)
        
        # Fallback Strategy for Textbox Locators
        textbox = page.get_by_role('textbox', name='Chat with ChatGPT')
        
        if textbox.count() == 0:
            print("[INFO] Fallback 1: Searching for 'Ask anything' paragraph inside textbox context...", flush=True)
            textbox = page.locator('div[contenteditable="true"]').filter(has=page.locator('p', has_text='Ask anything')).first
            
        if textbox.count() == 0:
            print("[INFO] Fallback 2: Searching via CSS Selector '#prompt-textarea'...", flush=True)
            textbox = page.locator('#prompt-textarea')

        # Trigger action if found
        if textbox.count() > 0:
            textbox.first.click()
            print("[OK] Textbox located and clicked successfully.", flush=True)
        else:
            raise RuntimeError("❌ Textbox locator load nahi ho paya (All strategies failed).")
            
        custom_random_wait(15, 30)

        # ============================================
        # PROMPT FOR REDDIT COMMENTING (WITH 300-500 CHAR LIMIT)
        # ============================================
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
            f"Do not print any JSON outside of a code block. "
            f"Do not add any text, explanation, or markdown before or after the code block.\n\n"

            f"Read the following Reddit post content carefully:\n"
            f"\"\"\"\n{post_content}\n\"\"\"\n\n"

            f"Your task is to write a highly engaging, attention-grabbing, and upvote-worthy Reddit comment responding to this post.\n\n"

            f"CRITICAL RULES FOR THE COMMENT:\n"
            f"1. Connect deeply with the author's feelings. Give them the exact validation, encouragement, and supportive response they want to hear based on their breakthrough.\n"
            f"2. Tone must sound 100% human, casual, conversational, and authentic. Talk like a supportive friend who also happens to be an expert in mindset/growth (an 'expert friend').\n"
            f"3. Strictly AVOID sounding robotic, overly technical, or medical. Do not use corporate fluff or clinical psychology jargon.\n"
            f"4. SPECIFICITY RULE: The comment MUST reference at least ONE specific detail "
            f"from the post itself — a concrete moment, result, or decision the author mentioned. "
            f"Generic observations that could apply to any post are not allowed.\n"
            f"5. CHARACTER LIMIT: The total length of the comment text must be strictly between 300 and 500 characters long (including spaces). No shorter than 300, no longer than 500.\n"
            f"6. NO NEWLINES: The final comment body text MUST NOT contain any newline characters (\\n), line breaks, or paragraphs. It must be written entirely as a single continuous line of text.\n"
            f"7. END STRONG: Do not end with phrases like 'huge respect', 'hope this helps', "
            f"'thanks for sharing', or 'hope more people see this'. "
            f"End with either a genuine personal reaction OR a natural curiosity-driven question "
            f"that directly relates to something specific in the post.\n"
            f"8. Do NOT use any markdown styling like asterisks (*) for bolding or italics.\n"
            f"9. Do NOT include greetings, intro phrases, or post-text outside the JSON block.\n\n"

            f"OUTPUT FORMAT — strictly inside a single JSON code block:\n"
            f"{{\n"
            f'  "comment": "Your direct, single-line conversational comment goes here"\n'
            f"}}\n"
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        # Initial wait taaki generation properly start ho sake
        custom_random_wait(30, 60)

        # ============================================
        # STABLE 15-SECOND POLLING LIVE STREAM CHECK
        # ============================================
        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        code_block_locator = page.locator('#code-block-viewer pre')
        
        json_content = None
        for attempt in range(1, 6):
            print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)
            
            if code_block_locator.count() > 0:
                print("[OK] Code block visible, parsing live text size variations...", flush=True)
                
                last_length = 0
                max_check_cycles = 15
                
                for cycle in range(max_check_cycles):
                    time.sleep(15)
                    
                    current_text = code_block_locator.first.inner_text().strip()
                    current_length = len(current_text)
                    
                    print(f"[STREAM INFO] Cycle {cycle+1}: Previous Length = {last_length}, Current Length = {current_length}", flush=True)
                    
                    if current_length > 0 and current_length == last_length:
                        if current_text.endswith("}"):
                            json_content = current_text
                            print("[OK] Content generation is fully finished and finalized.", flush=True)
                            break
                        else:
                            print("[WARNING] Text generation paused but JSON bracket '}' is missing. Waiting further...", flush=True)
                        
                    last_length = current_length
                
                if json_content:
                    break
            
            if attempt < 5:
                print(f"[WARNING] Code block completely write nahi hua ya block mila nahi. Next retry window...", flush=True)
                custom_random_wait(30, 60)
            else:
                print("❌ Max retries reached. Streaming complete nahi ho payi. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        # JSON parsing, validation and Status Sync
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                generated_comment_text = parsed_json.get("comment", "").strip()

                # Double safety: Remove any stray newlines from string
                generated_comment_text = generated_comment_text.replace("\n", " ").replace("\r", "")
                
                # =====================================
                # UPDATE STATUS.JSON ONLY (NO TOPICS.TXT INTERACTION)
                # =====================================
                print("[STEP] Updating status.json with comment data...", flush=True)
                status_data["comment"] = generated_comment_text
                status_data["comment_generated"] = True
                
                with status_file.open("w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                print("[OK] status.json successfully updated (comment appended & comment_generated=True)", flush=True)
                
            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse karne me fail hua: {je}. Exiting script...", flush=True)
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skip kiya gaya kyunki koi data fetch nahi hua. Exiting script...", flush=True)
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        # 15 to 30 seconds random wait before closing the browser normally
        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        # CAPTURE SCREENSHOT ON ERROR
        if 'page' in locals() and page:
            try:
                screenshot_path = "error_screenshot.png"
                page.screenshot(path=screenshot_path, full_page=True)
                print(f"[OK] Error screenshot captured: {screenshot_path}", flush=True)
            except Exception as screenshot_err:
                print(f"[WARNING] Could not capture screenshot: {screenshot_err}", flush=True)
        if browser:
            try:
                browser.close()
            except:
                pass
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

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()