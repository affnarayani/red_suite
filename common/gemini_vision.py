"""Gemini Vision wrapper used as the last-resort locator fallback.

When every locator expression for an element fails, the caller sends the current
browser screenshot plus a short description of the target element. Gemini
returns the element's pixel coordinates (viewport space) and, when possible, a
CSS selector that can be recorded as a future fallback locator.
"""
import json
import os

from .config import CONFIG

_PROMPT_TEMPLATE = """You are a precise UI element locator for browser automation.
The attached image is a screenshot of a web page viewport (top-left origin is 0,0).

TASK: Locate the following element:
{purpose}

Intended action: {action}

Return ONLY a single JSON object (no markdown, no code fences) with this exact shape:
{{
  "found": true or false,
  "x": <integer pixel x coordinate of the element center, relative to the image>,
  "y": <integer pixel y coordinate of the element center, relative to the image>,
  "suggested_locator": "<a CSS selector string for the element, or null>",
  "text": "<visible text content if action is read, else empty string>",
  "reason": "<short reason>"
}}

Rules:
- Coordinates must be integers in the image's own pixel space.
- "suggested_locator" must be a CSS selector only (for example: [data-testid="send-button"], div[contenteditable="true"], button[aria-label="Close"]). Do NOT wrap it in page.locator().
- If you cannot determine a reliable CSS selector, set "suggested_locator" to null.
- If the element is not visible, set "found" to false and still return valid JSON.
"""


class GeminiVision:
    def __init__(self, api_key=None, model=None):
        self.api_key = api_key or os.getenv("GEMINI_API_KEY")
        self.model = model or CONFIG.get("gemini_model", "gemini-3.5-flash-lite")
        self.enabled = bool(self.api_key) and CONFIG.get("vision_enabled", True)
        self._client = None
        if self.enabled:
            try:
                from google import genai

                self._client = genai.Client(api_key=self.api_key)
            except Exception as err:
                print(f"[WARNING] Gemini client init failed: {err}", flush=True)
                self.enabled = False

    def locate(self, screenshot_png, purpose, action="click"):
        """Return a dict {found, x, y, suggested_locator, text, reason} or None."""
        if not self.enabled or self._client is None:
            return None

        prompt = _PROMPT_TEMPLATE.format(purpose=purpose, action=action)
        try:
            from google.genai import types

            contents = [
                prompt,
                types.Part.from_bytes(data=screenshot_png, mime_type="image/png"),
            ]
            response = self._client.models.generate_content(
                model=self.model,
                contents=contents,
                config=types.GenerateContentConfig(temperature=0.0, max_output_tokens=512),
            )
            text = (response.text or "").strip()
            return self._parse(text)
        except Exception as err:
            print(f"[WARNING] Gemini vision call failed: {err}", flush=True)
            return None

    @staticmethod
    def _parse(text):
        if not text:
            return None
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            return None
        try:
            return json.loads(text[start:end + 1])
        except Exception:
            return None