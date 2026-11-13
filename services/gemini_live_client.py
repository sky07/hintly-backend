"""Gemini Live API client for real-time audio streaming.

This module provides a WebSocket-based client that connects to Gemini's Live API
for instant transcription and suggestion generation with minimal latency.

Key differences from the batch API client:
- Uses WebSocket for bidirectional streaming (not HTTP REST)
- Audio is processed in real-time as it arrives (no buffering delay)
- Responses stream back immediately
"""

import asyncio
import base64
import json
import logging
import struct
from typing import AsyncGenerator, Optional

from google import genai
from google.genai import types

from config import settings

logger = logging.getLogger(__name__)

# System prompt for interview assistance
SYSTEM_INSTRUCTION = """You are a Passive Interview Co-Pilot providing real-time assistance.

## Your Role:
Listen to the audio and:
1. Transcribe what the interviewer is saying
2. When you detect a complete question, provide a helpful answer suggestion

## Response Format:
For each audio segment, respond with a JSON object:
{
    "transcript": "<what was said>",
    "is_question": true/false,
    "suggestion": "<if is_question is true, provide suggestion with: response, key points, follow-up>"
}

## Guidelines:
- Transcribe accurately in real-time
- Only mark as question if it's actually asking something
- Suggestions should be concise but helpful
- Reference the candidate's context when available
"""


def resample_audio(audio_bytes: bytes, from_rate: int = 24000, to_rate: int = 16000) -> bytes:
    """Resample audio from one sample rate to another.

    Simple linear interpolation resampling for 16-bit PCM audio.
    """
    if from_rate == to_rate:
        return audio_bytes

    # Convert bytes to samples (16-bit signed integers)
    num_samples = len(audio_bytes) // 2
    samples = struct.unpack(f'<{num_samples}h', audio_bytes)

    # Calculate new number of samples
    ratio = to_rate / from_rate
    new_num_samples = int(num_samples * ratio)

    # Resample using linear interpolation
    new_samples = []
    for i in range(new_num_samples):
        src_idx = i / ratio
        idx_low = int(src_idx)
        idx_high = min(idx_low + 1, num_samples - 1)
        frac = src_idx - idx_low

        sample = int(samples[idx_low] * (1 - frac) + samples[idx_high] * frac)
        new_samples.append(sample)

    # Convert back to bytes
    return struct.pack(f'<{len(new_samples)}h', *new_samples)


class GeminiLiveClient:
    """Client for Gemini Live API with real-time WebSocket streaming.

    This client provides instant transcription and suggestions by maintaining
    a persistent WebSocket connection to Gemini's Live API.
    """

    def __init__(self):
        logger.info("[GEMINI-LIVE] Initializing GeminiLiveClient")
        self._connected = False
        self._client: Optional[genai.Client] = None
        self._session = None
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False
        self._receive_task: Optional[asyncio.Task] = None
        self._context = {}

    @property
    def is_connected(self) -> bool:
        """Check if connected to Gemini Live API."""
        return self._connected

    async def connect(
        self,
        job_description: str = "",
        resume: str = "",
        work_experience: str = "",
        verbosity: str = "moderate",
        prompt_key: str = None,
        pre_prepared_answers: str = "",
    ) -> bool:
        """Initialize connection to Gemini Live API."""
        try:
            logger.info("[GEMINI-LIVE] Connecting to Gemini Live API...")

            # Store context for suggestions
            self._context = {
                "job_description": job_description,
                "resume": resume,
                "work_experience": work_experience,
                "verbosity": verbosity,
            }

            # Initialize the genai client
            self._client = genai.Client(api_key=settings.gemini_api_key)

            # Build system instruction with context
            system_instruction = SYSTEM_INSTRUCTION
            if job_description:
                system_instruction += f"\n\n## Job Description:\n{job_description[:1000]}"
            if resume:
                system_instruction += f"\n\n## Candidate Resume:\n{resume[:1000]}"
            if work_experience:
                system_instruction += f"\n\n## Work Experience:\n{work_experience[:1000]}"

            from services.prompts import dump_system_prompt
            dump_system_prompt(system_instruction, provider="gemini_live", verbosity=verbosity)
            logger.info(
                f"\n{'='*80}\n[GEMINI-LIVE] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
                f"{system_instruction}\n{'='*80}\n[GEMINI-LIVE] END OF SYSTEM PROMPT\n{'='*80}"
            )

            # Configure the live session
            config = types.LiveConnectConfig(
                response_modalities=["TEXT"],  # We want text responses, not audio
                system_instruction=types.Content(
                    parts=[types.Part(text=system_instruction)]
                ),
            )

            # Try different model names
            model_names = [
                "gemini-2.0-flash-live-001",
                "gemini-2.0-flash-exp",
                "gemini-2.5-flash-preview-native-audio-dialog",
                "gemini-2.0-flash",
            ]

            for model_name in model_names:
                try:
                    logger.info(f"[GEMINI-LIVE] Trying model: {model_name}")

                    # Connect to the live session
                    self._session = await self._client.aio.live.connect(
                        model=model_name,
                        config=config,
                    )

                    logger.info(f"[GEMINI-LIVE] Connected with model: {model_name}")
                    break

                except Exception as e:
                    logger.warning(f"[GEMINI-LIVE] Model {model_name} failed: {e}")
                    continue

            if not self._session:
                raise Exception("No Gemini Live model available")

            self._connected = True
            self._running = True

            # Start background task to receive responses
            self._receive_task = asyncio.create_task(self._receive_responses())

            logger.info("[GEMINI-LIVE] Successfully connected to Gemini Live API")
            return True

        except Exception as e:
            logger.error(f"[GEMINI-LIVE] Failed to connect: {e}")
            import traceback
            logger.error(f"[GEMINI-LIVE] Traceback:\n{traceback.format_exc()}")
            self._connected = False
            return False

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """Send audio data to Gemini Live API in real-time.

        Audio is sent immediately without buffering for instant processing.
        """
        if not self._connected or not self._session:
            return

        try:
            # Resample from 24kHz (frontend) to 16kHz (Gemini Live API requirement)
            resampled_audio = resample_audio(audio_data, from_rate=24000, to_rate=16000)

            # Send audio chunk to Gemini Live
            await self._session.send(
                input=types.LiveClientRealtimeInput(
                    media_chunks=[
                        types.Blob(
                            mime_type="audio/pcm",
                            data=resampled_audio,
                        )
                    ]
                ),
                end_of_turn=False,  # Don't end turn, keep streaming
            )

        except Exception as e:
            logger.error(f"[GEMINI-LIVE] Error sending audio: {e}")

    async def _receive_responses(self) -> None:
        """Background task to receive responses from Gemini Live API."""
        logger.info("[GEMINI-LIVE] Starting response receiver task")

        try:
            while self._running and self._session:
                try:
                    # Receive the next response
                    async for response in self._session.receive():
                        if not self._running:
                            break

                        # Process the response
                        await self._process_response(response)

                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.error(f"[GEMINI-LIVE] Error receiving: {e}")
                    await asyncio.sleep(0.1)

        except asyncio.CancelledError:
            logger.info("[GEMINI-LIVE] Response receiver cancelled")
        except Exception as e:
            logger.error(f"[GEMINI-LIVE] Response receiver error: {e}")

    async def _process_response(self, response) -> None:
        """Process a response from Gemini Live API."""
        try:
            # Check for server content
            if hasattr(response, 'server_content') and response.server_content:
                content = response.server_content

                # Check for model turn (text response)
                if hasattr(content, 'model_turn') and content.model_turn:
                    for part in content.model_turn.parts:
                        if hasattr(part, 'text') and part.text:
                            await self._handle_text_response(part.text)

                # Check for transcript (if available)
                if hasattr(content, 'input_transcription') and content.input_transcription:
                    transcript = content.input_transcription
                    logger.info(f"[GEMINI-LIVE] Transcript: {transcript}")
                    await self._message_queue.put({
                        "type": "conversation.item.input_audio_transcription.completed",
                        "transcript": transcript,
                    })

        except Exception as e:
            logger.error(f"[GEMINI-LIVE] Error processing response: {e}")

    async def _handle_text_response(self, text: str) -> None:
        """Handle text response from Gemini Live API."""
        logger.info(f"[GEMINI-LIVE] Text response: {text[:200]}...")

        try:
            # Try to parse as JSON (our structured format)
            data = json.loads(text)

            # Extract transcript
            if "transcript" in data and data["transcript"]:
                await self._message_queue.put({
                    "type": "conversation.item.input_audio_transcription.completed",
                    "transcript": data["transcript"],
                })

            # Extract suggestion if it's a question
            if data.get("is_question") and data.get("suggestion"):
                suggestion = data["suggestion"]
                if isinstance(suggestion, str):
                    await self._message_queue.put({
                        "type": "response.text.done",
                        "text": suggestion,
                    })
                elif isinstance(suggestion, dict):
                    # Format structured suggestion
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

        except json.JSONDecodeError:
            # Not JSON, treat as plain text response
            # Check if it looks like a transcript or suggestion
            if len(text) > 20:
                # Assume it's a suggestion if it's substantial
                await self._message_queue.put({
                    "type": "response.text.done",
                    "text": text,
                })

    async def receive_messages(self) -> AsyncGenerator[dict, None]:
        """Yield messages from the queue."""
        logger.info("[GEMINI-LIVE] Starting message receiver")

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
            logger.info("[GEMINI-LIVE] Message receiver cancelled")

    async def disconnect(self) -> None:
        """Disconnect from Gemini Live API."""
        logger.info("[GEMINI-LIVE] Disconnecting...")

        self._running = False
        self._connected = False

        # Cancel receive task
        if self._receive_task:
            self._receive_task.cancel()
            try:
                await self._receive_task
            except asyncio.CancelledError:
                pass
            self._receive_task = None

        # Close the session
        if self._session:
            try:
                await self._session.close()
            except Exception as e:
                logger.warning(f"[GEMINI-LIVE] Error closing session: {e}")
            self._session = None

        self._client = None
        logger.info("[GEMINI-LIVE] Disconnected from Gemini Live API")
