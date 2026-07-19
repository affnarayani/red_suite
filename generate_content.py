import os
import sys
import json
import time
import base64
import random
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
# FILE PARSERS & WRITERS
# =========================
def get_random_subreddit() -> str:
    print("[STEP] Reading subreddits.txt...", flush=True)
    subreddits_file = Path("subreddits.txt")
    if not subreddits_file.exists():
        raise FileNotFoundError("❌ 'subreddits.txt' file nahi mila.")
    
    with subreddits_file.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]
        
    if not lines:
        raise ValueError("❌ 'subreddits.txt' khali hai.")
        
    selected_sub = random.choice(lines)
    print(f"[OK] Randomly selected subreddit: '{selected_sub}'", flush=True)
    return selected_sub


def get_posted_history() -> List[Dict[str, str]]:
    posted_file = Path("posted.json")
    if not posted_file.exists():
        return []
    try:
        with posted_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


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

    if status_data.get("content_generated") is True:
        print("Content already generated!", flush=True)
        sys.exit(0)
        
    print("[OK] Status check passed (content_generated is False). Proceeding...", flush=True)

    # File init/clear at the beginning
    article_file = Path("reddit_post.json")

    with article_file.open("w", encoding="utf-8") as f:
        f.write("")
    print("[OK] 'reddit_post.json' cleared/initialized", flush=True)

    # Get random subreddit and posted history
    try:
        subreddit_name = get_random_subreddit()
        posted_history = get_posted_history()
    except Exception as e:
        print(f"[ERROR] Configurations files read karne me dikkat aayi: {e}", flush=True)
        sys.exit(1)

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
        # NEW: CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
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

        # History context string format karein
        history_context = json.dumps(posted_history, indent=2) if posted_history else "None"

        # Prompt engineering for dynamic topic creation with expert persona
        prompt = (
            f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
            f"Do not print any JSON outside of a code block. "
            f"Do not add any text, explanation, or markdown before or after the code block.\n\n"

            f"TASK:\n"
            f"1. Personally come up with a highly engaging, thought-provoking, and relevant topic specifically tailored for the subreddit: {subreddit_name}.\n"
            f"2. Write a comprehensive Reddit post based on that generated topic.\n\n"

            f"CRITICAL RULES FOR TOPIC GENERATION:\n"
            f"- The topic must perfectly align with what users in {subreddit_name} love discussing.\n"
            f"- Here is the history of already posted topics on our end:\n"
            f"```json\n{history_context}\n```\n"
            f"- CRITICAL: Do NOT repeat any topic listed in the history above. If you address a similar core theme, you MUST approach it from a completely different, fresh, counterintuitive, or unexpected angle. A completely new topic is strongly preferred.\n\n"

            f"CRITICAL RULES FOR THE REDDIT POST:\n"
            f"1. NO promotional links, no CTAs, no product mentions anywhere in the post.\n"
            f"2. Write in first person.\n"
            f"3. Portray yourself as someone who has repeatedly observed, tested, experienced, or deeply studied this dynamic firsthand. Confidence should come from sharp observations and lived understanding, not from claiming authority.\n"
            f"4. DO NOT sound like a coach, guru, motivational speaker, productivity influencer, corporate blogger, LinkedIn writer, or self-help marketer.\n"
            f"5. Opening must hook immediately — start with 'I' and a gripping, relatable personal admission, realization, observation, contradiction, mistake, or uncomfortable truth.\n"
            f"6. Length: strictly 1500-2500 characters. No longer.\n"
            f"7. Use short paragraphs — maximum 3-4 lines each. Reddit readers skim.\n"
            f"8. Include 2-3 specific, highly actionable insights. The insights must emerge naturally from the story, observation, or experience. Avoid generic advice.\n"
            f"9. Include a counterintuitive observation that genuinely challenges common advice around the topic. The reframe should feel surprising yet obvious in hindsight, not like a motivational slogan.\n"
            f"10. Before the ending question, add a short closing reflection — 1-2 lines showing maturity, nuance, and a more evolved understanding of the issue.\n"
            f"11. End with an open question designed to invite genuine discussion and personal experiences.\n"
            f"12. Tone: like talking to a smart friend who respects your perspective, not teaching a rigid academic class.\n"
            f"13. NO bullet point walls — maximum one set of 2-3 points in the entire post.\n"
            f"14. Avoid sounding polished for the sake of sounding smart. Slight imperfections, hesitations, and natural human phrasing are welcome.\n"
            f"15. Every major insight should emerge from a concrete observation, habit, behavior, mistake, conversation, pattern, or real-world moment — not from abstract life advice.\n"
            f"16. Prefer specificity over wisdom. One oddly specific observation is more valuable than three generic truths.\n"
            f"17. Include at least ONE highly specific detail such as:\n"
            f"   - an exact time,\n"
            f"   - a sentence someone said,\n"
            f"   - a recurring habit,\n"
            f"   - a small embarrassing realization,\n"
            f"   - a strange behavior pattern,\n"
            f"   - a tiny moment that revealed something bigger.\n"
            f"18. The detail should feel naturally remembered, not artificially inserted.\n"
            f"19. The reader should occasionally think: 'I've done that exact thing' or 'That's weirdly specific.' Optimize for recognition rather than inspiration.\n"
            f"20. Do NOT manufacture dramatic stories. Small believable moments are better than cinematic ones.\n"
            f"21. Avoid broad claims about human psychology unless they emerge from an observation, example, or pattern in the story.\n"
            f"22. Do NOT end paragraphs with quote-like wisdom, slogan-style lines, or polished motivational statements.\n"
            f"23. If a sentence could easily appear on an Instagram quote card, rewrite it to sound more conversational and grounded.\n"
            f"24. The post should feel like something written by a thoughtful Redditor late at night after noticing something uncomfortable, not like a prepared article.\n\n"

            f"OUTPUT FORMAT — strictly inside a single JSON code block:\n"
            f"{{\n"
            f'  "title": "The exact title you came up with for this topic — problem-first, curiosity-driven, organic, no clickbait",\n'
            f'  "body": "Full Reddit post content here"\n'
            f"}}\n\n"

            f"REMINDER 1: Title must feel like a real Reddit post someone would naturally write, not a blog headline.\n"
            f"REMINDER 2: No Gumroad links, ebook references, funnels, marketing language, or promotional framing.\n"
            f"REMINDER 3: HUMAN VOICE IS NON-NEGOTIABLE.\n"
            f"Avoid generic AI phrasing and overused words such as 'journey', 'transformative', 'pivotal', 'delve', 'foster', 'crucial', 'unlock', 'game-changer', 'mindset shift', or similar content-marketing language.\n"
            f"REMINDER 4: Before finalizing, remove anything that sounds like a TED Talk, LinkedIn post, productivity blog, or motivational speech.\n"
            f"REMINDER 5: The final result should feel authentic enough that readers assume it came from a real person reflecting on a real observation."
        )

        print("[STEP] Entering prompt into textbox...", flush=True)
        textbox.first.fill(prompt)
        custom_random_wait(15, 30)

        print("[STEP] Locating and clicking send button...", flush=True)
        send_button = page.get_by_test_id('send-button')
        send_button.click()
        
        # Initial wait
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
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)

        # JSON parsing, validation and Topic Saving
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]
                
                parsed_json = json.loads(json_content.strip())
                
                # Naya ordered dictionary format
                final_ordered_json = {
                    "subreddit": subreddit_name,
                    "title": parsed_json.get("title", ""),
                    "body": parsed_json.get("body", "")
                }
                
                print("[STEP] Saving ordered dictionary to reddit_post.json...", flush=True)
                with article_file.open("w", encoding="utf-8") as f:
                    json.dump(final_ordered_json, f, indent=4, ensure_ascii=False)
                print("[OK] Article successfully saved to reddit_post.json with subreddit key on top", flush=True)

                # =====================================
                # UPDATE STATUS
                # =====================================
                print("[STEP] Updating status.json...", flush=True)
                status_data["content_generated"] = True
                with status_file.open("w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                print("[OK] status.json successfully updated (content_generated=True)", flush=True)
                
            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse karne me fail hua: {je}. Exiting script...", flush=True)
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
                try:
                    browser.close()
                except:
                    pass
                sys.exit(1)
        else:
            print("[ERROR] Save skip kiya gaya kyunki koi data fetch nahi hua. Exiting script...", flush=True)
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
            try:
                browser.close()
            except:
                pass
            sys.exit(1)

        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        custom_random_wait(15, 30)

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
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