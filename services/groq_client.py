"""Groq API client for ultra-fast transcription and LLM suggestions.

Groq provides:
- Whisper transcription: ~50-100ms for 3-second chunks
- Llama 3.3 70B: 10x faster than other providers

This module is used by the Adaptive provider for maximum speed.
"""

import asyncio
import base64
import io
import json
import logging
import time
import wave
from typing import Optional

import httpx

from config import settings
from services.prompts import get_prompt, get_prompt_with_prep, get_response_format, format_suggestion_for_display, uses_json_response, is_not_a_question, DEFAULT_PROMPT, get_max_tokens_for_verbosity
from services.turn_detector import TurnDetector, TurnDetectorConfig

logger = logging.getLogger(__name__)

# Common noise transcriptions from Whisper that should be filtered out.
# These are typically produced when background noise or silence is transcribed.
NOISE_TRANSCRIPTS = {
    "you", "the", "uh", "um", "oh", "ah", "hmm", "huh",
    "thank you.", "thanks.", "bye.", "okay.", "yeah.",
    "thank you for watching.", "thanks for watching.",
    "please subscribe.", "subscribe.",
    "like and subscribe.", "see you next time.",
    "thank you for listening.", "thanks for listening.",
    # Whisper hallucinations on silence/noise
    "...", ".", ",", "!", "?",
    "you.", "the.", "i.", "a.", "it.",
    "so.", "and.", "but.", "or.",
}

# Minimum word count for a valid transcript (filters out single-word noise)
MIN_TRANSCRIPT_WORDS = 2


def is_noise_transcript(transcript: str) -> bool:
    """Check if a transcript is likely noise rather than real speech.

    Returns True if the transcript should be filtered out.
    """
    cleaned = transcript.strip().lower()
    if not cleaned:
        return True
    if cleaned in NOISE_TRANSCRIPTS:
        return True
    # Filter single-word transcripts (usually noise)
    if len(cleaned.split()) < MIN_TRANSCRIPT_WORDS:
        return True
    return False


# Groq API endpoints
GROQ_API_BASE = "https://api.groq.com/openai/v1"
GROQ_TRANSCRIPTION_URL = f"{GROQ_API_BASE}/audio/transcriptions"
GROQ_CHAT_URL = f"{GROQ_API_BASE}/chat/completions"

# Models
WHISPER_MODEL = "whisper-large-v3-turbo"  # Fast and accurate
LLAMA_MODEL = "llama-3.3-70b-versatile"  # 10x faster than alternatives


def pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 16000, channels: int = 1) -> bytes:
    """Convert raw PCM16 audio to WAV format for Groq API."""
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit = 2 bytes
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


class GroqTranscriptionClient:
    """Client for Groq Whisper transcription API.

    Ultra-fast transcription: ~50-100ms for 3-second audio chunks.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._request_count = 0
        self._last_request_time = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={"Authorization": f"Bearer {self.api_key}"}
            )
        return self._client

    async def transcribe(self, audio_bytes: bytes, sample_rate: int = 16000) -> Optional[str]:
        """Transcribe audio using Groq Whisper API.

        Args:
            audio_bytes: Raw PCM16 audio data
            sample_rate: Audio sample rate (default 16000 for Whisper)

        Returns:
            Transcribed text or None if failed
        """
        try:
            start_time = time.time()

            # Convert to WAV format
            wav_bytes = pcm16_to_wav(audio_bytes, sample_rate=sample_rate)

            client = await self._get_client()

            # Send to Groq
            files = {
                "file": ("audio.wav", wav_bytes, "audio/wav"),
                "model": (None, WHISPER_MODEL),
                "response_format": (None, "text"),
                "language": (None, "en"),
            }

            response = await client.post(GROQ_TRANSCRIPTION_URL, files=files)

            elapsed = (time.time() - start_time) * 1000
            self._request_count += 1
            self._last_request_time = time.time()

            if response.status_code == 200:
                transcript = response.text.strip()
                logger.info(f"[GROQ-WHISPER] Transcribed in {elapsed:.0f}ms: '{transcript[:50]}...'")
                return transcript
            else:
                logger.error(f"[GROQ-WHISPER] Error {response.status_code}: {response.text}")
                return None

        except Exception as e:
            logger.error(f"[GROQ-WHISPER] Transcription error: {e}")
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class GroqLLMClient:
    """Client for Groq Llama suggestions.

    Ultra-fast LLM: Llama 3.3 70B is 10x faster than alternatives.
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._system_prompt = ""
        self._prompt_key = DEFAULT_PROMPT
        self._request_count = 0

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                timeout=30.0,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json"
                }
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
        """Set the system prompt with interview context.

        Args:
            job_description: The job being interviewed for
            resume: Candidate's resume
            work_experience: Additional experience details
            verbosity: Response length (concise/moderate/detailed)
            prompt_key: Which prompt to use (candidate/coach/star)
            pre_prepared_answers: Formatted pre-prepared Q&A to append to prompt
            company_name: Target company name
            role_type: Role being interviewed for
            round_type: Interview round type
        """
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
        logger.info(f"[GROQ-LLM] System prompt set: {len(self._system_prompt)} chars, "
                     f"company={company_name or 'N/A'}, role={role_type or 'N/A'}, "
                     f"round={round_type or 'N/A'}, prep_answers={len(pre_prepared_answers)} chars")
        logger.info(f"\n{'='*80}\n[GROQ-LLM] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
                     f"{self._system_prompt}\n{'='*80}\n[GROQ-LLM] END OF SYSTEM PROMPT\n{'='*80}")

    async def get_suggestion(self, transcript: str, conversation_context: str = "") -> Optional[dict]:
        """Get a suggestion for the given transcript.

        Args:
            transcript: The interviewer's statement/question
            conversation_context: Recent conversation history (both speakers) for follow-up context

        Returns:
            Dict with is_question, suggestion, and formatted_text, or None if failed
        """
        try:
            start_time = time.time()

            client = await self._get_client()

            # Check if this prompt expects JSON or plain text
            is_json = uses_json_response(self._prompt_key)

            # Build context prefix if conversation history is available
            context_prefix = ""
            if conversation_context:
                context_prefix = (
                    f"Here is the recent conversation for context (use this to understand "
                    f"what has already been discussed and what the candidate has said):\n"
                    f"---\n{conversation_context}\n---\n\n"
                )

            max_tokens = get_max_tokens_for_verbosity(getattr(self, '_verbosity', 'moderate'))

            if is_json:
                # JSON mode: structured response with is_question detection
                user_content = f'{context_prefix}The interviewer said: "{transcript}"\n\nAnalyze and respond in JSON format.'
                payload = {
                    "model": LLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                    "response_format": {"type": "json_object"},
                }
            else:
                # Plain text mode (personalized): direct spoken answer
                user_content = f'{context_prefix}The interviewer said: "{transcript}"'
                payload = {
                    "model": LLAMA_MODEL,
                    "messages": [
                        {"role": "system", "content": self._system_prompt},
                        {"role": "user", "content": user_content},
                    ],
                    "temperature": 0.7,
                    "max_tokens": max_tokens,
                }

            logger.info(f"[GROQ-LLM] Request to LLM - model={LLAMA_MODEL}, json_mode={is_json}, user_content='{user_content[:100]}...'")

            response = await client.post(GROQ_CHAT_URL, json=payload)

            elapsed = (time.time() - start_time) * 1000
            self._request_count += 1

            if response.status_code == 200:
                data = response.json()
                content = data["choices"][0]["message"]["content"]
                logger.info(f"[GROQ-LLAMA] Suggestion in {elapsed:.0f}ms (json={is_json})")

                if is_json:
                    # Parse structured JSON response
                    result = json.loads(content)

                    # Format the suggestion for display using the appropriate format
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
                        logger.info("[GROQ-LLAMA] Not a question (sentinel), skipping suggestion")
                        return {"is_question": False, "suggestion": None}

                    logger.info(f"[GROQ-LLAMA] Plain text response: '{plain_text[:80]}...'")

                    return {
                        "is_question": True,
                        "suggestion": {"response": plain_text},
                        "formatted_text": plain_text,
                    }
            else:
                logger.error(f"[GROQ-LLAMA] Error {response.status_code}: {response.text}")
                return None

        except Exception as e:
            logger.error(f"[GROQ-LLAMA] Suggestion error: {e}")
            return None

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None


class GroqAdaptiveClient:
    """Combined Groq client for the Adaptive provider.

    Uses Groq for both transcription (Whisper) and suggestions (Llama).
    Implements Semantic Turn Detection to avoid jittery suggestions.

    Key features:
    - Instant transcription (~50-100ms) shown to user immediately
    - Accumulates transcripts until speaker finishes (silence + complete sentence)
    - Only generates suggestions when a complete "turn" is detected
    - Avoids false triggers from connective words and incomplete sentences
    """

    def __init__(self, api_key: str = None):
        """Initialize the Groq Adaptive client.

        Args:
            api_key: Optional Groq API key. If not provided, uses server's key from settings.
        """
        self._api_key = api_key  # User's API key or None
        self._connected = False
        self._transcription_client: Optional[GroqTranscriptionClient] = None
        self._deepgram_client = None  # DeepgramStreamingClient when feature flag is on
        self._llm_client: Optional[GroqLLMClient] = None
        self._gemini_fallback = None  # Lazy loaded
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

        # Semantic turn detection
        self._turn_detector: Optional[TurnDetector] = None

        # Transcript merging for overlapping chunks
        self._last_transcript = ""
        self._transcript_buffer = []

        # Frontend VAD speech timing (Option B1)
        self._frontend_is_speaking = True  # Assume speaking until told otherwise
        self._frontend_silence_ms = 0.0

        # Speaker tracking for Issue #3: only suggest on interviewer speech
        self._current_speaker = "interviewer"  # Default assumption

        # Conversation history for LLM context (both speakers)
        self._conversation_history: list = []  # [{speaker, text}]

        # Deepgram streaming state
        self._deepgram_turn_buffer = ""  # Accumulates text between UtteranceEnd events
        self._deepgram_needs_new_turn = True  # First transcript is always a new turn

        # Per-segment speaker voting for Deepgram mode
        # Only accumulates votes while Deepgram is actively producing a segment
        self._segment_speaker_votes: list = []
        self._deepgram_segment_active = False

    @property
    def is_connected(self) -> bool:
        return self._connected

    @property
    def stt_mode(self) -> str:
        """Return the STT mode: 'streaming' for Deepgram, 'chunked' for Whisper."""
        return "streaming" if self._deepgram_client else "chunked"

    def update_speech_timing(self, is_speaking: bool, silence_ms: float) -> None:
        """Update speech timing from frontend VAD.

        This is used for more accurate turn detection - we wait for the
        frontend to signal that speech has ended before generating suggestions.
        """
        self._frontend_is_speaking = is_speaking
        self._frontend_silence_ms = silence_ms
        logger.debug(f"[GROQ-ADAPTIVE] Speech timing: speaking={is_speaking}, silence={silence_ms:.0f}ms")

    async def check_and_trigger_suggestion(self) -> None:
        """Check if conditions are met to trigger suggestion generation.

        This is called periodically when speech timing updates arrive,
        even if no audio is being sent (during silence).
        Note: We do NOT check _current_speaker here because it can be stale/wrong.
        The last audio chunk before silence may be misclassified as 'user' due to
        low-signal mic noise exceeding the now-silent system audio. The TurnDetector
        only accumulates interviewer text (user speech is filtered in send_audio),
        so if it has content, that content is from the interviewer.
        """
        if not self._connected or not self._turn_detector:
            return

        # Skip when Deepgram handles turn detection via UtteranceEnd
        if self._deepgram_client:
            return

        # Only check if using frontend VAD timing
        if not settings.use_speech_timing_for_turns:
            return

        # Check if frontend VAD says speech has ended with sufficient silence
        if not self._frontend_is_speaking and self._frontend_silence_ms >= settings.speech_silence_threshold_ms:
            # Check if we have accumulated content
            current_text = self._turn_detector.get_current_text()
            if current_text and len(current_text.split()) >= settings.turn_min_words:
                logger.info(f"[GROQ-ADAPTIVE] Triggering suggestion: silence={self._frontend_silence_ms:.0f}ms >= {settings.speech_silence_threshold_ms}ms")
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
        """Initialize Groq clients.

        Args:
            job_description: The job being interviewed for
            resume: Candidate's resume
            work_experience: Additional experience details
            verbosity: Response length (concise/moderate/detailed)
            prompt_key: Which prompt style to use (candidate/coach/star)
            pre_prepared_answers: Formatted pre-prepared Q&A to append to prompt
            company_name: Target company name
            role_type: Role being interviewed for
            round_type: Interview round type
        """
        try:
            # Use user's API key if provided, otherwise fall back to server's key
            groq_api_key = self._api_key or settings.groq_api_key

            if not groq_api_key:
                logger.error("[GROQ-ADAPTIVE] No GROQ_API_KEY configured")
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
                        logger.info("[GROQ-ADAPTIVE] Using Deepgram Nova-2 for STT (streaming)")
                    else:
                        logger.warning("[GROQ-ADAPTIVE] Deepgram connection failed, falling back to Whisper")
                        self._deepgram_client = None
                except Exception as e:
                    logger.warning(f"[GROQ-ADAPTIVE] Deepgram init failed, falling back to Whisper: {e}")
                    self._deepgram_client = None

            # Whisper fallback (or primary when Deepgram is disabled)
            if not self._deepgram_client:
                self._transcription_client = GroqTranscriptionClient(groq_api_key)

            self._llm_client = GroqLLMClient(groq_api_key)

            # Set context for suggestions with prompt selection
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

            # Initialize semantic turn detector (if enabled)
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

                # Option B1: Use frontend VAD for turn detection
                # DON'T start the internal monitoring loop - we'll manually trigger based on frontend VAD
                if settings.use_speech_timing_for_turns:
                    logger.info(f"[GROQ-ADAPTIVE] Turn detection with frontend VAD (silence={settings.speech_silence_threshold_ms}ms)")
                    # Don't call start() - we'll manually trigger turn completion
                else:
                    # Option A: Use turn_detector's internal monitoring (transcript timing)
                    await self._turn_detector.start()
                    logger.info(f"[GROQ-ADAPTIVE] Turn detection with transcript timing (silence={settings.turn_silence_threshold_ms}ms)")
            else:
                logger.info("[GROQ-ADAPTIVE] Turn detection disabled - suggestions on every chunk")

            self._connected = True
            self._running = True

            prompt_name = prompt_key or DEFAULT_PROMPT
            logger.info(f"[GROQ-ADAPTIVE] Connected with semantic turn detection (prompt: {prompt_name})")
            return True

        except Exception as e:
            logger.error(f"[GROQ-ADAPTIVE] Connection failed: {e}")
            return False

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """Process audio chunk with semantic turn detection and speaker awareness.

        Flow (Whisper/chunked mode):
        1. Transcribe immediately (~50-100ms) - fast feedback
        2. Send transcript to frontend for live display
        3. Track speaker and add to conversation history
        4. For interviewer speech: add to TurnDetector for suggestion generation
        5. For user speech: add to context only (no suggestion trigger)

        Flow (Deepgram/streaming mode):
        1. Forward audio bytes to Deepgram (non-blocking)
        2. Deepgram callbacks handle transcript display and turn detection
        """
        if not self._connected:
            return

        self._current_speaker = speaker

        # Deepgram streaming: just forward audio, callbacks handle the rest
        if self._deepgram_client:
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
            if is_noise_transcript(transcript):
                logger.debug(f"[GROQ-ADAPTIVE] Filtered noise transcript: '{transcript}'")
                return

            # Merge with previous transcript (handle overlapping chunks)
            merged_transcript = self._merge_transcript(transcript)

            if not merged_transcript:
                return

            # Send transcript to frontend immediately (live display)
            await self._message_queue.put({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": merged_transcript,
            })

            # Add to conversation history for LLM context (both speakers)
            self._conversation_history.append({
                "speaker": speaker,
                "text": merged_transcript,
            })
            # Keep last 20 entries to avoid unbounded growth
            if len(self._conversation_history) > 20:
                self._conversation_history = self._conversation_history[-20:]

            # Only accumulate interviewer speech for suggestion generation
            if speaker == "user":
                logger.debug(f"[GROQ-ADAPTIVE] User speech captured for context (not triggering suggestion): '{merged_transcript[:60]}...'")
                return

            # Use semantic turn detection if enabled (interviewer speech only)
            if self._turn_detector:
                # Add to turn detector buffer
                self._turn_detector.add_transcript(merged_transcript)

                # Check if frontend VAD says speech has ended with sufficient silence
                # This is more accurate than transcript-timing based detection
                if settings.use_speech_timing_for_turns:
                    if not self._frontend_is_speaking and self._frontend_silence_ms >= settings.speech_silence_threshold_ms:
                        # Frontend says speech ended - check if we have content and force complete
                        current_text = self._turn_detector.get_current_text()
                        if current_text and len(current_text.split()) >= settings.turn_min_words:
                            logger.info(f"[GROQ-ADAPTIVE] Frontend VAD: speech ended, silence={self._frontend_silence_ms:.0f}ms, forcing turn complete")
                            complete_text = self._turn_detector.force_complete()
                            if complete_text:
                                await self._on_turn_complete(complete_text)
                # If not using frontend VAD, turn_detector's internal monitoring handles it
            else:
                # No turn detection - generate suggestion immediately (legacy behavior)
                await self._on_turn_complete(merged_transcript)

        except Exception as e:
            logger.error(f"[GROQ-ADAPTIVE] Error processing audio: {e}")

    def _build_conversation_context(self) -> str:
        """Build a conversation context string from recent history.

        This gives the LLM context about what the user has already answered,
        so it can provide relevant suggestions for follow-up questions.
        """
        if not self._conversation_history:
            return ""

        lines = []
        for entry in self._conversation_history:
            speaker_label = "Candidate" if entry["speaker"] == "user" else "Interviewer"
            lines.append(f"{speaker_label}: {entry['text']}")

        return "\n".join(lines)

    async def _on_turn_complete(self, complete_text: str) -> None:
        """Called when TurnDetector identifies a complete interviewer turn.

        Generates AI suggestion with conversation context so the LLM knows
        what the user has already said.
        """
        logger.info(f"[GROQ-ADAPTIVE] Turn complete (interviewer): '{complete_text[:100]}...'")

        # Reset transcript merging state for next turn
        self._last_transcript = ""
        self._transcript_buffer = []

        try:
            # Build conversation context for the LLM
            conversation_context = self._build_conversation_context()

            # Get suggestion from Groq Llama (with conversation context)
            suggestion_data = await self._llm_client.get_suggestion(
                complete_text,
                conversation_context=conversation_context,
            )

            if suggestion_data and suggestion_data.get("is_question") and suggestion_data.get("suggestion"):
                # Use the pre-formatted text if available
                formatted = suggestion_data.get("formatted_text")

                if not formatted:
                    # Fallback formatting
                    suggestion = suggestion_data["suggestion"]
                    formatted = f"**Suggested Response:**\n{suggestion.get('response', '')}\n\n"

                    if suggestion.get('key_points'):
                        formatted += "**Key Points:**\n"
                        for point in suggestion['key_points']:
                            formatted += f"- {point}\n"
                        formatted += "\n"

                    if suggestion.get('follow_up'):
                        formatted += f"**If They Ask More:**\n{suggestion['follow_up']}"

                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": formatted,
                })
                logger.info("[GROQ-ADAPTIVE] Suggestion sent to client")
            else:
                logger.info("[GROQ-ADAPTIVE] Not a question, no suggestion generated")

        except Exception as e:
            logger.error(f"[GROQ-ADAPTIVE] Error generating suggestion: {e}")

    def _get_segment_speaker(self) -> str:
        """Determine dominant speaker from accumulated votes since last final transcript.

        Uses majority voting over all audio chunks received during this Deepgram segment.
        This is more reliable than the instantaneous _current_speaker because it averages
        over the entire segment rather than using a single point-in-time sample.
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

        # Send to frontend for display (update-or-add based on segment_id)
        await self._message_queue.put({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": transcript,
            "segmentId": segment_id,
            "isFinal": is_final,
            "isNewTurn": is_new_turn,
            "speaker": segment_speaker,
        })

        # Only accumulate final transcripts for LLM context
        if is_final:
            self._deepgram_turn_buffer += " " + transcript

            # Add to conversation history
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
        self._deepgram_needs_new_turn = True  # Mark next transcript as new turn

        complete_text = self._deepgram_turn_buffer.strip()
        self._deepgram_turn_buffer = ""

        if not complete_text:
            return

        # Note: We do NOT filter by speaker here. The energy-based speaker detection
        # in the AudioWorklet is unreliable (mic picks up ambient noise, causing
        # interviewer speech to be misclassified as "user" when system audio drops).
        # Instead, we let _on_turn_complete → LLM decide if the text is a question.

        if len(complete_text.split()) < settings.turn_min_words:
            logger.debug(f"[GROQ-ADAPTIVE] Deepgram utterance too short: '{complete_text}'")
            return

        logger.info(f"[GROQ-ADAPTIVE] Deepgram utterance complete: '{complete_text[:100]}...'")
        await self._on_turn_complete(complete_text)

    def _merge_transcript(self, new_transcript: str) -> str:
        """Merge overlapping transcripts to avoid word duplication.

        When using overlapping chunks (0-4s, 3-7s, etc.), the same words
        may appear at the end of one chunk and start of the next.
        This function detects and removes such duplicates.
        """
        if not self._last_transcript:
            self._last_transcript = new_transcript
            return new_transcript

        # Look for overlap between end of last transcript and start of new
        last_words = self._last_transcript.split()[-5:]  # Last 5 words
        new_words = new_transcript.split()

        # Find overlap
        overlap_start = -1
        for i in range(min(len(last_words), len(new_words))):
            # Check if last N words of previous match first N words of new
            if last_words[-(i+1):] == new_words[:i+1]:
                overlap_start = i + 1

        if overlap_start > 0:
            # Remove overlapping words from new transcript
            merged = " ".join(new_words[overlap_start:])
        else:
            merged = new_transcript

        self._last_transcript = new_transcript
        return merged.strip() if merged.strip() else None

    async def _try_gemini_fallback(self, audio_data: bytes):
        """Fall back to Gemini if Groq fails."""
        try:
            if self._gemini_fallback is None:
                from services.gemini_client import GeminiClient
                self._gemini_fallback = GeminiClient()
                # Connect would need context, skip for now

            logger.warning("[GROQ-ADAPTIVE] Falling back to Gemini")
            # Could forward audio to Gemini here

        except Exception as e:
            logger.error(f"[GROQ-ADAPTIVE] Gemini fallback failed: {e}")

    async def receive_messages(self):
        """Yield messages from the queue."""
        try:
            while self._running:
                try:
                    message = await asyncio.wait_for(
                        self._message_queue.get(),
                        timeout=0.1
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
                logger.info(f"[GROQ-ADAPTIVE] Processing remaining Deepgram buffer: '{remaining[:50]}...'")
                await self._on_turn_complete(remaining)

        # Stop turn detector and process any remaining turn
        if self._turn_detector:
            remaining = self._turn_detector.force_complete()
            if remaining:
                logger.info(f"[GROQ-ADAPTIVE] Processing remaining turn on disconnect: '{remaining[:50]}...'")
                await self._on_turn_complete(remaining)
            # Only stop if it was started (Option A mode)
            if not settings.use_speech_timing_for_turns:
                await self._turn_detector.stop()
            stats = self._turn_detector.get_stats()
            logger.info(f"[GROQ-ADAPTIVE] Turn detector stats: {stats}")

        if self._deepgram_client:
            await self._deepgram_client.close()
        if self._transcription_client:
            await self._transcription_client.close()
        if self._llm_client:
            await self._llm_client.close()

        logger.info("[GROQ-ADAPTIVE] Disconnected")
