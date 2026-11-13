"""WebSocket endpoint for relaying between browser and OpenAI."""

import asyncio
import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, List, Optional, Tuple

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.messages import (
    ConnectionStatusMessage,
    ErrorMessage,
    PongMessage,
    SessionReadyMessage,
    SuggestionMessage,
    TranscriptDeltaMessage,
)
from config import settings
from services.openai_relay import get_openai_client, get_llm_client
from services.session_manager import session_manager
from services.auth_service import auth_service
from services.conversation_history import ConversationHistory
from services.intent_classifier import parse_intent_from_response, INTENT_NOT_A_QUESTION
from services.prompts import is_not_a_question
from services.encryption import decrypt_api_key
from services.vision_service import get_suggestion_from_screenshot
from db.database import AsyncSessionLocal
from db.models import User, InterviewSession, UserAPIKey, LLMProvider, CreditType
from services import credit_service

logger = logging.getLogger(__name__)

router = APIRouter()


def parse_suggestion_response(text: str) -> Tuple[str, List[str], str]:
    """Parse the structured response from LLM into separate fields.

    Supports multiple formats:

    Markdown heading format (current - all modes):
    ### 🎤 Say First / ### 💬 Suggested Response / ### 📍 Situation / etc.
    Passed through as-is for MarkdownContent rendering on the frontend.

    Legacy Candidate Mode:
    **Say First:** <opening line>
    **Your Story:** <battle story>
    **Drop These:** <metrics>
    **Pro Tip:** <tactical advice>

    Legacy Coach Mode:
    **Suggested Response:**
    <response text>
    **Key Points:**
    - point 1
    - point 2
    **If They Ask More:**
    <follow-up text>

    Returns:
        Tuple of (response, key_points, follow_up)
    """
    response = ""
    key_points: List[str] = []
    follow_up = ""

    # Normalize line endings
    text = text.replace('\r\n', '\n')

    # New markdown heading format (### style) — pass through as-is.
    # MarkdownContent on the frontend renders headings, bullets, and structure inline.
    if '### ' in text:
        return text.strip(), [], ""

    # Check if this is Candidate Mode format (has "Say First" or "Your Story")
    is_candidate_mode = "**Say First:**" in text or "**Your Story:**" in text

    if is_candidate_mode:
        # Parse Candidate Mode format
        parts = []

        # Extract "Say First" section
        say_first_match = re.search(
            r'\*\*Say First[:\*]*\*?\*?\s*(.*?)(?=\n\*\*|\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if say_first_match:
            opening = say_first_match.group(1).strip()
            if opening:
                parts.append(f"**Start with:** {opening}")

        # Extract "Your Story" section
        story_match = re.search(
            r'\*\*Your Story[:\*]*\*?\*?\s*(.*?)(?=\n\*\*|\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if story_match:
            story = story_match.group(1).strip()
            if story:
                parts.append(f"\n**Then say:** {story}")

        # Extract "Drop These" section (metrics) -> becomes key_points
        metrics_match = re.search(
            r'\*\*Drop These[:\*]*\*?\*?\s*(.*?)(?=\n\*\*|\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if metrics_match:
            metrics = metrics_match.group(1).strip()
            # Split by | or newlines
            if '|' in metrics:
                key_points = [m.strip() for m in metrics.split('|') if m.strip()]
            else:
                key_points = [m.strip() for m in metrics.split('\n') if m.strip()]

        # Extract "Pro Tip" section -> becomes follow_up
        tip_match = re.search(
            r'\*\*Pro Tip[:\*]*\*?\*?\s*(.*?)(?=\n\*\*|\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if tip_match:
            follow_up = tip_match.group(1).strip()

        response = '\n'.join(parts) if parts else text.strip()

    else:
        # Parse Coach Mode format (original)

        # Try to extract "Suggested Response" section
        response_match = re.search(
            r'\*\*Suggested Response[:\*]*\*?\*?\s*\n(.*?)(?=\n\*\*Key Points|\n\*\*If They Ask|\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if response_match:
            response = response_match.group(1).strip()

        # Try to extract "Key Points" section
        key_points_match = re.search(
            r'\*\*Key Points[:\*]*\*?\*?\s*\n(.*?)(?=\n\*\*If They Ask|\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if key_points_match:
            points_text = key_points_match.group(1).strip()
            # Extract bullet points (lines starting with -, *, •, or numbers)
            for line in points_text.split('\n'):
                line = line.strip()
                # Remove leading bullet markers
                cleaned = re.sub(r'^[-*•]\s*|^\d+[.)]\s*', '', line).strip()
                if cleaned:
                    key_points.append(cleaned)

        # Try to extract "If They Ask More" section
        follow_up_match = re.search(
            r'\*\*If They Ask More[:\*]*\*?\*?\s*\n(.*?)(?=\Z)',
            text,
            re.DOTALL | re.IGNORECASE
        )
        if follow_up_match:
            follow_up = follow_up_match.group(1).strip()

        # If no structured format found, use the entire text as response
        if not response and not key_points and not follow_up:
            response = text.strip()

    return response, key_points, follow_up


# Silence threshold for detecting new turns (in seconds)
# Option A (legacy): Based on transcript arrival time
# If more than this much time passes between transcripts, it's a new turn
# Must be greater than audio_buffer_seconds (3s) to avoid false triggers
NEW_TURN_SILENCE_THRESHOLD = 5.0


class ConnectionState:
    """State for a single WebSocket connection."""

    def __init__(self, websocket: WebSocket):
        self.websocket = websocket
        self.session_id: Optional[str] = None
        self.openai_client: Optional[Any] = None  # OpenAIRealtimeClient or MockOpenAIClient
        self.receive_task: Optional[asyncio.Task] = None
        self.current_speaker: str = "interviewer"  # Track current speaker for transcripts
        self.last_transcript_time: float = 0.0  # Track last transcript time for new turn detection (Option A)

        # Option B1: Speech timing from frontend VAD
        self.speech_end_timestamp: Optional[float] = None  # When speech last ended (from frontend)
        self.silence_duration_ms: float = 0.0  # Current silence duration (from frontend)
        self.silence_before_speech_ms: float = 0.0  # Captured silence duration before speech resumed
        self.was_speaking: bool = True  # Track previous speaking state to detect transitions

        # User authentication
        self.user_id: Optional[str] = None  # Authenticated user ID
        self.user_api_key: Optional[str] = None  # User's decrypted API key for the provider

        # Database session tracking
        self.db_session_id: Optional[str] = None  # ID of InterviewSession in database
        self.session_start_time: Optional[datetime] = None
        self.provider_used: Optional[str] = None

        # Accumulated transcript and suggestions for saving to DB
        self.transcript_entries: List[dict] = []
        self.suggestion_entries: List[dict] = []
        self.context: dict = {}  # Job description, resume, etc.

        # Conversation intelligence (feature-flagged)
        self.conversation_history: Optional[ConversationHistory] = None
        self.last_interviewer_question: str = ""  # Track for intent/suggestion routing

        # Credit system
        self.credit_type: Optional[str] = None
        self.credit_deduction_task: Optional[asyncio.Task] = None
        self.total_seconds_charged: int = 0
        self.credits_exhausted: bool = False
        self.grace_period_started: Optional[float] = None

        # Mute control
        self.suggestions_muted: bool = False


async def authenticate_websocket(token: Optional[str]) -> Optional[str]:
    """Authenticate WebSocket connection using JWT token.

    Args:
        token: JWT token from query params

    Returns:
        User ID if valid, None otherwise
    """
    if not token:
        return None

    user_id = auth_service.decode_token(token)
    if not user_id:
        return None

    # Verify user exists
    async with AsyncSessionLocal() as db:
        user = await auth_service.get_user_by_id(db, user_id)
        if not user:
            return None

    return user_id


async def get_user_api_key(user_id: str, provider: str) -> Optional[str]:
    """Get user's decrypted API key for a provider.

    Args:
        user_id: The user's ID
        provider: LLM provider name (groq, openai, gemini)

    Returns:
        Decrypted API key or None if not found
    """
    try:
        # Map provider names to enum
        provider_map = {
            'adaptive': LLMProvider.GROQ,
            'groq': LLMProvider.GROQ,
            'openai': LLMProvider.OPENAI,
            'openai-adaptive': LLMProvider.OPENAI,
            'gemini': LLMProvider.GEMINI,
            'anthropic': LLMProvider.ANTHROPIC,
        }

        llm_provider = provider_map.get(provider.lower())
        if not llm_provider:
            return None

        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(UserAPIKey)
                .where(UserAPIKey.user_id == user_id)
                .where(UserAPIKey.provider == llm_provider)
            )
            key_record = result.scalar_one_or_none()

            if key_record and settings.encryption_key:
                return decrypt_api_key(key_record.encrypted_key)

    except Exception as e:
        logger.error(f"[WS] Error getting user API key: {e}")

    return None


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket, token: Optional[str] = Query(None)):
    """Main WebSocket endpoint for interview sessions.

    Args:
        websocket: The WebSocket connection
        token: Optional JWT token for authentication
    """
    logger.info("[WS] ===== New WebSocket connection =====")
    await websocket.accept()
    logger.info("[WS] WebSocket accepted")
    state = ConnectionState(websocket)
    audio_message_count = 0

    # Authenticate if token provided
    if token:
        user_id = await authenticate_websocket(token)
        if user_id:
            state.user_id = user_id
            logger.info(f"[WS] Authenticated user: {user_id}")
        else:
            logger.warning("[WS] Invalid authentication token")
    else:
        logger.info("[WS] No authentication token provided (anonymous session)")

    try:
        while True:
            # Receive message from client
            message = await websocket.receive()

            if message["type"] == "websocket.receive":
                if "bytes" in message:
                    # Binary audio data
                    audio_message_count += 1
                    if audio_message_count == 1:
                        logger.info(f"[WS] ===== First audio message received! Size: {len(message['bytes'])} bytes =====")
                    elif audio_message_count % 50 == 0:
                        logger.info(f"[WS] Audio messages received: {audio_message_count}")
                    await handle_audio_data(state, message["bytes"])
                elif "text" in message:
                    # JSON message
                    data = json.loads(message["text"])
                    msg_type = data.get("type", "unknown")
                    logger.info(f"[WS] JSON message received: type={msg_type}")
                    await handle_json_message(state, data)

    except WebSocketDisconnect:
        logger.info(f"[WS] Client disconnected: {state.session_id} (total audio messages: {audio_message_count})")
    except RuntimeError as e:
        if "disconnect message has been received" in str(e):
            logger.info(f"[WS] Client disconnected during receive: {state.session_id} (total audio messages: {audio_message_count})")
        else:
            logger.error(f"[WS] !!!!! WebSocket runtime error: {e}")
    except Exception as e:
        logger.error(f"[WS] !!!!! WebSocket error: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"[WS] Traceback:\n{traceback.format_exc()}")
        try:
            error_msg = ErrorMessage(
                code="CONN_002",
                message="Connection error occurred",
                recoverable=False,
            )
            await websocket.send_text(error_msg.model_dump_json())
        except Exception:
            pass
    finally:
        logger.info(f"[WS] Cleaning up connection...")
        await cleanup_connection(state)
        logger.info(f"[WS] Connection cleanup complete")


async def handle_json_message(state: ConnectionState, data: dict) -> None:
    """Handle incoming JSON messages from client."""
    msg_type = data.get("type")

    if msg_type == "session.start":
        await handle_session_start(state, data)
    elif msg_type == "session.end":
        await handle_session_end(state)
    elif msg_type == "verbosity.change":
        await handle_verbosity_change(state, data)
    elif msg_type == "ping":
        await handle_ping(state, data)
    elif msg_type == "speaker.update":
        await handle_speaker_update(state, data)
    elif msg_type == "speech.timing":
        await handle_speech_timing(state, data)
    elif msg_type == "suggestions.toggle":
        enabled = data.get("enabled", True)
        state.suggestions_muted = not enabled
        logger.info(f"[WS] Suggestions {'ENABLED' if enabled else 'MUTED'}")
    elif msg_type == "screen.context":
        await handle_screen_context(state, data)
    else:
        logger.warning(f"Unknown message type: {msg_type}")


async def handle_screen_context(state: ConnectionState, data: dict) -> None:
    """Handle a screenshot/text capture from the desktop CAP button.

    Synchronous: makes one Claude Vision call and emits a single `suggestion`
    message. If we later need streaming for UX, refactor vision_service to
    yield chunks and switch this to suggestion.delta + suggestion.
    """
    source = data.get("source", "unknown")
    image = data.get("image")
    text = data.get("text")

    if not image:
        # Text-paste path is not wired through vision; ignore for now so we
        # don't silently drop without a log.
        logger.warning(f"[SCREEN] screen.context received with source={source} but no image; text-only path not yet wired")
        return

    logger.info(f"[SCREEN] Vision request: source={source}, image={len(image)} b64 chars")

    # Build context from session state so the model knows the role/JD
    ctx = state.context or {}
    context_parts = []
    if ctx.get("companyName"):
        context_parts.append(f"Company: {ctx['companyName']}")
    if ctx.get("roleType"):
        context_parts.append(f"Role: {ctx['roleType']}")
    if ctx.get("roundType"):
        context_parts.append(f"Round: {ctx['roundType']}")
    if ctx.get("jobDescription"):
        context_parts.append(f"Job description: {ctx['jobDescription'][:1000]}")
    if ctx.get("resume"):
        context_parts.append(f"Candidate resume: {ctx['resume'][:1500]}")
    context_prompt = "\n".join(context_parts)

    # Prefer the user's Anthropic key if they have one, else server fallback
    api_key = ""
    if state.user_id:
        user_key = await get_user_api_key(state.user_id, "anthropic")
        if user_key:
            api_key = user_key

    suggestion_text = await get_suggestion_from_screenshot(
        image_base64=image,
        context_prompt=context_prompt,
        api_key=api_key,
    )

    if not suggestion_text:
        logger.error("[SCREEN] Vision call returned no suggestion")
        err = ErrorMessage(
            code="VISION_001",
            message="Could not get a suggestion from the screenshot. Please try again.",
            recoverable=True,
        )
        await state.websocket.send_text(err.model_dump_json())
        return

    response, key_points, follow_up = parse_suggestion_response(suggestion_text)
    suggestion_id = str(uuid.uuid4())

    suggestion_msg = SuggestionMessage(
        id=suggestion_id,
        response=response,
        keyPoints=key_points,
        followUp=follow_up,
        intent="screen_capture",
        question="(from screenshot)",
    )
    await state.websocket.send_text(suggestion_msg.model_dump_json())
    logger.info(f"[SCREEN] Suggestion sent (id={suggestion_id}, {len(response)} chars)")

    state.suggestion_entries.append({
        "id": suggestion_id,
        "response": response,
        "keyPoints": key_points,
        "followUp": follow_up,
        "intent": "screen_capture",
        "question": "(from screenshot)",
        "timestamp": time.time(),
    })


async def credit_deduction_loop(state: ConnectionState) -> None:
    """Background task that deducts credits every N seconds during a session."""
    interval = settings.credit_deduction_interval_seconds
    grace_period = settings.credit_grace_period_seconds

    logger.info(f"[CREDITS] Deduction loop started (interval={interval}s, grace={grace_period}s)")

    try:
        while True:
            await asyncio.sleep(interval)

            if not state.user_id or not state.credit_type:
                continue

            async with AsyncSessionLocal() as db:
                success, remaining = await credit_service.deduct_credits(
                    db=db,
                    user_id=state.user_id,
                    credit_type=state.credit_type,
                    seconds=interval,
                    session_id=state.db_session_id,
                )
                await db.commit()

                state.total_seconds_charged += interval

                # Send credits.update to client
                update_msg = json.dumps({
                    "type": "credits.update",
                    "remaining_seconds": remaining,
                    "seconds_used": state.total_seconds_charged,
                })
                await state.websocket.send_text(update_msg)

                # Warning at 5 minutes remaining
                if 0 < remaining <= 300 and remaining > 300 - interval:
                    warning_msg = json.dumps({
                        "type": "credits.warning",
                        "remaining_seconds": remaining,
                        "message": f"Low credits: {remaining // 60} minutes remaining",
                    })
                    await state.websocket.send_text(warning_msg)
                    logger.info(f"[CREDITS] Low credit warning: {remaining}s remaining")

                # Warning at 1 minute remaining
                if 0 < remaining <= 60 and remaining > 60 - interval:
                    warning_msg = json.dumps({
                        "type": "credits.warning",
                        "remaining_seconds": remaining,
                        "message": "Less than 1 minute of credits remaining!",
                    })
                    await state.websocket.send_text(warning_msg)
                    logger.warning(f"[CREDITS] Critical credit warning: {remaining}s remaining")

                # Credits exhausted — start grace period
                if remaining <= 0 and not state.credits_exhausted:
                    state.credits_exhausted = True
                    state.grace_period_started = time.time()
                    exhausted_msg = json.dumps({
                        "type": "credits.exhausted",
                        "grace_period_seconds": grace_period,
                        "message": f"Credits exhausted. Session will end in {grace_period} seconds.",
                    })
                    await state.websocket.send_text(exhausted_msg)
                    logger.warning(f"[CREDITS] Credits exhausted for user {state.user_id}, grace period started")

                # Grace period expired — force end session
                if state.grace_period_started:
                    elapsed_grace = time.time() - state.grace_period_started
                    if elapsed_grace >= grace_period:
                        force_end_msg = json.dumps({
                            "type": "credits.force_end",
                            "message": "Session ended: credits exhausted and grace period expired.",
                        })
                        await state.websocket.send_text(force_end_msg)
                        logger.warning(f"[CREDITS] Force-ending session for user {state.user_id}")
                        # Trigger session end
                        await handle_session_end(state)
                        return

    except asyncio.CancelledError:
        logger.info(f"[CREDITS] Deduction loop cancelled (charged {state.total_seconds_charged}s total)")
    except Exception as e:
        logger.error(f"[CREDITS] Deduction loop error: {e}")


async def handle_session_start(state: ConnectionState, data: dict) -> None:
    """Start a new interview session."""
    logger.info(f"[SESSION] ===== Starting new session =====")

    context = data.get("context", {})
    verbosity = data.get("verbosity", "moderate")
    provider = data.get("provider")  # Can be 'openai', 'gemini', 'adaptive', or 'mock'
    prompt_key = data.get("promptKey")  # Can be 'candidate', 'coach', or 'star'
    prepared_answers = data.get("preparedAnswers", "")  # Pre-prepared Q&A prompt injection

    logger.info(f"[SESSION] Provider requested: {provider}")
    logger.info(f"[SESSION] Prompt style: {prompt_key or 'default (candidate)'}")
    logger.info(f"[SESSION] Verbosity: {verbosity}")
    company_name = context.get("companyName", "")
    role_type = context.get("roleType", "")
    round_type = context.get("roundType", "")

    logger.info(f"[SESSION] Context - Company: {company_name or '(not set)'}")
    logger.info(f"[SESSION] Context - Role: {role_type or '(not set)'}")
    logger.info(f"[SESSION] Context - Round: {round_type or '(not set)'}")
    logger.info(f"[SESSION] Context - Job desc: {len(context.get('jobDescription', ''))} chars")
    logger.info(f"[SESSION] Context - Resume: {len(context.get('resume', ''))} chars")
    logger.info(f"[SESSION] Context - Work exp: {len(context.get('workExperience', ''))} chars")
    logger.info(f"[SESSION] Pre-prepared answers: {len(prepared_answers)} chars")
    if prepared_answers:
        logger.info(f"[SESSION] Pre-prepared answers content (first 500 chars):\n{prepared_answers[:500]}")
    logger.info(f"[SESSION] User ID: {state.user_id or 'anonymous'}")

    # Store context for later database saving
    state.context = context
    state.provider_used = provider
    state.session_start_time = datetime.utcnow()

    # Determine credit type based on whether user has BYO API key
    # If user provides their own key → BYO_KEY pricing, otherwise → PLATFORM_AI
    if state.user_id and provider and provider.lower() != 'mock':
        has_own_key = await get_user_api_key(state.user_id, provider) is not None
        state.credit_type = CreditType.BYO_KEY.value if has_own_key else CreditType.PLATFORM_AI.value

        # Check credit balance before starting
        async with AsyncSessionLocal() as db:
            can_start, message, available = await credit_service.can_start_session(
                db, state.user_id, state.credit_type
            )
            if not can_start:
                logger.warning(f"[SESSION] Insufficient credits for user {state.user_id}: {message}")
                error_msg = ErrorMessage(
                    code="INSUFFICIENT_CREDITS",
                    message=message,
                    recoverable=False,
                )
                await state.websocket.send_text(error_msg.model_dump_json())
                return
            logger.info(f"[SESSION] Credit check passed: {available}s available ({state.credit_type})")

    # Get user's API key if authenticated
    # Falls back to platform server keys for free trial and Platform+AI users
    user_api_key = None
    whisper_api_key = None
    using_platform_key = False
    if state.user_id and provider and provider.lower() != 'mock':
        user_api_key = await get_user_api_key(state.user_id, provider)
        if user_api_key:
            state.user_api_key = user_api_key
            logger.info(f"[SESSION] Using user's {provider} API key")
        else:
            # No user key — check if platform key is available (free trial / Platform+AI)
            # credit_type is PLATFORM_AI when user has no saved key (set above at line 503)
            if state.credit_type == CreditType.PLATFORM_AI.value:
                # Use platform server key — user is on free trial or Platform+AI plan
                user_api_key = None  # Let the client class fall back to settings.*_api_key
                using_platform_key = True
                logger.info(f"[SESSION] Using platform {provider} key (credit_type={state.credit_type})")
            else:
                # BYO_KEY user without a saved key — this shouldn't happen, but guard anyway
                logger.warning(f"[SESSION] BYO_KEY user has no API key for {provider}")
                error_msg = ErrorMessage(
                    code="API_KEY_MISSING",
                    message=f"No API key found for {provider}. Please add your API key in Settings.",
                    recoverable=False,
                )
                await state.websocket.send_text(error_msg.model_dump_json())
                return

        # Anthropic needs a separate Whisper key for transcription (Groq preferred, OpenAI fallback)
        # When using platform keys, skip this — the client falls back to Deepgram (server key)
        # or settings.groq_api_key / settings.openai_api_key internally
        if provider.lower() == 'anthropic' and not using_platform_key:
            whisper_api_key = await get_user_api_key(state.user_id, 'groq')
            if whisper_api_key:
                logger.info("[SESSION] Using user's Groq key for Whisper transcription (Anthropic)")
            else:
                whisper_api_key = await get_user_api_key(state.user_id, 'openai')
                if whisper_api_key:
                    logger.info("[SESSION] Using user's OpenAI key for Whisper transcription (Anthropic)")
                else:
                    logger.warning("[SESSION] Anthropic provider requires a Groq or OpenAI key for Whisper transcription")
                    error_msg = ErrorMessage(
                        code="API_KEY_MISSING",
                        message="Anthropic provider requires a Groq or OpenAI API key for audio transcription. Please add one in Settings.",
                        recoverable=False,
                    )
                    await state.websocket.send_text(error_msg.model_dump_json())
                    return

    # Initialize conversation intelligence (feature-flagged)
    if settings.enable_conversation_memory:
        state.conversation_history = ConversationHistory(
            max_active_turns=settings.conversation_memory_turns
        )
        logger.info(f"[SESSION] Conversation memory enabled (buffer={settings.conversation_memory_turns} turns)")

    # Create in-memory session
    session = session_manager.create_session(
        job_description=context.get("jobDescription", ""),
        resume=context.get("resume", ""),
        work_experience=context.get("workExperience", ""),
        verbosity=verbosity,
    )
    state.session_id = session.id
    logger.info(f"[SESSION] Session created: {session.id}")

    # Connect to LLM provider with user's API key
    logger.info(f"[SESSION] Getting LLM client...")
    state.openai_client = get_llm_client(provider, api_key=user_api_key, whisper_api_key=whisper_api_key)
    provider_name = provider or "default"
    logger.info(f"[SESSION] LLM client obtained: {type(state.openai_client).__name__}")

    # Pass conversation history to LLM client if supported
    if state.conversation_history and hasattr(state.openai_client, 'set_conversation_history'):
        state.openai_client.set_conversation_history(state.conversation_history)

    logger.info(f"[SESSION] Connecting to LLM provider...")
    connected = await state.openai_client.connect(
        job_description=session.context.job_description,
        resume=session.context.resume,
        work_experience=session.context.work_experience,
        verbosity=verbosity,
        prompt_key=prompt_key,
        pre_prepared_answers=prepared_answers,
        company_name=company_name,
        role_type=role_type,
        round_type=round_type,
    )
    logger.info(f"[SESSION] LLM connection result: {connected}")

    if connected:
        # Start receiving from LLM provider in background
        logger.info(f"[SESSION] Starting receive_from_openai background task...")
        state.receive_task = asyncio.create_task(
            receive_from_openai(state)
        )
        logger.info(f"[SESSION] Background task started")

        # Send ready message with STT mode
        stt_mode = "chunked"
        if hasattr(state.openai_client, 'stt_mode'):
            stt_mode = state.openai_client.stt_mode
        ready_msg = SessionReadyMessage(sttMode=stt_mode)
        await state.websocket.send_text(ready_msg.model_dump_json())
        logger.info(f"[SESSION] Sent session.ready message to client")

        # Send connection status
        status_msg = ConnectionStatusMessage(status="connected")
        await state.websocket.send_text(status_msg.model_dump_json())
        logger.info(f"[SESSION] Sent connection.status=connected to client")

        # Start credit deduction loop if authenticated and not mock
        if state.user_id and state.credit_type:
            state.credit_deduction_task = asyncio.create_task(
                credit_deduction_loop(state)
            )
            logger.info(f"[SESSION] Credit deduction loop started ({state.credit_type})")

        logger.info(f"[SESSION] ===== Session started successfully: {session.id} (provider: {provider_name}) =====")
    else:
        logger.error(f"[SESSION] !!!!! Failed to connect to LLM provider: {provider_name}")
        error_msg = ErrorMessage(
            code="API_001",
            message=f"Failed to connect to LLM provider ({provider_name})",
            recoverable=True,
        )
        await state.websocket.send_text(error_msg.model_dump_json())


async def save_session_to_db(state: ConnectionState) -> Optional[str]:
    """Save session data to the database.

    Args:
        state: Connection state with session data

    Returns:
        Database session ID or None if failed
    """
    if not state.user_id:
        logger.info("[SESSION] Skipping DB save - no authenticated user")
        return None

    if not state.transcript_entries and not state.suggestion_entries:
        logger.info("[SESSION] Skipping DB save - no content to save")
        return None

    try:
        async with AsyncSessionLocal() as db:
            # Calculate duration
            duration_seconds = 0
            if state.session_start_time:
                duration_seconds = int((datetime.utcnow() - state.session_start_time).total_seconds())

            # Create database session record
            db_session = InterviewSession(
                user_id=state.user_id,
                job_description=state.context.get("jobDescription"),
                resume=state.context.get("resume"),
                work_experience=state.context.get("workExperience"),
                transcript=state.transcript_entries,
                suggestions=state.suggestion_entries,
                duration_seconds=duration_seconds,
                provider_used=state.provider_used,
                credit_type_used=state.credit_type,
                seconds_charged=state.total_seconds_charged,
                created_at=state.session_start_time or datetime.utcnow(),
                ended_at=datetime.utcnow(),
            )
            db.add(db_session)
            await db.commit()

            logger.info(f"[SESSION] Saved to database: {db_session.id} ({len(state.transcript_entries)} transcripts, {len(state.suggestion_entries)} suggestions, {duration_seconds}s)")
            return db_session.id

    except Exception as e:
        logger.error(f"[SESSION] Failed to save to database: {e}")
        import traceback
        logger.error(f"[SESSION] Traceback:\n{traceback.format_exc()}")
        return None


async def handle_session_end(state: ConnectionState) -> None:
    """End the current session."""
    logger.info(f"[SESSION] Ending session: {state.session_id}")

    # Cancel credit deduction loop
    if state.credit_deduction_task:
        state.credit_deduction_task.cancel()
        try:
            await state.credit_deduction_task
        except asyncio.CancelledError:
            pass
        state.credit_deduction_task = None

    # Save session to database before clearing
    if state.user_id and (state.transcript_entries or state.suggestion_entries):
        db_session_id = await save_session_to_db(state)
        if db_session_id:
            state.db_session_id = db_session_id

    if state.session_id:
        session_manager.clear_session(state.session_id)
        logger.info(f"[SESSION] In-memory session cleared: {state.session_id}")

    if state.openai_client:
        await state.openai_client.disconnect()

    if state.receive_task:
        state.receive_task.cancel()
        try:
            await state.receive_task
        except asyncio.CancelledError:
            pass

    state.session_id = None
    state.openai_client = None
    state.receive_task = None


async def handle_verbosity_change(state: ConnectionState, data: dict) -> None:
    """Update verbosity setting."""
    if state.session_id:
        verbosity = data.get("verbosity", "moderate")
        session_manager.set_verbosity(state.session_id, verbosity)
        logger.debug(f"Verbosity changed to: {verbosity}")


async def handle_ping(state: ConnectionState, data: dict) -> None:
    """Respond to ping with pong."""
    timestamp = data.get("timestamp", 0)
    pong_msg = PongMessage(
        timestamp=timestamp,
        serverTime=int(time.time() * 1000),
    )
    await state.websocket.send_text(pong_msg.model_dump_json())


async def handle_speaker_update(state: ConnectionState, data: dict) -> None:
    """Update the current speaker for transcript labeling."""
    speaker = data.get("speaker", "interviewer")
    if speaker in ("user", "interviewer"):
        state.current_speaker = speaker
        logger.debug(f"[WS] Speaker updated to: {speaker}")


async def handle_speech_timing(state: ConnectionState, data: dict) -> None:
    """Update speech timing from frontend VAD for Option B1 turn detection."""
    is_speaking = data.get("isSpeaking", True)
    silence_ms = data.get("silenceDurationMs", 0.0)

    # Detect transition from silence to speech (speech just resumed)
    # Capture the silence duration that occurred before speech resumed
    if is_speaking and not state.was_speaking:
        # Speech just resumed - capture how long the silence was
        state.silence_before_speech_ms = state.silence_duration_ms
        logger.info(f"[WS] Speech resumed after {state.silence_before_speech_ms:.0f}ms of silence")

    # Update current state
    state.silence_duration_ms = silence_ms
    state.was_speaking = is_speaking

    # Track if speech just ended
    if not is_speaking and state.speech_end_timestamp is None:
        state.speech_end_timestamp = time.time()
    elif is_speaking:
        state.speech_end_timestamp = None  # Reset when speech resumes

    # Pass to LLM client for AI suggestion timing AND trigger check
    # This is critical because audio chunks are NOT sent during silence
    if state.openai_client and hasattr(state.openai_client, 'update_speech_timing'):
        state.openai_client.update_speech_timing(is_speaking, silence_ms)

    # Also trigger suggestion check if LLM client supports it
    # This handles the case where silence threshold is reached but no audio is being sent
    if state.openai_client and hasattr(state.openai_client, 'check_and_trigger_suggestion'):
        await state.openai_client.check_and_trigger_suggestion()

    logger.debug(f"[WS] Speech timing: speaking={is_speaking}, silence={silence_ms:.0f}ms, beforeSpeech={state.silence_before_speech_ms:.0f}ms")


async def handle_audio_data(state: ConnectionState, audio_bytes: bytes) -> None:
    """Forward audio data to LLM client with speaker context."""
    if not state.openai_client:
        logger.warning("[WS-AUDIO] No LLM client available!")
        return
    if not state.openai_client.is_connected:
        logger.warning("[WS-AUDIO] LLM client not connected!")
        return
    await state.openai_client.send_audio(audio_bytes, speaker=state.current_speaker)


async def receive_from_openai(state: ConnectionState) -> None:
    """Background task to receive messages from LLM and forward to client."""
    logger.info("[WS-RECV] ===== receive_from_openai task started =====")

    if not state.openai_client:
        logger.error("[WS-RECV] No LLM client available!")
        return

    current_transcript_id: Optional[str] = None
    message_count = 0

    try:
        logger.info("[WS-RECV] Starting to receive messages from LLM client...")
        async for message in state.openai_client.receive_messages():
            message_count += 1
            event_type = message.get("type", "")
            logger.info(f"[WS-RECV] ===== Message #{message_count} from LLM: type={event_type} =====")

            if event_type == "conversation.item.input_audio_transcription.completed":
                # Transcription completed
                transcript = message.get("transcript", "")

                # Deepgram streaming provides segment ID, finality, new turn, and speaker
                entry_id = message.get("segmentId") or str(uuid.uuid4())
                is_final_transcript = message.get("isFinal", True)
                is_new_turn = message.get("isNewTurn", False)
                speaker = message.get("speaker", state.current_speaker)

                logger.info(f"[WS-RECV] TRANSCRIPT from {speaker}: '{transcript[:100]}...' "
                            f"(id={entry_id}, final={is_final_transcript}, newTurn={is_new_turn})")

                # For Whisper mode (no segmentId), detect new turns using silence
                if "segmentId" not in message:
                    current_time = time.time()
                    if state.last_transcript_time == 0:
                        is_new_turn = True
                    elif settings.use_speech_timing_for_turns:
                        # B1: Use frontend VAD silence-before-speech detection
                        if state.silence_before_speech_ms >= settings.speech_silence_threshold_ms:
                            is_new_turn = True
                            logger.info(f"[WS-RECV] New turn detected (B1 VAD silence: {state.silence_before_speech_ms:.0f}ms)")
                            state.silence_before_speech_ms = 0

                        # Fallback: also check time gap between transcripts
                        # (handles case where interviewee is silent so B1 never fires)
                        if not is_new_turn:
                            silence_duration = current_time - state.last_transcript_time
                            if silence_duration >= NEW_TURN_SILENCE_THRESHOLD:
                                is_new_turn = True
                                logger.info(f"[WS-RECV] New turn detected (time fallback: {silence_duration:.1f}s)")
                    else:
                        # Option A: Pure time-based detection
                        silence_duration = current_time - state.last_transcript_time
                        if silence_duration >= NEW_TURN_SILENCE_THRESHOLD:
                            is_new_turn = True
                            logger.info(f"[WS-RECV] New turn detected (Option A silence: {silence_duration:.1f}s)")
                    state.last_transcript_time = current_time

                # Add to session
                if state.session_id:
                    session_manager.add_transcript_entry(
                        state.session_id,
                        entry_id,
                        speaker,
                        transcript,
                        is_final=is_final_transcript,
                    )

                # Only accumulate final transcripts for database saving
                if is_final_transcript:
                    state.transcript_entries.append({
                        "id": entry_id,
                        "speaker": speaker,
                        "text": transcript,
                        "timestamp": time.time(),
                        "isNewTurn": is_new_turn,
                    })
                    # Track last interviewer question for conversation intelligence
                    if speaker == "interviewer":
                        state.last_interviewer_question = transcript

                # Send to client (supports update-or-add based on entry_id)
                delta_msg = TranscriptDeltaMessage(
                    id=entry_id,
                    speaker=speaker,
                    text=transcript,
                    isFinal=is_final_transcript,
                    isNewTurn=is_new_turn,
                )
                await state.websocket.send_text(delta_msg.model_dump_json())
                logger.info(f"[WS-RECV] Transcript SENT to client (final={is_final_transcript})")

            elif event_type == "suggestion.delta":
                # Skip if suggestions are muted
                if state.suggestions_muted:
                    continue
                # Streaming suggestion text chunk — forward to client immediately
                delta = message.get("delta", "")
                delta_id = message.get("id", "")
                is_first = message.get("isFirst", False)
                if delta:
                    delta_msg = json.dumps({
                        "type": "suggestion.delta",
                        "id": delta_id,
                        "delta": delta,
                        "isFirst": is_first,
                    })
                    await state.websocket.send_text(delta_msg)
                    if is_first:
                        logger.info(f"[WS-RECV] Streaming suggestion started: id={delta_id}")

            elif event_type == "suggestion.cancel":
                # LLM determined input was not a question — cancel streaming suggestion
                cancel_id = message.get("id", "")
                cancel_msg = json.dumps({
                    "type": "suggestion.cancel",
                    "id": cancel_id,
                })
                await state.websocket.send_text(cancel_msg)
                logger.info(f"[WS-RECV] Streaming suggestion cancelled (not a question): id={cancel_id}")

            elif event_type == "response.text.delta":
                # Legacy streaming text response (non-Anthropic providers)
                delta = message.get("delta", "")
                if not current_transcript_id:
                    current_transcript_id = str(uuid.uuid4())
                logger.info(f"[WS-RECV] Text delta received: '{delta[:50]}...'")

            elif event_type == "response.text.done":
                # Skip if suggestions are muted
                if state.suggestions_muted:
                    logger.info("[WS-RECV] Suggestions muted - skipping suggestion")
                    continue
                # Response complete - parse and send as final suggestion
                try:
                    text = message.get("text", "")
                    # Use the suggestion ID from streaming if available
                    suggestion_id = message.get("id") or str(uuid.uuid4())
                    logger.info(f"[WS-RECV] SUGGESTION complete: id={suggestion_id}, '{text[:100]}...'")

                    if text:
                        # Extract intent classification if enabled
                        intent = "new_question"
                        display_text = text
                        if settings.enable_intent_classification:
                            intent, display_text = parse_intent_from_response(text)
                            logger.info(f"[WS-RECV] Classified intent: {intent}")

                        # Parse the structured response into separate fields
                        response, key_points, follow_up = parse_suggestion_response(display_text)
                        logger.info(f"[WS-RECV] Parsed suggestion: response={len(response)} chars, {len(key_points)} key points, follow_up={len(follow_up)} chars")

                        # Get the question this suggestion responds to
                        question_text = message.get("question", "") or state.last_interviewer_question

                        suggestion_msg = SuggestionMessage(
                            id=suggestion_id,
                            response=response,
                            keyPoints=key_points,
                            followUp=follow_up,
                            intent=intent,
                            question=question_text,
                        )
                        logger.info(f"[WS-RECV] Sending final suggestion to client WebSocket...")
                        await state.websocket.send_text(suggestion_msg.model_dump_json())
                        logger.info(f"[WS-RECV] Final suggestion SENT to client!")

                        # Record turns in conversation history
                        try:
                            if state.conversation_history and not is_not_a_question(response):
                                state.conversation_history.add_turn(
                                    role="interviewer",
                                    text=question_text,
                                    intent=intent,
                                )
                                state.conversation_history.add_turn(
                                    role="candidate",
                                    text=response[:500],  # Truncate to save memory
                                    intent="answer",
                                )
                                logger.info(f"[WS-RECV] Recorded conversation turn (history size: {len(state.conversation_history)})")
                        except Exception as hist_err:
                            logger.error(f"[WS-RECV] Error recording conversation history: {hist_err}")

                        # Accumulate for database saving
                        state.suggestion_entries.append({
                            "id": suggestion_id,
                            "response": response,
                            "keyPoints": key_points,
                            "followUp": follow_up,
                            "intent": intent,
                            "question": question_text,
                            "timestamp": time.time(),
                        })
                except Exception as suggestion_err:
                    logger.error(f"[WS-RECV] Error processing suggestion: {type(suggestion_err).__name__}: {suggestion_err}")
                    import traceback
                    logger.error(f"[WS-RECV] Suggestion error traceback:\n{traceback.format_exc()}")
                    # Cancel the streaming suggestion so frontend doesn't get stuck
                    try:
                        cancel_msg = json.dumps({
                            "type": "suggestion.cancel",
                            "id": message.get("id", ""),
                        })
                        await state.websocket.send_text(cancel_msg)
                    except Exception:
                        pass
                current_transcript_id = None

            elif event_type == "error":
                error = message.get("error", {})
                logger.error(f"[WS-RECV] ERROR from LLM: {error}")
                error_msg = ErrorMessage(
                    code="API_003",
                    message=error.get("message", "LLM error"),
                    recoverable=True,
                )
                await state.websocket.send_text(error_msg.model_dump_json())

            elif event_type in ("rate_limit.status", "rate_limit.update"):
                # Forward rate limit messages directly to client (dev mode)
                logger.info(f"[WS-RECV] Rate limit message: {event_type}")
                await state.websocket.send_text(json.dumps(message))

    except asyncio.CancelledError:
        logger.info(f"[WS-RECV] Task cancelled after {message_count} messages")
    except Exception as e:
        logger.error(f"[WS-RECV] !!!!! ERROR: {type(e).__name__}: {e}")
        import traceback
        logger.error(f"[WS-RECV] Traceback:\n{traceback.format_exc()}")


async def cleanup_connection(state: ConnectionState) -> None:
    """Clean up connection resources."""
    # Cancel credit deduction loop
    if state.credit_deduction_task:
        state.credit_deduction_task.cancel()
        try:
            await state.credit_deduction_task
        except asyncio.CancelledError:
            pass
        state.credit_deduction_task = None

    # Save session to database if not already saved
    if state.user_id and state.session_id and not state.db_session_id:
        if state.transcript_entries or state.suggestion_entries:
            logger.info("[WS] Saving session on disconnect...")
            await save_session_to_db(state)

    if state.receive_task:
        state.receive_task.cancel()
        try:
            await state.receive_task
        except asyncio.CancelledError:
            pass

    if state.openai_client:
        await state.openai_client.disconnect()

    if state.session_id:
        session_manager.clear_session(state.session_id)
