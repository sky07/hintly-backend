"""Questionnaire REST API endpoints.

Provides:
- GET  /api/questionnaire/{role_type}            - Get questionnaire template
- GET  /api/questionnaire/{role_type}/responses   - Get user's saved responses
- PUT  /api/questionnaire/{role_type}/save        - Save answers (partial, anytime)
- POST /api/questionnaire/{role_type}/submit      - Submit completed questionnaire
- POST /api/questionnaire/{role_type}/evaluate    - Trigger AI confidence evaluation
- POST /api/questionnaire/{role_type}/follow-up/save - Save follow-up answers
- GET  /api/questionnaire/progress                - Get progress across all roles
- GET  /api/questionnaire/available               - List available questionnaires
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, UserAPIKey, LLMProvider, CreditType
from routers.auth import require_auth
from services.encryption import decrypt_api_key
from services.credit_service import get_effective_balance, deduct_credits
from services.mongodb_service import (
    get_questionnaire,
    get_available_questionnaires,
    get_user_responses,
    save_user_responses,
    submit_questionnaire,
    save_evaluation_result,
    save_follow_up_answers,
    get_all_user_progress,
)
from config import settings

# Credit cost for one Anthropic evaluation (in seconds)
EVAL_CREDIT_COST_SECONDS = 30

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/questionnaire", tags=["questionnaire"])


# --- Request Models ---


class SaveResponsesRequest(BaseModel):
    answers: Dict[str, Any]  # question_id -> {answer_text, last_updated}
    total_questions: int


class FollowUpSaveRequest(BaseModel):
    follow_up_answers: Dict[str, str]  # follow_up_id -> answer_text


class EvaluateRequest(BaseModel):
    provider: str = "groq"  # LLM provider to use for evaluation


# --- Helper ---


async def _get_user_api_key(user_id: str, provider: str, db: AsyncSession) -> Optional[str]:
    """Get user's decrypted API key for a provider."""
    from sqlalchemy import select

    provider_map = {
        "anthropic": LLMProvider.ANTHROPIC,
        "adaptive": LLMProvider.GROQ,
        "groq": LLMProvider.GROQ,
        "openai": LLMProvider.OPENAI,
        "gemini": LLMProvider.GEMINI,
    }
    llm_provider = provider_map.get(provider.lower())
    if not llm_provider:
        return None

    result = await db.execute(
        select(UserAPIKey)
        .where(UserAPIKey.user_id == user_id)
        .where(UserAPIKey.provider == llm_provider)
    )
    key_record = result.scalar_one_or_none()
    if key_record and settings.encryption_key:
        return decrypt_api_key(key_record.encrypted_key)
    return None


# --- Endpoints ---


@router.get("/available")
async def list_available_questionnaires(
    user: User = Depends(require_auth),
):
    """List all available questionnaire templates."""
    questionnaires = await get_available_questionnaires()
    return {"questionnaires": questionnaires}


@router.get("/progress")
async def get_progress(
    user: User = Depends(require_auth),
):
    """Get questionnaire progress across all roles for the current user."""
    progress = await get_all_user_progress(user.id)
    return {"progress": progress}


@router.get("/{role_type}")
async def get_questionnaire_template(
    role_type: str,
    user: User = Depends(require_auth),
):
    """Get the questionnaire template for a specific role."""
    questionnaire = await get_questionnaire(role_type)
    if not questionnaire:
        raise HTTPException(
            status_code=404,
            detail=f"No questionnaire available for role: {role_type}. Questionnaire is being prepared.",
        )
    return questionnaire


@router.get("/{role_type}/responses")
async def get_responses(
    role_type: str,
    user: User = Depends(require_auth),
):
    """Get user's saved responses for a role's questionnaire."""
    responses = await get_user_responses(user.id, role_type)
    if not responses:
        return {
            "status": "not_started",
            "answers": {},
            "answered_count": 0,
            "total_questions": 0,
        }
    return responses


@router.put("/{role_type}/save")
async def save_responses(
    role_type: str,
    request: SaveResponsesRequest,
    user: User = Depends(require_auth),
):
    """Save questionnaire answers (partial save, available anytime).

    Users can save progress at any point and come back later.
    """
    result = await save_user_responses(
        user_id=user.id,
        role_type=role_type,
        answers=request.answers,
        total_questions=request.total_questions,
    )
    return result


@router.post("/{role_type}/submit")
async def submit(
    role_type: str,
    user: User = Depends(require_auth),
):
    """Submit a completed questionnaire for AI evaluation.

    Marks the questionnaire as 'completed' and ready for evaluation.
    """
    # Verify responses exist and are sufficiently complete
    responses = await get_user_responses(user.id, role_type)
    if not responses:
        raise HTTPException(status_code=400, detail="No responses found. Start answering questions first.")

    answered = responses.get("answered_count", 0)
    total = responses.get("total_questions", 0)

    if total > 0 and answered < total * 0.5:
        raise HTTPException(
            status_code=400,
            detail=f"Please answer at least 50% of questions before submitting. Currently: {answered}/{total}",
        )

    result = await submit_questionnaire(user.id, role_type)
    return result


@router.post("/{role_type}/evaluate")
async def evaluate(
    role_type: str,
    request: EvaluateRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Trigger AI confidence evaluation on submitted questionnaire.

    The AI agent evaluates all answers holistically and returns:
    - confidence_score (0-100%)
    - strengths (list of strong areas)
    - gaps (list of areas needing more info)
    - follow_up_questions (if confidence < 70%)
    """
    # Get user's responses
    responses = await get_user_responses(user.id, role_type)
    if not responses:
        raise HTTPException(status_code=400, detail="No responses found.")

    if responses.get("status") not in ("completed", "needs_follow_up"):
        raise HTTPException(
            status_code=400,
            detail="Please submit the questionnaire before requesting evaluation.",
        )

    # Get questionnaire template for context
    questionnaire = await get_questionnaire(role_type)
    if not questionnaire:
        raise HTTPException(status_code=404, detail=f"No questionnaire found for role: {role_type}")

    # Determine evaluation provider & API key
    # Priority: 1) Anthropic platform key (if credits available)
    #           2) User's own key for requested provider
    #           3) Auto-detect from user's saved keys (groq → openai → gemini)
    eval_provider = None
    api_key = None
    used_platform_credits = False

    # 1. Try Anthropic platform key first (requires credits)
    if settings.anthropic_api_key:
        available = await get_effective_balance(db, user.id, CreditType.PLATFORM_AI.value)
        if available >= EVAL_CREDIT_COST_SECONDS:
            eval_provider = "anthropic"
            api_key = settings.anthropic_api_key
            used_platform_credits = True
            logger.info(f"[EVAL] Using platform Anthropic key (credits available: {available}s)")

    # 2. Try user's own key for the requested provider
    if not api_key:
        eval_provider = request.provider
        api_key = await _get_user_api_key(user.id, eval_provider, db)

    # 3. Auto-detect from user's saved keys (anthropic first, then others)
    if not api_key:
        for fallback_provider, llm_enum in [
            ("anthropic", LLMProvider.ANTHROPIC),
            ("groq", LLMProvider.GROQ),
            ("openai", LLMProvider.OPENAI),
            ("gemini", LLMProvider.GEMINI),
        ]:
            result = await db.execute(
                select(UserAPIKey)
                .where(UserAPIKey.user_id == user.id)
                .where(UserAPIKey.provider == llm_enum)
            )
            key_record = result.scalar_one_or_none()
            if key_record and settings.encryption_key:
                api_key = decrypt_api_key(key_record.encrypted_key)
                if api_key:
                    eval_provider = fallback_provider
                    logger.info(f"[EVAL] Falling back to user's {eval_provider} key")
                    break

    if not api_key:
        raise HTTPException(
            status_code=400,
            detail="No API key or credits available for evaluation. Please add a Groq, OpenAI, or Gemini API key in Settings, or purchase credits.",
        )

    # Run AI evaluation
    from services.ai_evaluator import evaluate_questionnaire

    try:
        evaluation = await evaluate_questionnaire(
            role_type=role_type,
            questionnaire=questionnaire,
            answers=responses.get("answers", {}),
            follow_up_answers=responses.get("follow_up_answers", {}),
            provider=eval_provider,
            api_key=api_key,
        )

        # Deduct credits only after successful evaluation with platform key
        if used_platform_credits:
            success, remaining = await deduct_credits(
                db, user.id, CreditType.PLATFORM_AI.value,
                EVAL_CREDIT_COST_SECONDS, session_id=None,
            )
            logger.info(f"[EVAL] Deducted {EVAL_CREDIT_COST_SECONDS}s platform credits, remaining: {remaining}s")
    except Exception as e:
        logger.error(f"[EVAL] Evaluation failed for user {user.id[:8]}... role {role_type}: {e}")
        raise HTTPException(status_code=500, detail=f"Evaluation failed: {str(e)}")

    # Save evaluation result
    confidence = evaluation.get("confidence_score", 0)
    follow_ups = evaluation.get("follow_up_questions", [])

    await save_evaluation_result(
        user_id=user.id,
        role_type=role_type,
        confidence_score=confidence,
        evaluation_result=evaluation,
        follow_up_questions=follow_ups,
    )

    # After evaluation succeeds, trigger profile optimization in background (non-blocking)
    try:
        from services.profile_optimizer import optimize_profile
        await optimize_profile(user.id, role_type, db)
    except Exception as e:
        logger.warning(f"[EVAL] Profile optimization failed (non-blocking): {e}")

    return evaluation


@router.post("/{role_type}/follow-up/save")
async def save_follow_ups(
    role_type: str,
    request: FollowUpSaveRequest,
    user: User = Depends(require_auth),
):
    """Save answers to follow-up questions from the AI evaluator."""
    result = await save_follow_up_answers(
        user_id=user.id,
        role_type=role_type,
        follow_up_answers=request.follow_up_answers,
    )
    return result
