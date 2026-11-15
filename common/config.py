"""Loads json/config.json once and exposes it to the rest of the project."""
import json

from dotenv import load_dotenv

from .paths import CONFIG_FILE, ROOT

# Load .env from the project root so DECRYPT_KEY / GEMINI_API_KEY are available
# regardless of the current working directory.
load_dotenv(ROOT / ".env")


def load_config():
    if not CONFIG_FILE.exists():
        raise FileNotFoundError(
            f"Config file not found at {CONFIG_FILE}. Create json/config.json first."
        )
    with open(CONFIG_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


# Loaded once at import time and shared by every module.
CONFIG = load_config()