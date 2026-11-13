"""Profile Optimizer service.

Processes raw Q&A data (questionnaire responses + quick Q&A) through an LLM
to produce a lean, structured "Candidate Experience & Behavioral Profile"
for use in the interview session system prompt.

Uses SHA-256 hashing to cache optimized profiles and skip redundant LLM calls.
"""

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import ApprovedAnswer
from services.mongodb_service import (
    get_user_responses,
    get_questionnaire,
    get_db as get_mongo_db,
)
from services.prompts import Profile_Optimizer_System_Prompt

logger = logging.getLogger(__name__)


def compute_qa_hash(
    questionnaire_answers: Dict[str, Any],
    follow_up_answers: Dict[str, str],
    approved_answers: List[Dict[str, str]],
) -> str:
    """Compute SHA-256 hash of all raw Q&A data.

    Args:
        questionnaire_answers: Answers dict from MongoDB questionnaire_responses
        follow_up_answers: Follow-up answers dict
        approved_answers: List of {question_text, answer_data} from SQLite

    Returns:
        Hex-encoded SHA-256 hash string.
    """
    # Build a deterministic representation
    parts = []

    # Sort questionnaire answers by key for determinism
    for key in sorted(questionnaire_answers.keys()):
        val = questionnaire_answers[key]
        text = val.get("answer_text", "") if isinstance(val, dict) else str(val)
        parts.append(f"q:{key}={text}")

    # Follow-up answers
    for key in sorted(follow_up_answers.keys()):
        parts.append(f"fu:{key}={follow_up_answers[key]}")

    # Approved answers (sorted by question text for determinism)
    for aa in sorted(approved_answers, key=lambda x: x.get("question_text", "")):
        answer_data = aa.get("answer_data", "")
        # answer_data can be a dict (JSON) or a string
        if isinstance(answer_data, dict):
            answer_data = json.dumps(answer_data, sort_keys=True)
        parts.append(f"aa:{aa.get('question_text', '')}={answer_data}")

    combined = "\n".join(parts)
    return hashlib.sha256(combined.encode("utf-8")).hexdigest()


async def collect_raw_qa(
    user_id: str,
    role_type: str,
    db: AsyncSession,
) -> Tuple[Optional[Dict], Dict[str, Any], Dict[str, str], List[Dict[str, str]]]:
    """Gather raw Q&A from both MongoDB questionnaire responses and SQLite approved answers.

    Returns:
        Tuple of (questionnaire_template, questionnaire_answers, follow_up_answers, approved_answers)
    """
    # 1. MongoDB questionnaire responses
    q_responses = await get_user_responses(user_id, role_type)
    q_template = await get_questionnaire(role_type)

    questionnaire_answers = {}
    follow_up_answers = {}
    if q_responses:
        questionnaire_answers = q_responses.get("answers", {})
        follow_up_answers = q_responses.get("follow_up_answers", {})

    # 2. SQLite approved answers
    result = await db.execute(
        select(ApprovedAnswer)
        .where(ApprovedAnswer.user_id == user_id)
        .where(ApprovedAnswer.role_type == role_type)
        .where(ApprovedAnswer.is_approved == True)
        .order_by(ApprovedAnswer.created_at.asc())
    )
    sql_answers = result.scalars().all()
    approved_answers = [
        {"question_text": a.question_text, "answer_data": a.answer_data}
        for a in sql_answers
    ]

    return q_template, questionnaire_answers, follow_up_answers, approved_answers


def format_raw_qa_for_optimizer(
    questionnaire: Optional[Dict],
    answers: Dict[str, Any],
    follow_up_answers: Dict[str, str],
    approved_answers: List[Dict[str, str]],
) -> str:
    """Format all Q&A into readable text for the optimizer LLM.

    Args:
        questionnaire: Questionnaire template (or None)
        answers: Questionnaire answers keyed by question_id
        follow_up_answers: Follow-up answers dict
        approved_answers: List of {question_text, answer_data} from SQLite

    Returns:
        Formatted text block for the optimizer.
    """
    lines = []

    # Questionnaire answers
    if questionnaire and answers:
        lines.append(f"# Questionnaire: {questionnaire.get('title', 'Unknown Role')}")
        lines.append("")

        for section in questionnaire.get("sections", []):
            lines.append(f"## {section['title']}")
            for question in section.get("questions", []):
                q_id = question["question_id"]
                answer = answers.get(q_id, {})
                answer_text = answer.get("answer_text", "").strip() if isinstance(answer, dict) else ""
                if answer_text:
                    lines.append(f"**Q: {question['question_text']}**")
                    lines.append(f"A: {answer_text}")
                    lines.append("")

        # Follow-up answers
        if follow_up_answers:
            lines.append("## Follow-Up Answers")
            for fu_id, fu_answer in follow_up_answers.items():
                if fu_answer.strip():
                    lines.append(f"A: {fu_answer}")
                    lines.append("")

    # Quick Q&A answers from SQLite
    if approved_answers:
        lines.append("# Quick Q&A Answers")
        lines.append("")
        for aa in approved_answers:
            q_text = aa.get("question_text", "").strip()
            answer_data = aa.get("answer_data", "")
            # answer_data can be a dict (JSON) or a string
            if isinstance(answer_data, dict):
                # Extract meaningful fields from the structured answer
                a_parts = []
                if answer_data.get("core_message"):
                    a_parts.append(answer_data["core_message"])
                if answer_data.get("example_reference"):
                    a_parts.append(f"Example: {answer_data['example_reference']}")
                if answer_data.get("impact_metrics"):
                    a_parts.append(f"Impact: {answer_data['impact_metrics']}")
                if answer_data.get("talking_points"):
                    for tp in answer_data["talking_points"]:
                        if tp:
                            a_parts.append(f"- {tp}")
                a_text = "\n".join(a_parts).strip()
            else:
                a_text = str(answer_data).strip()
            if q_text and a_text:
                lines.append(f"**Q: {q_text}**")
                lines.append(f"A: {a_text}")
                lines.append("")

    return "\n".join(lines)


# === LLM Provider Calls (text output, not JSON) ===


async def _call_anthropic(system_prompt: str, user_message: str, api_key: str) -> str:
    """Call Anthropic API for profile optimization (platform key)."""
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "server-side-fallback-2026-07-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-5-5",
                # Sonnet 5.5 always thinks (counted in max_tokens) and rejects
                # a custom temperature, so neither is set as before.
                "max_tokens": 16000,
                "fallbacks": "default",
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_message},
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("Claude declined this request (stop_reason=refusal)")
        # Read the answer by block type: the response opens with thinking blocks
        text_out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        return text_out


async def _call_openai(system_prompt: str, user_message: str, api_key: str) -> str:
    """Call OpenAI API for profile optimization (platform key fallback)."""
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 4000,
            },
        )
        response.raise_for_status()
        data = response.json()
        return data["choices"][0]["message"]["content"]


# === MongoDB helpers for optimized_profiles collection ===


async def _save_optimized_profile(
    user_id: str,
    role_type: str,
    profile_text: str,
    data_hash: str,
    provider_used: str,
) -> None:
    """Save optimized profile to MongoDB (upsert)."""
    mongo_db = get_mongo_db()
    if mongo_db is None:
        raise RuntimeError("MongoDB not connected")

    collection = mongo_db["optimized_profiles"]
    now = datetime.utcnow()

    await collection.update_one(
        {"user_id": user_id, "role_type": role_type},
        {
            "$set": {
                "optimized_profile": profile_text,
                "data_hash": data_hash,
                "provider_used": provider_used,
                "updated_at": now,
            },
            "$setOnInsert": {
                "user_id": user_id,
                "role_type": role_type,
                "created_at": now,
            },
        },
        upsert=True,
    )
    logger.info(f"[PROFILE_OPT] Saved optimized profile for user {user_id[:8]}... role {role_type}")


async def get_optimized_profile(user_id: str, role_type: str) -> Optional[Dict]:
    """Retrieve optimized profile from MongoDB.

    Returns:
        Document with optimized_profile, data_hash, etc. or None.
    """
    mongo_db = get_mongo_db()
    if mongo_db is None:
        return None

    try:
        collection = mongo_db["optimized_profiles"]
        doc = await collection.find_one(
            {"user_id": user_id, "role_type": role_type},
            {"_id": 0},
        )
        return doc
    except Exception as e:
        logger.error(f"[PROFILE_OPT] Error fetching optimized profile: {e}")
        return None


async def ensure_optimized_profiles_index() -> None:
    """Ensure unique compound index on (user_id, role_type) for optimized_profiles."""
    mongo_db = get_mongo_db()
    if mongo_db is None:
        return

    try:
        collection = mongo_db["optimized_profiles"]
        await collection.create_index(
            [("user_id", 1), ("role_type", 1)], unique=True
        )
        logger.info("[PROFILE_OPT] Index ensured on optimized_profiles")
    except Exception as e:
        logger.warning(f"[PROFILE_OPT] Index creation failed (non-fatal): {e}")


# === Main entry point ===


async def optimize_profile(
    user_id: str,
    role_type: str,
    db: AsyncSession,
) -> str:
    """Main entry point: collect raw Q&A, check cache, optimize if needed.

    Uses platform-level API keys (Anthropic first, OpenAI fallback).
    Does not require user API keys.

    Args:
        user_id: User UUID string
        role_type: Role type string
        db: SQLAlchemy async session for SQLite queries

    Returns:
        Optimized profile text string.
    """
    # 1. Collect raw Q&A from both sources
    q_template, q_answers, fu_answers, approved = await collect_raw_qa(user_id, role_type, db)

    # If there's no data at all, nothing to optimize
    if not q_answers and not approved:
        logger.info(f"[PROFILE_OPT] No Q&A data for user {user_id[:8]}... role {role_type}")
        return ""

    # 2. Compute hash
    data_hash = compute_qa_hash(q_answers, fu_answers, approved)

    # 3. Check existing optimized profile
    existing = await get_optimized_profile(user_id, role_type)
    if existing and existing.get("data_hash") == data_hash:
        logger.info(f"[PROFILE_OPT] Cache hit for user {user_id[:8]}... role {role_type}")
        return existing["optimized_profile"]

    # 4. Format raw Q&A for optimizer
    formatted_qa = format_raw_qa_for_optimizer(q_template, q_answers, fu_answers, approved)
    if not formatted_qa.strip():
        return ""

    user_message = (
        "Please analyze the following raw questionnaire and Q&A data, "
        "then produce the optimized Candidate Experience & Behavioral Profile.\n\n"
        f"{formatted_qa}"
    )

    # 5. Call LLM using platform keys (Anthropic first, OpenAI fallback)
    provider_used = "none"
    try:
        if settings.anthropic_api_key:
            provider_used = "anthropic"
            logger.info(
                f"[PROFILE_OPT] Running optimization for user {user_id[:8]}... "
                f"role {role_type}, provider=anthropic (platform key), input_length={len(formatted_qa)}"
            )
            profile_text = await _call_anthropic(Profile_Optimizer_System_Prompt, user_message, settings.anthropic_api_key)
        elif settings.openai_api_key:
            provider_used = "openai"
            logger.info(
                f"[PROFILE_OPT] Running optimization for user {user_id[:8]}... "
                f"role {role_type}, provider=openai (platform key fallback), input_length={len(formatted_qa)}"
            )
            profile_text = await _call_openai(Profile_Optimizer_System_Prompt, user_message, settings.openai_api_key)
        else:
            raise RuntimeError("No platform API key configured for profile optimization (need ANTHROPIC_API_KEY or OPENAI_API_KEY)")
    except httpx.HTTPStatusError as e:
        logger.error(f"[PROFILE_OPT] API error: {e.response.status_code} - {e.response.text[:200]}")
        raise RuntimeError(f"Profile optimization API error ({e.response.status_code})")
    except Exception as e:
        logger.error(f"[PROFILE_OPT] LLM call failed: {e}")
        raise

    # 6. Store in MongoDB
    await _save_optimized_profile(user_id, role_type, profile_text, data_hash, provider_used)

    logger.info(
        f"[PROFILE_OPT] Optimization complete for user {user_id[:8]}... "
        f"role {role_type}, output_length={len(profile_text)}"
    )

    return profile_text
