"""MongoDB service for interview question bank.

Provides:
- Motor async client singleton (init at startup, close at shutdown)
- fetch_likely_questions() with scoring and grouping
- Graceful fallback when MongoDB is unavailable
"""

import logging
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from config import settings

logger = logging.getLogger(__name__)

# Global motor client singleton
_mongo_client = None
_mongo_db = None

# In-memory cache for available questionnaires (rarely changes)
_available_questionnaires_cache: Optional[List[Dict]] = None
_available_questionnaires_cache_ts: float = 0
_AVAILABLE_CACHE_TTL = 300  # 5 minutes


async def init_mongodb() -> None:
    """Initialize MongoDB connection at startup."""
    global _mongo_client, _mongo_db

    if not settings.mongodb_uri:
        logger.info("[MONGODB] No MONGODB_URI configured - question bank disabled")
        return

    try:
        import motor.motor_asyncio

        _mongo_client = motor.motor_asyncio.AsyncIOMotorClient(
            settings.mongodb_uri,
            serverSelectionTimeoutMS=5000,
        )
        _mongo_db = _mongo_client[settings.mongodb_db_name]

        # Test connection
        await _mongo_client.admin.command("ping")
        logger.info(f"[MONGODB] Connected to {settings.mongodb_db_name}")

        # Ensure indexes exist (idempotent — no-op if already created)
        try:
            questionnaires_col = _mongo_db["role_questionnaires"]
            await questionnaires_col.create_index("role_type", unique=True)

            responses_col = _mongo_db["questionnaire_responses"]
            # Match the existing unique compound index from seed scripts
            await responses_col.create_index(
                [("user_id", 1), ("role_type", 1)], unique=True
            )
            await responses_col.create_index("user_id")
            optimized_col = _mongo_db["optimized_profiles"]
            await optimized_col.create_index(
                [("user_id", 1), ("role_type", 1)], unique=True
            )

            logger.info("[MONGODB] Indexes ensured on startup")
        except Exception as idx_err:
            logger.warning(f"[MONGODB] Index creation failed (non-fatal): {idx_err}")
    except Exception as e:
        logger.warning(f"[MONGODB] Failed to connect: {e} - question bank disabled")
        _mongo_client = None
        _mongo_db = None


async def close_mongodb() -> None:
    """Close MongoDB connection at shutdown."""
    global _mongo_client, _mongo_db

    if _mongo_client:
        _mongo_client.close()
        logger.info("[MONGODB] Connection closed")
        _mongo_client = None
        _mongo_db = None


def get_db():
    """Get the MongoDB database instance."""
    return _mongo_db


def _extract_keywords(text: str) -> set:
    """Extract keywords from text for matching."""
    if not text:
        return set()
    # Lowercase, split on non-alpha, filter short words
    words = re.findall(r"[a-z]+", text.lower())
    stop_words = {
        "the", "a", "an", "is", "are", "was", "were", "be", "been",
        "being", "have", "has", "had", "do", "does", "did", "will",
        "would", "could", "should", "may", "might", "shall", "can",
        "to", "of", "in", "for", "on", "with", "at", "by", "from",
        "and", "or", "but", "not", "no", "if", "then", "else",
        "this", "that", "these", "those", "it", "its", "we", "our",
        "you", "your", "they", "their", "he", "she", "him", "her",
    }
    return {w for w in words if len(w) > 2 and w not in stop_words}


def _infer_role_from_jd(jd_text: str) -> Optional[str]:
    """Infer role type from job description text."""
    jd_lower = jd_text.lower()
    role_patterns = {
        "software_engineer": ["software engineer", "swe", "developer", "programmer", "backend", "frontend", "fullstack", "full-stack"],
        "data_scientist": ["data scientist", "machine learning", "ml engineer", "data analyst"],
        "product_manager": ["product manager", "pm", "product owner"],
        "devops": ["devops", "sre", "site reliability", "infrastructure", "platform engineer"],
        "designer": ["designer", "ux", "ui", "product design"],
    }
    for role, patterns in role_patterns.items():
        for pattern in patterns:
            if pattern in jd_lower:
                return role
    return None


def _score_question(
    question: dict,
    company_name: str,
    jd_text: str,
    round_type: str,
    jd_keywords: set,
    inferred_role: Optional[str],
) -> int:
    """Score a question based on relevance.

    Scoring:
    - Company match: +10
    - Round match: +5
    - Role match: +3
    - Tag/keyword overlap: +1 each
    - Recent (<90 days): +2
    - Verified count: +1 each (capped at 5)
    """
    score = 0

    # Company match
    q_company = (question.get("company_name") or "").lower()
    if company_name and q_company == company_name.lower():
        score += 10

    # Round match
    q_round = (question.get("interview_round") or "").lower()
    if round_type and q_round == round_type.lower():
        score += 5

    # Role match
    q_role = (question.get("role") or "").lower()
    if inferred_role and q_role == inferred_role.lower():
        score += 3

    # Tag/keyword overlap
    q_tags = set(t.lower() for t in (question.get("tags") or []))
    overlap = jd_keywords & q_tags
    score += len(overlap)

    # Recency bonus
    last_seen = question.get("last_seen")
    if last_seen and isinstance(last_seen, datetime):
        if datetime.utcnow() - last_seen < timedelta(days=90):
            score += 2

    # Verified count bonus
    verified = question.get("verified_count", 0)
    score += min(verified, 5)

    return score


async def fetch_likely_questions(
    company_name: str = "",
    jd_text: str = "",
    round_type: str = "",
    limit: int = 6,
) -> Dict[str, List[dict]]:
    """Fetch and score likely interview questions.

    Uses broad query with client-side scoring and tier grouping.

    Args:
        company_name: Target company name
        jd_text: Job description text for keyword matching
        round_type: Interview round type
        limit: Maximum total questions to return

    Returns:
        Dict with tiers: {"must_ask": [...], "high_probability": [...], "stretch": [...]}
    """
    if _mongo_db is None:
        logger.info("[MONGODB] Not connected - returning empty questions")
        return {"must_ask": [], "high_probability": [], "stretch": []}

    try:
        collection = _mongo_db["questions"]
        jd_keywords = _extract_keywords(jd_text)
        inferred_role = _infer_role_from_jd(jd_text)

        # Build broad query - company OR round type OR general
        or_conditions: List[dict] = []
        if company_name:
            or_conditions.append({"company_name": {"$regex": company_name, "$options": "i"}})
        if round_type:
            or_conditions.append({"interview_round": {"$regex": round_type, "$options": "i"}})
        # Always include general questions as fallback
        or_conditions.append({"company_name": "General"})

        query = {"$or": or_conditions} if or_conditions else {}

        # Fetch broad set of candidates
        cursor = collection.find(query).limit(50)
        questions = await cursor.to_list(length=50)

        if not questions:
            # Fallback: get any questions
            cursor = collection.find({}).limit(limit)
            questions = await cursor.to_list(length=limit)

        # Score each question
        scored = []
        for q in questions:
            score = _score_question(q, company_name, jd_text, round_type, jd_keywords, inferred_role)
            scored.append((score, q))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)

        # Take top N
        top = scored[:limit]
        if not top:
            return {"must_ask": [], "high_probability": [], "stretch": []}

        # Group into tiers
        # Must-Ask: top 20%, High Probability: next 50%, Stretch: rest
        n = len(top)
        must_ask_count = max(1, n // 5)
        high_prob_count = max(1, n // 2)

        must_ask = []
        high_probability = []
        stretch = []

        for i, (score, q) in enumerate(top):
            # Serialize for JSON
            q_data = {
                "id": str(q.get("_id", "")),
                "question_text": q.get("question_text", ""),
                "company_name": q.get("company_name", ""),
                "interview_round": q.get("interview_round", ""),
                "role": q.get("role", ""),
                "tags": q.get("tags", []),
                "difficulty": q.get("difficulty", "medium"),
                "verified_count": q.get("verified_count", 0),
                "score": score,
            }
            if i < must_ask_count:
                q_data["tier"] = "must_ask"
                must_ask.append(q_data)
            elif i < must_ask_count + high_prob_count:
                q_data["tier"] = "high_probability"
                high_probability.append(q_data)
            else:
                q_data["tier"] = "stretch"
                stretch.append(q_data)

        logger.info(
            f"[MONGODB] Questions fetched: {len(must_ask)} must-ask, "
            f"{len(high_probability)} high-prob, {len(stretch)} stretch "
            f"(company={company_name}, round={round_type})"
        )

        return {
            "must_ask": must_ask,
            "high_probability": high_probability,
            "stretch": stretch,
        }

    except Exception as e:
        logger.error(f"[MONGODB] Error fetching questions: {e}")
        return {"must_ask": [], "high_probability": [], "stretch": []}


_ROLE_SYNONYMS: Dict[str, List[str]] = {
    "software_engineer": ["software_engineer", "swe", "developer", "sde"],
    "senior_swe_l3_l5": ["senior_swe_l3_l5", "senior_software_engineer", "software_engineer", "swe"],
    "senior_software_engineer": ["senior_software_engineer", "principal_software_engineer", "software_engineer", "swe"],
    "staff_engineer": ["staff_engineer", "principal_software_engineer", "senior_software_engineer"],
    "product_manager": ["product_manager", "pm", "product_owner"],
    "technical_program_manager": ["technical_program_manager", "tpm", "program_manager"],
    "senior_technical_program_manager": ["senior_technical_program_manager", "technical_program_manager", "tpm"],
    "data_scientist": ["data_scientist", "ds", "data_analyst"],
    "data_engineer": ["data_engineer", "de", "data_scientist"],
    "machine_learning_engineer": ["machine_learning_engineer", "ml_engineer", "ml", "data_scientist"],
    "engineering_manager": ["engineering_manager", "em", "manager"],
    "other": [],
}


async def fetch_questions_by_role(role_type: str) -> Dict[str, List[dict]]:
    """Fetch all interview questions for a specific role, grouped by tier.

    Unlike fetch_likely_questions (which scores by company/JD), this returns
    questions tagged with the given role (or its synonyms), sorted by
    verified_count. Falls back to general/untagged questions if no role
    matches exist, so the dropdown is never empty when the collection has data.

    Args:
        role_type: Role identifier (e.g., 'software_engineer')

    Returns:
        Dict with tiers: {"must_ask": [...], "high_probability": [...], "stretch": [...]}
    """
    empty = {"must_ask": [], "high_probability": [], "stretch": []}

    if _mongo_db is None:
        logger.info("[MONGODB] Not connected - returning empty questions")
        return empty

    try:
        collection = _mongo_db["questions"]

        # Primary query: role matches role_type or any of its synonyms (case-insensitive)
        synonyms = _ROLE_SYNONYMS.get(role_type, [role_type]) or [role_type]
        role_pattern = "|".join(f"^{re.escape(s)}$" for s in synonyms)
        cursor = collection.find(
            {"role": {"$regex": role_pattern, "$options": "i"}}
        ).sort("verified_count", -1).limit(100)
        questions = await cursor.to_list(length=100)

        # Fallback: if no role-specific questions, fetch general/untagged ones so
        # the dropdown still has content
        if not questions:
            logger.info(
                f"[MONGODB] No role-specific questions for '{role_type}' — "
                f"falling back to general questions"
            )
            fallback_cursor = collection.find(
                {
                    "$or": [
                        {"role": {"$in": [None, ""]}},
                        {"role": {"$exists": False}},
                        {"company_name": {"$regex": "^general$", "$options": "i"}},
                    ]
                }
            ).sort("verified_count", -1).limit(100)
            questions = await fallback_cursor.to_list(length=100)

        if not questions:
            logger.info(f"[MONGODB] No questions found for role: {role_type} (no fallback either)")
            return empty

        # Group into tiers by position: top 20% must_ask, next 50% high_prob, rest stretch
        n = len(questions)
        must_ask_count = max(1, n // 5)
        high_prob_count = max(1, n // 2)

        must_ask = []
        high_probability = []
        stretch = []

        for i, q in enumerate(questions):
            q_data = {
                "id": str(q.get("_id", "")),
                "question_text": q.get("question_text", ""),
                "company_name": q.get("company_name", ""),
                "interview_round": q.get("interview_round", ""),
                "role": q.get("role", ""),
                "tags": q.get("tags", []),
                "difficulty": q.get("difficulty", "medium"),
                "verified_count": q.get("verified_count", 0),
            }
            if i < must_ask_count:
                q_data["tier"] = "must_ask"
                must_ask.append(q_data)
            elif i < must_ask_count + high_prob_count:
                q_data["tier"] = "high_probability"
                high_probability.append(q_data)
            else:
                q_data["tier"] = "stretch"
                stretch.append(q_data)

        logger.info(
            f"[MONGODB] Questions by role '{role_type}': {len(must_ask)} must-ask, "
            f"{len(high_probability)} high-prob, {len(stretch)} stretch"
        )

        return {
            "must_ask": must_ask,
            "high_probability": high_probability,
            "stretch": stretch,
        }

    except Exception as e:
        logger.error(f"[MONGODB] Error fetching questions by role: {e}")
        return empty


# === Questionnaire Functions ===


async def get_questionnaire(role_type: str) -> Optional[Dict]:
    """Fetch the questionnaire template for a role.

    Args:
        role_type: e.g. 'technical_program_manager'

    Returns:
        Questionnaire document or None if not found.
    """
    if _mongo_db is None:
        logger.info("[MONGODB] Not connected - cannot fetch questionnaire")
        return None

    try:
        collection = _mongo_db["role_questionnaires"]
        doc = await collection.find_one(
            {"role_type": role_type},
            {"_id": 0},  # Exclude MongoDB ObjectId
        )
        if doc:
            logger.info(f"[MONGODB] Questionnaire found for {role_type}: {doc.get('total_questions', 0)} questions")
        else:
            logger.info(f"[MONGODB] No questionnaire found for {role_type}")
        return doc
    except Exception as e:
        logger.error(f"[MONGODB] Error fetching questionnaire: {e}")
        return None


async def get_available_questionnaires() -> List[Dict]:
    """Get list of all available questionnaire roles with metadata.

    Returns cached result (TTL 5 min) since this data rarely changes.

    Returns:
        List of {role_type, title, total_questions, version}.
    """
    global _available_questionnaires_cache, _available_questionnaires_cache_ts

    if _mongo_db is None:
        return []

    # Return cached result if still fresh
    if (
        _available_questionnaires_cache is not None
        and (time.monotonic() - _available_questionnaires_cache_ts) < _AVAILABLE_CACHE_TTL
    ):
        return _available_questionnaires_cache

    try:
        collection = _mongo_db["role_questionnaires"]
        cursor = collection.find(
            {},
            {"_id": 0, "role_type": 1, "title": 1, "total_questions": 1, "version": 1, "description": 1},
        )
        result = await cursor.to_list(length=50)
        _available_questionnaires_cache = result
        _available_questionnaires_cache_ts = time.monotonic()
        return result
    except Exception as e:
        logger.error(f"[MONGODB] Error listing questionnaires: {e}")
        return []


async def get_user_responses(user_id: str, role_type: str) -> Optional[Dict]:
    """Fetch user's questionnaire responses for a role.

    Args:
        user_id: User UUID string
        role_type: Role type string

    Returns:
        Response document or None if not started.
    """
    if _mongo_db is None:
        return None

    try:
        collection = _mongo_db["questionnaire_responses"]
        doc = await collection.find_one(
            {"user_id": user_id, "role_type": role_type},
            {"_id": 0},
        )
        if doc:
            logger.info(
                f"[MONGODB] Responses found for user {user_id[:8]}... role {role_type}: "
                f"{doc.get('answered_count', 0)}/{doc.get('total_questions', 0)}"
            )
        return doc
    except Exception as e:
        logger.error(f"[MONGODB] Error fetching responses: {e}")
        return None


async def save_user_responses(
    user_id: str,
    role_type: str,
    answers: Dict[str, Any],
    total_questions: int,
) -> Dict:
    """Save or update user's questionnaire answers.

    Uses upsert pattern - creates new doc if first save, updates if exists.

    Args:
        user_id: User UUID string
        role_type: Role type string
        answers: Dict of question_id -> {answer_text, last_updated}
        total_questions: Total questions in the questionnaire

    Returns:
        Updated response summary.
    """
    if _mongo_db is None:
        raise RuntimeError("MongoDB not connected")

    try:
        collection = _mongo_db["questionnaire_responses"]
        now = datetime.utcnow()

        # Count non-empty answers
        answered_count = sum(
            1 for a in answers.values()
            if a.get("answer_text", "").strip()
        )

        # Upsert: create or update
        result = await collection.update_one(
            {"user_id": user_id, "role_type": role_type},
            {
                "$set": {
                    "answers": answers,
                    "answered_count": answered_count,
                    "total_questions": total_questions,
                    "last_saved_at": now,
                },
                "$setOnInsert": {
                    "user_id": user_id,
                    "role_type": role_type,
                    "questionnaire_version": 1,
                    "status": "in_progress",
                    "confidence_score": None,
                    "evaluation_result": None,
                    "follow_up_questions": [],
                    "follow_up_answers": {},
                    "started_at": now,
                    "submitted_at": None,
                    "evaluated_at": None,
                },
            },
            upsert=True,
        )

        logger.info(
            f"[MONGODB] Saved responses for user {user_id[:8]}... role {role_type}: "
            f"{answered_count}/{total_questions} answered"
        )

        return {
            "status": "saved",
            "answered_count": answered_count,
            "total_questions": total_questions,
            "last_saved_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"[MONGODB] Error saving responses: {e}")
        raise


async def submit_questionnaire(user_id: str, role_type: str) -> Dict:
    """Mark questionnaire as completed/submitted for evaluation.

    Args:
        user_id: User UUID string
        role_type: Role type string

    Returns:
        Updated status.
    """
    if _mongo_db is None:
        raise RuntimeError("MongoDB not connected")

    try:
        collection = _mongo_db["questionnaire_responses"]
        now = datetime.utcnow()

        result = await collection.update_one(
            {"user_id": user_id, "role_type": role_type},
            {
                "$set": {
                    "status": "completed",
                    "submitted_at": now,
                },
            },
        )

        if result.matched_count == 0:
            return {"error": "No responses found to submit"}

        logger.info(f"[MONGODB] Questionnaire submitted for user {user_id[:8]}... role {role_type}")
        return {"status": "completed", "submitted_at": now.isoformat()}
    except Exception as e:
        logger.error(f"[MONGODB] Error submitting questionnaire: {e}")
        raise


async def save_evaluation_result(
    user_id: str,
    role_type: str,
    confidence_score: float,
    evaluation_result: Dict,
    follow_up_questions: List[Dict],
) -> Dict:
    """Save AI evaluation result for a questionnaire.

    Args:
        user_id: User UUID string
        role_type: Role type string
        confidence_score: 0-100 confidence percentage
        evaluation_result: Full evaluation details (strengths, gaps, etc.)
        follow_up_questions: List of follow-up questions if confidence < 70%

    Returns:
        Updated status.
    """
    if _mongo_db is None:
        raise RuntimeError("MongoDB not connected")

    try:
        collection = _mongo_db["questionnaire_responses"]
        now = datetime.utcnow()

        status = "evaluated" if confidence_score >= 70 else "needs_follow_up"

        result = await collection.update_one(
            {"user_id": user_id, "role_type": role_type},
            {
                "$set": {
                    "confidence_score": confidence_score,
                    "evaluation_result": evaluation_result,
                    "follow_up_questions": follow_up_questions,
                    "status": status,
                    "evaluated_at": now,
                },
            },
        )

        logger.info(
            f"[MONGODB] Evaluation saved for user {user_id[:8]}... role {role_type}: "
            f"confidence={confidence_score}%, status={status}"
        )

        return {
            "status": status,
            "confidence_score": confidence_score,
            "evaluated_at": now.isoformat(),
        }
    except Exception as e:
        logger.error(f"[MONGODB] Error saving evaluation: {e}")
        raise


async def save_follow_up_answers(
    user_id: str,
    role_type: str,
    follow_up_answers: Dict[str, str],
) -> Dict:
    """Save answers to follow-up questions.

    Args:
        user_id: User UUID string
        role_type: Role type string
        follow_up_answers: Dict of follow_up_id -> answer_text

    Returns:
        Updated response summary.
    """
    if _mongo_db is None:
        raise RuntimeError("MongoDB not connected")

    try:
        collection = _mongo_db["questionnaire_responses"]
        now = datetime.utcnow()

        result = await collection.update_one(
            {"user_id": user_id, "role_type": role_type},
            {
                "$set": {
                    "follow_up_answers": follow_up_answers,
                    "status": "completed",
                    "last_saved_at": now,
                },
            },
        )

        logger.info(
            f"[MONGODB] Follow-up answers saved for user {user_id[:8]}... role {role_type}: "
            f"{len(follow_up_answers)} answers"
        )

        return {
            "status": "saved",
            "follow_up_count": len(follow_up_answers),
        }
    except Exception as e:
        logger.error(f"[MONGODB] Error saving follow-up answers: {e}")
        raise


async def get_all_user_progress(user_id: str) -> List[Dict]:
    """Get questionnaire progress for all roles for a user.

    Returns:
        List of {role_type, status, answered_count, total_questions, confidence_score}.
    """
    if _mongo_db is None:
        return []

    try:
        collection = _mongo_db["questionnaire_responses"]
        cursor = collection.find(
            {"user_id": user_id},
            {
                "_id": 0,
                "role_type": 1,
                "status": 1,
                "answered_count": 1,
                "total_questions": 1,
                "confidence_score": 1,
                "last_saved_at": 1,
            },
        )
        results = await cursor.to_list(length=50)

        # Convert datetime fields to ISO strings for JSON serialization
        for r in results:
            if r.get("last_saved_at"):
                r["last_saved_at"] = r["last_saved_at"].isoformat()

        return results
    except Exception as e:
        logger.error(f"[MONGODB] Error fetching user progress: {e}")
        return []
