"""Central path constants for the whole project.

Every script imports paths from here so folder layout changes only need to be
made in a single place.
"""
from pathlib import Path

# Project root (parent of the common/ folder)
ROOT = Path(__file__).resolve().parent.parent

# Folders
JSON_DIR = ROOT / "json"
LOCATORS_DIR = ROOT / "locators"
ERROR_DIR = ROOT / "error"
DATA_DIR = ROOT / "data"
CHATGPT_COOKIES_DIR = ROOT / "chatgpt_cookies"
REDDIT_COOKIES_DIR = ROOT / "reddit_cookies"

# Config / state files
CONFIG_FILE = JSON_DIR / "config.json"
STATUS_FILE = JSON_DIR / "status.json"
POSTED_FILE = JSON_DIR / "posted.json"
COMMENTED_JSON_FILE = JSON_DIR / "commented.json"
REDDIT_POST_FILE = JSON_DIR / "reddit_post.json"

# Plain text data files
SUBREDDITS_FILE = DATA_DIR / "subreddits.txt"
BANNED_FILE = DATA_DIR / "banned.txt"

# Encrypted cookies
REDDIT_COOKIES_FILE = REDDIT_COOKIES_DIR / "reddit_cookies.json.encrypted"

# Locators
CHANGED_LOCATORS_FILE = LOCATORS_DIR / "changed_locators.json"

# Error screenshot (single fixed file, replaced on every run)
SCREENSHOT_FILE = ERROR_DIR / "error_screenshot.png"