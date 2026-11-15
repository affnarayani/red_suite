import json
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import COMMENTED_JSON_FILE, REDDIT_COOKIES_FILE, STATUS_FILE


def _reset_status(status_data, status_path):
    status_data["post_to_comment_found"] = False
    status_data["comment_generated"] = False
    status_data["link_to_post_to_comment"] = ""
    status_data["content_of_post_to_comment"] = ""
    status_data["comment"] = ""
    with status_path.open("w", encoding="utf-8") as sf:
        json.dump(status_data, sf, indent=4)


def run():
    print("[START] Script started", flush=True)

    apply_auto_correct()

    # ========================================================
    # STATUS JSON VALIDATION
    # ========================================================
    status_path = Path(STATUS_FILE)
    if not status_path.exists():
        raise FileNotFoundError(f"[ERROR] Status file {STATUS_FILE} not found!")

    with status_path.open("r", encoding="utf-8") as sf:
        status_data = json.load(sf)

    post_found = status_data.get("post_to_comment_found")
    comment_gen = status_data.get("comment_generated")

    if not (post_found is True and comment_gen is True):
        print("Not Ready to Comment Yet!", flush=True)
        sys.exit(0)

    target_url = status_data.get("link_to_post_to_comment")
    comment_to_post = status_data.get("comment")

    if not target_url or not comment_to_post:
        print("[ERROR] URL or Comment text missing in status.json despite flags being True.", flush=True)
        sys.exit(1)

    cookies = load_cookies(REDDIT_COOKIES_FILE)
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

        book = LocatorBook("reddit_locators", page)

        # ========================================================
        # DIRECT NAVIGATION TO TARGET URL
        # ========================================================
        print(f"[STEP] Navigating directly to post URL: {target_url}...", flush=True)
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"[OK] {target_url} opened completely", flush=True)

        browser.wait("medium")

        # URL extraction & duplicate handling
        full_url = page.url
        clean_url_match = re.match(r"(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)", full_url)
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
            _reset_status(status_data, status_path)
            if browser_instance:
                browser_instance.close()
            sys.exit(0)

        # =========================
        # COMMENT AUTOMATION
        # =========================
        # 1. Click 'Go to comments' button if available
        print("[STEP] Locating and clicking 'Go to comments' button...", flush=True)
        if book.is_visible("go_to_comments_btn", purpose="the 'Go to comments' button on a post page"):
            book.click("go_to_comments_btn", purpose="the 'Go to comments' button on a post page")
            browser.wait("medium")

        # 2. Locate empty comment input box
        print("[STEP] Locating comment input field...", flush=True)
        book.click(
            "comment_composer",
            purpose="the rich-text comment composer box on a Reddit post",
        )
        browser.wait("medium")

        # 3. Type comment like a human
        print("[STEP] Typing comment from status.json via native keyboard emulation...", flush=True)
        browser.human_type(page, comment_to_post)

        browser.wait("medium")

        # 4. Click 'Comment' submit button
        print("[STEP] Clicking 'Comment' submit button...", flush=True)
        book.click("submit_comment_btn", purpose="the 'Comment' submit button")
        print("[OK] Comment posted successfully!", flush=True)
        browser.wait("medium")

        # =========================
        # UPVOTE AUTOMATION
        # =========================
        print("[STEP] Attempting to upvote the post...", flush=True)
        if book.is_visible("upvote_button", purpose="the Upvote button on a Reddit post"):
            book.click("upvote_button", purpose="the Upvote button on a Reddit post")
            print("[OK] Post Upvoted!", flush=True)
        else:
            print("[WARNING] Upvote button could not be located or click state is blocked.", flush=True)

        browser.wait("medium")

        # History update
        if clean_post_url not in existing_urls:
            existing_urls.insert(0, clean_post_url)
        with json_path.open("w", encoding="utf-8") as jf:
            json.dump(existing_urls, jf, indent=4)
        print("[OK] History JSON updated successfully.", flush=True)

        # ========================================================
        # RESET STATUS JSON ON SUCCESSFUL RUN
        # ========================================================
        print(f"[STEP] Resetting workflow keys in {STATUS_FILE}...", flush=True)
        _reset_status(status_data, status_path)
        print("[OK] status.json reset process complete.", flush=True)

        browser.wait("final")

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
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

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)


if __name__ == "__main__":
    run()