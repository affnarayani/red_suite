import json
import sys
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import (
    POSTED_FILE,
    REDDIT_COOKIES_FILE,
    REDDIT_POST_FILE,
    STATUS_FILE,
)


def _clean_subreddit(subreddit_name):
    if subreddit_name.startswith("r/"):
        return subreddit_name[2:]
    if subreddit_name.startswith("/r/"):
        return subreddit_name[3:]
    return subreddit_name


def run():
    print("[START] Script started", flush=True)

    # Apply any pending locator auto-corrections before running.
    apply_auto_correct()

    # 1. Check status.json first
    status_path = Path(STATUS_FILE)
    if not status_path.exists():
        print(f"[ERROR] {STATUS_FILE} not found. Please create it first.", flush=True)
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
        print(f"[ERROR] Configured file {REDDIT_POST_FILE} not found!", flush=True)
        sys.exit(1)

    with post_path.open("r", encoding="utf-8") as pf:
        post_data = json.load(pf)

    subreddit_name = post_data.get("subreddit", "").strip()
    post_title = post_data.get("title", "").strip()
    post_body = post_data.get("body", "").strip()

    if not subreddit_name or not post_title or not post_body:
        print("[ERROR] Subreddit, title, or body missing in reddit_post.json!", flush=True)
        sys.exit(1)

    target_subreddit_url = f"https://www.reddit.com/r/{_clean_subreddit(subreddit_name)}/"

    # Load cookies
    cookies = load_cookies(REDDIT_COOKIES_FILE)
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # Playwright stealth setup
    pw, pw_cm = browser.start_playwright()
    browser_instance = None
    page = None
    program_success = False

    try:
        browser_instance = browser.launch_browser(pw)
        context = browser.new_context(browser_instance)
        context.add_cookies(cookies)
        page = context.new_page()
        print("[OK] Cookies added successfully", flush=True)

        book = LocatorBook("reddit_locators", page)

        # Step 1: Open target subreddit directly after login
        print(f"[STEP] Navigating to subreddit URL: {target_subreddit_url}...", flush=True)
        page.goto(target_subreddit_url, wait_until="domcontentloaded")
        print(f"[OK] {target_subreddit_url} opened completely.", flush=True)

        # Step 2: Wait and click 'Create Post'
        browser.wait("absolute")
        print("[STEP] Clicking 'Create Post' button...", flush=True)
        book.click("create_post_btn", purpose="the 'Create Post' button on a Reddit subreddit page")

        # Step 3: Wait and type the title
        browser.wait("absolute")
        print("[STEP] Focusing Title field and typing title...", flush=True)
        book.type_human(
            "post_title_box",
            f"[Story] {post_title}",
            purpose="the post Title textbox on the create post form",
        )

        # ====================================================
        # CONDITIONAL FLAIR SELECTION WITH KEYBOARD SEQUENCES
        # ====================================================
        print("[STEP] Checking if flair button is available on page...", flush=True)
        flair_present = book.is_visible(
            "flair_and_tags_btn",
            purpose="the 'Add flair and tags' button (only present when a flair is required)",
        )

        if flair_present:
            print("[INFO] Mandatory flair button found. Initiating interaction sequence...", flush=True)
            book.click(
                "flair_and_tags_btn",
                purpose="the 'Add flair and tags' button",
            )
            browser.wait("short")

            for i in range(1, 4):
                print(f"[STEP] Sending TAB key ({i}/3)...", flush=True)
                page.keyboard.press("Tab")
                browser.wait("short")

            print("[STEP] Sending SPACE key...", flush=True)
            page.keyboard.press("Space")
            browser.wait("short")

            print("[STEP] Clicking 'Add' button to submit flair...", flush=True)
            book.click(
                "flair_add_btn",
                purpose="the 'Add' button that submits the selected flair",
            )
            browser.wait("short")
            print("[OK] Flair interaction sequence completed successfully.", flush=True)
        else:
            print("[INFO] Flair button not found or not visible. Skipping flair selection safely...", flush=True)
        # ====================================================

        # Step 4: Wait and type the post body
        browser.wait("absolute")
        print("[STEP] Focusing Post body field and typing body paragraphs...", flush=True)
        book.type_human(
            "post_body_box",
            post_body,
            purpose="the main post body text editor on the create post form",
        )

        # Step 5: Wait and click 'Post'
        browser.wait("absolute")
        print("[STEP] Clicking 'Post' submit button...", flush=True)
        book.click(
            "submit_post_btn",
            purpose="the final 'Post' submit button",
        )
        print("[OK] Post submitted successfully!", flush=True)

        # Step 6: Final wait before closing browser
        browser.wait("absolute")
        program_success = True

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation workflow failed:", e, flush=True)
        browser.report_error(page)
        if browser_instance:
            try:
                browser_instance.close()
            except Exception:
                pass
        sys.exit(1)

    finally:
        try:
            if browser_instance:
                browser_instance.close()
            print("[INFO] Browser context closed.", flush=True)
        except Exception:
            pass

        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass

        # Update status/history only when the post was actually submitted.
        if program_success:
            print(f"[STEP] Appending new topic to the top of {POSTED_FILE}...", flush=True)
            posted_path = Path(POSTED_FILE)
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
                "subreddit": subreddit_name,
            }

            updated_history = [new_entry] + existing_history

            with posted_path.open("w", encoding="utf-8") as hf:
                json.dump(updated_history, hf, indent=4, ensure_ascii=False)
            print(f"[OK] {POSTED_FILE} successfully updated (New entry added on top).", flush=True)

            print(f"[STEP] Resetting content_generated to false in {STATUS_FILE}...", flush=True)
            status_data["content_generated"] = False
            with status_path.open("w", encoding="utf-8") as sf:
                json.dump(status_data, sf, indent=4)
            print("[OK] status.json updated successfully.", flush=True)

        print("[DONE] Process context terminated cleanly.", flush=True)


if __name__ == "__main__":
    run()