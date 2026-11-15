import random
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import BANNED_FILE, REDDIT_COOKIES_FILE, SUBREDDITS_FILE


def run():
    print("[START] Script started", flush=True)

    apply_auto_correct()

    cookies = load_cookies(REDDIT_COOKIES_FILE)
    print(f"[OK] Total cookies loaded: {len(cookies)}", flush=True)

    # Load subreddits list
    print(f"[STEP] Reading subreddits from {SUBREDDITS_FILE}...", flush=True)
    subreddits_path = Path(SUBREDDITS_FILE)

    if not subreddits_path.exists():
        raise FileNotFoundError(f"[ERROR] Configured file {SUBREDDITS_FILE} not found!")

    with subreddits_path.open("r", encoding="utf-8") as f:
        raw_subreddits = [line.strip() for line in f if line.strip()]

    if not raw_subreddits:
        raise ValueError(f"[ERROR] {SUBREDDITS_FILE} is empty!")

    banned_subs_detected = []

    # Stealth setup
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

        # Loop through each subreddit one by one
        for current_sub in raw_subreddits:
            print(f"\n[LOOP] Processing Subreddit: {current_sub}", flush=True)

            clean_sub = current_sub[1:] if current_sub.startswith("/") else current_sub
            target_url = f"https://www.reddit.com/{clean_sub}/rising/?feedViewType=compactView"

            print(f"[STEP] Opening Subreddit URL: {target_url}...", flush=True)
            try:
                page.goto(target_url, wait_until="domcontentloaded")
                browser.wait("absolute")

                # ========================================================
                # LINK INTERACTION (WITH SCROLL RETRY FOR SLOW PAGES)
                # ========================================================
                print("[STEP] Searching for the first post link...", flush=True)

                post_link = None
                # Try up to 6 times, scrolling to trigger render.
                for post_search_attempt in range(6):
                    loc = book.resolve_quick("first_post_link")
                    if loc is not None:
                        post_link = loc
                        break

                    if post_search_attempt < 5:
                        print(
                            f"[SCROLL] First post not found yet. Scrolling to trigger render "
                            f"(Attempt {post_search_attempt + 1}/5)...",
                            flush=True,
                        )
                        page.evaluate("window.scrollBy(0, 300);")
                        time.sleep(random.uniform(1.5, 2.5))

                if post_link is None:
                    print(
                        f"[WARNING] Failed to locate any post link on {current_sub} even after "
                        f"multiple scrolls. Skipping...",
                        flush=True,
                    )
                    continue

                print("[STEP] Clicking on the first post link...", flush=True)
                post_link.click()
                browser.wait("absolute")

                full_url = page.url
                clean_url_match = re.match(
                    r"(https://www\.reddit\.com/r/[^/]+/comments/[^/]+/)", full_url
                )
                clean_post_url = clean_url_match.group(1) if clean_url_match else full_url
                print(f"[NAVIGATED URL]: {clean_post_url}", flush=True)

                # ========================================================
                # BAN DETECTION WITH SCROLLING LOOP
                # ========================================================
                is_banned = False

                for scroll_attempt in range(6):
                    if book.resolve_quick("ban_warning_text") is not None:
                        is_banned = True
                        break

                    if scroll_attempt < 5:
                        print(
                            f"[SCROLL] Ban message not found. Scrolling down "
                            f"(Attempt {scroll_attempt + 1}/5)...",
                            flush=True,
                        )
                        page.evaluate("window.scrollBy(0, 400);")
                        time.sleep(random.uniform(1.5, 2.5))

                if is_banned:
                    print(f"[ALERT] Banned from {current_sub}! Adding to tracking list.", flush=True)
                    banned_subs_detected.append(current_sub)

                    banned_path = Path(BANNED_FILE)
                    with banned_path.open("a", encoding="utf-8") as bf:
                        bf.write(f"{current_sub}\n")
                    print(f"[OK] Appended {current_sub} to {BANNED_FILE}", flush=True)
                else:
                    print(f"[INFO] No ban detected for {current_sub}. Moving to next subreddit.", flush=True)

                # Buffer delay before next subreddit
                browser.wait("short")

            except Exception as e:
                print(f"[WARNING] Failed to process {current_sub} due to error: {e}. Skipping...", flush=True)
                continue

        # ========================================================
        # REWRITE SUBREDDITS.TXT (CLEAN REMOVAL)
        # ========================================================
        if banned_subs_detected:
            print("\n[STEP] Cleaning up subreddits.txt...", flush=True)
            remaining_subs = [sub for sub in raw_subreddits if sub not in banned_subs_detected]

            with subreddits_path.open("w", encoding="utf-8") as f:
                for sub in remaining_subs:
                    f.write(f"{sub}\n")
            print(f"[OK] Removed banned subreddits from {SUBREDDITS_FILE}. No blank lines left.", flush=True)

        print("\n[STEP] All subreddits processed. Initiating final wait...", flush=True)
        browser.wait("cooldown")

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
            print("[INFO] Browser context closed.", flush=True)
        except Exception:
            pass

        try:
            pw_cm.__exit__(None, None, None)
        except Exception:
            pass

        print("[DONE] Script execution phase closed. Terminating process context cleanly.", flush=True)


if __name__ == "__main__":
    run()