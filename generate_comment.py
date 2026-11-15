import json
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import CHATGPT_COOKIES_DIR, STATUS_FILE


def _pick_chatgpt_cookie():
    encrypted_files = list(Path(CHATGPT_COOKIES_DIR).glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError(f"[ERROR] No .encrypted cookie files found in '{CHATGPT_COOKIES_DIR}' folder")
    chosen = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {chosen.name}", flush=True)
    return chosen


def _build_comment_prompt(post_content):
    return (
        f"IMPORTANT: Your entire response must be wrapped in a single ```json code block. "
        f"Do not print any JSON outside of a code block. "
        f"Do not add any text, explanation, or markdown before or after the code block.\n\n"

        f"Read the following Reddit post content carefully:\n"
        f"\"\"\"\n{post_content}\n\"\"\"\n\n"

        f"Your task is to write a thoughtful, natural Reddit comment that adds value to the discussion.\n\n"

        f"Before writing the comment, silently analyze the post and determine:\n"
        f"- Is the author asking for advice?\n"
        f"- Sharing a success or breakthrough?\n"
        f"- Telling a personal story?\n"
        f"- Expressing an opinion?\n"
        f"- Venting or expressing frustration?\n"
        f"- Starting a discussion?\n\n"

        f"Identify the part of the post that creates the strongest opportunity for meaningful discussion and build the comment around it naturally.\n"
        f"Do not explicitly state that it is the most important, key, interesting, central, or main point of the post.\n\n"

        f"Do not try to respond equally to every point.\n\n"

        f"CRITICAL RULES FOR THE COMMENT:\n\n"

        f"1. Match the emotional tone and intent of the post.\n"
        f"   - If the author is sharing a success, breakthrough, lesson, or positive change, respond with validation, encouragement, and thoughtful reflection.\n"
        f"   - If the author is asking a question or seeking advice, contribute a useful insight, personal experience, or perspective that directly helps answer the question.\n"
        f"   - If the author is venting or struggling, respond with empathy and understanding before offering perspective.\n"
        f"   - Do not default to encouragement when it does not fit the post.\n\n"

        f"2. Sound 100% human, casual, conversational, and authentic.\n"
        f"   Write like a thoughtful Reddit user speaking from genuine experience.\n"
        f"   Do not sound like a coach, therapist, motivational speaker, marketer, content creator, or AI assistant.\n\n"

        f"3. Strictly avoid robotic, overly polished, corporate, motivational, clinical, or self-help-book language.\n\n"

        f"4. SPECIFICITY RULE:\n"
        f"   The comment must directly reference and build upon a specific idea, example, decision, result, observation, or question from the post.\n"
        f"   The comment should feel impossible to paste under a different post without obvious mismatch.\n\n"

        f"5. TARGET LENGTH:\n"
        f"   Target 150-450 characters including spaces.\n"
        f"   Use only as many words as needed.\n"
        f"   Never add filler just to reach a length target.\n\n"

        f"6. NO NEWLINES:\n"
        f"   The final comment text must be a single continuous line.\n"
        f"   No newline characters, paragraphs, or line breaks are allowed.\n\n"

        f"7. END NATURALLY:\n"
        f"   The comment may end with:\n"
        f"   - A thoughtful question\n"
        f"   - A personal observation\n"
        f"   - A concise insight\n"
        f"   - A genuine reaction\n"
        f"   Do not force a question if the comment already feels complete.\n\n"

        f"8. Do not use markdown formatting such as *, **, _, bullet points, numbered lists, emojis, hashtags, or decorative characters.\n\n"

        f"9. Do not include greetings, introductions, disclaimers, or any text outside the JSON block.\n\n"

        f"10. Avoid common AI-sounding or overused Reddit phrases such as:\n"
        f"    'you've cracked the code'\n"
        f"    'you're onto something'\n"
        f"    'this resonates'\n"
        f"    '100% agree'\n"
        f"    'this is the way'\n"
        f"    'more people need to hear this'\n"
        f"    'thanks for sharing'\n"
        f"    'hope this helps'\n"
        f"    'huge respect'\n"
        f"    'keep it up'\n"
        f"    or similar generic filler.\n\n"

        f"11. Whenever natural, move the discussion forward by adding a useful observation, personal experience, alternative perspective, or follow-up thought rather than simply agreeing.\n\n"

        f"12. Valuable discussion is preferred over automatic validation.\n"
        f"    If appropriate, respectfully challenge, refine, or expand on the author's idea instead of automatically agreeing.\n\n"

        f"13. Vary the structure naturally.\n"
        f"    Do not repeatedly use the same patterns such as:\n"
        f"    - Starting with 'What helped me was...'\n"
        f"    - Starting with agreement\n"
        f"    - Ending every comment with a question\n"
        f"    - Following the same advice → explanation → question structure\n"
        f"    Mix observations, experiences, realizations, questions, and alternative viewpoints naturally.\n\n"

        f"14. Write as if you are a real Reddit user contributing to the discussion, not someone trying to maximize engagement.\n"
        f"    The comment should feel useful, authentic, spontaneous, and believable.\n\n"

        f"15. Prioritize comments that earn replies and upvotes because they add value, insight, perspective, relatability, or useful experience—not because they are excessively positive.\n\n"

        f"16. Prefer adding a new observation, distinction, implication, or perspective rather than simply rephrasing the author's point.\n"
        f"    Avoid comments that mainly summarize, paraphrase, or restate what was already said in the post.\n"
        f"    Whenever possible, contribute a thought that expands, refines, challenges, or deepens the discussion.\n\n"

        f"17. NATURAL OPENING RULE:\n"
        f"   Do not begin the comment with meta-analysis phrases that summarize the post.\n\n"

        f"   Avoid openings such as:\n"
        f"   - 'The important part is...'\n"
        f"   - 'The key thing is...'\n"
        f"   - 'The interesting thing is...'\n"
        f"   - 'What stands out is...'\n"
        f"   - 'The real issue is...'\n"
        f"   - 'The biggest takeaway is...'\n"
        f"   - 'The part that caught my attention...'\n"
        f"   - 'What jumped out at me...'\n\n"

        f"   Instead, begin naturally by:\n"
        f"   - reacting to a specific detail from the post\n"
        f"   - sharing an observation\n"
        f"   - sharing a related experience\n"
        f"   - asking a focused follow-up question\n"
        f"   - offering a perspective directly connected to the post\n\n"

        f"   The first sentence should feel like a real Reddit user entering the discussion,\n"
        f"   not an analyst summarizing the post.\n\n"

        f"OUTPUT FORMAT — strictly inside a single JSON code block:\n"
        f"{{\n"
        f'  "comment": "Your direct single-line Reddit comment here"\n'
        f"}}\n"
    )


def _extract_code_block_text(book):
    """Poll the ChatGPT code block until the response is fully written."""
    for attempt in range(1, 6):
        print(f"[STEP] Checking code block locator (Attempt {attempt}/5)...", flush=True)

        code_block_locator = book.resolve("code_block")
        if code_block_locator is not None:
            print("[OK] Code block visible, parsing live text size variations...", flush=True)

            last_length = 0
            max_check_cycles = 15

            for cycle in range(max_check_cycles):
                time.sleep(15)

                current_text = code_block_locator.inner_text().strip()
                current_length = len(current_text)

                print(
                    f"[STREAM INFO] Cycle {cycle + 1}: Previous Length = {last_length}, "
                    f"Current Length = {current_length}",
                    flush=True,
                )

                if current_length > 0 and current_length == last_length:
                    print("[OK] Content generation is fully finished and finalized.", flush=True)
                    return current_text

                last_length = current_length

        if attempt < 5:
            print("[WARNING] Code block not written yet or not found. Next retry window...", flush=True)
            browser.wait("random")
        else:
            print("[ERROR] Max retries reached. Streaming did not complete. Exiting script...", flush=True)
            return None

    return None


def run():
    print("[START] Script started", flush=True)

    apply_auto_correct()

    # =========================
    # STATUS CHECKS
    # =========================
    status_path = Path(STATUS_FILE)
    if not status_path.exists():
        raise FileNotFoundError(f"[ERROR] Status file {STATUS_FILE} not found.")

    with status_path.open("r", encoding="utf-8") as sf:
        status_data = json.load(sf)

    if status_data.get("comment_generated") is True:
        print("[CLEAN EXIT] comment_generated is already true. Exiting script now.", flush=True)
        sys.exit(0)

    if status_data.get("post_to_comment_found") is not True:
        print(
            "[CLEAN EXIT] post_to_comment_found is not True. Comment generation shouldn't run. Exiting now.",
            flush=True,
        )
        sys.exit(0)

    post_text = status_data.get("content_of_post_to_comment")
    post_link = status_data.get("link_to_post_to_comment")
    if not post_text or not post_link:
        print("[ERROR] Missing content_of_post_to_comment or link_to_post_to_comment in status.json.", flush=True)
        sys.exit(1)

    print("[OK] Prerequisite status checks passed. Initializing cookies...", flush=True)

    cookies = load_cookies(_pick_chatgpt_cookie())

    pw, pw_cm = browser.start_playwright()
    browser_instance = None
    page = None

    try:
        browser_instance = browser.launch_browser(pw)
        context = browser.new_context(browser_instance)
        context.add_cookies(cookies)
        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        book = LocatorBook("chatgpt_locators", page)

        print("[STEP] Opening ChatGPT Main URL...", flush=True)
        page.goto("https://chatgpt.com/", wait_until="load")
        print("[OK] URL opened successfully (Logged In)", flush=True)

        # Random wait after page load
        browser.wait("random")

        # CHECK LOGIN SUCCESS VIA USER PROFILE BUTTON
        print("[STEP] Checking login success via profile button...", flush=True)
        profile_button = book.resolve("profile_button")
        if profile_button is not None:
            label = profile_button.get_attribute("aria-label") or "User Account"
            print(f"[OK] LOGIN SUCCESS: Profile button found -> '{label}'", flush=True)
        else:
            print("[WARNING] Profile button not detected directly, proceeding with caution...", flush=True)

        # AUTOMATION FLOW
        print("[STEP] Locating chat textbox...", flush=True)
        book.click("chatgpt_textbox", purpose="the main ChatGPT message input textbox")
        print("[OK] Textbox located and clicked successfully.", flush=True)

        browser.wait("absolute")

        prompt = _build_comment_prompt(post_text)

        print("[STEP] Entering comment generation prompt into textbox...", flush=True)
        book.fill("chatgpt_textbox", prompt, purpose="the main ChatGPT message input textbox")
        browser.wait("absolute")

        print("[STEP] Locating and clicking send button...", flush=True)
        book.click("send_button", purpose="the send button next to the ChatGPT message input")

        # Initial wait for generation to start
        browser.wait("random")

        # Wait for the generated JSON code block to finish writing
        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        json_content = _extract_code_block_text(book)

        if json_content:
            try:
                parsed = json.loads(json_content)
                cleaned_comment = parsed["comment"].strip()

                print("[STEP] Updating status.json with the generated comment...", flush=True)
                status_data["comment"] = cleaned_comment
                status_data["comment_generated"] = True

                with status_path.open("w", encoding="utf-8") as sf:
                    json.dump(status_data, sf, indent=4)

                print("[OK] Status updated successfully.", flush=True)
                print(f"[INFO] Generated Comment:\n{cleaned_comment}", flush=True)

            except json.JSONDecodeError as je:
                print(f"[ERROR] Content JSON parse failed: {je}. Exiting script...", flush=True)
                browser.report_error(page)
                sys.exit(1)
        else:
            print("[ERROR] Save skipped because no data was fetched. Exiting script...", flush=True)
            browser.report_error(page)
            sys.exit(1)

        print("[STEP] Performing random wait before normal browser closure...", flush=True)
        browser.wait("absolute")

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR]", e, flush=True)
        browser.report_error(page)
        sys.exit(1)

    finally:
        if browser_instance:
            try:
                browser_instance.close()
            except Exception:
                pass

        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass

        print("[DONE] Script finished", flush=True)


if __name__ == "__main__":
    run()