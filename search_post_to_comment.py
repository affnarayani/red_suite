import json
import random
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import (
    COMMENTED_JSON_FILE,
    REDDIT_COOKIES_FILE,
    STATUS_FILE,
    SUBREDDITS_FILE,
)


def _default_status():
    return {
        "content_generated": False,
        "post_to_comment_found": False,
        "link_to_post_to_comment": "",
        "content_of_post_to_comment": "",
        "comment_generated": False,
        "comment": "",
    }


def run():
    print("[START] Script started", flush=True)

    apply_auto_correct()

    # ========================================================
    # STATUS JSON PRE-CHECK
    # ========================================================
    status_path = Path(STATUS_FILE)
    status_data = _default_status()

    if status_path.exists():
        try:
            with status_path.open("r", encoding="utf-8") as sf:
                existing_status = json.load(sf)
                if isinstance(existing_status, dict):
                    if "commented" in existing_status:
                        del existing_status["commented"]
                    status_data.update(existing_status)
        except Exception as json_err:
            print(f"[WARNING] Reading status.json failed, using defaults: {json_err}", flush=True)

    if status_data.get("post_to_comment_found") is True:
        print(
            "[CLEAN EXIT] New Post to Comment Not Found (post_to_comment_found is already true). "
            "Exiting with status 0.",
            flush=True,
        )
        sys.exit(0)

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

        # =========================
        # SUBREDDIT SELECTION
        # =========================
        print(f"[STEP] Reading subreddits from {SUBREDDITS_FILE}...", flush=True)
        subreddits_path = Path(SUBREDDITS_FILE)

        if not subreddits_path.exists():
            raise FileNotFoundError(f"[ERROR] Configured file {SUBREDDITS_FILE} not found!")

        with subreddits_path.open("r", encoding="utf-8") as f:
            subreddits = [line.strip() for line in f if line.strip()]

        if not subreddits:
            raise ValueError(f"[ERROR] {SUBREDDITS_FILE} is empty!")

        selected_subreddit = random.choice(subreddits)

        if selected_subreddit.startswith("/"):
            selected_subreddit = selected_subreddit[1:]

        target_url = f"https://www.reddit.com/{selected_subreddit}/new/?feedViewType=compactView"

        print(f"[STEP] Opening chosen Subreddit URL (Compact View): {target_url}...", flush=True)
        page.goto(target_url, wait_until="domcontentloaded")
        print(f"[OK] {target_url} opened completely", flush=True)

        browser.wait("medium")

        # ========================================================
        # LINK INTERACTION
        # ========================================================
        print("[STEP] Searching for the first post link using locators...", flush=True)

        post_link = book.resolve("first_post_link")
        if post_link is None:
            raise RuntimeError("[ERROR] Could not locate any post link on the subreddit feed.")

        print("[STEP] Clicking on the post link...", flush=True)
        post_link.click()
        print("[OK] Post link clicked successfully!", flush=True)

        browser.wait("medium")

        # ========================================================
        # URL EXTRACTION & CLEANING
        # ========================================================
        full_url = page.url
        clean_url_match = re.match(
            r"(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)", full_url
        )
        clean_post_url = clean_url_match.group(1) if clean_url_match else full_url

        print(f"[NAVIGATED URL]: {clean_post_url}", flush=True)

        # Duplicate check against commented.json
        commented_path = Path(COMMENTED_JSON_FILE)
        if commented_path.exists():
            try:
                with commented_path.open("r", encoding="utf-8") as cf:
                    commented_data = json.load(cf)

                if isinstance(commented_data, list):
                    current_url_clean = clean_post_url.rstrip("/")
                    commented_urls_clean = [
                        url.rstrip("/") for url in commented_data if isinstance(url, str)
                    ]

                    if current_url_clean in commented_urls_clean:
                        print(
                            "[CLEAN EXIT] URL already exists in commented.json (Slash Matched). "
                            "Skipping. Exiting 1.",
                            flush=True,
                        )
                        browser.report_error(page)
                        sys.exit(1)
            except Exception as e:
                print(f"[WARNING] Reading commented.json failed: {e}", flush=True)

        print("[PROCEED] Extracting text body...", flush=True)

        # =========================
        # TEXT EXTRACTION
        # =========================
        post_text = book.inner_text(
            "post_text_body",
            purpose="the readable body text of the Reddit post",
        ).strip()

        print(f"[DEBUG] Extracted text length: {len(post_text)} characters", flush=True)

        # ========================================================
        # CHARACTER COUNT VALIDATION & STATUS UPDATE
        # ========================================================
        if len(post_text) <= 150:
            print(
                "[CLEAN EXIT] Post content is 150 characters or less. Skipping this post. "
                "Terminating cleanly with status 1.",
                flush=True,
            )
            browser.report_error(page)
            sys.exit(1)

        print("[PROCEED] Eligible post found (> 150 characters). Updating status.json...", flush=True)

        status_data["post_to_comment_found"] = True
        status_data["link_to_post_to_comment"] = clean_post_url
        status_data["content_of_post_to_comment"] = post_text
        status_data["content_generated"] = False
        status_data["comment_generated"] = False
        status_data["comment"] = ""

        with status_path.open("w", encoding="utf-8") as sf:
            json.dump(status_data, sf, indent=4)

        print("[OK] status.json has been successfully updated with the comment key!", flush=True)

        browser.wait("final")

    except SystemExit:
        raise
    except Exception as e:
        print("[ERROR] Automation cycle interrupted due to runtime trace:", e, flush=True)
        browser.report_error(page)
        sys.exit(1)

    finally:
        try:
            if browser_instance:
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