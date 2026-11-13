"""Deepgram Streaming STT Client — Continuous Streaming Mode.

Uses Deepgram Nova-2 via WebSocket for real-time speech-to-text with
continuous audio streaming and built-in speech detection.

Architecture:
  Audio is streamed continuously from the AudioWorklet (~200ms packets).
  Deepgram handles all speech detection internally:
  - endpointing (400ms): Detects pauses within speech → speech_final
  - utterance_end (1200ms): Detects end of complete turn → UtteranceEnd
  - interim_results: Word-by-word live transcription

  The adaptive client (Groq/OpenAI/Anthropic) receives callbacks:
  - on_transcript(text, segment_id, is_final): For UI display and accumulation
  - on_utterance_end(): Triggers LLM suggestion generation
"""

import asyncio
import json
import logging
import time
from typing import Awaitable, Callable, Optional

import websockets

logger = logging.getLogger(__name__)

DEEPGRAM_WS_URL = "wss://api.deepgram.com/v1/listen"

# Type aliases for callbacks
TranscriptCallback = Callable[[str, str, bool], Awaitable[None]]  # (transcript, segment_id, is_final)
UtteranceEndCallback = Callable[[], Awaitable[None]]


class DeepgramStreamingClient:
    """Continuous streaming speech-to-text using Deepgram Nova-2.

    Audio is streamed continuously. Deepgram handles speech detection,
    endpointing, and utterance boundary detection internally.

    Usage:
        client = DeepgramStreamingClient(api_key)
        await client.connect(on_transcript=..., on_utterance_end=...)
        await client.send_audio(pcm16_bytes)  # Call frequently with ~200ms packets
        await client.close()
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self._ws: Optional[websockets.WebSocketClientProtocol] = None
        self._receive_task: Optional[asyncio.Task] = None
        self._keepalive_task: Optional[asyncio.Task] = None
        self._connected = False

        # Callbacks
        self._on_transcript: Optional[TranscriptCallback] = None
        self._on_utterance_end: Optional[UtteranceEndCallback] = None

        # Segment tracking — each speech_final increments the counter
        self._segment_counter = 0

        # Stats
        self._bytes_sent = 0
        self._transcripts_received = 0

    async def connect(
        self,
        on_transcript: TranscriptCallback,
        on_utterance_end: UtteranceEndCallback,
    ) -> bool:
        """Open a streaming WebSocket connection to Deepgram.

        Args:
            on_transcript: Called with (transcript, segment_id, is_final) for each result
            on_utterance_end: Called when a complete utterance/turn is detected

        Returns:
            True if connected successfully
        """
        self._on_transcript = on_transcript
        self._on_utterance_end = on_utterance_end

        try:
            params = "&".join([
                "model=nova-2",
                "encoding=linear16",
                "sample_rate=16000",
                "channels=1",
                "punctuate=true",
                "smart_format=true",
                "language=en",
                # Endpointing: detect pauses within speech (400ms silence)
                "endpointing=400",
                # UtteranceEnd: detect complete turn (1200ms after last word)
                "utterance_end_ms=1200",
                # Interim results for live word-by-word display
                "interim_results=true",
            ])

            url = f"{DEEPGRAM_WS_URL}?{params}"

            self._ws = await websockets.connect(
                url,
                additional_headers={"Authorization": f"Token {self.api_key}"},
                ping_interval=None,  # We handle keepalive ourselves
            )

            self._connected = True
            self._segment_counter = 0

            # Start background tasks
            self._receive_task = asyncio.create_task(self._receive_loop())
            self._keepalive_task = asyncio.create_task(self._keepalive_loop())

            logger.info("[DEEPGRAM] Connected to streaming API (Nova-2, continuous mode)")
            return True

        except Exception as e:
            logger.error(f"[DEEPGRAM] Connection failed: {e}")
            return False

    async def send_audio(self, audio_bytes: bytes) -> None:
        """Forward audio data to Deepgram.

        Call this frequently with small packets (~200ms).
        Non-blocking — Deepgram processes audio and returns results via callbacks.
        """
        if not self._ws or not self._connected:
            return

        try:
            await self._ws.send(audio_bytes)
            self._bytes_sent += len(audio_bytes)
        except Exception as e:
            logger.error(f"[DEEPGRAM] Send error: {e}")

    async def _receive_loop(self):
        """Listen for Deepgram results and dispatch to callbacks."""
        try:
            async for msg in self._ws:
                try:
                    data = json.loads(msg)
                    msg_type = data.get("type")

                    if msg_type == "Results":
                        await self._handle_results(data)

                    elif msg_type == "UtteranceEnd":
                        logger.info("[DEEPGRAM] UtteranceEnd — complete turn detected")
                        if self._on_utterance_end:
                            await self._on_utterance_end()

                    elif msg_type == "Metadata":
                        request_id = data.get("request_id", "")
                        logger.info(f"[DEEPGRAM] Session metadata: request_id={request_id}")

                    elif msg_type == "Error":
                        error_msg = data.get("message", "Unknown error")
                        logger.error(f"[DEEPGRAM] API error: {error_msg}")

                except json.JSONDecodeError:
                    logger.warning("[DEEPGRAM] Non-JSON message received")

        except websockets.exceptions.ConnectionClosed as e:
            logger.warning(f"[DEEPGRAM] Connection closed: {e}")
            self._connected = False
        except asyncio.CancelledError:
            pass
        except Exception as e:
            logger.error(f"[DEEPGRAM] Receive loop error: {e}")
            self._connected = False

    async def _handle_results(self, data: dict) -> None:
        """Process a Results message from Deepgram."""
        is_final = data.get("is_final", False)
        speech_final = data.get("speech_final", False)
        channel = data.get("channel", {})
        alternatives = channel.get("alternatives", [])

        if not alternatives:
            return

        transcript = alternatives[0].get("transcript", "").strip()
        confidence = alternatives[0].get("confidence", 0)

        if not transcript:
            return

        segment_id = f"dg-{self._segment_counter}"

        if not is_final:
            # Interim result — live word-by-word display
            logger.debug(f"[DEEPGRAM] Interim (seg={segment_id}): '{transcript[:60]}'")
            if self._on_transcript:
                await self._on_transcript(transcript, segment_id, False)
        else:
            # Final result for this segment
            logger.info(
                f"[DEEPGRAM] Final (seg={segment_id}, speech_final={speech_final}, "
                f"confidence={confidence:.2f}): '{transcript[:80]}'"
            )
            self._transcripts_received += 1
            if self._on_transcript:
                await self._on_transcript(transcript, segment_id, True)

            # Increment segment counter for next speech segment
            self._segment_counter += 1

    async def _keepalive_loop(self):
        """Send periodic KeepAlive to prevent connection timeout.

        Deepgram closes connections after ~12s of inactivity.
        In continuous streaming mode, audio data acts as keepalive during speech,
        but we need explicit KeepAlive during extended silence.
        """
        try:
            while self._connected:
                await asyncio.sleep(8)
                if self._ws and self._connected:
                    try:
                        await self._ws.send(json.dumps({"type": "KeepAlive"}))
                    except Exception:
                        break
        except asyncio.CancelledError:
            pass

    async def close(self):
        """Close the Deepgram connection gracefully."""
        self._connected = False

        # Cancel background tasks
        for task in [self._keepalive_task, self._receive_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass

        # Close WebSocket
        if self._ws:
            try:
                await self._ws.send(json.dumps({"type": "CloseStream"}))
                await self._ws.close()
            except Exception:
                pass

        self._ws = None
        logger.info(
            f"[DEEPGRAM] Closed (bytes_sent={self._bytes_sent}, "
            f"transcripts={self._transcripts_received})"
        )
