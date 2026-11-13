"""Interview preparation REST API endpoints.

Provides:
- POST /api/interview-prep/parse-resume - File upload, returns extracted text
- POST /api/interview-prep/fetch-questions - Query MongoDB, return scored/grouped questions
- POST /api/interview-prep/generate-answers - Generate answers using user's API key
- POST /api/interview-prep/approve-answers - Save to PostgreSQL, return prompt_injection text
- GET  /api/interview-prep/saved-answers - Get user's saved answers (optionally by role)
- PUT  /api/interview-prep/save-answer - Save/update a single Q&A answer
- DELETE /api/interview-prep/saved-answer/{id} - Delete a saved answer
- GET  /api/interview-prep/prep-stats - Get saved answer counts per role
- GET  /api/interview-prep/load-for-session - Get prompt injection text for all saved answers by role
"""

import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import ApprovedAnswer, UserAPIKey, LLMProvider, User
from routers.auth import require_auth
from services.encryption import decrypt_api_key
from services.mongodb_service import fetch_likely_questions, fetch_questions_by_role
from services.resume_parser import parse_resume
from services.answer_generator import generate_answers_batch
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/interview-prep", tags=["interview-prep"])


# --- Request/Response Models ---

class FetchQuestionsRequest(BaseModel):
    company_name: str = ""
    role_type: str = ""
    jd_text: str = ""
    round_type: str = ""
    limit: int = 6


class GenerateAnswersRequest(BaseModel):
    questions: List[Dict[str, Any]]
    resume_text: str = ""
    jd_text: str = ""
    work_experience: str = ""
    company_name: str = ""
    role_type: str = ""
    round_type: str = ""
    provider: str = "groq"


class ApproveAnswerItem(BaseModel):
    question_id: Optional[str] = None
    question_text: str
    answer_data: Dict[str, Any]


class ApproveAnswersRequest(BaseModel):
    answers: List[ApproveAnswerItem]
    company_name: str = ""
    role_type: str = ""
    round_type: str = ""
    session_id: Optional[str] = None


class SaveAnswerRequest(BaseModel):
    question_id: Optional[str] = None
    question_text: str
    answer_data: Dict[str, Any]
    role_type: str
    round_type: str = ""
    company_name: str = ""
    existing_id: Optional[str] = None  # For updates


# --- Helper ---

async def _get_user_api_key(user_id: str, provider: str, db: AsyncSession) -> Optional[str]:
    """Get user's decrypted API key for a provider."""
    provider_map = {
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


async def _get_any_user_api_key(user: User, db: AsyncSession) -> Optional[Dict[str, str]]:
    """Find the user's first available API key (any provider).

    Returns:
        Dict with 'provider' and 'key', or None if no keys configured.
    """
    provider_priority = [
        ("groq", LLMProvider.GROQ),
        ("openai", LLMProvider.OPENAI),
        ("gemini", LLMProvider.GEMINI),
    ]
    for provider_name, llm_provider in provider_priority:
        result = await db.execute(
            select(UserAPIKey)
            .where(UserAPIKey.user_id == user.id)
            .where(UserAPIKey.provider == llm_provider)
        )
        key_record = result.scalar_one_or_none()
        if key_record and settings.encryption_key:
            decrypted = decrypt_api_key(key_record.encrypted_key)
            if decrypted:
                return {"provider": provider_name, "key": decrypted}
    return None


def _build_prompt_injection(
    answers: List[Dict[str, Any]],
    company_name: str = "",
    round_type: str = "",
) -> str:
    """Format approved Q&A pairs into a system prompt section.

    Args:
        answers: List of approved answer dicts
        company_name: Target company name for context
        round_type: Interview round type for context

    Returns:
        Formatted string to append to system prompt
    """
    if not answers:
        return ""

    lines = [
        "\n\n---",
        "## PRE-PREPARED ANSWERS REFERENCE",
    ]

    # Add interview context header
    if company_name or round_type:
        context_parts = []
        if company_name:
            context_parts.append(f"Company: {company_name}")
        if round_type:
            context_parts.append(f"Round: {round_type.replace('_', ' ').title()}")
        lines.append(f"Interview: {' | '.join(context_parts)}")
        lines.append("")

    lines.extend([
        "The candidate has prepared the following answers. When these questions or similar ones come up,",
        "use these as the PRIMARY source for your response. You may adapt the wording but MUST use",
        "the same examples, metrics, and talking points:\n",
    ])

    for i, answer in enumerate(answers, 1):
        q_text = answer.get("question_text", "")
        data = answer.get("answer_data", {})

        lines.append(f"### Q{i}: {q_text}")

        # New simple format: single answer_text field
        if isinstance(data, str):
            lines.append(f"**Answer:** {data}")
        elif data.get("answer_text"):
            lines.append(f"**Answer:** {data['answer_text']}")
        else:
            # Legacy structured format
            if data.get("core_message"):
                lines.append(f"**Core Message:** {data['core_message']}")
            if data.get("example_reference"):
                lines.append(f"**Example:** {data['example_reference']}")
            if data.get("impact_metrics"):
                lines.append(f"**Metrics:** {data['impact_metrics']}")
            if data.get("talking_points"):
                points = data["talking_points"]
                if isinstance(points, list):
                    lines.append("**Talking Points:**")
                    for pt in points:
                        lines.append(f"  - {pt}")
        lines.append("")

    lines.append("---")
    return "\n".join(lines)


# --- Endpoints ---

@router.post("/parse-resume")
async def parse_resume_endpoint(
    file: UploadFile = File(...),
    user: User = Depends(require_auth),
):
    """Upload and parse a resume file (PDF or DOCX).

    Max file size: 5MB.
    """
    # Validate file size (5MB)
    content = await file.read()
    if len(content) > 5 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="File size exceeds 5MB limit")

    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename is required")

    try:
        text = parse_resume(content, file.filename)
        return {"text": text, "filename": file.filename, "size": len(content)}
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/fetch-questions")
async def fetch_questions_endpoint(
    request: FetchQuestionsRequest,
    user: User = Depends(require_auth),
):
    """Fetch likely interview questions from MongoDB."""
    questions = await fetch_likely_questions(
        company_name=request.company_name,
        jd_text=request.jd_text,
        round_type=request.round_type,
        limit=request.limit,
    )
    return questions


@router.get("/questions-for-role")
async def questions_for_role_endpoint(
    role_type: str = Query(..., description="Role type to fetch questions for"),
    user: User = Depends(require_auth),
):
    """Fetch all interview questions for a role, grouped by tier.

    Returns all questions tagged with the given role, sorted by popularity
    and grouped into must_ask / high_probability / stretch tiers.
    """
    questions = await fetch_questions_by_role(role_type=role_type)
    return questions


@router.post("/generate-answers")
async def generate_answers_endpoint(
    request: GenerateAnswersRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Generate answers for interview questions using user's API key."""
    # Get user's API key for the provider
    api_key = await _get_user_api_key(user.id, request.provider, db)
    if not api_key:
        raise HTTPException(
            status_code=400,
            detail=f"No API key found for {request.provider}. Please add your API key in Settings.",
        )

    results = await generate_answers_batch(
        questions=request.questions,
        resume_text=request.resume_text,
        jd_text=request.jd_text,
        work_experience=request.work_experience,
        company_name=request.company_name,
        round_type=request.round_type,
        provider=request.provider,
        api_key=api_key,
    )

    return {"answers": results}


@router.post("/approve-answers")
async def approve_answers_endpoint(
    request: ApproveAnswersRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Save approved answers to PostgreSQL and return prompt injection text."""
    saved_answers = []

    for item in request.answers:
        approved = ApprovedAnswer(
            user_id=user.id,
            session_id=request.session_id,
            question_id=item.question_id,
            question_text=item.question_text,
            company_name=request.company_name,
            role_type=request.role_type,
            round_type=request.round_type,
            answer_data=item.answer_data,
            is_approved=True,
        )
        db.add(approved)
        saved_answers.append({
            "question_text": item.question_text,
            "answer_data": item.answer_data,
        })

    await db.flush()

    # Build prompt injection text
    prompt_injection = _build_prompt_injection(
        saved_answers,
        company_name=request.company_name,
        round_type=request.round_type,
    )

    logger.info(f"[PREP] Approved {len(saved_answers)} answers for user {user.id}")

    return {
        "approved_count": len(saved_answers),
        "prompt_injection": prompt_injection,
    }


@router.get("/saved-answers")
async def get_saved_answers(
    role_type: Optional[str] = Query(None),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get user's saved answers, optionally filtered by role_type."""
    query = (
        select(ApprovedAnswer)
        .where(ApprovedAnswer.user_id == user.id)
        .where(ApprovedAnswer.is_approved == True)
    )
    if role_type:
        query = query.where(ApprovedAnswer.role_type == role_type)

    query = query.order_by(ApprovedAnswer.created_at.desc())
    result = await db.execute(query)
    answers = result.scalars().all()

    return [
        {
            "id": a.id,
            "question_id": a.question_id,
            "question_text": a.question_text,
            "answer_data": a.answer_data,
            "role_type": a.role_type,
            "round_type": a.round_type,
            "company_name": a.company_name,
            "is_approved": a.is_approved,
            "created_at": a.created_at.isoformat() if a.created_at else None,
        }
        for a in answers
    ]


@router.put("/save-answer")
async def save_answer(
    request: SaveAnswerRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Save or update a single Q&A answer for the user's prep library."""
    if request.existing_id:
        # Update existing answer
        result = await db.execute(
            select(ApprovedAnswer)
            .where(ApprovedAnswer.id == request.existing_id)
            .where(ApprovedAnswer.user_id == user.id)
        )
        existing = result.scalar_one_or_none()
        if not existing:
            raise HTTPException(status_code=404, detail="Answer not found")

        existing.question_text = request.question_text
        existing.answer_data = request.answer_data
        existing.role_type = request.role_type
        existing.round_type = request.round_type
        existing.company_name = request.company_name
        existing.is_approved = True
        await db.flush()

        logger.info(f"[PREP] Updated answer {existing.id} for user {user.id}")
        return {"id": existing.id, "status": "updated"}
    else:
        # Create new answer
        new_answer = ApprovedAnswer(
            user_id=user.id,
            question_id=request.question_id,
            question_text=request.question_text,
            answer_data=request.answer_data,
            role_type=request.role_type,
            round_type=request.round_type,
            company_name=request.company_name,
            is_approved=True,
        )
        db.add(new_answer)
        await db.flush()

        logger.info(f"[PREP] Saved new answer {new_answer.id} for user {user.id}")
        return {"id": new_answer.id, "status": "created"}


@router.delete("/saved-answer/{answer_id}")
async def delete_saved_answer(
    answer_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a saved answer."""
    result = await db.execute(
        select(ApprovedAnswer)
        .where(ApprovedAnswer.id == answer_id)
        .where(ApprovedAnswer.user_id == user.id)
    )
    answer = result.scalar_one_or_none()
    if not answer:
        raise HTTPException(status_code=404, detail="Answer not found")

    await db.delete(answer)
    await db.flush()

    logger.info(f"[PREP] Deleted answer {answer_id} for user {user.id}")
    return {"status": "deleted"}


@router.get("/prep-stats")
async def get_prep_stats(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get preparation stats - count of saved answers per role."""
    result = await db.execute(
        select(
            ApprovedAnswer.role_type,
            func.count(ApprovedAnswer.id).label("count"),
        )
        .where(ApprovedAnswer.user_id == user.id)
        .where(ApprovedAnswer.is_approved == True)
        .group_by(ApprovedAnswer.role_type)
    )
    rows = result.all()

    stats = {}
    total = 0
    for role_type, count in rows:
        key = role_type or "unassigned"
        stats[key] = count
        total += count

    return {"by_role": stats, "total": total}


@router.get("/load-for-session")
async def load_for_session(
    role_type: str = Query(...),
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Load optimized profile for a role and return prompt injection text.

    Flow:
    1. Try to use optimized profile (cached or freshly generated)
    2. Fall back to raw Q&A injection if optimization fails
    """
    from services.mongodb_service import get_user_responses as get_q_responses, get_questionnaire as get_q_template
    from services.profile_optimizer import (
        get_optimized_profile,
        optimize_profile,
        collect_raw_qa,
        compute_qa_hash,
    )

    parts = []
    total_count = 0
    used_optimized = False

    # 1. Try optimized profile path
    try:
        q_responses = await get_q_responses(user.id, role_type)
        has_evaluated = (
            q_responses
            and q_responses.get("confidence_score")
            and q_responses["confidence_score"] >= 70
        )

        if has_evaluated:
            # Check if we have a valid cached optimized profile
            existing_profile = await get_optimized_profile(user.id, role_type)

            # Compute current hash to check staleness
            q_template, q_answers, fu_answers, approved = await collect_raw_qa(user.id, role_type, db)
            current_hash = compute_qa_hash(q_answers, fu_answers, approved)

            if existing_profile and existing_profile.get("data_hash") == current_hash:
                # Cache hit — use stored optimized profile
                profile_text = existing_profile["optimized_profile"]
            else:
                # Stale or missing — try to run optimizer
                try:
                    profile_text = await optimize_profile(user.id, role_type, db)
                except Exception as opt_err:
                    logger.warning(f"[PREP] Profile optimization failed, falling back to raw: {opt_err}")
                    profile_text = None

            if profile_text:
                optimized_lines = [
                    "\n\n---",
                    "## DIGITAL TWIN PROFILE (Optimized)",
                    f"Role: {role_type.replace('_', ' ').title()}",
                    f"Confidence Score: {q_responses['confidence_score']}%",
                    "",
                    "The following is a curated, optimized profile of the candidate.",
                    "Use this as the PRIMARY source for ALL interview answers.",
                    "Speak in first person AS this candidate. Use their specific examples, metrics, and stories.",
                    "",
                    profile_text,
                    "",
                    "---",
                ]
                parts.append("\n".join(optimized_lines))
                total_count += 1  # Counts as one optimized block
                used_optimized = True

    except Exception as e:
        logger.warning(f"[PREP] Error in optimized profile path: {e}")

    # 2. Fall back to raw Q&A injection if optimization didn't work
    if not used_optimized:
        try:
            q_responses = await get_q_responses(user.id, role_type)
            if q_responses and q_responses.get("confidence_score") and q_responses["confidence_score"] >= 70:
                q_template = await get_q_template(role_type)
                if q_template:
                    twin_lines = [
                        "\n\n---",
                        "## DIGITAL TWIN PROFILE",
                        f"Role: {role_type.replace('_', ' ').title()}",
                        f"Confidence Score: {q_responses['confidence_score']}%",
                        "",
                        "The candidate has completed a comprehensive intake questionnaire.",
                        "Use the following profile as the PRIMARY source for ALL interview answers.",
                        "Speak in first person AS this candidate. Use their specific examples, metrics, and stories.",
                        "",
                    ]

                    answers = q_responses.get("answers", {})
                    for section in q_template.get("sections", []):
                        twin_lines.append(f"### {section['title']}")
                        for question in section.get("questions", []):
                            q_id = question["question_id"]
                            answer = answers.get(q_id, {})
                            answer_text = answer.get("answer_text", "").strip() if isinstance(answer, dict) else ""
                            if answer_text:
                                twin_lines.append(f"**Q: {question['question_text']}**")
                                twin_lines.append(f"A: {answer_text}")
                                twin_lines.append("")
                                total_count += 1

                    # Include follow-up answers
                    follow_ups = q_responses.get("follow_up_answers", {})
                    if follow_ups:
                        twin_lines.append("### Additional Context (Follow-up Answers)")
                        for fu_id, fu_answer in follow_ups.items():
                            if fu_answer.strip():
                                twin_lines.append(f"A: {fu_answer}")
                                twin_lines.append("")
                                total_count += 1

                    twin_lines.append("---")
                    parts.append("\n".join(twin_lines))
        except Exception as e:
            logger.warning(f"[PREP] Error loading questionnaire responses: {e}")

    # 3. Also load quick Q&A answers from SQLite (supplement, unless already included in optimized profile)
    if not used_optimized:
        result = await db.execute(
            select(ApprovedAnswer)
            .where(ApprovedAnswer.user_id == user.id)
            .where(ApprovedAnswer.role_type == role_type)
            .where(ApprovedAnswer.is_approved == True)
            .order_by(ApprovedAnswer.created_at.asc())
        )
        sql_answers = result.scalars().all()

        if sql_answers:
            answer_dicts = [
                {"question_text": a.question_text, "answer_data": a.answer_data}
                for a in sql_answers
            ]
            qa_injection = _build_prompt_injection(
                answer_dicts,
                company_name=sql_answers[0].company_name or "",
                round_type=sql_answers[0].round_type or "",
            )
            if qa_injection:
                parts.append(qa_injection)
                total_count += len(sql_answers)

    prompt_injection = "\n".join(parts) if parts else ""
    return {"count": total_count, "prompt_injection": prompt_injection}
