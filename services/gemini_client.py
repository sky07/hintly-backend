"""Gemini API client for interview assistance.

This module provides a Gemini-based client that matches the interface of the
OpenAI client, enabling users to switch between providers.

Note: Unlike OpenAI's Realtime API which handles both transcription and generation,
Gemini uses a different approach. This implementation uses Gemini's multimodal
capabilities to transcribe audio and generate suggestions.
"""

import asyncio
import io
import json
import logging
import os
import time
import wave
from typing import Any, AsyncGenerator, Optional

import google.generativeai as genai
from google.generativeai import protos
from google.generativeai.types import GenerationConfig

from config import settings
from services.rate_limiter import (
    RateLimitedExecutor,
    RateLimiterConfig,
    TranscriptCache,
    get_rate_limiter,
    get_transcript_cache,
)
from services.prompts import get_prompt, get_prompt_with_prep, get_response_format, format_suggestion_for_display, DEFAULT_PROMPT, get_max_tokens_for_verbosity

logger = logging.getLogger(__name__)

# Legacy system prompt template - now uses prompts.py module
# Kept for reference but get_prompt() from prompts.py is the source of truth


def pcm16_to_wav(pcm_bytes: bytes, sample_rate: int = 24000, channels: int = 1) -> bytes:
    """Convert raw PCM16 audio to WAV format for Gemini API.

    Args:
        pcm_bytes: Raw PCM16 audio data
        sample_rate: Audio sample rate (default 24000 Hz to match frontend)
        channels: Number of audio channels (default 1 for mono)

    Returns:
        WAV file bytes
    """
    buffer = io.BytesIO()
    with wave.open(buffer, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(2)  # 16-bit = 2 bytes
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(pcm_bytes)
    return buffer.getvalue()


def get_max_tokens(verbosity: str) -> int:
    """Get max response tokens based on verbosity setting."""
    return get_max_tokens_for_verbosity(verbosity)


def build_instructions(
    job_description: str,
    resume: str,
    work_experience: str,
    verbosity: str = "moderate",
    prompt_key: str = None,
    pre_prepared_answers: str = "",
) -> str:
    """Build the system instructions with user context.

    Args:
        job_description: The job being interviewed for
        resume: Candidate's resume
        work_experience: Additional experience details
        verbosity: Response length (concise/moderate/detailed)
        prompt_key: Which prompt style to use (candidate/coach/star)
        pre_prepared_answers: Formatted pre-prepared Q&A to append

    Returns:
        Formatted system prompt string
    """
    return get_prompt_with_prep(
        prompt_key=prompt_key,
        job_description=job_description,
        resume=resume,
        work_experience=work_experience,
        verbosity=verbosity,
        pre_prepared_answers=pre_prepared_answers,
    )


class GeminiClient:
    """Client for Gemini API with interview assistance capabilities.

    This client provides similar functionality to the OpenAI Realtime client
    but uses Gemini's API. It handles:
    - Audio transcription using Gemini's multimodal capabilities
    - Suggestion generation for interview questions
    - Rate limiting for free tier testing

    Key feature: Waits for the interviewer to complete their question (detected
    by a pause in speech) before generating suggestions.
    """

    # Configuration for speech completion detection
    PAUSE_THRESHOLD_SECONDS = 2.0  # Seconds of silence to consider speech complete
    MIN_QUESTION_LENGTH = 10  # Minimum characters for a valid question
    
    # Audio buffer configuration (calculated in __init__)
    # At 24kHz, 16-bit mono: 48000 bytes = 1 second
    BYTES_PER_SECOND = 48000

    def __init__(self, api_key: str = None):
        """Initialize the Gemini client.

        Args:
            api_key: Optional Gemini API key. If not provided, uses server's key from settings.
        """
        logger.info("[GEMINI] Initializing GeminiClient")
        self._api_key = api_key  # User's API key or None
        self._connected = False
        self._model: Optional[genai.GenerativeModel] = None
        self._chat = None
        self._audio_buffer = bytearray()
        self._audio_chunks_received = 0
        self._total_audio_bytes_received = 0
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._verbosity = "moderate"
        self._instructions = ""
        self._prompt_key = DEFAULT_PROMPT

        # Dev mode / rate limiting configuration
        self._dev_mode = settings.dev_mode
        self._audio_buffer_seconds = settings.audio_buffer_seconds
        # Each chunk is ~4096 samples at 24kHz = ~0.17 seconds
        # Calculate chunks needed for desired buffer size
        self._audio_chunk_threshold = int(self._audio_buffer_seconds / 0.17)

        # Rate limiter for API calls
        self._rate_limiter: Optional[RateLimitedExecutor] = None
        self._transcript_cache: Optional[TranscriptCache] = None

        logger.info(f"[GEMINI] GeminiClient initialized (dev_mode={self._dev_mode}, buffer={self._audio_buffer_seconds}s, chunk_threshold={self._audio_chunk_threshold})")

    @property
    def is_connected(self) -> bool:
        """Check if connected to Gemini."""
        return self._connected
    
    def _on_rate_limit_status(self, status: str, data: dict):
        """Handle rate limiter status changes - notify frontend."""
        try:
            # Queue status update for frontend
            asyncio.create_task(self._message_queue.put({
                "type": "rate_limit.update",
                "status": status,
                **data,
            }))
        except Exception as e:
            logger.error(f"[GEMINI] Error sending rate limit status: {e}")

    async def _call_gemini_transcription(self, prompt: str, audio_part) -> Any:
        """Make a rate-limited call to Gemini for transcription.
        
        This is extracted as a separate method so it can be passed to the rate limiter.
        """
        return await asyncio.to_thread(
            self._model.generate_content,
            [prompt, audio_part]
        )

    async def _call_gemini_chat(self, message: str) -> Any:
        """Make a rate-limited call to Gemini chat for suggestions.
        
        This is extracted as a separate method so it can be passed to the rate limiter.
        """
        return await asyncio.to_thread(
            self._chat.send_message,
            message
        )

    async def _handle_transcript(self, transcript: str, audio_bytes: bytes, from_cache: bool = False) -> None:
        """Handle a transcript result (from cache or API).
        
        Updates speech time, accumulates transcript, and sends to message queue.
        """
        if not transcript or transcript == "[SILENCE]" or len(transcript) < 3:
            logger.info(f"[PROCESS] No speech detected (transcript='{transcript}'), skipping")
            return

        logger.info(f"[PROCESS] Speech detected! Transcript length: {len(transcript)}, from_cache={from_cache}")

        # Update last speech time and accumulate transcript
        self._last_speech_time = time.time()
        self._transcript_buffer.append(transcript)
        logger.info(f"[PROCESS] Updated last_speech_time, buffer now has {len(self._transcript_buffer)} segments")

        # Send transcription to message queue for live display
        logger.info(f"[PROCESS] Putting transcript in message queue for live display...")
        await self._message_queue.put({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": transcript,
            "from_cache": from_cache,
        })
        logger.info(f"[PROCESS] DONE - Transcript queued for live display")

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
        """Initialize Gemini client and start session.

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
            # Configure Gemini API - use user's key if provided
            api_key = self._api_key or settings.gemini_api_key
            genai.configure(api_key=api_key)

            # Store prompt key for later use
            self._prompt_key = prompt_key or DEFAULT_PROMPT

            # Build system instructions using prompts module
            self._instructions = build_instructions(
                job_description, resume, work_experience, verbosity, prompt_key, pre_prepared_answers,
            )
            self._verbosity = verbosity

            logger.info(f"[GEMINI] System prompt config: prompt_key={self._prompt_key}, verbosity={verbosity}, "
                        f"company={company_name or 'N/A'}, role={role_type or 'N/A'}, "
                        f"round={round_type or 'N/A'}, prep_answers={len(pre_prepared_answers)} chars")
            logger.info(f"\n{'='*80}\n[GEMINI] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
                        f"{self._instructions}\n{'='*80}\n[GEMINI] END OF SYSTEM PROMPT\n{'='*80}")

            # Initialize the model
            # Try different model names for compatibility (ordered by preference)
            model_names = [
                "gemini-2.5-flash",      # Newest flash model
                "gemini-2.0-flash",      # Stable flash model
                "gemini-2.0-flash-lite", # Lite version (may have different quota)
                "gemini-flash-latest",   # Latest alias
                "gemini-pro-latest",     # Pro fallback
            ]

            self._model = None
            last_error = None

            for model_name in model_names:
                try:
                    logger.info(f"Trying Gemini model: {model_name}")
                    self._model = genai.GenerativeModel(
                        model_name=model_name,
                        generation_config=GenerationConfig(
                            temperature=0.7,
                            max_output_tokens=get_max_tokens(verbosity),
                        ),
                        system_instruction=self._instructions,
                    )
                    # Test the model with a simple request
                    test_response = self._model.generate_content("Say 'ok' if you're ready.")
                    logger.info(f"Successfully connected to Gemini model: {model_name}")
                    break
                except Exception as e:
                    last_error = e
                    logger.warning(f"Model {model_name} not available: {e}")
                    self._model = None
                    continue

            if self._model is None:
                raise Exception(f"No Gemini model available. Last error: {last_error}")

            # Start a chat session
            self._chat = self._model.start_chat(history=[])

            self._connected = True
            self._running = True

            # Initialize rate limiter if in dev mode
            if self._dev_mode:
                config = RateLimiterConfig(
                    requests_per_minute=settings.rate_limit_rpm,
                    burst_capacity=2,
                    queue_max_size=5,
                    request_timeout=60.0,
                )
                self._rate_limiter = RateLimitedExecutor(
                    config,
                    on_status_change=self._on_rate_limit_status,
                )
                await self._rate_limiter.start()
                logger.info(f"[GEMINI] Rate limiter started ({settings.rate_limit_rpm} RPM)")
                
                # Initialize transcript cache
                self._transcript_cache = TranscriptCache(max_size=50, ttl_seconds=300)
                logger.info("[GEMINI] Transcript cache initialized")
                
                # Send dev mode status to frontend
                await self._message_queue.put({
                    "type": "rate_limit.status",
                    "dev_mode": True,
                    "rpm": settings.rate_limit_rpm,
                    "buffer_seconds": self._audio_buffer_seconds,
                })

            # Note: Speech completion monitoring task is no longer needed
            # since we now combine transcription + suggestion in a single API call
            # for instant response without waiting for pause detection

            logger.info("Connected to Gemini API")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Gemini: {e}")
            self._connected = False
            return False

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """Process audio data and generate transcription/suggestions.

        Accumulates audio chunks and processes them periodically using
        Gemini's multimodal capabilities for transcription.
        """
        if not self._connected:
            logger.warning("[AUDIO] send_audio called but not connected!")
            return

        # Log first audio chunk received
        if self._audio_chunks_received == 0 and self._total_audio_bytes_received == 0:
            logger.info(f"[AUDIO] ===== First audio chunk received! Size: {len(audio_data)} bytes =====")

        self._audio_buffer.extend(audio_data)
        self._audio_chunks_received += 1
        self._total_audio_bytes_received += len(audio_data)

        # Log every 10 chunks to show audio is flowing
        if self._audio_chunks_received % 10 == 0:
            logger.info(f"[AUDIO] Buffering... chunks={self._audio_chunks_received}/{self._audio_chunk_threshold}, buffer={len(self._audio_buffer)} bytes, total={self._total_audio_bytes_received} bytes")

        # Process audio when threshold reached
        # Each chunk is 4096 samples @ 24kHz = ~0.17 seconds
        # Default: 60 chunks = ~10 seconds of audio (configurable via audio_buffer_seconds)
        if self._audio_chunks_received >= self._audio_chunk_threshold:
            logger.info(f"[AUDIO] ===== Chunk threshold reached! Processing audio =====")
            self._audio_chunks_received = 0
            audio_bytes = bytes(self._audio_buffer)
            self._audio_buffer.clear()

            logger.info(f"[AUDIO] Audio buffer size: {len(audio_bytes)} bytes ({len(audio_bytes)/48000:.2f}s at 24kHz)")

            # Only process if we have meaningful audio data (> ~1 second)
            if len(audio_bytes) > 48000:  # ~1 second at 24kHz, 16-bit mono
                logger.info(f"[AUDIO] ===== Spawning _process_audio task =====")
                asyncio.create_task(self._process_audio(audio_bytes))
            else:
                logger.warning(f"[AUDIO] Audio buffer too small ({len(audio_bytes)} bytes), skipping processing")

    async def _process_audio(self, audio_bytes: bytes) -> None:
        """Process audio chunk for transcription AND suggestion in a SINGLE API call.

        Converts PCM16 audio to WAV format and sends to Gemini for both
        transcription and suggestion generation simultaneously. This ensures
        both transcript and suggestion appear together without lag.

        Uses rate limiting and caching in dev mode to stay within free tier limits.
        """
        logger.info(f"[PROCESS] ===== _process_audio started with {len(audio_bytes)} bytes =====")

        try:
            # Step 1: Convert PCM16 to WAV format for Gemini
            logger.info(f"[PROCESS] Step 1: Converting PCM16 to WAV...")
            wav_bytes = pcm16_to_wav(audio_bytes, sample_rate=24000, channels=1)
            logger.info(f"[PROCESS] Step 1: DONE - WAV size: {len(wav_bytes)} bytes")

            # Step 2: Create audio part using protos.Part with inline_data
            logger.info(f"[PROCESS] Step 2: Creating Gemini Part with inline_data...")
            audio_part = protos.Part(
                inline_data=protos.Blob(
                    mime_type="audio/wav",
                    data=wav_bytes
                )
            )
            logger.info(f"[PROCESS] Step 2: DONE - Part created with mime_type=audio/wav")

            # Step 3: Send to Gemini for COMBINED transcription + suggestion (single API call)
            logger.info(f"[PROCESS] Step 3: Sending audio to Gemini for transcription + suggestion...")
            logger.info(f"[PROCESS] Step 3: Model = {self._model.model_name if self._model else 'None'}")

            # Build the combined prompt for transcription AND suggestion in ONE call
            combined_prompt = """Listen to this audio and perform the following tasks:

1. Transcribe exactly what is being said
2. Determine if the transcribed text is an interview question
3. If it's a question, provide a helpful suggestion for how to answer it

IMPORTANT: You must respond in EXACTLY this format:

TRANSCRIPT: <exact transcription of the audio, or [SILENCE] if no speech>
IS_QUESTION: <YES or NO>
SUGGESTION: <if IS_QUESTION is YES, provide a structured suggestion with:
**Suggested Response:** <direct answer suggestion based on the candidate's resume and experience>
**Key Points:** <2-3 bullet points to mention>
**If They Ask More:** <follow-up tip>

If IS_QUESTION is NO, write "NONE">

Consider it a question if:
- It's asking about experience, skills, or background
- It's asking for opinions or thoughts
- It's asking about past situations or behaviors
- It ends with a question mark or uses question words (what, why, how, tell me about, describe, etc.)

Do NOT consider it a question if:
- It's just a greeting or small talk
- It's a statement or comment
- It's giving instructions or information
- The audio is silent or contains no meaningful speech"""

            # Execute with rate limiting if in dev mode
            if self._dev_mode and self._rate_limiter:
                logger.info(f"[PROCESS] Step 3: Using rate-limited execution...")
                response = await self._rate_limiter.execute(
                    self._call_gemini_transcription,
                    combined_prompt,
                    audio_part,
                    request_id=f"combined_{time.time():.0f}",
                )
            else:
                response = await asyncio.to_thread(
                    self._model.generate_content,
                    [combined_prompt, audio_part]
                )
            logger.info(f"[PROCESS] Step 3: DONE - Got response from Gemini")

            # Step 4: Parse the combined response
            logger.info(f"[PROCESS] Step 4: Parsing combined response...")
            response_text = response.text.strip()
            logger.info(f"[PROCESS] Step 4: Raw response: {response_text[:300]}")

            # Parse the combined response to extract transcript, is_question, and suggestion
            transcript, is_question, suggestion = self._parse_combined_response(response_text)

            logger.info(f"[PROCESS] Step 4: Extracted - transcript: '{transcript[:100]}...', is_question: {is_question}")

            # Step 5: Check if silence or no meaningful content
            if not transcript or transcript == "[SILENCE]" or len(transcript) < 3:
                logger.info(f"[PROCESS] Step 5: No speech detected (transcript='{transcript}'), skipping")
                return

            logger.info(f"[PROCESS] Step 5: Speech detected! Transcript length: {len(transcript)}")

            # Step 6: Send BOTH transcription and suggestion to message queue TOGETHER
            logger.info(f"[PROCESS] Step 6: Putting transcript in message queue...")
            await self._message_queue.put({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": transcript,
            })
            logger.info(f"[PROCESS] Step 6: Transcript queued")

            # Send suggestion immediately if it's a question
            if is_question and suggestion:
                logger.info(f"[PROCESS] Step 6: Question detected! Putting suggestion in message queue...")
                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": suggestion,
                })
                logger.info(f"[PROCESS] Step 6: Suggestion queued")
            else:
                logger.info(f"[PROCESS] Step 6: Not a question or no suggestion, skipping suggestion")

            logger.info(f"[PROCESS] ===== _process_audio completed =====")

        except Exception as e:
            error_str = str(e)
            logger.error(f"[PROCESS] !!!!! ERROR in _process_audio: {type(e).__name__}: {e}")
            import traceback
            logger.error(f"[PROCESS] Traceback:\n{traceback.format_exc()}")

            if "429" in error_str or "quota" in error_str.lower() or "rate" in error_str.lower():
                logger.warning(f"[PROCESS] Rate limit detected, falling back to simulation")
                await self._fallback_text_analysis()
            else:
                logger.error(f"[PROCESS] Non-rate-limit error, not retrying")

    async def _monitor_speech_completion(self) -> None:
        """Background task that monitors for speech completion.

        When the interviewer stops speaking (detected by a pause), this task
        analyzes the accumulated transcript and generates a suggestion if
        it's a complete question.
        """
        logger.info("[MONITOR] ===== Speech completion monitor started =====")

        try:
            while self._running:
                await asyncio.sleep(0.5)  # Check every 500ms

                # Skip if no speech has been detected yet
                if self._last_speech_time == 0:
                    continue

                # Skip if we're already processing a suggestion
                if self._processing_suggestion:
                    continue

                # Skip if transcript buffer is empty
                if not self._transcript_buffer:
                    continue

                # Check if enough time has passed since last speech
                time_since_speech = time.time() - self._last_speech_time

                if time_since_speech >= self.PAUSE_THRESHOLD_SECONDS:
                    logger.info(f"[MONITOR] Pause detected! {time_since_speech:.1f}s since last speech")

                    # Get accumulated transcript
                    full_transcript = " ".join(self._transcript_buffer)
                    logger.info(f"[MONITOR] Accumulated transcript ({len(self._transcript_buffer)} segments): '{full_transcript[:150]}...'")

                    # Clear the buffer and reset
                    self._transcript_buffer.clear()
                    self._last_speech_time = 0

                    # Check if transcript is long enough
                    if len(full_transcript) < self.MIN_QUESTION_LENGTH:
                        logger.info(f"[MONITOR] Transcript too short ({len(full_transcript)} chars), skipping suggestion")
                        continue

                    # Generate suggestion for the complete question
                    self._processing_suggestion = True
                    try:
                        await self._generate_suggestion_for_complete_question(full_transcript)
                    finally:
                        self._processing_suggestion = False

        except asyncio.CancelledError:
            logger.info("[MONITOR] Speech completion monitor cancelled")
        except Exception as e:
            logger.error(f"[MONITOR] Error in speech completion monitor: {e}")
            import traceback
            logger.error(f"[MONITOR] Traceback:\n{traceback.format_exc()}")

    async def _generate_suggestion_for_complete_question(self, transcript: str) -> None:
        """Generate a suggestion for a complete question.

        This is called when the interviewer has finished speaking (pause detected).
        It analyzes the complete transcript and generates a suggestion if it's a question.
        """
        logger.info(f"[SUGGEST] ===== Generating suggestion for complete question =====")
        logger.info(f"[SUGGEST] Complete transcript: '{transcript[:200]}...'")

        if not self._chat:
            logger.warning(f"[SUGGEST] No chat session available")
            return

        try:
            # Ask Gemini to analyze if this is a question and generate a suggestion
            prompt = f"""The interviewer just finished saying: "{transcript}"

Analyze this statement and determine if it contains an interview question that needs a suggested response.

IMPORTANT: You must respond in EXACTLY this format:

IS_QUESTION: <YES or NO>
SUGGESTION: <if IS_QUESTION is YES, provide a structured suggestion with:
**Suggested Response:** <direct answer suggestion based on the candidate's resume and experience>
**Key Points:** <2-3 bullet points to mention>
**If They Ask More:** <follow-up tip>

If IS_QUESTION is NO, write "NONE" - the interviewer is just making a statement, greeting, or small talk>

Consider it a question if:
- It's asking about experience, skills, or background
- It's asking for opinions or thoughts
- It's asking about past situations or behaviors
- It ends with a question mark or uses question words (what, why, how, tell me about, describe, etc.)

Do NOT consider it a question if:
- It's just a greeting or small talk
- It's a statement or comment
- It's giving instructions or information"""

            # Execute with rate limiting if in dev mode
            if self._dev_mode and self._rate_limiter:
                logger.info(f"[SUGGEST] Using rate-limited execution for suggestion...")
                response = await self._rate_limiter.execute(
                    self._call_gemini_chat,
                    prompt,
                    request_id=f"suggest_{time.time():.0f}",
                )
            else:
                response = await asyncio.to_thread(
                    self._chat.send_message,
                    prompt
                )

            response_text = response.text.strip()
            logger.info(f"[SUGGEST] Response from Gemini:\n{response_text[:500]}...")

            # Parse the response
            is_question = False
            suggestion = ""

            lines = response_text.split('\n')
            suggestion_lines = []
            in_suggestion = False

            for line in lines:
                line_stripped = line.strip()
                if line_stripped.startswith("IS_QUESTION:"):
                    value = line_stripped[len("IS_QUESTION:"):].strip().upper()
                    is_question = value == "YES"
                elif line_stripped.startswith("SUGGESTION:"):
                    in_suggestion = True
                    suggestion_start = line_stripped[len("SUGGESTION:"):].strip()
                    if suggestion_start and suggestion_start.upper() != "NONE":
                        suggestion_lines.append(suggestion_start)
                elif in_suggestion and line_stripped:
                    suggestion_lines.append(line)

            suggestion = '\n'.join(suggestion_lines).strip()
            if suggestion.upper() == "NONE":
                suggestion = ""

            logger.info(f"[SUGGEST] Parsed - is_question: {is_question}, suggestion_length: {len(suggestion)}")

            # Send suggestion if it was a question
            if is_question and suggestion:
                logger.info(f"[SUGGEST] Question detected! Putting suggestion in message queue...")
                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": suggestion,
                })
                logger.info(f"[SUGGEST] DONE - Suggestion queued")
            else:
                logger.info(f"[SUGGEST] Not a question or no suggestion generated")

        except Exception as e:
            error_str = str(e)
            logger.error(f"[SUGGEST] ERROR: {e}")
            if "429" in error_str or "quota" in error_str.lower():
                logger.warning(f"[SUGGEST] Rate limit, sending fallback suggestion")
                await self._send_fallback_suggestion()
            import traceback
            logger.error(f"[SUGGEST] Traceback:\n{traceback.format_exc()}")

    def _parse_combined_response(self, response_text: str) -> tuple:
        """Parse the combined transcription + suggestion response.

        Returns:
            Tuple of (transcript, is_question, suggestion)
        """
        transcript = ""
        is_question = False
        suggestion = ""

        lines = response_text.strip().split('\n')
        current_section = None
        suggestion_lines = []

        for line in lines:
            line_stripped = line.strip()

            if line_stripped.startswith("TRANSCRIPT:"):
                current_section = "transcript"
                transcript = line_stripped[len("TRANSCRIPT:"):].strip()
            elif line_stripped.startswith("IS_QUESTION:"):
                current_section = "is_question"
                value = line_stripped[len("IS_QUESTION:"):].strip().upper()
                is_question = value == "YES"
            elif line_stripped.startswith("SUGGESTION:"):
                current_section = "suggestion"
                suggestion_start = line_stripped[len("SUGGESTION:"):].strip()
                if suggestion_start and suggestion_start != "NONE":
                    suggestion_lines.append(suggestion_start)
            elif current_section == "suggestion" and line_stripped:
                suggestion_lines.append(line)

        suggestion = '\n'.join(suggestion_lines).strip()

        # If suggestion is just "NONE", clear it
        if suggestion.upper() == "NONE":
            suggestion = ""

        logger.info(f"[PARSE] Extracted - transcript: {len(transcript)} chars, is_question: {is_question}, suggestion: {len(suggestion)} chars")

        return transcript, is_question, suggestion

    async def _generate_suggestion_standalone(self, transcript: str) -> None:
        """Generate a suggestion for the given transcript (FALLBACK method).

        Note: This is now a fallback method. The main flow uses _process_audio
        which does transcription + suggestion in a single API call for lower latency.
        """
        logger.info(f"[SUGGEST-FALLBACK] Generating suggestion separately for: '{transcript[:50]}...'")

        if not self._chat:
            logger.warning(f"[SUGGEST-FALLBACK] No chat session available")
            return

        try:
            response = await asyncio.to_thread(
                self._chat.send_message,
                f"The interviewer said: \"{transcript}\"\n\n"
                "If this is an interview question, provide a helpful suggestion for how to answer it. "
                "Include key points and a follow-up tip in the structured format. "
                "If this is NOT a question (e.g., small talk, statements, or general comments), "
                "respond with exactly: NOT_A_QUESTION"
            )

            response_text = response.text.strip()
            if response_text and response_text != "NOT_A_QUESTION":
                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": response_text,
                })
                logger.info(f"[SUGGEST-FALLBACK] Suggestion queued")

        except Exception as e:
            error_str = str(e)
            logger.error(f"[SUGGEST-FALLBACK] ERROR: {e}")
            if "429" in error_str or "quota" in error_str.lower():
                await self._send_fallback_suggestion()

    async def _send_fallback_suggestion(self) -> None:
        """Send a generic fallback suggestion when API is rate limited."""
        await self._message_queue.put({
            "type": "response.text.done",
            "text": (
                "**Suggested Response:**\n"
                "Take a moment to structure your answer using the STAR method "
                "(Situation, Task, Action, Result).\n\n"
                "**Key Points:**\n"
                "- Be specific and use concrete examples\n"
                "- Quantify your achievements when possible\n"
                "- Connect your answer to the role requirements\n\n"
                "**If They Ask More:**\n"
                "Be ready to dive deeper into specific details or provide additional examples."
            ),
        })

    async def inject_screen_context(self, text: str) -> None:
        """Inject pasted text as an interview question and generate a suggestion."""
        logger.info(f"[SCREEN] Injecting screen context text: '{text[:100]}...'")
        await self._generate_suggestion_for_complete_question(text)

    async def inject_screen_capture(self, image_base64: str) -> None:
        """Inject a screenshot — not natively supported by Gemini client.
        The websocket handler will use a standalone Claude Vision call instead."""
        logger.warning("[SCREEN] inject_screen_capture called on GeminiClient — use standalone vision call")

    async def _fallback_text_analysis(self) -> None:
        """Simulate interview questions and generate Gemini-powered suggestions.

        This uses simulated transcription with real Gemini suggestions.
        For production, integrate Google Cloud Speech-to-Text for real transcription.
        """
        import random

        # Sample interviewer questions for simulation
        mock_questions = [
            "Tell me about yourself and your background.",
            "What interests you about this role?",
            "Can you describe a challenging project you worked on?",
            "How do you handle tight deadlines?",
            "What are your greatest strengths?",
            "Where do you see yourself in 5 years?",
            "Why are you leaving your current position?",
            "How do you handle conflicts with team members?",
            "What's your experience with agile methodologies?",
            "Can you walk me through your problem-solving process?",
        ]

        # Randomly decide to simulate a question (30% chance)
        if random.random() < 0.3:
            question = random.choice(mock_questions)

            # Send transcription
            await self._message_queue.put({
                "type": "conversation.item.input_audio_transcription.completed",
                "transcript": question,
            })

            # Generate suggestion using Gemini
            if self._chat:
                try:
                    response = await asyncio.to_thread(
                        self._chat.send_message,
                        f"The interviewer asked: \"{question}\"\n\nProvide a helpful suggestion for how to answer this interview question. Include key points and a follow-up tip."
                    )

                    if response and response.text:
                        await self._message_queue.put({
                            "type": "response.text.done",
                            "text": response.text,
                        })
                        logger.debug(f"Generated Gemini suggestion for: {question[:50]}...")
                except Exception as e:
                    error_str = str(e)
                    if "429" in error_str or "quota" in error_str.lower():
                        logger.warning(f"Gemini rate limit hit, using fallback suggestion")
                    else:
                        logger.error(f"Error generating Gemini suggestion: {e}")
                    # Send a fallback suggestion when rate limited or error occurs
                    await self._message_queue.put({
                        "type": "response.text.done",
                        "text": f"**Suggested Response:**\nTake a moment to structure your answer using the STAR method (Situation, Task, Action, Result).\n\n**Key Points:**\n- Be specific and use concrete examples\n- Quantify your achievements when possible\n- Connect your answer to the role requirements\n\n**If They Ask More:**\nBe ready to dive deeper into specific details or provide additional examples.",
                    })

    async def send_text(self, text: str) -> None:
        """Send text directly to Gemini for suggestion generation.

        This is useful when transcription is done externally.
        """
        if not self._connected or not self._chat:
            return

        try:
            response = await asyncio.to_thread(
                self._chat.send_message,
                f"The interviewer said: \"{text}\"\n\nIf this is a question, provide a suggestion for how to answer it. If it's not a question, respond with 'NOT_A_QUESTION'."
            )

            response_text = response.text
            if response_text.strip() != "NOT_A_QUESTION":
                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": response_text,
                })

        except Exception as e:
            logger.error(f"Error sending text to Gemini: {e}")

    async def receive_messages(self) -> AsyncGenerator[dict, None]:
        """Yield messages from the queue."""
        logger.info(f"[RECEIVE] ===== receive_messages loop started =====")
        message_count = 0
        try:
            while self._running:
                try:
                    message = await asyncio.wait_for(
                        self._message_queue.get(),
                        timeout=0.1
                    )
                    message_count += 1
                    msg_type = message.get("type", "unknown")
                    logger.info(f"[RECEIVE] Message #{message_count} from queue: type={msg_type}")
                    if msg_type == "conversation.item.input_audio_transcription.completed":
                        logger.info(f"[RECEIVE] Transcript: '{message.get('transcript', '')[:100]}...'")
                    elif msg_type == "response.text.done":
                        logger.info(f"[RECEIVE] Suggestion: '{message.get('text', '')[:100]}...'")
                    yield message
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            logger.info(f"[RECEIVE] receive_messages loop cancelled after {message_count} messages")

    async def disconnect(self) -> None:
        """Disconnect from Gemini."""
        self._connected = False
        self._running = False

        # Stop the rate limiter
        if self._rate_limiter:
            await self._rate_limiter.stop()
            # Log final stats
            stats = self._rate_limiter.get_stats()
            logger.info(f"[GEMINI] Rate limiter stats: {stats}")
            self._rate_limiter = None

        # Log cache stats
        if self._transcript_cache:
            stats = self._transcript_cache.get_stats()
            logger.info(f"[GEMINI] Transcript cache stats: {stats}")
            self._transcript_cache = None

        self._model = None
        self._chat = None
        self._audio_buffer.clear()
        logger.info("Disconnected from Gemini API")


class GeminiTextClient:
    """Simplified Gemini client for text-only interactions.

    This client is useful when using a separate transcription service
    and only need Gemini for generating suggestions.
    """

    def __init__(self):
        self._connected = False
        self._model: Optional[genai.GenerativeModel] = None
        self._chat = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._prompt_key = DEFAULT_PROMPT

    @property
    def is_connected(self) -> bool:
        return self._connected

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
        """Initialize Gemini for text-based suggestion generation.

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
            genai.configure(api_key=settings.gemini_api_key)

            self._prompt_key = prompt_key or DEFAULT_PROMPT
            instructions = build_instructions(
                job_description, resume, work_experience, verbosity, prompt_key, pre_prepared_answers,
            )

            logger.info(f"[GEMINI-TEXT] System prompt config: prompt_key={self._prompt_key}, verbosity={verbosity}")
            logger.info(f"\n{'='*80}\n[GEMINI-TEXT] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
                        f"{instructions}\n{'='*80}\n[GEMINI-TEXT] END OF SYSTEM PROMPT\n{'='*80}")

            # Try different model names for compatibility
            model_names = [
                "gemini-2.5-flash",
                "gemini-2.0-flash",
                "gemini-2.0-flash-lite",
                "gemini-flash-latest",
                "gemini-pro-latest",
            ]

            self._model = None
            for model_name in model_names:
                try:
                    self._model = genai.GenerativeModel(
                        model_name=model_name,
                        generation_config=GenerationConfig(
                            temperature=0.7,
                            max_output_tokens=get_max_tokens(verbosity),
                        ),
                        system_instruction=instructions,
                    )
                    # Test the model
                    self._model.generate_content("Say 'ok'")
                    logger.info(f"GeminiTextClient using model: {model_name}")
                    break
                except Exception as e:
                    logger.warning(f"Model {model_name} not available: {e}")
                    self._model = None
                    continue

            if self._model is None:
                raise Exception("No Gemini model available")

            self._chat = self._model.start_chat(history=[])
            self._connected = True
            self._running = True
            logger.info("Connected to Gemini Text API")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to Gemini: {e}")
            return False

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """For text-only client, audio is ignored."""
        pass

    async def process_transcript(self, transcript: str) -> None:
        """Process a transcript and generate suggestion if needed."""
        if not self._connected or not self._chat:
            return

        try:
            response = await asyncio.to_thread(
                self._chat.send_message,
                f"Interviewer: \"{transcript}\"\n\nProvide interview coaching if this is a question."
            )

            if response.text.strip():
                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": response.text,
                })

        except Exception as e:
            logger.error(f"Error processing transcript: {e}")

    async def receive_messages(self) -> AsyncGenerator[dict, None]:
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

    async def disconnect(self) -> None:
        """Disconnect from Gemini."""
        self._connected = False
        self._running = False
        self._model = None
        self._chat = None
        logger.info("Disconnected from Gemini Text API")
