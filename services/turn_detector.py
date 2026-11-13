"""Semantic Turn Detection for Interview Assistant.

This module implements a Hybrid Accumulator approach to detect when
the interviewer has finished speaking, avoiding jittery partial suggestions.

Key Features:
1. Accumulates transcript segments instead of processing each independently
2. Uses Voice Activity Detection (VAD) to detect speech/silence
3. Checks for "connective tissue" words that indicate speaker isn't done
4. Only triggers LLM when: silence > threshold AND complete sentence

Technical Flow:
1. Accumulate: Append new transcripts to current_turn buffer
2. Wait for Silence: VAD detects "Speech Stopped" event
3. Last Word Logic: Check if last words are connective (and, because, so)
4. Final Call: Silence > 1s AND complete punctuation → trigger suggestion
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Optional, List

logger = logging.getLogger(__name__)


class SpeechState(Enum):
    """Current speech state."""
    IDLE = "idle"           # No speech detected
    SPEAKING = "speaking"   # Currently speaking
    PAUSED = "paused"       # Brief pause, might continue
    FINISHED = "finished"   # Turn complete, ready for LLM


@dataclass
class TurnDetectorConfig:
    """Configuration for turn detection."""
    # Silence thresholds
    pause_threshold_ms: int = 800       # Brief pause (might continue)
    silence_threshold_ms: int = 1000    # Long silence (turn complete)

    # Minimum content requirements
    min_words: int = 3                  # Minimum words for a valid turn
    min_chars: int = 10                 # Minimum characters for a valid turn

    # Audio settings
    sample_rate: int = 16000            # Audio sample rate
    frame_duration_ms: int = 30         # VAD frame duration

    # VAD sensitivity (0.0-1.0, higher = more sensitive to speech)
    vad_threshold: float = 0.5


# Words that indicate the speaker is not finished
CONNECTIVE_WORDS = {
    # Conjunctions that typically continue
    "and", "but", "or", "nor", "yet", "so", "because", "since", "although",
    "though", "while", "whereas", "if", "unless", "until", "when", "where",
    "whether", "that", "which", "who", "whom", "whose",

    # Incomplete phrases
    "like", "such", "as", "for", "with", "about", "into", "onto", "upon",
    "the", "a", "an", "to", "of", "in", "on", "at", "by",

    # Thinking indicators
    "um", "uh", "er", "ah", "hmm", "well", "now", "then", "also",

    # Question starters (might be rhetorical pause)
    "what", "why", "how", "can", "could", "would", "should", "do", "does", "is", "are",
}

# Punctuation that indicates sentence completion
COMPLETE_PUNCTUATION = {'.', '?', '!'}

# Patterns that indicate incomplete thoughts
INCOMPLETE_PATTERNS = [
    r'\b(is|are|was|were|will|would|could|should|can|may|might)\s*$',  # Trailing auxiliary verbs
    r'\b(to|for|with|about|from|into|onto)\s*$',  # Trailing prepositions
    r',\s*$',  # Trailing comma
    r':\s*$',  # Trailing colon
    r'-\s*$',  # Trailing dash
]


class TurnDetector:
    """Detects complete speaker turns using semantic analysis.

    This class accumulates transcript segments and determines when
    a speaker has finished their turn, avoiding premature LLM calls.
    """

    def __init__(
        self,
        config: TurnDetectorConfig = None,
        on_turn_complete: Optional[Callable[[str], None]] = None,
    ):
        """Initialize turn detector.

        Args:
            config: Detection configuration
            on_turn_complete: Async callback when turn is complete
        """
        self.config = config or TurnDetectorConfig()
        self.on_turn_complete = on_turn_complete

        # State tracking
        self._state = SpeechState.IDLE
        self._current_turn: List[str] = []  # Accumulated transcript segments
        self._last_speech_time: float = 0
        self._last_transcript_time: float = 0
        self._turn_start_time: float = 0

        # VAD state (for audio-based detection)
        self._vad_model = None
        self._is_speaking = False
        self._silence_start: float = 0

        # Monitoring task
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

        # Stats
        self._turns_detected = 0
        self._false_triggers_avoided = 0

        logger.info(f"[TURN] TurnDetector initialized (silence={self.config.silence_threshold_ms}ms)")

    async def start(self) -> None:
        """Start the turn detection monitor."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor_loop())
        logger.info("[TURN] Turn detector started")

    async def stop(self) -> None:
        """Stop the turn detection monitor."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass

        # Process any remaining turn
        if self._current_turn:
            await self._finalize_turn()

        logger.info(f"[TURN] Turn detector stopped (turns={self._turns_detected}, avoided={self._false_triggers_avoided})")

    def add_transcript(self, text: str) -> None:
        """Add a new transcript segment.

        Args:
            text: Transcribed text segment
        """
        if not text or not text.strip():
            return

        text = text.strip()
        now = time.time()

        # Start new turn if idle
        if self._state == SpeechState.IDLE:
            self._turn_start_time = now
            self._state = SpeechState.SPEAKING
            logger.info(f"[TURN] New turn started")

        # Add to current turn
        self._current_turn.append(text)
        self._last_speech_time = now
        self._last_transcript_time = now
        self._state = SpeechState.SPEAKING

        full_text = self._get_full_turn()
        logger.debug(f"[TURN] Accumulated: '{full_text[:100]}...' ({len(full_text)} chars)")

    def on_speech_detected(self) -> None:
        """Called when VAD detects speech."""
        self._is_speaking = True
        self._last_speech_time = time.time()
        if self._state == SpeechState.PAUSED:
            self._state = SpeechState.SPEAKING
            logger.debug("[TURN] Speech resumed")

    def on_silence_detected(self) -> None:
        """Called when VAD detects silence."""
        if self._is_speaking:
            self._is_speaking = False
            self._silence_start = time.time()
            if self._state == SpeechState.SPEAKING:
                self._state = SpeechState.PAUSED
                logger.debug("[TURN] Speech paused")

    def _get_full_turn(self) -> str:
        """Get the full accumulated turn text."""
        return " ".join(self._current_turn)

    def _is_turn_complete(self) -> bool:
        """Check if the current turn is semantically complete.

        Returns:
            True if turn appears complete, False otherwise
        """
        if not self._current_turn:
            return False

        full_text = self._get_full_turn()

        # Check minimum content
        words = full_text.split()
        if len(words) < self.config.min_words:
            logger.debug(f"[TURN] Too few words ({len(words)} < {self.config.min_words})")
            return False

        if len(full_text) < self.config.min_chars:
            logger.debug(f"[TURN] Too few chars ({len(full_text)} < {self.config.min_chars})")
            return False

        # Check for connective tissue at the very end
        # Only check the LAST word - if it ends with punctuation, it's likely complete
        last_word = words[-1]
        last_word_clean = last_word.lower().rstrip('.,?!')
        last_char = last_word[-1] if last_word else ''

        # If last word has complete punctuation, trust it even if word is connective
        if last_char not in COMPLETE_PUNCTUATION:
            # No punctuation - check if last word is connective
            if last_word_clean in CONNECTIVE_WORDS:
                logger.debug(f"[TURN] Ends with connective word (no punct): '{last_word_clean}'")
                self._false_triggers_avoided += 1
                return False

            # Also check second-to-last word for dangling prepositions
            if len(words) >= 2:
                second_last = words[-2].lower().rstrip('.,?!')
                if second_last in {"to", "of", "in", "on", "at", "by", "for", "with", "about"}:
                    logger.debug(f"[TURN] Ends with preposition phrase: '{second_last} {last_word_clean}'")
                    self._false_triggers_avoided += 1
                    return False

        # Check for incomplete patterns
        for pattern in INCOMPLETE_PATTERNS:
            if re.search(pattern, full_text, re.IGNORECASE):
                logger.debug(f"[TURN] Matches incomplete pattern: {pattern}")
                self._false_triggers_avoided += 1
                return False

        # Check for complete punctuation (strong indicator)
        last_char = full_text.rstrip()[-1] if full_text.rstrip() else ''
        has_complete_punctuation = last_char in COMPLETE_PUNCTUATION

        # Check silence duration
        now = time.time()
        silence_duration_ms = (now - self._last_speech_time) * 1000

        # Complete if: has punctuation AND sufficient silence
        # OR: longer silence without punctuation (1.5x threshold)
        if has_complete_punctuation and silence_duration_ms >= self.config.silence_threshold_ms:
            logger.info(f"[TURN] Complete: punctuation='{last_char}', silence={silence_duration_ms:.0f}ms")
            return True

        # Without punctuation, wait a bit longer but not too long (1.5x instead of 2x)
        if silence_duration_ms >= self.config.silence_threshold_ms * 1.5:
            logger.info(f"[TURN] Complete: silence={silence_duration_ms:.0f}ms (no punctuation)")
            return True

        return False

    async def _monitor_loop(self) -> None:
        """Background loop that monitors for turn completion."""
        logger.info("[TURN] Monitor loop started")

        try:
            while self._running:
                await asyncio.sleep(0.1)  # Check every 100ms

                if self._state == SpeechState.IDLE:
                    continue

                if self._state in (SpeechState.SPEAKING, SpeechState.PAUSED):
                    # Check if turn is complete
                    if self._is_turn_complete():
                        await self._finalize_turn()

        except asyncio.CancelledError:
            logger.info("[TURN] Monitor loop cancelled")
        except Exception as e:
            logger.error(f"[TURN] Monitor loop error: {e}")
            import traceback
            logger.error(traceback.format_exc())

    async def _finalize_turn(self) -> None:
        """Finalize the current turn and trigger callback."""
        if not self._current_turn:
            return

        full_text = self._get_full_turn()
        self._turns_detected += 1

        logger.info(f"[TURN] === Turn #{self._turns_detected} complete ===")
        logger.info(f"[TURN] Content: '{full_text[:200]}...'")

        # Clear state
        self._current_turn = []
        self._state = SpeechState.IDLE
        self._turn_start_time = 0

        # Trigger callback
        if self.on_turn_complete:
            try:
                if asyncio.iscoroutinefunction(self.on_turn_complete):
                    await self.on_turn_complete(full_text)
                else:
                    self.on_turn_complete(full_text)
            except Exception as e:
                logger.error(f"[TURN] Callback error: {e}")

    def force_complete(self) -> Optional[str]:
        """Force complete the current turn and return the text.

        Useful for session end or timeout scenarios.

        Returns:
            The accumulated turn text, or None if no content
        """
        if not self._current_turn:
            return None

        full_text = self._get_full_turn()
        self._current_turn = []
        self._state = SpeechState.IDLE
        self._turns_detected += 1

        logger.info(f"[TURN] Force completed turn: '{full_text[:100]}...'")
        return full_text

    def get_current_text(self) -> str:
        """Get the current accumulated text without completing."""
        return self._get_full_turn()

    def get_stats(self) -> dict:
        """Get detection statistics."""
        return {
            "turns_detected": self._turns_detected,
            "false_triggers_avoided": self._false_triggers_avoided,
            "current_state": self._state.value,
            "current_words": len(self._current_turn),
        }


class TranscriptAccumulator:
    """Simpler accumulator for when full VAD is not needed.

    This accumulator focuses on:
    1. Merging overlapping transcript chunks
    2. Detecting semantic completion
    3. Avoiding duplicate words
    """

    def __init__(
        self,
        silence_threshold_ms: int = 1500,
        min_words: int = 3,
        on_complete: Optional[Callable[[str], None]] = None,
    ):
        self.silence_threshold_ms = silence_threshold_ms
        self.min_words = min_words
        self.on_complete = on_complete

        self._buffer: List[str] = []
        self._last_update: float = 0
        self._monitor_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """Start the accumulator monitor."""
        self._running = True
        self._monitor_task = asyncio.create_task(self._monitor())
        logger.info("[ACCUMULATOR] Started")

    async def stop(self) -> None:
        """Stop the accumulator."""
        self._running = False
        if self._monitor_task:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("[ACCUMULATOR] Stopped")

    def add(self, text: str) -> None:
        """Add a transcript segment, merging with previous if overlapping."""
        if not text or not text.strip():
            return

        text = text.strip()
        self._last_update = time.time()

        if not self._buffer:
            self._buffer.append(text)
            return

        # Try to merge with previous segment
        merged = self._merge_segments(self._buffer[-1], text)
        if merged != text:
            # Overlap detected, replace last segment
            self._buffer[-1] = merged
        else:
            # No overlap, append as new segment
            self._buffer.append(text)

    def _merge_segments(self, prev: str, new: str) -> str:
        """Merge two transcript segments, removing overlap."""
        prev_words = prev.split()
        new_words = new.split()

        # Look for overlap between end of prev and start of new
        max_overlap = min(5, len(prev_words), len(new_words))

        for overlap_size in range(max_overlap, 0, -1):
            if prev_words[-overlap_size:] == new_words[:overlap_size]:
                # Found overlap, merge
                merged_words = prev_words + new_words[overlap_size:]
                return " ".join(merged_words)

        # No overlap found
        return new

    def get_text(self) -> str:
        """Get the accumulated text."""
        return " ".join(self._buffer)

    def clear(self) -> str:
        """Clear and return the accumulated text."""
        text = self.get_text()
        self._buffer = []
        return text

    def is_complete(self) -> bool:
        """Check if the current accumulation seems complete."""
        if not self._buffer:
            return False

        text = self.get_text()
        words = text.split()

        # Minimum content check
        if len(words) < self.min_words:
            return False

        # Check silence duration
        now = time.time()
        silence_ms = (now - self._last_update) * 1000
        if silence_ms < self.silence_threshold_ms:
            return False

        # Check for connective words at end
        last_word = words[-1].lower().rstrip('.,?!')
        if last_word in CONNECTIVE_WORDS:
            return False

        return True

    async def _monitor(self) -> None:
        """Monitor for completion."""
        try:
            while self._running:
                await asyncio.sleep(0.2)

                if self.is_complete():
                    text = self.clear()
                    if text and self.on_complete:
                        if asyncio.iscoroutinefunction(self.on_complete):
                            await self.on_complete(text)
                        else:
                            self.on_complete(text)

        except asyncio.CancelledError:
            pass
