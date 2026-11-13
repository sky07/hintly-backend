"""Rule-based intent pre-classifier and response parser.

Handles obvious cases at zero latency. Ambiguous cases are left
for the LLM to classify inline via INTENT_CLASSIFICATION_BLOCK.

Feature flag: settings.enable_intent_classification
"""

import re
import logging
from typing import Optional, Tuple

logger = logging.getLogger(__name__)


# Intent type constants
INTENT_NEW_QUESTION = "new_question"
INTENT_FOLLOW_UP = "follow_up"
INTENT_CLARIFICATION = "clarification"
INTENT_CHALLENGE = "challenge"
INTENT_RAPID_FIRE = "rapid_fire"
INTENT_COMPOUND = "compound"
INTENT_NOT_A_QUESTION = "not_a_question"

# Token limits per intent type — kept only as a safety ceiling.
# Actual length is controlled by the prompt's verbosity instructions.
from services.prompts import MAX_TOKENS_SAFETY_CEILING

INTENT_TOKEN_MAP = {
    INTENT_NEW_QUESTION: MAX_TOKENS_SAFETY_CEILING,
    INTENT_FOLLOW_UP: MAX_TOKENS_SAFETY_CEILING,
    INTENT_CLARIFICATION: MAX_TOKENS_SAFETY_CEILING,
    INTENT_CHALLENGE: MAX_TOKENS_SAFETY_CEILING,
    INTENT_RAPID_FIRE: MAX_TOKENS_SAFETY_CEILING,
    INTENT_COMPOUND: MAX_TOKENS_SAFETY_CEILING,
    INTENT_NOT_A_QUESTION: 50,
}

# Rule-based detection signals
_CLARIFY_SIGNALS = [
    "can you explain", "what do you mean", "elaborate",
    "clarify", "be more specific", "i don't follow",
    "can you walk me through", "what does that mean",
    "can you break that down",
]

_ACK_PHRASES = [
    "okay", "ok", "got it", "sure", "that's interesting",
    "good", "great", "right", "i see", "makes sense",
    "understood", "perfect", "nice", "cool", "fair enough",
    "interesting", "that makes sense", "good answer",
]

_CHALLENGE_SIGNALS = [
    "but what about", "but wouldn't", "but couldn't",
    "however", "what if", "why not", "isn't it true",
    "don't you think", "that doesn't", "but how",
    "are you sure", "that seems", "why didn't",
]


def pre_classify_intent(text: str) -> Optional[str]:
    """Rule-based pre-filter for obvious intent cases.

    Returns intent string if obvious, None if LLM should decide.
    Zero latency — runs before the LLM call.
    """
    text_lower = text.lower().strip()
    word_count = len(text_lower.split())

    # Obvious acknowledgments (< 6 words, no question mark)
    if word_count < 6 and "?" not in text:
        if any(text_lower.startswith(p) or text_lower == p for p in _ACK_PHRASES):
            return INTENT_NOT_A_QUESTION

    # Obvious clarification requests
    if any(sig in text_lower for sig in _CLARIFY_SIGNALS):
        return INTENT_CLARIFICATION

    # Obvious challenge/pushback
    if any(sig in text_lower for sig in _CHALLENGE_SIGNALS):
        return INTENT_CHALLENGE

    # Obvious rapid-fire (very short + question mark)
    if word_count <= 8 and "?" in text:
        return INTENT_RAPID_FIRE

    # Compound (multiple question marks)
    question_marks = text.count("?")
    if question_marks >= 2 and word_count > 10:
        return INTENT_COMPOUND

    return None  # Let LLM classify


def parse_intent_from_response(text: str) -> Tuple[str, str]:
    """Extract [INTENT_TAG] from LLM response prefix.

    The LLM is instructed to prefix responses with tags like:
    [NEW_QUESTION] Here is the answer...
    [FOLLOW_UP] Building on what was said...

    Returns:
        (intent, clean_text) — intent string and text with tag removed
    """
    match = re.match(r"^\[([A-Z_]+)\]\s*", text)
    if match:
        raw_intent = match.group(1).lower()
        clean_text = text[match.end():].strip()

        # Map tag to our intent constants
        intent_map = {
            "new_question": INTENT_NEW_QUESTION,
            "follow_up": INTENT_FOLLOW_UP,
            "clarification": INTENT_CLARIFICATION,
            "challenge": INTENT_CHALLENGE,
            "rapid_fire": INTENT_RAPID_FIRE,
            "compound": INTENT_COMPOUND,
            "not_a_question": INTENT_NOT_A_QUESTION,
        }
        intent = intent_map.get(raw_intent, INTENT_NEW_QUESTION)
        return intent, clean_text

    # No tag found — default to new_question
    return INTENT_NEW_QUESTION, text


def get_max_tokens_for_intent(intent: str, verbosity: str) -> int:
    """Get max_tokens based on classified intent.

    Falls back to verbosity-based limit when adaptive tokens are disabled
    or intent is unknown.
    """
    from config import settings
    from services.prompts import get_max_tokens_for_verbosity

    if not settings.enable_adaptive_tokens:
        return get_max_tokens_for_verbosity(verbosity)

    return INTENT_TOKEN_MAP.get(intent, get_max_tokens_for_verbosity(verbosity))
