"""Seed MongoDB with Principal SWE role questionnaire.

Run with: cd backend && python -m scripts.seed_swe_questionnaire

Creates/updates the Principal SWE intake questionnaire in `role_questionnaires`.
Replaces the older Senior SWE questionnaire under the same role_type key.
"""

import asyncio
import sys
import os
from datetime import datetime, UTC

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


SWE_QUESTIONNAIRE = {
    "role_type": "senior_software_engineer",
    "version": 2,
    "title": "Principal Engineer Identity \u2013 User Intake Question Set",
    "description": "Focused questionnaire to build your Principal SWE digital twin. Covers system design mastery, production resilience, influence without authority, technical DNA, opinionated leadership, and risk guardrails. Designed for L5+ / Staff+ / Principal engineers.",
    "ground_rules": "Answer only from your real experience. If you haven\u2019t done something, say so. I will not invent systems, scale, or impact.",
    "sections": [
        # ================================================================
        # SECTION 1: Engineering Identity
        # ================================================================
        {
            "section_id": "engineering_identity",
            "section_number": 1,
            "title": "Engineering Identity",
            "description": "Your career arc and engineering background.",
            "questions": [
                {
                    "question_id": "s1_q1",
                    "question_text": "Describe your engineering career: companies, roles, product domains, and years.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Company (or anonymized)",
                        "Role / title",
                        "Product domain (e.g., e-commerce, fintech, SaaS)",
                        "Years/months in each role",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Anchors your identity, scope, and career arc. Cover each role separately. Be specific about what you built vs. what the team built.",
                },
            ],
        },
        # ================================================================
        # SECTION 2: The "Magnum Opus" (System Design Anchor)
        # ================================================================
        {
            "section_id": "magnum_opus",
            "section_number": 2,
            "title": "The \"Magnum Opus\" (System Design Anchor)",
            "description": "This answers 80% of System Design questions. The AI uses this single story as the \u201cTemplate\u201d for all design answers, scaling it up or down as needed.",
            "questions": [
                {
                    "question_id": "s2_q1",
                    "question_text": "Describe the single most complex system you designed or re-architected. What was the before/after scale (RPS/Data/Latency), and what was the one critical trade-off you made (e.g., 'We sacrificed strong consistency for availability because...')?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Go deep: before/after scale numbers, the architecture diagram in words, the one trade-off that defined the system. This becomes your anchor story for all system design rounds.",
                },
            ],
        },
        # ================================================================
        # SECTION 3: The "Black Swan" Event (The Failure Story)
        # ================================================================
        {
            "section_id": "black_swan",
            "section_number": 3,
            "title": "The \"Black Swan\" Event (The Failure Story)",
            "description": "Principal Engineers are judged by how they handle catastrophe. This gives the AI your \u201cWar Story\u201d to demonstrate resilience and maturity.",
            "questions": [
                {
                    "question_id": "s3_q1",
                    "question_text": "Describe your worst production outage. What was the root cause (technical vs. process), how did you stabilize it (immediate fix), and what long-term mechanism did you invent to prevent recurrence?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Cover the full timeline: detection, triage, immediate stabilization, root cause analysis, and the permanent fix/mechanism you put in place. Include impact (users affected, revenue lost, duration).",
                },
            ],
        },
        # ================================================================
        # SECTION 4: The "Influence Without Authority" (The Level Check)
        # ================================================================
        {
            "section_id": "influence_without_authority",
            "section_number": 4,
            "title": "The \"Influence Without Authority\" (The Level Check)",
            "description": "This separates \u201cSenior\u201d (who builds what they are told) from \u201cPrincipal\u201d (who guides the ship). The AI needs this to answer \u201cBehavioral/Conflict\u201d questions.",
            "questions": [
                {
                    "question_id": "s4_q1",
                    "question_text": "Give one specific example where you disagreed with a Product Manager or another Senior Engineer on a technical decision, fought for a different approach, and won. What was the technical argument you used?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Be specific: what was the original decision, why you disagreed, the technical argument you made, how you persuaded stakeholders, and what the outcome was. This is your \u201cleadership\u201d proof point.",
                },
            ],
        },
        # ================================================================
        # SECTION 5: The "Technical DNA" (The Stack)
        # ================================================================
        {
            "section_id": "technical_dna",
            "section_number": 5,
            "title": "The \"Technical DNA\" (The Stack)",
            "description": "Prevents the AI from hallucinating expertise you don\u2019t have. It establishes your \u201cTechnical Personality.\u201d",
            "questions": [
                {
                    "question_id": "s5_q1",
                    "question_text": "List your 'Tier 1' skills (I can debug the kernel/internals of this) vs. your 'Tier 2' skills (I can use this to build a feature). Which database/language/framework do you refuse to use, and why?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Tier 1 = deep expertise (you can debug internals, teach others, make architectural decisions). Tier 2 = working knowledge (you can build features but wouldn\u2019t design a system around it). Also list your \u201cnever use\u201d technologies with reasons.",
                },
            ],
        },
        # ================================================================
        # SECTION 6: The "Unpopular Opinion" (The Bar Raiser)
        # ================================================================
        {
            "section_id": "unpopular_opinion",
            "section_number": 6,
            "title": "The \"Unpopular Opinion\" (The Bar Raiser)",
            "description": "Principal Engineers have strong opinions weakly held. This gives the AI the \u201cSpiciness\u201d needed to sound like a leader, not a follower.",
            "questions": [
                {
                    "question_id": "s6_q1",
                    "question_text": "What is a controversial engineering opinion you hold? (e.g., 'Monoliths are better than Microservices for X,' or 'Code coverage is a vanity metric').",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "State the opinion clearly, then back it up with evidence from your experience. The best answers are opinions you\u2019ve acted on and seen results from, not just theoretical hot takes.",
                },
            ],
        },
        # ================================================================
        # SECTION 7: The "Risk & Weakness" (The Guardrails)
        # ================================================================
        {
            "section_id": "risk_and_weakness",
            "section_number": 7,
            "title": "The \"Risk & Weakness\" (The Guardrails)",
            "description": "Sets a \u201cNegative Constraint\u201d so the AI knows to pivot away from these topics immediately if asked.",
            "questions": [
                {
                    "question_id": "s7_q1",
                    "question_text": "What is the one project or time period on your resume that you do not want to discuss in depth? (e.g., 'The Crypto project from 2018').",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 1,
                    "help_text": "The AI will actively pivot away from these topics if the interviewer asks. You can also list areas where you want the AI to say \u2018I haven\u2019t gone deep on this\u2019 rather than fabricate an answer.",
                },
            ],
        },
        # ================================================================
        # SECTION 8: The Development Lifecycle (SDLC)
        # ================================================================
        {
            "section_id": "development_lifecycle",
            "section_number": 8,
            "title": "The Development Lifecycle (SDLC)",
            "description": "Allows the AI to suggest specific tool-based answers rather than generic ones.",
            "questions": [
                {
                    "question_id": "s8_q1",
                    "question_text": "Walk me through your code's journey from 'Local Machine' to 'Production.' What tools do you use for: Local Dev (e.g., Docker/Minikube), CI/CD (e.g., GitHub Actions/GitLab), and Deployment (e.g., K8s/AWS Lambda)?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Be specific about each stage: local environment setup, branching strategy, CI pipeline steps, deployment targets, and rollback procedures. Name exact tools you use daily.",
                },
            ],
        },
        # ================================================================
        # SECTION 9: The Quality Gate (Testing & Review)
        # ================================================================
        {
            "section_id": "quality_gate",
            "section_number": 9,
            "title": "The Quality Gate (Testing & Review)",
            "description": "Anchors your behavioral answers about quality. Prevents the AI from suggesting a process that doesn't match your background.",
            "questions": [
                {
                    "question_id": "s9_q1",
                    "question_text": "What is your team's 'Definition of Done'? Do you enforce TDD, 80% coverage, mandatory peer reviews, or automated integration tests in a staging environment?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Describe your actual quality bar: code review rules, test coverage targets, staging requirements, QA handoff, feature flag discipline. Be honest about what's enforced vs. aspirational.",
                },
            ],
        },
        # ================================================================
        # SECTION 10: The Observability Stack
        # ================================================================
        {
            "section_id": "observability_stack",
            "section_number": 10,
            "title": "The Observability Stack",
            "description": "Knowing your specific tools makes your 'thinking out loud' sound authentic during troubleshooting questions.",
            "questions": [
                {
                    "question_id": "s10_q1",
                    "question_text": "When a service is slow in production, what is the first dashboard or tool you open (e.g., Datadog, Splunk, Grafana)? What specific metric (p99 latency, CPU, memory) do you look at first?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Walk through your actual debugging flow: which tool first, what metric, what you look for next, how you narrow down the root cause. Name specific dashboards, alerts, or runbooks you rely on.",
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


async def seed_swe_questionnaire():
    """Seed the Principal SWE questionnaire into MongoDB (replaces old Senior SWE)."""
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
    role_key = "senior_software_engineer"
    existing = await collection.find_one({"role_type": role_key})
    if existing:
        print(f"\nExisting questionnaire found: '{existing.get('title', '?')}' (version {existing.get('version', '?')})")
        print("Replacing with Principal Engineer Identity questionnaire (version 2)...")
        await collection.delete_one({"role_type": role_key})
        print("Old questionnaire deleted.")

    # Set total question count
    total = _count_questions(SWE_QUESTIONNAIRE)
    SWE_QUESTIONNAIRE["total_questions"] = total

    # Insert
    print(f"\nInserting Principal SWE questionnaire ({total} questions across {len(SWE_QUESTIONNAIRE['sections'])} sections)...")
    result = await collection.insert_one(SWE_QUESTIONNAIRE)
    print(f"Inserted with ID: {result.inserted_id}")

    # Print summary
    print("\nSections:")
    for section in SWE_QUESTIONNAIRE["sections"]:
        q_count = len(section["questions"])
        required = sum(1 for q in section["questions"] if q["is_required"])
        print(f"  {section['section_number']:2d}. {section['title']} ({q_count} questions, {required} required)")

    print(f"\nTotal: {total} questions")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_swe_questionnaire())
