"""Claude Vision service for extracting questions from screenshots.

Uses the standard Anthropic Messages API with image content blocks.
Same API, same key — just a different content shape.
"""

import logging
from typing import Optional

import httpx

from config import settings

logger = logging.getLogger(__name__)

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
# Use Sonnet for vision — better accuracy for reading screenshots
VISION_MODEL = "claude-sonnet-5-5"
# Lets the API retry on another model if a safety classifier declines the request
FALLBACK_BETA = "server-side-fallback-2026-07-01"

VISION_SYSTEM_PROMPT = (
    "You are an interview assistant. The user is in a live interview and has captured "
    "a screenshot of their screen. Extract the interview question or task from the screenshot "
    "and provide a helpful, structured response.\n\n"
    "Rules:\n"
    "- First identify what the interviewer is asking (it may be in a chat window, shared screen, "
    "whiteboard, or code editor)\n"
    "- If it's a coding question, provide a clear solution with explanation\n"
    "- If it's a system design question, provide a structured approach\n"
    "- If it's a behavioral question, suggest a STAR-method response\n"
    "- If it's a general question, provide a thoughtful answer\n"
    "- Be concise but thorough — the user needs to respond quickly\n"
    "- Use markdown formatting with ### headings and bullet points\n"
    "- If you cannot identify a clear question in the screenshot, describe what you see "
    "and ask the user to clarify"
)


async def get_suggestion_from_screenshot(
    image_base64: str,
    context_prompt: str = "",
    api_key: str = "",
) -> Optional[str]:
    """Send a screenshot to Claude Vision and get a suggestion.

    Args:
        image_base64: Base64-encoded PNG screenshot
        context_prompt: Additional context (job description, etc.)
        api_key: Anthropic API key. Falls back to server config if empty.

    Returns:
        The suggestion text, or None if failed.
    """
    key = api_key or settings.anthropic_api_key
    if not key:
        logger.error("[VISION] No Anthropic API key available for vision call")
        return None

    user_content = [
        {
            "type": "image",
            "source": {
                "type": "base64",
                "media_type": "image/png",
                "data": image_base64,
            },
        },
        {
            "type": "text",
            "text": (
                "Extract the interview question or task from this screenshot and provide "
                "a helpful response. "
                + (f"Additional context: {context_prompt}" if context_prompt else "")
            ),
        },
    ]

    payload = {
        "model": VISION_MODEL,
        # Sonnet 5.5 always thinks, and thinking counts against max_tokens.
        # Low effort keeps the answer quick for a live interview.
        "max_tokens": 8000,
        "output_config": {"effort": "low"},
        "fallbacks": "default",
        "system": VISION_SYSTEM_PROMPT,
        "messages": [
            {"role": "user", "content": user_content},
        ],
    }

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                ANTHROPIC_API_URL,
                json=payload,
                headers={
                    "x-api-key": key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "anthropic-beta": FALLBACK_BETA,
                    "content-type": "application/json",
                },
            )

            if response.status_code != 200:
                logger.error(f"[VISION] API error {response.status_code}: {response.text[:500]}")
                return None

            data = response.json()
            if data.get("stop_reason") == "refusal":
                logger.warning("[VISION] Model declined this screenshot (stop_reason=refusal)")
                return None
            # Read the answer by block type: the response opens with thinking blocks
            text = "".join(
                b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"
            )

            usage = data.get("usage", {})
            logger.info(
                f"[VISION] Success — {usage.get('input_tokens', 0)} input, "
                f"{usage.get('output_tokens', 0)} output tokens"
            )

            return text.strip() if text else None

    except Exception as e:
        logger.error(f"[VISION] Request failed: {e}")
        return None
