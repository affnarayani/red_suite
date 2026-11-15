import sys
from pathlib import Path

from dotenv import load_dotenv

from common import browser
from common.config import CONFIG
from common.crypto import load_cookies
from common.locators import LocatorBook, apply_auto_correct
from common.paths import BANNED_FILE, REDDIT_COOKIES_FILE, SUBREDDITS_FILE


def _read_list_from_file(file_path: Path):
    if not file_path.exists():
        return []
    with file_path.open("r", encoding="utf-8") as f:
        return [line.strip().lower() for line in f if line.strip()]


def _append_to_file(file_path: Path, text: str):
    import os

    ensure_newline = False
    if file_path.exists() and file_path.stat().st_size > 0:
        with file_path.open("rb") as f:
            f.seek(-1, os.SEEK_END)
            last_char = f.read(1)
            if last_char != b"\n" and last_char != b"\r":
                ensure_newline = True

    with file_path.open("a", encoding="utf-8") as f:
        if ensure_newline:
            f.write("\n")
        f.write(text + "\n")

    print(f"[FILE WRITE] '{text}' successfully appended to {file_path.name}.", flush=True)


def _parse_stat_value(val_str: str) -> int:
    val_str = val_str.strip().upper()
    if not val_str:
        return 0
    try:
        if "M" in val_str:
            return int(float(val_str.replace("M", "")) * 1_000_000)
        if "K" in val_str:
            return int(float(val_str.replace("K", "")) * 1_000)
        return int(val_str.replace(",", ""))
    except ValueError:
        return 0


def run():
    print("[START] Reddit Target Hunter Script Activated.", flush=True)

    apply_auto_correct()

    scraper_cfg = CONFIG["scraper"]
    search_url = scraper_cfg["search_url"]
    target_count = scraper_cfg["target_subreddit_count"]
    min_weekly_users = scraper_cfg["min_weekly_users"]
    min_weekly_contributions = scraper_cfg["min_weekly_contributions"]

    cookies = load_cookies(REDDIT_COOKIES_FILE)

    pw, pw_cm = browser.start_playwright()
    browser_instance = None
    page = None

    try:
        browser_instance = browser.launch_browser(pw)
        context = browser.new_context(browser_instance)
        context.add_cookies(cookies)
        page = context.new_page()
        print("[BROWSER] Session initialized completely. Starting target lookup loop.\n", flush=True)

        book = LocatorBook("reddit_locators", page)

        processed_in_current_session = set()

        while True:
            subs_list = _read_list_from_file(SUBREDDITS_FILE)
            banned_list = _read_list_from_file(BANNED_FILE)

            current_count = len(subs_list)

            print("=" * 65, flush=True)
            print(f"[STATUS REPORT] Current targets: {current_count} / {target_count}", flush=True)
            print("=" * 65, flush=True)

            if current_count >= target_count:
                print(
                    f"\n[SUCCESS] Target count of {target_count} achieved inside subreddits.txt! "
                    f"Stopping cycles.",
                    flush=True,
                )
                break

            print(f"[SEARCH] Navigating to matrix node: {search_url}", flush=True)
            page.goto(search_url, wait_until="domcontentloaded")
            page.wait_for_timeout(5000)

            all_links = book.raw("all_page_links")
            candidate_subs = []

            for link in all_links.all() if all_links else []:
                href = link.get_attribute("href")
                if href and href.startswith("/r/") and not href.startswith("/r/search"):
                    parts = [p for p in href.split("/") if p]
                    if len(parts) >= 2 and parts[0] == "r":
                        sub_name = parts[1].strip().lower()
                        full_formatted_name = f"r/{sub_name}"

                        if (
                            full_formatted_name in subs_list
                            or full_formatted_name in banned_list
                            or full_formatted_name in processed_in_current_session
                        ):
                            continue

                        if full_formatted_name not in candidate_subs:
                            candidate_subs.append(full_formatted_name)

            print(f"[INFO] Found {len(candidate_subs)} unique unprocessed subreddits on this scroll.", flush=True)

            if not candidate_subs:
                print("⚠️  [WARNING] Zero new matches here. Scrolling down to trigger dynamic rendering...", flush=True)
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                browser.wait("short")
                continue

            target_sub = candidate_subs[0]
            processed_in_current_session.add(target_sub)

            print(f"\n[TARGET] >>> Processing Candidate: {target_sub} <<<", flush=True)
            target_sub_url = f"https://www.reddit.com/{target_sub}/"

            # --- STEP 1: Direct About Page & Bot Bouncer Audit ---
            about_url = f"{target_sub_url}about/"
            print(f"   [STEP 1] Direct security check on About page: {about_url}", flush=True)
            page.goto(about_url, wait_until="domcontentloaded")
            page.wait_for_timeout(3000)

            for _ in range(1, 4):
                page.evaluate("window.scrollBy(0, 1000);")
                page.wait_for_timeout(1000)

            if book.is_visible("bot_bouncer_link", purpose="the 'Bot Bouncer' link on a subreddit about page"):
                print("      [ALERT] 'Bot Bouncer' detected! Moving directly to banned.txt and skipping.", flush=True)
                _append_to_file(BANNED_FILE, target_sub)
                continue

            print("   [PASS] No Bot Bouncer found. Proceeding to metrics.", flush=True)

            # --- STEP 2: Main URL & Weekly Traffic Counts Check ---
            print(f"   [STEP 2] Returning to landing workspace for metrics: {target_sub_url}", flush=True)
            page.goto(target_sub_url, wait_until="domcontentloaded")

            print("      [SYNC] Holding thread for structural components sync...", flush=True)
            browser.wait("absolute")

            print("      [STATS] Fetching community traffic parameters...", flush=True)
            try:
                users_text = book.inner_text(
                    "weekly_active_users",
                    purpose="the weekly active users count number",
                )
                contrib_text = book.inner_text(
                    "weekly_contributions",
                    purpose="the weekly contributions count number",
                )

                if users_text and contrib_text:
                    users_count = _parse_stat_value(users_text)
                    contrib_count = _parse_stat_value(contrib_text)

                    print(
                        f"      [DATA] Users: {users_count} ({users_text}) | "
                        f"Contribs: {contrib_count} ({contrib_text})",
                        flush=True,
                    )

                    if users_count <= min_weekly_users or contrib_count <= min_weekly_contributions:
                        print("      [SKIP] Metrics below requirements. Simply ignoring.", flush=True)
                        continue
                else:
                    print("      [SKIP] Analytics elements layout hidden or unpopulated. Simply ignoring.", flush=True)
                    continue

            except Exception as stat_err:
                print(f"      [ERROR] Metrics parsing exception: {stat_err}. Simply ignoring.", flush=True)
                continue

            print("   [PASS] Traffic volume matches profile constraints.")

            # --- STEP 3: Create Post Overlay Checks ---
            print("   [STEP 3] Triggering Post creation workspace overlay evaluation...", flush=True)

            if not book.is_visible("create_post_btn", purpose="the 'Create Post' button on a subreddit page"):
                print("      [SKIP] Editor trigger button missing or protected. Simply ignoring.", flush=True)
                continue

            print("      [ACTION] Clicking 'Create Post' element...", flush=True)
            book.click("create_post_btn", purpose="the 'Create Post' button on a subreddit page")

            print("      [SYNC] Holding post-click buffer window active...", flush=True)
            browser.wait("absolute")

            restricted = book.is_visible(
                "similar_communities_btn",
                purpose="the 'Post in Similar Communities' button shown when posting is restricted",
            ) or book.is_visible(
                "cannot_contribute_text",
                purpose="the \"You can't contribute in this\" text shown when posting is restricted",
            )

            if restricted:
                print("      [SKIP] Interface restrictions flagged inside publishing wrapper. Simply ignoring.", flush=True)
                continue

            # --- ALL FILTERS PASSED ---
            print(f"[QUALIFIED] Candidate matches all constraints perfectly! Recording data...", flush=True)
            _append_to_file(SUBREDDITS_FILE, target_sub)

        print("\n[FINAL] Target limit reached or loop broke safely. Preparing system teardown.", flush=True)
        browser.wait("absolute")

    except SystemExit:
        raise
    except Exception as e:
        print(f"\n[CRASH] Execution lifecycle dropped context error: {e}", flush=True)
        browser.report_error(page)
        sys.exit(1)

    finally:
        print("\n" + "=" * 65, flush=True)
        print("[CLEANUP] Executing secure interface teardown sequence...", flush=True)
        try:
            if browser_instance:
                browser_instance.close()
            print("   [OK] Browser profiles and dynamic window contexts deleted.", flush=True)
        except Exception:
            pass
        try:
            pw_cm.__exit__(None, None, None)
            print("   [OK] Core automation engine disconnected cleanly.", flush=True)
        except Exception:
            pass
        print("[DONE] Script engine terminated flawlessly.", flush=True)


if __name__ == "__main__":
    run()