"""Anthropic Adaptive client: Whisper STT + Claude Chat.

Uses existing Whisper transcription (Groq or OpenAI) paired with
Anthropic Claude for suggestion generation.

Anthropic Messages API format differs from OpenAI:
- Auth: x-api-key header (not Bearer)
- System prompt: top-level "system" field (not in messages array)
- Response: content[0].text (not choices[0].message.content)
"""

import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from config import settings
from services.prompts import (
    get_prompt_with_prep,
    get_response_format,
    format_suggestion_for_display,
    uses_json_response,
    is_not_a_question,
    DEFAULT_PROMPT,
    get_max_tokens_for_verbosity,
    build_conversation_intelligence_suffix,
)
from services.intent_classifier import get_max_tokens_for_intent, pre_classify_intent
from services.turn_detector import TurnDetector, TurnDetectorConfig

logger = logging.getLogger(__name__)

# Anthropic API endpoint
ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"

# Models
#CLAUDE_MODEL = "claude-sonnet-4-6"
CLAUDE_MODEL = "claude-haiku-5-5"

# Haiku 5.5 thinks by default, which delays the first streamed word and counts
# against max_tokens (the NOT_A_QUESTION route allows only 50). The live path
# keeps thinking off, as it was on Haiku 4.5. Remove this to let the model think.
CLAUDE_THINKING = {"type": "disabled"}


class AnthropicLLMClient:
    """Client for Anthropic Claude Messages API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._system_prompt = ""
        self._prompt_key = DEFAULT_PROMPT
        self._request_count = 0
        self._conversation_history_obj = None  # ConversationHistory object (feature-flagged)

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=60.0,
                headers={
                    "x-api-key": self.api_key,
                    "anthropic-version": ANTHROPIC_VERSION,
                    "anthropic-beta": "prompt-caching-2024-07-31",
                    "content-type": "application/json",
                },
            )
        return self._client

    def set_context(
        self,
        job_description: str = "",
        resume: str = "",
        work_experience: str = "",
        verbosity: str = "moderate",
        prompt_key: str = None,
        pre_prepared_answers: str = "",
        company_name: str = "",
        role_type: str = "",
        round_type: str = "",
    ):
        """Set the system prompt with interview context."""
        self._prompt_key = prompt_key or DEFAULT_PROMPT
        self._verbosity = verbosity
        self._system_prompt = get_prompt_with_prep(
            prompt_key=self._prompt_key,
            job_description=job_description[:2000] if job_description else "",
            resume=resume[:2000] if resume else "",
            work_experience=work_experience[:2000] if work_experience else "",
            verbosity=verbosity,
            pre_prepared_answers=pre_prepared_answers,
            company_name=company_name,
            role_type=role_type,
            round_type=round_type,
        )
        logger.info(
            f"[ANTHROPIC-LLM] System prompt set: {len(self._system_prompt)} chars, "
            f"company={company_name or 'N/A'}, role={role_type or 'N/A'}, "
            f"round={round_type or 'N/A'}, prep_answers={len(pre_prepared_answers)} chars"
        )
        logger.info(
            f"\n{'='*80}\n[ANTHROPIC-LLM] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
            f"{self._system_prompt}\n{'='*80}\n[ANTHROPIC-LLM] END OF SYSTEM PROMPT\n{'='*80}"
        )

    def _build_payload(self, user_content: str, is_json: bool, stream: bool = False) -> dict:
        """Build the Anthropic Messages API payload."""
        verbosity = getattr(self, '_verbosity', 'moderate')

        # Build conversation intelligence suffix (additive, feature-flagged)
        ci_suffix = ""
        if self._conversation_history_obj:
            ci_suffix = build_conversation_intelligence_suffix(
                conversation_history=self._conversation_history_obj.get_formatted_history(),
                phase_instruction=self._conversation_history_obj.get_phase_instruction(),
                question_count=self._conversation_history_obj.get_question_count(),
            )

        # Adaptive max_tokens based on intent pre-classification
        if settings.enable_adaptive_tokens and not is_json:
            pre_intent = pre_classify_intent(user_content)
            max_tokens = get_max_tokens_for_intent(pre_intent or "new_question", verbosity)
        else:
            max_tokens = get_max_tokens_for_verbosity(verbosity)

        # Build system prompt: original (cached) + intelligence suffix (dynamic)
        system_blocks = [
            {
                "type": "text",
                "text": self._system_prompt,
                "cache_control": {"type": "ephemeral"},
            }
        ]
        if ci_suffix:
            system_blocks.append({
                "type": "text",
                "text": ci_suffix,
            })

        payload = {
            "model": CLAUDE_MODEL,
            "max_tokens": max_tokens,
            "thinking": CLAUDE_THINKING,
            "system": system_blocks,
            "messages": [
                {"role": "user", "content": user_content},
            ],
        }
        if stream:
            payload["stream"] = True
        return payload

    def _build_user_content(self, transcript: str, conversation_context: str, is_json: bool) -> str:
        """Build the user message content."""
        context_prefix = ""
        if conversation_context:
            context_prefix = (
                f"Here is the recent conversation for context (use this to understand "
                f"what has already been discussed and what the candidate has said):\n"
                f"---\n{conversation_context}\n---\n\n"
            )

        if is_json:
            return f'{context_prefix}The interviewer said: "{transcript}"\n\nAnalyze and respond in JSON format.'
        return f'{context_prefix}The interviewer said: "{transcript}"'

    async def get_suggestion_streaming(
        self,
        transcript: str,
        conversation_context: str = "",
        on_delta: callable = None,
    ) -> Optional[dict]:
        """Get a suggestion using Claude with streaming output.

        For plain text prompts (personalized/coach), streams deltas via on_delta callback
        so the user sees text appearing progressively. Falls back to non-streaming for
        JSON response modes (candidate/star).

        Args:
            transcript: The interviewer's statement/question
            conversation_context: Recent conversation history
            on_delta: Async callback(delta_text: str) called for each text chunk

        Returns:
            Dict with is_question, suggestion, and formatted_text, or None if failed
        """
        is_json = uses_json_response(self._prompt_key)

        # JSON modes can't be streamed progressively (need full JSON to parse)
        if is_json:
            return await self.get_suggestion(transcript, conversation_context)

        try:
            start_time = time.time()
            client = await self._get_client()

            user_content = self._build_user_content(transcript, conversation_context, is_json)
            payload = self._build_payload(user_content, is_json, stream=True)

            logger.info(
                f"[ANTHROPIC-LLM] Streaming request - model={CLAUDE_MODEL}, "
                f"prompt_caching=enabled, user_content='{user_content[:100]}...'"
            )

            accumulated_text = ""
            input_tokens = 0
            output_tokens = 0
            cache_creation = 0
            cache_read = 0
            first_token_time = None
            # Buffer for stripping intent classification tags (e.g., [NEW_QUESTION])
            # from streaming output so the client never sees raw tags
            _intent_tag_checked = False
            _intent_buffer = ""

            async with client.stream("POST", ANTHROPIC_API_URL, json=payload) as response:
                if response.status_code != 200:
                    body = await response.aread()
                    error_body = body.decode()
                    logger.error(f"[ANTHROPIC-CLAUDE] Stream error {response.status_code}: {error_body}")
                    # Return error info so caller can surface it to the user
                    return {"error": True, "status_code": response.status_code, "message": error_body}

                # Parse SSE events from the stream
                async for line in response.aiter_lines():
                    if not line.startswith("data: "):
                        continue

                    data_str = line[6:]  # strip "data: "
                    if data_str.strip() == "[DONE]":
                        break

                    try:
                        event = json.loads(data_str)
                    except json.JSONDecodeError:
                        continue

                    event_type = event.get("type")

                    if event_type == "message_start":
                        usage = event.get("message", {}).get("usage", {})
                        input_tokens = usage.get("input_tokens", 0)
                        cache_creation = usage.get("cache_creation_input_tokens", 0)
                        cache_read = usage.get("cache_read_input_tokens", 0)

                    elif event_type == "content_block_delta":
                        delta = event.get("delta", {})
                        if delta.get("type") == "text_delta":
                            text_chunk = delta.get("text", "")
                            if text_chunk:
                                if first_token_time is None:
                                    first_token_time = time.time()
                                accumulated_text += text_chunk

                                # Check for NOT_A_QUESTION sentinel early to avoid
                                # streaming a non-answer to the client
                                if is_not_a_question(accumulated_text):
                                    logger.info("[ANTHROPIC-CLAUDE] Stream detected NOT_A_QUESTION sentinel, aborting")
                                    break

                                # Strip intent classification tags from streaming output
                                # e.g., [NEW_QUESTION] prefix should not be shown to user
                                if not _intent_tag_checked:
                                    _intent_buffer += text_chunk
                                    # Check if we have enough to determine if there's a tag
                                    stripped_buf = _intent_buffer.lstrip()
                                    if stripped_buf.startswith("["):
                                        # Looks like an intent tag — buffer until ] is found
                                        bracket_end = stripped_buf.find("]")
                                        if bracket_end >= 0:
                                            # Found complete tag — strip it, send remainder
                                            _intent_tag_checked = True
                                            remainder = stripped_buf[bracket_end + 1:].lstrip()
                                            if remainder and on_delta:
                                                await on_delta(remainder)
                                        # else: still buffering, don't send to client yet
                                    else:
                                        # No tag prefix — flush buffer and stream normally
                                        _intent_tag_checked = True
                                        if on_delta:
                                            await on_delta(_intent_buffer)
                                    continue

                                if on_delta:
                                    await on_delta(text_chunk)

                    elif event_type == "message_delta":
                        usage = event.get("usage", {})
                        output_tokens = usage.get("output_tokens", 0)
                        if event.get("delta", {}).get("stop_reason") == "refusal":
                            logger.warning("[ANTHROPIC-CLAUDE] Model declined this request (stop_reason=refusal)")

            elapsed = (time.time() - start_time) * 1000
            ttft = ((first_token_time - start_time) * 1000) if first_token_time else elapsed
            self._request_count += 1

            cache_status = "HIT" if cache_read > 0 else ("CREATED" if cache_creation > 0 else "NONE")
            logger.info(
                f"[ANTHROPIC-CLAUDE] Stream complete in {elapsed:.0f}ms (TTFT={ttft:.0f}ms) | "
                f"Cache: {cache_status} | Tokens: in={input_tokens} out={output_tokens} "
                f"cache_create={cache_creation} cache_read={cache_read}"
            )

            plain_text = accumulated_text.strip()
            if not plain_text:
                return None

            # Check for question gate sentinel
            if is_not_a_question(plain_text):
                logger.info("[ANTHROPIC-CLAUDE] Not a question (sentinel), skipping suggestion")
                return {"is_question": False, "suggestion": None}

            return {
                "is_question": True,
                "suggestion": {"response": plain_text},
                "formatted_text": plain_text,
            }

        except Exception as e:
            logger.error(f"[ANTHROPIC-CLAUDE] Streaming error: {e}")
            return None

    async def get_suggestion(self, transcript: str, conversation_context: str = "") -> Optional[dict]:
        """Get a suggestion using Claude (non-streaming, used for JSON modes).

        Args:
            transcript: The interviewer's statement/question
            conversation_context: Recent conversation history for follow-up context

        Returns:
            Dict with is_question, suggestion, and formatted_text, or None if failed
        """
        try:
            start_time = time.time()

            client = await self._get_client()

            is_json = uses_json_response(self._prompt_key)
            user_content = self._build_user_content(transcript, conversation_context, is_json)
            payload = self._build_payload(user_content, is_json)

            logger.info(
                f"[ANTHROPIC-LLM] Request to LLM - model={CLAUDE_MODEL}, json_mode={is_json}, "
                f"prompt_caching=enabled, user_content='{user_content[:100]}...'"
            )

            response = await client.post(ANTHROPIC_API_URL, json=payload)

            elapsed = (time.time() - start_time) * 1000
            self._request_count += 1

            if response.status_code == 200:
                data = response.json()
                if data.get("stop_reason") == "refusal":
                    logger.warning("[ANTHROPIC-CLAUDE] Model declined this request (stop_reason=refusal)")
                    return None
                # Read the answer by block type: a response can open with thinking blocks
                content = next(
                    (b.get("text", "") for b in data.get("content", []) if b.get("type") == "text"),
                    "",
                )

                usage = data.get("usage", {})
                cache_creation = usage.get("cache_creation_input_tokens", 0)
                cache_read = usage.get("cache_read_input_tokens", 0)
                input_tokens = usage.get("input_tokens", 0)
                output_tokens = usage.get("output_tokens", 0)
                cache_status = "HIT" if cache_read > 0 else ("CREATED" if cache_creation > 0 else "NONE")
                logger.info(
                    f"[ANTHROPIC-CLAUDE] Suggestion in {elapsed:.0f}ms (json={is_json}) | "
                    f"Cache: {cache_status} | Tokens: in={input_tokens} out={output_tokens} "
                    f"cache_create={cache_creation} cache_read={cache_read}"
                )

                if is_json:
                    result = json.loads(content)

                    if result.get("is_question") and result.get("suggestion"):
                        response_format = get_response_format(self._prompt_key)
                        formatted_text = format_suggestion_for_display(
                            result["suggestion"],
                            response_format,
                        )
                        result["formatted_text"] = formatted_text

                    return result
                else:
                    plain_text = content.strip()
                    if not plain_text:
                        return None

                    # Check for question gate sentinel
                    if is_not_a_question(plain_text):
                        logger.info("[ANTHROPIC-CLAUDE] Not a question (sentinel), skipping suggestion")
                        return {"is_question": False, "suggestion": None}

                    return {
                        "is_question": True,
                        "suggestion": {"response": plain_text},
                        "formatted_text": plain_text,
                    }
            else:
                logger.error(f"[ANTHROPIC-CLAUDE] Error {response.status_code}: {response.text}")
                return None

        except Exception as e:
            logger.error(f"[ANTHROPIC-CLAUDE] Suggestion error: {e}")
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class AnthropicAdaptiveClient:
    """Combined client: Whisper (Groq/OpenAI) + Claude.

    Since Anthropic has no STT API, transcription is handled by
    Groq Whisper (preferred, fastest) or OpenAI Whisper (fallback).
    """

    def __init__(self, api_key: str = None, whisper_api_key: str = None):
        """Initialize the Anthropic Adaptive client.

        Args:
            api_key: Anthropic API key for Claude
            whisper_api_key: API key for Whisper transcription (Groq or OpenAI)
        """
        self._api_key = api_key
        self._whisper_api_key = whisper_api_key
        self._connected = False
        self._transcription_client = None  # GroqTranscriptionClient or OpenAITranscriptionClient
        self._deepgram_client = None  # DeepgramStreamingClient when feature flag is on
        self._llm_client: Optional[AnthropicLLMClient] = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

        # Semantic turn detection
        self._turn_detector: Optional[TurnDetector] = None

        # Transcript merging for overlapping chunks
        self._last_transcript = ""
        self._transcript_buffer = []

        # Frontend VAD speech timing (Option B1)
        self._frontend_is_speaking = True
        self._frontend_silence_ms = 0.0

        # Speaker tracking for Issue #3: only suggest on interviewer speech
        self._current_speaker = "interviewer"

        # Conversation history for LLM context (both speakers)
        self._conversation_history: list = []

        # Deepgram streaming state
        self._deepgram_turn_buffer = ""
        self._deepgram_needs_new_turn = True

        # Per-segment speaker voting for Deepgram mode
        # Only accumulates votes while Deepgram is actively producing a segment
        # (between first interim and final). Silence periods between segments are ignored.
        self._segment_speaker_votes: list = []  # ["interviewer", "user", ...]
        self._deepgram_segment_active = False  # True while Deepgram is producing interims

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stt_mode(self) -> str:
        """Return the STT mode: 'streaming' for Deepgram, 'chunked' for Whisper."""
        return "streaming" if self._deepgram_client else "chunked"

    def set_conversation_history(self, history) -> None:
        """Set the ConversationHistory object for conversation intelligence."""
        self._ci_history = history

    def update_speech_timing(self, is_speaking: bool, silence_ms: float) -> None:
        """Update speech timing from frontend VAD."""
        self._frontend_is_speaking = is_speaking
        self._frontend_silence_ms = silence_ms
        logger.debug(f"[ANTHROPIC-ADAPTIVE] Speech timing: speaking={is_speaking}, silence={silence_ms:.0f}ms")

    async def check_and_trigger_suggestion(self) -> None:
        """Check if conditions are met to trigger suggestion generation.
        Only triggers for interviewer speech."""
        if not self._connected or not self._turn_detector:
            return

        # Skip when Deepgram handles turn detection via UtteranceEnd
        if self._deepgram_client:
            return

        if not settings.use_speech_timing_for_turns:
            return

        # Note: We do NOT check _current_speaker here — it can be stale/wrong.
        # The last audio chunk before silence may be misclassified as 'user' due to
        # low-signal mic noise exceeding the now-silent system audio. The TurnDetector
        # only accumulates interviewer text, so if it has content, it's from the interviewer.

        if not self._frontend_is_speaking and self._frontend_silence_ms >= settings.speech_silence_threshold_ms:
            current_text = self._turn_detector.get_current_text()
            if current_text and len(current_text.split()) >= settings.turn_min_words:
                logger.info(
                    f"[ANTHROPIC-ADAPTIVE] Triggering suggestion: silence={self._frontend_silence_ms:.0f}ms "
                    f">= {settings.speech_silence_threshold_ms}ms"
                )
                complete_text = self._turn_detector.force_complete()
                if complete_text:
                    await self._on_turn_complete(complete_text)

    async def connect(
        self,
        job_description: str = "",
        resume: str = "",
        work_experience: str = "",
        verbosity: str = "moderate",
        prompt_key: str = None,
        pre_prepared_answers: str = "",
        company_name: str = "",
        role_type: str = "",
        round_type: str = "",
    ) -> bool:
        """Initialize Anthropic + Whisper clients."""
        try:
            anthropic_key = self._api_key or settings.anthropic_api_key

            if not anthropic_key:
                logger.error("[ANTHROPIC-ADAPTIVE] No Anthropic API key configured")
                return False

            # Initialize STT client — Deepgram (streaming) or Whisper (batch)
            if settings.use_deepgram_stt and settings.deepgram_api_key:
                try:
                    from services.deepgram_client import DeepgramStreamingClient
                    self._deepgram_client = DeepgramStreamingClient(settings.deepgram_api_key)
                    dg_connected = await self._deepgram_client.connect(
                        on_transcript=self._on_deepgram_transcript,
                        on_utterance_end=self._on_deepgram_utterance_end,
                    )
                    if dg_connected:
                        self._deepgram_turn_buffer = ""
                        self._deepgram_needs_new_turn = True
                        self._segment_speaker_votes = []
                        self._deepgram_segment_active = False
                        logger.info("[ANTHROPIC-ADAPTIVE] Using Deepgram Nova-2 for STT (streaming)")
                    else:
                        logger.warning("[ANTHROPIC-ADAPTIVE] Deepgram connection failed, falling back to Whisper")
                        self._deepgram_client = None
                except Exception as e:
                    logger.warning(f"[ANTHROPIC-ADAPTIVE] Deepgram init failed, falling back to Whisper: {e}")
                    self._deepgram_client = None

            # Whisper fallback (or primary when Deepgram is disabled)
            if not self._deepgram_client:
                whisper_key = self._whisper_api_key
                if whisper_key:
                    if whisper_key.startswith("gsk_"):
                        from services.groq_client import GroqTranscriptionClient
                        self._transcription_client = GroqTranscriptionClient(whisper_key)
                        logger.info("[ANTHROPIC-ADAPTIVE] Using Groq Whisper for transcription")
                    else:
                        from services.openai_adaptive_client import OpenAITranscriptionClient
                        self._transcription_client = OpenAITranscriptionClient(whisper_key)
                        logger.info("[ANTHROPIC-ADAPTIVE] Using OpenAI Whisper for transcription")
                else:
                    if settings.groq_api_key:
                        from services.groq_client import GroqTranscriptionClient
                        self._transcription_client = GroqTranscriptionClient(settings.groq_api_key)
                        logger.info("[ANTHROPIC-ADAPTIVE] Using server Groq Whisper for transcription")
                    elif settings.openai_api_key:
                        from services.openai_adaptive_client import OpenAITranscriptionClient
                        self._transcription_client = OpenAITranscriptionClient(settings.openai_api_key)
                        logger.info("[ANTHROPIC-ADAPTIVE] Using server OpenAI Whisper for transcription")
                    else:
                        logger.error("[ANTHROPIC-ADAPTIVE] No Whisper API key available (need Groq or OpenAI)")
                        return False

            # Initialize Claude LLM client
            self._llm_client = AnthropicLLMClient(anthropic_key)

            # Pass conversation intelligence history if set
            if hasattr(self, '_ci_history') and self._ci_history:
                self._llm_client._conversation_history_obj = self._ci_history

            self._llm_client.set_context(
                job_description=job_description,
                resume=resume,
                work_experience=work_experience,
                verbosity=verbosity,
                prompt_key=prompt_key,
                pre_prepared_answers=pre_prepared_answers,
                company_name=company_name,
                role_type=role_type,
                round_type=round_type,
            )

            # Initialize semantic turn detector
            if settings.turn_detection_enabled:
                turn_config = TurnDetectorConfig(
                    pause_threshold_ms=800,
                    silence_threshold_ms=settings.turn_silence_threshold_ms,
                    min_words=settings.turn_min_words,
                    min_chars=10,
                )
                self._turn_detector = TurnDetector(
                    config=turn_config,
                    on_turn_complete=self._on_turn_complete,
                )

                if settings.use_speech_timing_for_turns:
                    logger.info(
                        f"[ANTHROPIC-ADAPTIVE] Turn detection with frontend VAD "
                        f"(silence={settings.speech_silence_threshold_ms}ms)"
                    )
                else:
                    await self._turn_detector.start()
                    logger.info(
                        f"[ANTHROPIC-ADAPTIVE] Turn detection with transcript timing "
                        f"(silence={settings.turn_silence_threshold_ms}ms)"
                    )
            else:
                logger.info("[ANTHROPIC-ADAPTIVE] Turn detection disabled")

            self._connected = True
            self._running = True

            prompt_name = prompt_key or DEFAULT_PROMPT
            logger.info(f"[ANTHROPIC-ADAPTIVE] Connected (prompt: {prompt_name})")
            return True

        except Exception as e:
            logger.error(f"[ANTHROPIC-ADAPTIVE] Connection failed: {e}")
            return False

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """Process audio chunk with speaker awareness.
        Only generates suggestions for interviewer speech.
        User speech is captured for conversation context only.
        """
        if not self._connected:
            return

        self._current_speaker = speaker

        # Deepgram streaming: just forward audio, callbacks handle the rest
        if self._deepgram_client:
            # Only accumulate speaker votes while Deepgram is actively producing a segment.
            # During silence between segments, mic ambient > system silence → false "user" votes.
            if self._deepgram_segment_active:
                self._segment_speaker_votes.append(speaker)
            await self._deepgram_client.send_audio(audio_data)
            return

        try:
            # Whisper batch transcription
            transcript = await self._transcription_client.transcribe(audio_data)

            if not transcript or len(transcript.strip()) < 3:
                return

            # Filter out noise transcripts (Whisper hallucinations, single words, etc.)
            from services.groq_client import is_noise_transcript
            if is_noise_transcript(transcript):
                logger.debug(f"[ANTHROPIC-ADAPTIVE] Filtered noise transcript: '{transcript}'")
                return

            merged_transcript = self._merge_transcript(transcript)

            if not merged_transcript:
                return

            await self._message_queue.put({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": merged_transcript,
            })

            # Add to conversation history (both speakers)
            self._conversation_history.append({
                "speaker": speaker,
                "text": merged_transcript,
            })
            if len(self._conversation_history) > 20:
                self._conversation_history = self._conversation_history[-20:]

            # Only accumulate interviewer speech for suggestion generation
            if speaker == "user":
                logger.debug(f"[ANTHROPIC-ADAPTIVE] User speech captured for context: '{merged_transcript[:60]}...'")
                return

            if self._turn_detector:
                self._turn_detector.add_transcript(merged_transcript)

                if settings.use_speech_timing_for_turns:
                    if not self._frontend_is_speaking and self._frontend_silence_ms >= settings.speech_silence_threshold_ms:
                        current_text = self._turn_detector.get_current_text()
                        if current_text and len(current_text.split()) >= settings.turn_min_words:
                            logger.info(
                                f"[ANTHROPIC-ADAPTIVE] Frontend VAD: speech ended, "
                                f"silence={self._frontend_silence_ms:.0f}ms, forcing turn complete"
                            )
                            complete_text = self._turn_detector.force_complete()
                            if complete_text:
                                await self._on_turn_complete(complete_text)
            else:
                await self._on_turn_complete(merged_transcript)

        except Exception as e:
            logger.error(f"[ANTHROPIC-ADAPTIVE] Error processing audio: {e}")

    def _build_conversation_context(self) -> str:
        """Build conversation context string from recent history."""
        if not self._conversation_history:
            return ""
        lines = []
        for entry in self._conversation_history:
            label = "Candidate" if entry["speaker"] == "user" else "Interviewer"
            lines.append(f"{label}: {entry['text']}")
        return "\n".join(lines)

    async def _on_turn_complete(self, complete_text: str) -> None:
        """Called when a complete interviewer turn is detected.
        Uses streaming for plain text modes to reduce perceived latency.
        """
        logger.info(f"[ANTHROPIC-ADAPTIVE] Turn complete (interviewer): '{complete_text[:100]}...'")

        self._last_transcript = ""
        self._transcript_buffer = []

        try:
            conversation_context = self._build_conversation_context()

            # Generate a stable suggestion ID for this turn
            import uuid
            suggestion_id = str(uuid.uuid4())
            is_first_delta = True

            async def on_streaming_delta(delta_text: str):
                """Push each text chunk to the WebSocket via message queue."""
                nonlocal is_first_delta
                await self._message_queue.put({
                    "type": "suggestion.delta",
                    "id": suggestion_id,
                    "delta": delta_text,
                    "isFirst": is_first_delta,
                })
                if is_first_delta:
                    is_first_delta = False

            suggestion_data = await self._llm_client.get_suggestion_streaming(
                complete_text,
                conversation_context=conversation_context,
                on_delta=on_streaming_delta,
            )

            # Handle API errors (e.g., 401 invalid key) — surface to user
            if suggestion_data and suggestion_data.get("error"):
                status_code = suggestion_data.get("status_code", 0)
                if status_code == 401:
                    error_msg = "Invalid Anthropic API key. Please update your API key in Settings."
                else:
                    error_msg = f"Anthropic API error ({status_code}). Please try again."
                logger.error(f"[ANTHROPIC-ADAPTIVE] LLM API error {status_code}, notifying client")
                await self._message_queue.put({
                    "type": "error",
                    "error": {"message": error_msg, "code": f"API_{status_code}"},
                })
                return

            if suggestion_data and suggestion_data.get("is_question") and suggestion_data.get("suggestion"):
                formatted = suggestion_data.get("formatted_text")

                if not formatted:
                    suggestion = suggestion_data["suggestion"]
                    formatted = f"**Suggested Response:**\n{suggestion.get('response', '')}\n\n"

                    if suggestion.get("key_points"):
                        formatted += "**Key Points:**\n"
                        for point in suggestion["key_points"]:
                            formatted += f"- {point}\n"
                        formatted += "\n"

                    if suggestion.get("follow_up"):
                        formatted += f"**If They Ask More:**\n{suggestion['follow_up']}"

                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": formatted,
                    "id": suggestion_id,
                })
                logger.info("[ANTHROPIC-ADAPTIVE] Suggestion stream complete, sent to client")
            else:
                # Not a question — cancel any in-progress streaming suggestion on frontend
                if not is_first_delta:
                    # Some deltas were already sent before sentinel was detected
                    await self._message_queue.put({
                        "type": "suggestion.cancel",
                        "id": suggestion_id,
                    })
                if suggestion_data is None:
                    logger.warning("[ANTHROPIC-ADAPTIVE] LLM returned no response (possible error)")
                else:
                    logger.info("[ANTHROPIC-ADAPTIVE] Not a question, no suggestion generated")

        except Exception as e:
            logger.error(f"[ANTHROPIC-ADAPTIVE] Error generating suggestion: {e}")

    def _get_segment_speaker(self) -> str:
        """Determine dominant speaker from accumulated votes since last final transcript.

        Uses majority voting over all audio chunks received during this Deepgram segment.
        This is more reliable than the instantaneous _current_speaker because it averages
        over the entire segment rather than using a single point-in-time sample that may
        be stale (e.g., mic ambient noise after interviewer stops speaking).
        """
        if not self._segment_speaker_votes:
            return self._current_speaker

        interviewer_count = self._segment_speaker_votes.count("interviewer")
        user_count = self._segment_speaker_votes.count("user")
        return "interviewer" if interviewer_count >= user_count else "user"

    async def _on_deepgram_transcript(self, transcript: str, segment_id: str, is_final: bool) -> None:
        """Called by Deepgram for each transcript result (interim or final)."""
        if not transcript:
            return

        # Mark segment as active on first interim — starts accumulating speaker votes
        if not self._deepgram_segment_active:
            self._deepgram_segment_active = True
            self._segment_speaker_votes = []  # Fresh votes for this segment

        is_new_turn = self._deepgram_needs_new_turn and is_final
        if is_new_turn:
            self._deepgram_needs_new_turn = False

        # Use majority-vote speaker for this segment instead of instantaneous _current_speaker
        segment_speaker = self._get_segment_speaker()

        await self._message_queue.put({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": transcript,
            "segmentId": segment_id,
            "isFinal": is_final,
            "isNewTurn": is_new_turn,
            "speaker": segment_speaker,
        })

        if is_final:
            self._deepgram_turn_buffer += " " + transcript
            self._conversation_history.append({
                "speaker": segment_speaker,
                "text": transcript,
            })
            if len(self._conversation_history) > 20:
                self._conversation_history = self._conversation_history[-20:]
            # Segment complete — stop accumulating votes until next segment starts
            self._deepgram_segment_active = False
            self._segment_speaker_votes = []

    async def _on_deepgram_utterance_end(self) -> None:
        """Called by Deepgram when a complete utterance/turn is detected."""
        self._deepgram_needs_new_turn = True

        complete_text = self._deepgram_turn_buffer.strip()
        self._deepgram_turn_buffer = ""

        if not complete_text:
            return

        # Note: We do NOT filter by speaker here. The energy-based speaker detection
        # in the AudioWorklet is unreliable (mic picks up ambient noise, causing
        # interviewer speech to be misclassified as "user" when system audio drops).
        # Instead, we let _on_turn_complete → LLM decide if the text is a question.

        if len(complete_text.split()) < settings.turn_min_words:
            logger.debug(f"[ANTHROPIC-ADAPTIVE] Deepgram utterance too short: '{complete_text}'")
            return

        logger.info(f"[ANTHROPIC-ADAPTIVE] Deepgram utterance complete: '{complete_text[:100]}...'")
        await self._on_turn_complete(complete_text)

    def _merge_transcript(self, new_transcript: str) -> str:
        """Merge overlapping transcripts to avoid word duplication."""
        if not self._last_transcript:
            self._last_transcript = new_transcript
            return new_transcript

        last_words = self._last_transcript.split()[-5:]
        new_words = new_transcript.split()

        overlap_start = -1
        for i in range(min(len(last_words), len(new_words))):
            if last_words[-(i + 1) :] == new_words[: i + 1]:
                overlap_start = i + 1

        if overlap_start > 0:
            merged = " ".join(new_words[overlap_start:])
        else:
            merged = new_transcript

        self._last_transcript = new_transcript
        return merged.strip() if merged.strip() else None

    async def receive_messages(self):
        """Yield messages from the queue."""
        try:
            while self._running:
                try:
                    message = await asyncio.wait_for(
                        self._message_queue.get(),
                        timeout=0.1,
                    )
                    yield message
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            pass

    async def disconnect(self):
        """Disconnect and cleanup."""
        self._connected = False
        self._running = False

        # Process remaining Deepgram turn buffer
        if self._deepgram_client and self._deepgram_turn_buffer.strip():
            remaining = self._deepgram_turn_buffer.strip()
            self._deepgram_turn_buffer = ""
            if remaining and len(remaining.split()) >= settings.turn_min_words:
                logger.info(f"[ANTHROPIC-ADAPTIVE] Processing remaining Deepgram buffer: '{remaining[:50]}...'")
                await self._on_turn_complete(remaining)

        if self._turn_detector:
            remaining = self._turn_detector.force_complete()
            if remaining:
                logger.info(f"[ANTHROPIC-ADAPTIVE] Processing remaining turn: '{remaining[:50]}...'")
                await self._on_turn_complete(remaining)
            if not settings.use_speech_timing_for_turns:
                await self._turn_detector.stop()
            stats = self._turn_detector.get_stats()
            logger.info(f"[ANTHROPIC-ADAPTIVE] Turn detector stats: {stats}")

        if self._deepgram_client:
            await self._deepgram_client.close()
        if self._transcription_client:
            await self._transcription_client.close()
        if self._llm_client:
            await self._llm_client.close()

        logger.info("[ANTHROPIC-ADAPTIVE] Disconnected")
