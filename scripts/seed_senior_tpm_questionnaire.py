"""Seed MongoDB with Senior TPM role questionnaire.

Run with: cd backend && python -m scripts.seed_senior_tpm_questionnaire

Creates/updates the Senior TPM intake questionnaire in `role_questionnaires`.
"""

import asyncio
import sys
import os
from datetime import datetime, UTC

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


SENIOR_TPM_QUESTIONNAIRE = {
    "role_type": "senior_technical_program_manager",
    "version": 1,
    "title": "Senior TPM Interview AI \u2013 User Intake Question Set",
    "description": "Focused questionnaire to build your Senior TPM digital twin. 7 high-signal questions that replace 30+ surface-level ones. Covers career spine, flagship program ownership, failure recovery, operating rhythm, technical depth, coding posture, and behavioral pressure points.",
    "ground_rules": "Answer only from your real experience. If you haven\u2019t done something, say so. I will not invent programs, scale, or impact.",
    "sections": [
        # ================================================================
        # SECTION 1: Career Spine
        # ================================================================
        {
            "section_id": "career_spine",
            "section_number": 1,
            "title": "Career Spine",
            "description": "Your TPM career arc, scope growth, and archetype.",
            "questions": [
                {
                    "question_id": "s1_q1",
                    "question_text": "Walk me through your TPM career \u2014 not role by role, but as a story. What kind of programs have you owned, how has your scope grown, and what kind of TPM would you say you are today?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Tell the story of your career trajectory. Cover: types of programs owned, how scope expanded over time, your TPM archetype (execution/strategy/technical), domain and industry context, and self-awareness of growth.",
                },
            ],
        },
        # ================================================================
        # SECTION 2: Flagship Program
        # ================================================================
        {
            "section_id": "flagship_program",
            "section_number": 2,
            "title": "Flagship Program",
            "description": "Your anchor program story \u2014 ownership, risks, dependencies, tracking, and outcome.",
            "questions": [
                {
                    "question_id": "s2_q1",
                    "question_text": "Describe the one program you're most proud of owning. Tell me what it was, what was yours vs. engineering vs. product, the biggest risks and dependencies, how you tracked it week to week, and what actually happened at the end.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Go deep on one program: ownership boundaries (your scope vs. eng vs. PM), risk management approach, dependency tracking, weekly execution cadence, and the real outcome. This becomes your anchor story for most TPM interview rounds.",
                },
            ],
        },
        # ================================================================
        # SECTION 3: Where It Got Hard
        # ================================================================
        {
            "section_id": "where_it_got_hard",
            "section_number": 3,
            "title": "Where It Got Hard",
            "description": "Your failure/recovery story \u2014 conflict, pressure, and demonstrated growth.",
            "questions": [
                {
                    "question_id": "s3_q1",
                    "question_text": "Tell me about a time something went seriously wrong on a program you owned \u2014 a missed deadline, a conflict with engineering, a pushback to leadership, or a mistake you made. What happened, what did you do, and what\u2019s different in how you work because of it?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Cover: the situation, your conflict resolution style, relationship with engineering and leadership, recovery under pressure, demonstrated self-correction, and the behavioral change that resulted. Be honest \u2014 interviewers value authenticity.",
                },
            ],
        },
        # ================================================================
        # SECTION 4: How You Operate
        # ================================================================
        {
            "section_id": "how_you_operate",
            "section_number": 4,
            "title": "How You Operate",
            "description": "Your day-to-day execution methodology \u2014 artifacts, reviews, early warnings, and escalation.",
            "questions": [
                {
                    "question_id": "s4_q1",
                    "question_text": "Walk me through how you actually run a program day-to-day. What artifacts do you create, how do you run reviews, how do you know something\u2019s going off-track before anyone tells you, and when do you escalate vs. handle it yourself?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Describe your operating rhythm: planning artifacts (RACI, tracker, risk log), review cadence (standups, steering committees), early warning signals you watch for, and your escalation judgment framework.",
                },
            ],
        },
        # ================================================================
        # SECTION 5: Technical Honest Self-Assessment
        # ================================================================
        {
            "section_id": "technical_self_assessment",
            "section_number": 5,
            "title": "Technical Honest Self-Assessment",
            "description": "Your technical credibility boundaries \u2014 critical for the AI to not over-claim expertise.",
            "questions": [
                {
                    "question_id": "s5_q1",
                    "question_text": "Be honest about your technical depth. What areas have you worked hands-on in \u2014 even briefly? What do you stay intentionally high-level on? Have you ever co-designed a system, and if so, describe it. What kind of technical questions make you uncomfortable?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Map your technical credibility: hands-on areas (even brief), intentionally high-level areas, system co-design experience, domain vocabulary you're comfortable with, and technical blind spots. The AI uses this to calibrate \u2014 never over-claiming your depth.",
                },
            ],
        },
        # ================================================================
        # SECTION 6: Coding Posture [Conditional]
        # ================================================================
        {
            "section_id": "coding_posture",
            "section_number": 6,
            "title": "Coding Posture",
            "description": "Your coding interview readiness \u2014 show only if job description includes a coding round.",
            "questions": [
                {
                    "question_id": "s6_q1",
                    "question_text": "What\u2019s your honest coding situation? Which language would you use in an interview, how do you approach a problem you haven\u2019t seen before, and what level of correctness do you realistically aim for?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 1,
                    "help_text": "Only answer if your target role includes a coding round. Cover: preferred interview language, problem-solving approach for unfamiliar problems, realistic self-calibration on correctness, and comfort level with complexity discussions. This prevents the AI from over-coaching.",
                },
            ],
        },
        # ================================================================
        # SECTION 7: The Hardest Behavioral Moment
        # ================================================================
        {
            "section_id": "hardest_behavioral_moment",
            "section_number": 7,
            "title": "The Hardest Behavioral Moment",
            "description": "Your single best story for pressure, ambiguity, influence, and crisis management.",
            "questions": [
                {
                    "question_id": "s7_q1",
                    "question_text": "What\u2019s the single hardest situation you\u2019ve faced as a TPM \u2014 extreme ambiguity, having to influence without authority, a forced tradeoff, a broken process you had to fix, or a crisis you had to manage? Pick the one that best shows how you think under pressure.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Pick ONE story that demonstrates: pressure response, influence style (lateral + upward), judgment under ambiguity, process improvement instinct, or crisis management. This becomes your go-to behavioral answer.",
                },
            ],
        },
    ],
    "created_at": datetime.now(UTC),
    "updated_at": datetime.now(UTC),
}


def _count_questions(questionnaire: dict) -> int:
    """Count total questions across all sections."""
    count = 0
    for section in questionnaire["sections"]:
        count += len(section["questions"])
    return count


async def seed_senior_tpm_questionnaire():
    """Seed the Senior TPM questionnaire into MongoDB."""
    if not settings.mongodb_uri:
        print("ERROR: MONGODB_URI not set in .env")
        print("Add MONGODB_URI=mongodb+srv://... to backend/.env")
        sys.exit(1)

    import motor.motor_asyncio

    client = motor.motor_asyncio.AsyncIOMotorClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=10000,
    )
    db = client[settings.mongodb_db_name]

    try:
        await client.admin.command("ping")
        print(f"Connected to MongoDB: {settings.mongodb_db_name}")
    except Exception as e:
        print(f"ERROR: Cannot connect to MongoDB: {e}")
        sys.exit(1)

    collection = db["role_questionnaires"]

    # Ensure indexes exist
    print("\nEnsuring indexes on role_questionnaires...")
    await collection.create_index("role_type", unique=True)
    await collection.create_index("version")

    responses_collection = db["questionnaire_responses"]
    print("Ensuring indexes on questionnaire_responses...")
    await responses_collection.create_index(
        [("user_id", 1), ("role_type", 1)],
        unique=True,
    )
    await responses_collection.create_index("status")
    print("Indexes OK")

    # Check if questionnaire already exists
    role_key = "senior_technical_program_manager"
    existing = await collection.find_one({"role_type": role_key})
    if existing:
        print(f"\nSenior TPM questionnaire already exists (version {existing.get('version', '?')})")
        print("Replacing with latest version...")
        await collection.delete_one({"role_type": role_key})
        print("Existing questionnaire deleted.")

    # Set total question count
    total = _count_questions(SENIOR_TPM_QUESTIONNAIRE)
    SENIOR_TPM_QUESTIONNAIRE["total_questions"] = total

    # Insert
    print(f"\nInserting Senior TPM questionnaire ({total} questions across {len(SENIOR_TPM_QUESTIONNAIRE['sections'])} sections)...")
    result = await collection.insert_one(SENIOR_TPM_QUESTIONNAIRE)
    print(f"Inserted with ID: {result.inserted_id}")

    # Print summary
    print("\nSections:")
    for section in SENIOR_TPM_QUESTIONNAIRE["sections"]:
        q_count = len(section["questions"])
        required = sum(1 for q in section["questions"] if q["is_required"])
        print(f"  {section['section_number']:2d}. {section['title']} ({q_count} questions, {required} required)")

    print(f"\nTotal: {total} questions")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_senior_tpm_questionnaire())
