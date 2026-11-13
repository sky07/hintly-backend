"""OpenAI Adaptive client: Whisper STT + GPT-4o Chat Completions.

Mirrors the Groq Adaptive pattern but uses OpenAI APIs:
- Whisper API for transcription (accepts 16kHz WAV)
- GPT-4o Chat Completions for suggestion generation

This bypasses all OpenAI Realtime API issues (BUG-O2 through BUG-O8).
"""

import asyncio
import json
import logging
import time
from typing import Optional

import httpx

from config import settings
from services.groq_client import pcm16_to_wav, is_noise_transcript
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

# OpenAI API endpoints
OPENAI_API_BASE = "https://api.openai.com/v1"
OPENAI_TRANSCRIPTION_URL = f"{OPENAI_API_BASE}/audio/transcriptions"
OPENAI_CHAT_URL = f"{OPENAI_API_BASE}/chat/completions"

# Models
WHISPER_MODEL = "whisper-1"
CHAT_MODEL = "gpt-4o"


class OpenAITranscriptionClient:
    """Client for OpenAI Whisper transcription API."""

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._request_count = 0
        self._last_request_time = 0.0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Authorization": f"Bearer {self.api_key}"},
            )
        return self._client

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> Optional[str]:
        """Transcribe audio using OpenAI Whisper API.

        Args:
            audio_bytes: Raw PCM16 audio data
            sample_rate: Audio sample rate (default 16000)

        Returns:
            Transcribed text or None if failed
        """
        try:
            start_time = time.time()

            wav_bytes = pcm16_to_wav(audio_bytes, sample_rate=sample_rate)

            client = await self._get_client()

            files = {
                "file": ("audio.wav", wav_bytes, "audio/wav"),
                "model": (None, WHISPER_MODEL),
                "response_format": (None, "text"),
                "language": (None, "en"),
            }

            response = await client.post(OPENAI_TRANSCRIPTION_URL, files=files)

            elapsed = (time.time() - start_time) * 1000
            self._request_count += 1
            self._last_request_time = time.time()

            if response.status_code == 200:
                transcript = response.text.strip()
                logger.info(f"[OPENAI-WHISPER] Transcribed in {elapsed:.0f}ms: '{transcript[:50]}...'")
                return transcript
            else:
                logger.error(f"[OPENAI-WHISPER] Error {response.status_code}: {response.text}")
                return None

        except Exception as e:
            logger.error(f"[OPENAI-WHISPER] Transcription error: {e}")
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class OpenAILLMClient:
    """Client for OpenAI GPT-4o Chat Completions."""

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
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
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
            f"[OPENAI-LLM] System prompt set: {len(self._system_prompt)} chars, "
            f"company={company_name or 'N/A'}, role={role_type or 'N/A'}, "
            f"round={round_type or 'N/A'}, prep_answers={len(pre_prepared_answers)} chars"
        )
        logger.info(
            f"\n{'='*80}\n[OPENAI-LLM] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
            f"{self._system_prompt}\n{'='*80}\n[OPENAI-LLM] END OF SYSTEM PROMPT\n{'='*80}"
        )

    async def get_suggestion(self, transcript: str, conversation_context: str = "") -> Optional[dict]:
        """Get a suggestion for the given transcript.

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

            context_prefix = ""
            if conversation_context:
                context_prefix = (
                    f"Here is the recent conversation for context (use this to understand "
                    f"what has already been discussed and what the candidate has said):\n"
                    f"---\n{conversation_context}\n---\n\n"
                )

            verbosity = getattr(self, '_verbosity', 'moderate')

            # Build conversation intelligence suffix (additive, feature-flagged)
            ci_suffix = ""
            if self._conversation_history_obj:
                ci_suffix = build_conversation_intelligence_suffix(
                    conversation_history=self._conversation_history_obj.get_formatted_history(),
                    phase_instruction=self._conversation_history_obj.get_phase_instruction(),
                    question_count=self._conversation_history_obj.get_question_count(),
                )

            # Build system prompt with optional intelligence suffix
            system_prompt = self._system_prompt
            if ci_suffix:
                system_prompt = self._system_prompt + "\n\n" + ci_suffix

            # Adaptive max_tokens based on intent pre-classification
            if settings.enable_adaptive_tokens and not is_json:
                pre_intent = pre_classify_intent(transcript)
                max_tokens = get_max_tokens_for_intent(pre_intent or "new_question", verbosity)
            else:
                max_tokens = get_max_tokens_for_verbosity(verbosity)

            if is_json:
                user_content = f'{context_prefix}The interviewer said: "{transcript}"\n\nAnalyze and respond in JSON format.'
                payload = {
                    "model": CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                }
            else:
                user_content = f'{context_prefix}The interviewer said: "{transcript}"'
                payload = {
                    "model": CHAT_MODEL,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                }

            logger.info(
                f"[OPENAI-LLM] Request to LLM - model={CHAT_MODEL}, json_mode={is_json}, "
                f"user_content='{user_content[:100]}...'"
            )

            response = await client.post(OPENAI_CHAT_URL, json=payload)

            elapsed = (time.time() - start_time) * 1000
            self._request_count += 1

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(f"[OPENAI-GPT] Suggestion in {elapsed:.0f}ms (json={is_json})")

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
                        logger.info("[OPENAI-GPT] Not a question (sentinel), skipping suggestion")
                        return {"is_question": False, "suggestion": None}

                    logger.info(f"[OPENAI-GPT] Plain text response: '{plain_text[:80]}...'")

                    return {
                        "is_question": True,
                        "suggestion": {"response": plain_text},
                        "formatted_text": plain_text,
                    }
            else:
                logger.error(f"[OPENAI-GPT] Error {response.status_code}: {response.text}")
                return None

        except Exception as e:
            logger.error(f"[OPENAI-GPT] Suggestion error: {e}")
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class OpenAIAdaptiveClient:
    """Combined OpenAI client using Whisper + GPT-4o.

    Mirrors GroqAdaptiveClient but uses OpenAI APIs.
    Implements the same Semantic Turn Detection pattern.
    """

    def __init__(self, api_key: str = None):
        self._api_key = api_key
        self._connected = False
        self._transcription_client: Optional[OpenAITranscriptionClient] = None
        self._deepgram_client = None  # DeepgramStreamingClient when feature flag is on
        self._llm_client: Optional[OpenAILLMClient] = None
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
        logger.debug(f"[OPENAI-ADAPTIVE] Speech timing: speaking={is_speaking}, silence={silence_ms:.0f}ms")

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
                    f"[OPENAI-ADAPTIVE] Triggering suggestion: silence={self._frontend_silence_ms:.0f}ms "
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
        """Initialize OpenAI clients."""
        try:
            api_key = self._api_key or settings.openai_api_key

            if not api_key:
                logger.error("[OPENAI-ADAPTIVE] No OpenAI API key configured")
                return False

            # Initialize STT — Deepgram (streaming) or Whisper (batch)
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
                        logger.info("[OPENAI-ADAPTIVE] Using Deepgram Nova-2 for STT (streaming)")
                    else:
                        logger.warning("[OPENAI-ADAPTIVE] Deepgram failed, falling back to Whisper")
                        self._deepgram_client = None
                except Exception as e:
                    logger.warning(f"[OPENAI-ADAPTIVE] Deepgram init failed, falling back to Whisper: {e}")
                    self._deepgram_client = None

            if not self._deepgram_client:
                self._transcription_client = OpenAITranscriptionClient(api_key)

            self._llm_client = OpenAILLMClient(api_key)

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
                        f"[OPENAI-ADAPTIVE] Turn detection with frontend VAD "
                        f"(silence={settings.speech_silence_threshold_ms}ms)"
                    )
                else:
                    await self._turn_detector.start()
                    logger.info(
                        f"[OPENAI-ADAPTIVE] Turn detection with transcript timing "
                        f"(silence={settings.turn_silence_threshold_ms}ms)"
                    )
            else:
                logger.info("[OPENAI-ADAPTIVE] Turn detection disabled")

            self._connected = True
            self._running = True

            prompt_name = prompt_key or DEFAULT_PROMPT
            logger.info(f"[OPENAI-ADAPTIVE] Connected (prompt: {prompt_name})")
            return True

        except Exception as e:
            logger.error(f"[OPENAI-ADAPTIVE] Connection failed: {e}")
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
            await self._deepgram_client.send_audio(audio_data)
            return

        try:
            # Whisper batch transcription
            transcript = await self._transcription_client.transcribe(audio_data)

            if not transcript or len(transcript.strip()) < 3:
                return

            # Filter out noise transcripts (Whisper hallucinations, single words, etc.)
            if is_noise_transcript(transcript):
                logger.debug(f"[OPENAI-ADAPTIVE] Filtered noise transcript: '{transcript}'")
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
                logger.debug(f"[OPENAI-ADAPTIVE] User speech captured for context: '{merged_transcript[:60]}...'")
                return

            if self._turn_detector:
                self._turn_detector.add_transcript(merged_transcript)

                if settings.use_speech_timing_for_turns:
                    if not self._frontend_is_speaking and self._frontend_silence_ms >= settings.speech_silence_threshold_ms:
                        current_text = self._turn_detector.get_current_text()
                        if current_text and len(current_text.split()) >= settings.turn_min_words:
                            logger.info(
                                f"[OPENAI-ADAPTIVE] Frontend VAD: speech ended, "
                                f"silence={self._frontend_silence_ms:.0f}ms, forcing turn complete"
                            )
                            complete_text = self._turn_detector.force_complete()
                            if complete_text:
                                await self._on_turn_complete(complete_text)
            else:
                await self._on_turn_complete(merged_transcript)

        except Exception as e:
            logger.error(f"[OPENAI-ADAPTIVE] Error processing audio: {e}")

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
        """Called when a complete interviewer turn is detected."""
        logger.info(f"[OPENAI-ADAPTIVE] Turn complete (interviewer): '{complete_text[:100]}...'")

        self._last_transcript = ""
        self._transcript_buffer = []

        try:
            conversation_context = self._build_conversation_context()
            suggestion_data = await self._llm_client.get_suggestion(
                complete_text,
                conversation_context=conversation_context,
            )

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
                })
                logger.info("[OPENAI-ADAPTIVE] Suggestion sent to client")
            else:
                logger.info("[OPENAI-ADAPTIVE] Not a question, no suggestion generated")

        except Exception as e:
            logger.error(f"[OPENAI-ADAPTIVE] Error generating suggestion: {e}")

    async def _on_deepgram_transcript(self, transcript: str, segment_id: str, is_final: bool) -> None:
        """Called by Deepgram for each transcript result (interim or final)."""
        if not transcript:
            return

        is_new_turn = self._deepgram_needs_new_turn and is_final
        if is_new_turn:
            self._deepgram_needs_new_turn = False

        await self._message_queue.put({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": transcript,
            "segmentId": segment_id,
            "isFinal": is_final,
            "isNewTurn": is_new_turn,
            "speaker": self._current_speaker,
        })

        if is_final:
            self._deepgram_turn_buffer += " " + transcript
            self._conversation_history.append({
                "speaker": self._current_speaker,
                "text": transcript,
            })
            if len(self._conversation_history) > 20:
                self._conversation_history = self._conversation_history[-20:]

    async def _on_deepgram_utterance_end(self) -> None:
        """Called by Deepgram when a complete utterance/turn is detected."""
        self._deepgram_needs_new_turn = True

        complete_text = self._deepgram_turn_buffer.strip()
        self._deepgram_turn_buffer = ""

        if not complete_text:
            return

        if self._current_speaker == "user":
            logger.debug("[OPENAI-ADAPTIVE] Deepgram utterance end (user speech, no suggestion)")
            return

        if len(complete_text.split()) < settings.turn_min_words:
            logger.debug(f"[OPENAI-ADAPTIVE] Deepgram utterance too short: '{complete_text}'")
            return

        logger.info(f"[OPENAI-ADAPTIVE] Deepgram utterance complete: '{complete_text[:100]}...'")
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
                logger.info(f"[OPENAI-ADAPTIVE] Processing remaining Deepgram buffer: '{remaining[:50]}...'")
                await self._on_turn_complete(remaining)

        if self._turn_detector:
            remaining = self._turn_detector.force_complete()
            if remaining:
                logger.info(f"[OPENAI-ADAPTIVE] Processing remaining turn: '{remaining[:50]}...'")
                await self._on_turn_complete(remaining)
            if not settings.use_speech_timing_for_turns:
                await self._turn_detector.stop()
            stats = self._turn_detector.get_stats()
            logger.info(f"[OPENAI-ADAPTIVE] Turn detector stats: {stats}")

        if self._deepgram_client:
            await self._deepgram_client.close()
        if self._transcription_client:
            await self._transcription_client.close()
        if self._llm_client:
            await self._llm_client.close()

        logger.info("[OPENAI-ADAPTIVE] Disconnected")
