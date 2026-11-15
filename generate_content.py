import json
import random
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import (
    CHATGPT_COOKIES_DIR,
    POSTED_FILE,
    REDDIT_POST_FILE,
    STATUS_FILE,
    SUBREDDITS_FILE,
)


def _get_random_subreddit() -> str:
    print(f"[STEP] Reading {SUBREDDITS_FILE}...", flush=True)
    subreddits_file = Path(SUBREDDITS_FILE)
    if not subreddits_file.exists():
        raise FileNotFoundError(f"[ERROR] '{SUBREDDITS_FILE}' file not found.")

    with subreddits_file.open("r", encoding="utf-8") as f:
        lines = [line.strip() for line in f if line.strip()]

    if not lines:
        raise ValueError(f"[ERROR] '{SUBREDDITS_FILE}' is empty.")

    selected_sub = random.choice(lines)
    print(f"[OK] Randomly selected subreddit: '{selected_sub}'", flush=True)
    return selected_sub


def _get_posted_history():
    posted_file = Path(POSTED_FILE)
    if not posted_file.exists():
        return []
    try:
        with posted_file.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _pick_chatgpt_cookie():
    encrypted_files = list(Path(CHATGPT_COOKIES_DIR).glob("*.encrypted"))
    if not encrypted_files:
        raise RuntimeError(f"[ERROR] No .encrypted cookie files found in '{CHATGPT_COOKIES_DIR}' folder")
    chosen = random.choice(encrypted_files)
    print(f"[OK] Randomly selected cookie file: {chosen.name}", flush=True)
    return chosen


def _build_prompt(subreddit_name, posted_history):
    history_context = json.dumps(posted_history, indent=2) if posted_history else "None"
    return (
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


def _extract_code_block_text(book):
    """Poll the ChatGPT code block until the JSON response is fully written."""
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
                    if current_text.endswith("}"):
                        print("[OK] Content generation is fully finished and finalized.", flush=True)
                        return current_text
                    print("[WARNING] Text generation paused but JSON bracket '}' is missing. Waiting further...", flush=True)

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
    # STATUS CHECK
    # =========================
    status_file = Path(STATUS_FILE)
    if not status_file.exists():
        print(f"[ERROR] {STATUS_FILE} file not found. Exiting...", flush=True)
        sys.exit(0)

    try:
        with status_file.open("r", encoding="utf-8") as f:
            status_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] status.json parse failed: {e}. Exiting...", flush=True)
        sys.exit(0)

    if status_data.get("content_generated") is True:
        print("Content already generated!", flush=True)
        sys.exit(0)

    print("[OK] Status check passed (content_generated is False). Proceeding...", flush=True)

    # Initialize/clear the article file at the beginning
    article_file = Path(REDDIT_POST_FILE)
    with article_file.open("w", encoding="utf-8") as f:
        f.write("")
    print(f"[OK] '{REDDIT_POST_FILE}' cleared/initialized", flush=True)

    try:
        subreddit_name = _get_random_subreddit()
        posted_history = _get_posted_history()
    except Exception as e:
        print(f"[ERROR] Failed to read configuration files: {e}", flush=True)
        sys.exit(1)

    cookies = load_cookies(_pick_chatgpt_cookie())
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

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

        prompt = _build_prompt(subreddit_name, posted_history)

        print("[STEP] Entering prompt into textbox...", flush=True)
        book.fill("chatgpt_textbox", prompt, purpose="the main ChatGPT message input textbox")
        browser.wait("absolute")

        print("[STEP] Locating and clicking send button...", flush=True)
        book.click("send_button", purpose="the send button next to the ChatGPT message input")

        # Initial wait for generation to start
        browser.wait("random")

        # Wait for the generated JSON code block to finish writing
        print("[STEP] Waiting for generated JSON code block to complete writing (15s checks)...", flush=True)
        json_content = _extract_code_block_text(book)

        # JSON parsing, validation and Topic Saving
        if json_content:
            try:
                print("[STEP] Parsing content as JSON...", flush=True)
                if json_content.startswith("```json"):
                    json_content = json_content.split("```json", 1)[1]
                if json_content.endswith("```"):
                    json_content = json_content.rsplit("```", 1)[0]

                parsed_json = json.loads(json_content.strip())

                final_ordered_json = {
                    "subreddit": subreddit_name,
                    "title": parsed_json.get("title", ""),
                    "body": parsed_json.get("body", ""),
                }

                print(f"[STEP] Saving ordered dictionary to {REDDIT_POST_FILE}...", flush=True)
                with article_file.open("w", encoding="utf-8") as f:
                    json.dump(final_ordered_json, f, indent=4, ensure_ascii=False)
                print(f"[OK] Article successfully saved to {REDDIT_POST_FILE} with subreddit key on top", flush=True)

                print("[STEP] Updating status.json...", flush=True)
                status_data["content_generated"] = True
                with status_file.open("w", encoding="utf-8") as f:
                    json.dump(status_data, f, indent=4, ensure_ascii=False)
                print("[OK] status.json successfully updated (content_generated=True)", flush=True)

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