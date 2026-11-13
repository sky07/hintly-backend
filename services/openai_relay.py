"""OpenAI Realtime API client for WebSocket communication."""

import asyncio
import json
import logging
import random
from typing import AsyncGenerator, Optional

import websockets
from websockets.client import WebSocketClientProtocol

from config import settings
from services.prompts import get_prompt, get_prompt_with_prep, DEFAULT_PROMPT, get_max_tokens_for_verbosity

logger = logging.getLogger(__name__)

OPENAI_REALTIME_URL = "wss://api.openai.com/v1/realtime?model=gpt-4o-realtime-preview"

# Legacy prompt templates - now using prompts.py module as source of truth
# These are kept for backwards compatibility but get_prompt() should be used


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
    company_name: str = "",
    role_type: str = "",
    round_type: str = "",
) -> str:
    """Build the system instructions with user context.

    Args:
        job_description: The job being interviewed for
        resume: Candidate's resume
        work_experience: Additional experience details
        verbosity: Response length (concise/moderate/detailed)
        prompt_key: Which prompt style to use (candidate/coach/star)
        pre_prepared_answers: Formatted pre-prepared Q&A to append
        company_name: Target company name
        role_type: Role being interviewed for
        round_type: Interview round type

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
        company_name=company_name,
        role_type=role_type,
        round_type=round_type,
    )


class OpenAIRealtimeClient:
    """Client for OpenAI Realtime API."""

    def __init__(self, api_key: str = None):
        """Initialize the OpenAI Realtime client.

        Args:
            api_key: Optional OpenAI API key. If not provided, uses server's key from settings.
        """
        self._api_key = api_key  # User's API key or None
        self._ws: Optional[WebSocketClientProtocol] = None
        self._connected = False
        self._prompt_key = DEFAULT_PROMPT

    @property
    def is_connected(self) -> bool:
        """Check if connected to OpenAI."""
        return self._connected and self._ws is not None

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
        """Establish connection to OpenAI Realtime API.

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
            api_key = self._api_key or settings.openai_api_key
            headers = {
                "Authorization": f"Bearer {api_key}",
                "OpenAI-Beta": "realtime=v1",
            }

            self._ws = await websockets.connect(
                OPENAI_REALTIME_URL,
                extra_headers=headers,
            )
            self._connected = True
            self._prompt_key = prompt_key or DEFAULT_PROMPT

            # Send session configuration
            await self._send_session_update(
                job_description, resume, work_experience, verbosity, prompt_key, pre_prepared_answers,
                company_name, role_type, round_type,
            )

            logger.info(f"Connected to OpenAI Realtime API (prompt: {self._prompt_key})")
            return True

        except Exception as e:
            logger.error(f"Failed to connect to OpenAI: {e}")
            self._connected = False
            return False

    async def _send_session_update(
        self,
        job_description: str,
        resume: str,
        work_experience: str,
        verbosity: str,
        prompt_key: str = None,
        pre_prepared_answers: str = "",
        company_name: str = "",
        role_type: str = "",
        round_type: str = "",
    ) -> None:
        """Send session.update to configure the session."""
        if not self._ws:
            return

        instructions = build_instructions(
            job_description, resume, work_experience, verbosity, prompt_key, pre_prepared_answers,
            company_name, role_type, round_type,
        )

        logger.info(f"[OPENAI] System prompt config: prompt_key={prompt_key or 'default'}, verbosity={verbosity}, "
                    f"company={company_name or 'N/A'}, role={role_type or 'N/A'}, "
                    f"round={round_type or 'N/A'}, prep_answers={len(pre_prepared_answers)} chars")
        logger.info(f"\n{'='*80}\n[OPENAI] FULL SYSTEM PROMPT BEING SENT TO LLM:\n{'='*80}\n"
                    f"{instructions}\n{'='*80}\n[OPENAI] END OF SYSTEM PROMPT\n{'='*80}")

        session_config = {
            "type": "session.update",
            "session": {
                "modalities": ["text"],
                "instructions": instructions,
                "input_audio_format": "pcm16",
                "input_audio_transcription": {"model": "whisper-1"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 300,
                    "silence_duration_ms": 700,
                },
                "temperature": 0.7,
                "max_response_output_tokens": get_max_tokens(verbosity),
            },
        }

        await self._ws.send(json.dumps(session_config))
        logger.debug("Sent session.update to OpenAI")

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """Send audio data to OpenAI."""
        if not self._ws or not self._connected:
            return

        try:
            # Send audio as input_audio_buffer.append
            import base64

            audio_b64 = base64.b64encode(audio_data).decode("utf-8")
            message = {
                "type": "input_audio_buffer.append",
                "audio": audio_b64,
            }
            await self._ws.send(json.dumps(message))
        except Exception as e:
            logger.error(f"Error sending audio: {e}")

    async def receive_messages(self) -> AsyncGenerator[dict, None]:
        """Receive messages from OpenAI."""
        if not self._ws:
            return

        try:
            async for message in self._ws:
                if isinstance(message, str):
                    yield json.loads(message)
        except websockets.exceptions.ConnectionClosed:
            logger.warning("OpenAI connection closed")
            self._connected = False
        except Exception as e:
            logger.error(f"Error receiving message: {e}")
            self._connected = False

    async def inject_screen_context(self, text: str) -> None:
        """Inject pasted text as a conversation item to OpenAI Realtime API."""
        if not self._ws or not self._connected:
            logger.warning("[SCREEN] Cannot inject screen context — not connected")
            return
        logger.info(f"[SCREEN] Injecting screen context to OpenAI: '{text[:100]}...'")
        # Send as a conversation item (user message) so OpenAI processes it
        message = {
            "type": "conversation.item.create",
            "item": {
                "type": "message",
                "role": "user",
                "content": [
                    {
                        "type": "input_text",
                        "text": f"The interviewer posted this question in the chat: \"{text}\"\n\nPlease provide a suggested response.",
                    }
                ],
            },
        }
        await self._ws.send(json.dumps(message))
        # Trigger a response
        await self._ws.send(json.dumps({"type": "response.create"}))

    async def inject_screen_capture(self, image_base64: str) -> None:
        """Inject a screenshot — not natively supported by OpenAI Realtime.
        The websocket handler will use a standalone Claude Vision call instead."""
        logger.warning("[SCREEN] inject_screen_capture called on OpenAIRealtimeClient — use standalone vision call")

    async def disconnect(self) -> None:
        """Close the connection to OpenAI."""
        if self._ws:
            await self._ws.close()
            self._ws = None
        self._connected = False
        logger.info("Disconnected from OpenAI Realtime API")


class MockOpenAIClient:
    """Mock client for testing without OpenAI API key."""

    # Sample interviewer questions for simulation
    MOCK_QUESTIONS = [
        "Tell me about yourself and your background.",
        "What interests you about this role?",
        "Can you describe a challenging project you worked on?",
        "How do you handle tight deadlines?",
        "What are your greatest strengths?",
        "Where do you see yourself in 5 years?",
        "Why are you leaving your current position?",
        "How do you handle conflicts with team members?",
    ]

    # Mock suggestions based on question patterns
    MOCK_SUGGESTIONS = {
        "yourself": {
            "response": "Start with your current role, then highlight 2-3 key achievements that align with this position. End with why you're excited about this opportunity.",
            "keyPoints": [
                "Lead with your most relevant experience",
                "Quantify achievements where possible",
                "Connect your background to the role",
            ],
            "followUp": "Be ready to elaborate on any specific project or achievement you mention.",
        },
        "interest": {
            "response": "Focus on the company's mission, the role's challenges, and how it aligns with your career goals.",
            "keyPoints": [
                "Research the company beforehand",
                "Mention specific aspects of the role",
                "Show genuine enthusiasm",
            ],
            "followUp": "Have specific examples of company news or products you admire.",
        },
        "challenging": {
            "response": "Use the STAR method: Situation, Task, Action, Result. Pick a project that shows problem-solving skills.",
            "keyPoints": [
                "Choose a relevant technical challenge",
                "Explain your specific contribution",
                "Quantify the positive outcome",
            ],
            "followUp": "Be prepared to dive into technical details if asked.",
        },
        "deadline": {
            "response": "Describe your prioritization process and give a specific example of delivering under pressure.",
            "keyPoints": [
                "Show you can break down large tasks",
                "Mention communication with stakeholders",
                "Highlight successful delivery",
            ],
            "followUp": "Discuss how you handle scope changes or blockers.",
        },
        "strength": {
            "response": "Choose strengths relevant to the role and back them up with specific examples.",
            "keyPoints": [
                "Pick 2-3 genuine strengths",
                "Provide concrete examples",
                "Tie them to job requirements",
            ],
            "followUp": "Be ready to discuss how you've developed these strengths.",
        },
        "default": {
            "response": "Take a moment to think, then provide a structured response with specific examples from your experience.",
            "keyPoints": [
                "Listen carefully to the full question",
                "Use specific examples",
                "Keep your answer focused and relevant",
            ],
            "followUp": "Ask for clarification if the question is unclear.",
        },
    }

    def __init__(self):
        self._connected = False
        self._audio_buffer = bytearray()
        self._audio_chunks_received = 0
        self._message_queue: asyncio.Queue = asyncio.Queue()
        self._running = False

    @property
    def is_connected(self) -> bool:
        """Check if mock client is connected."""
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
        """Simulate connection to OpenAI."""
        self._connected = True
        self._running = True
        self._verbosity = verbosity
        self._prompt_key = prompt_key or DEFAULT_PROMPT
        logger.info(f"[MOCK] Connected to Mock OpenAI client (prompt: {self._prompt_key})")
        return True

    async def send_audio(self, audio_data: bytes, speaker: str = "interviewer") -> None:
        """Receive audio data and simulate transcription after enough chunks."""
        if not self._connected:
            return

        self._audio_buffer.extend(audio_data)
        self._audio_chunks_received += 1

        # Simulate transcription every ~50 chunks (roughly 2-3 seconds of audio)
        if self._audio_chunks_received >= 50:
            self._audio_chunks_received = 0
            self._audio_buffer.clear()

            # Randomly decide to "detect" a question
            if random.random() < 0.3:  # 30% chance to simulate a question
                question = random.choice(self.MOCK_QUESTIONS)
                await self._simulate_transcription(question)
                await self._simulate_suggestion(question)

    async def _simulate_transcription(self, text: str) -> None:
        """Queue a transcription message."""
        await self._message_queue.put({
            "type": "conversation.item.input_audio_transcription.completed",
            "transcript": text,
        })

    async def _simulate_suggestion(self, question: str) -> None:
        """Queue a suggestion based on the question."""
        # Find matching suggestion
        suggestion = self.MOCK_SUGGESTIONS["default"]
        question_lower = question.lower()

        for keyword, sugg in self.MOCK_SUGGESTIONS.items():
            if keyword in question_lower:
                suggestion = sugg
                break

        # Simulate some delay for "processing"
        await asyncio.sleep(0.5)

        # Build response text
        response_text = f"""**Suggested Response:**
{suggestion['response']}

**Key Points:**
{chr(10).join('• ' + point for point in suggestion['keyPoints'])}

**If They Ask More:**
{suggestion['followUp']}"""

        await self._message_queue.put({
            "type": "response.text.done",
            "text": response_text,
        })

    async def receive_messages(self) -> AsyncGenerator[dict, None]:
        """Yield messages from the mock queue."""
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
            logger.debug("[MOCK] Receive task cancelled")

    async def inject_screen_context(self, text: str) -> None:
        """Inject pasted text and simulate a suggestion for it."""
        logger.info(f"[MOCK-SCREEN] Injecting screen context: '{text[:100]}...'")
        await self._simulate_suggestion(text)

    async def inject_screen_capture(self, image_base64: str) -> None:
        """Mock handler for screenshot — returns a generic suggestion."""
        logger.info(f"[MOCK-SCREEN] Screenshot received ({len(image_base64)} chars) — returning mock suggestion")
        await self._message_queue.put({
            "type": "response.text.done",
            "text": (
                "**Suggested Response:**\n"
                "I can see the question on screen. Here's a structured approach:\n\n"
                "**Key Points:**\n"
                "- Break down the problem into smaller components\n"
                "- Consider edge cases and scalability\n"
                "- Communicate your thought process clearly\n\n"
                "**If They Ask More:**\n"
                "Be prepared to discuss trade-offs and alternative approaches."
            ),
        })

    async def disconnect(self) -> None:
        """Disconnect the mock client."""
        self._connected = False
        self._running = False
        self._audio_buffer.clear()
        logger.info("[MOCK] Disconnected from Mock OpenAI client")


def get_openai_client():
    """Factory function to get the appropriate OpenAI client.

    Deprecated: Use get_llm_client() instead for provider selection.
    """
    if settings.use_mock_openai:
        logger.info("Using Mock OpenAI client (USE_MOCK_OPENAI=true)")
        return MockOpenAIClient()
    else:
        logger.info("Using real OpenAI Realtime API client")
        return OpenAIRealtimeClient()


def get_llm_client(provider: str = None, api_key: str = None, whisper_api_key: str = None):
    """Factory function to get the appropriate LLM client based on provider.

    Args:
        provider: The LLM provider to use:
            - 'adaptive': Groq Whisper + Llama (fastest, recommended)
            - 'openai-adaptive': OpenAI Whisper + GPT-4o (standard REST)
            - 'anthropic': Claude + Whisper (Groq/OpenAI for STT)
            - 'gemini-live': Gemini Live API (real-time WebSocket)
            - 'gemini': Standard Gemini (batch mode)
            - 'openai': OpenAI Realtime API (legacy WebSocket)
            - 'mock': Demo mode (no API key needed)
        api_key: Optional API key to use instead of server's default
        whisper_api_key: Optional separate Whisper API key (for Anthropic provider)

    Returns:
        An LLM client instance.
    """
    # Determine which provider to use
    if provider is None:
        provider = settings.effective_provider

    provider = provider.lower()

    if provider == "mock":
        logger.info("Using Mock client")
        return MockOpenAIClient()

    elif provider == "adaptive":
        # Adaptive: Groq Whisper + Llama (ultra-fast, dev/internal use)
        try:
            from services.groq_client import GroqAdaptiveClient
            logger.info("Using Adaptive client (Groq Whisper + Llama)")
            return GroqAdaptiveClient(api_key=api_key)
        except ImportError as e:
            logger.error(f"Failed to import GroqAdaptiveClient: {e}")
            logger.warning("Falling back to Mock client")
            return MockOpenAIClient()

    elif provider == "openai-adaptive":
        # OpenAI Adaptive: Whisper + GPT-4o (standard REST APIs)
        try:
            from services.openai_adaptive_client import OpenAIAdaptiveClient
            logger.info("Using OpenAI Adaptive client (Whisper + GPT-4o)")
            return OpenAIAdaptiveClient(api_key=api_key)
        except ImportError as e:
            logger.error(f"Failed to import OpenAIAdaptiveClient: {e}")
            logger.warning("Falling back to OpenAI Realtime")
            provider = "openai"

    elif provider == "anthropic":
        # Anthropic: Claude + Whisper (Groq or OpenAI for STT)
        try:
            from services.anthropic_adaptive_client import AnthropicAdaptiveClient
            logger.info("Using Anthropic Adaptive client (Claude + Whisper)")
            return AnthropicAdaptiveClient(api_key=api_key, whisper_api_key=whisper_api_key)
        except ImportError as e:
            logger.error(f"Failed to import AnthropicAdaptiveClient: {e}")
            logger.warning("Falling back to Mock client")
            return MockOpenAIClient()

    elif provider == "gemini-live":
        # Gemini Live API - real-time WebSocket streaming
        try:
            from services.gemini_live_client import GeminiLiveClient
            logger.info("Using Gemini Live API client (real-time streaming)")
            return GeminiLiveClient(api_key=api_key)
        except ImportError as e:
            logger.error(f"Failed to import GeminiLiveClient: {e}")
            logger.warning("Falling back to standard Gemini client")
            provider = "gemini"

    if provider == "gemini":
        # Standard Gemini API - batch processing
        try:
            from services.gemini_client import GeminiClient
            logger.info("Using Gemini API client (batch mode)")
            return GeminiClient(api_key=api_key)
        except ImportError as e:
            logger.error(f"Failed to import GeminiClient: {e}")
            logger.warning("Falling back to Mock client")
            return MockOpenAIClient()

    elif provider == "openai":
        logger.info("Using OpenAI Realtime API client")
        return OpenAIRealtimeClient(api_key=api_key)

    else:
        logger.warning(f"Unknown provider '{provider}', falling back to Mock client")
        return MockOpenAIClient()
