"""Seed MongoDB with Senior SWE (L3-L5) role questionnaire.

Run with: cd backend && python -m scripts.seed_senior_swe_l3l5_questionnaire

Creates/updates the Senior SWE (L3-L5) intake questionnaire in `role_questionnaires`.
"""

import asyncio
import sys
import os
from datetime import datetime, UTC

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


SENIOR_SWE_L3L5_QUESTIONNAIRE = {
    "role_type": "senior_swe_l3_l5",
    "version": 1,
    "title": "Senior SWE (L3-L5) Interview AI \u2013 User Intake Question Set",
    "description": "Focused questionnaire to build your Senior SWE digital twin. Covers ownership, service-level architecture, mentorship, code quality, production operations, and technical leadership. Designed for L3\u2013L5 engineers with 3\u201310+ years of experience.",
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
        # SECTION 2: The "End-to-End" Ownership
        # ================================================================
        {
            "section_id": "end_to_end_ownership",
            "section_number": 2,
            "title": "The \"End-to-End\" Ownership",
            "description": "Seniors don\u2019t just write code; they own the outcome. The AI needs to know what you delivered and how you measured success.",
            "questions": [
                {
                    "question_id": "s2_q1",
                    "question_text": "Describe a specific service or major feature you owned from design to deployment. What was the hardest technical requirement (e.g., latency under 50ms) and how did you verify it in production?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Cover the full lifecycle: requirements gathering, design decisions, implementation, testing, deployment, and production verification. Include specific metrics you tracked.",
                },
            ],
        },
        # ================================================================
        # SECTION 3: The "Refactor" (Technical Debt)
        # ================================================================
        {
            "section_id": "refactor_technical_debt",
            "section_number": 3,
            "title": "The \"Refactor\" (Technical Debt)",
            "description": "Proves \u201cAgency.\u201d Seniors find problems; Juniors wait for tickets.",
            "questions": [
                {
                    "question_id": "s3_q1",
                    "question_text": "Tell me about a time you identified bad code or a bottleneck in an existing system and fixed it without being asked. What was the impact (e.g., reduced build time by 50%)?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Describe how you spotted the problem, how you justified the work (to yourself or your team), what you changed, and the measurable impact. Concrete before/after numbers make this story strong.",
                },
            ],
        },
        # ================================================================
        # SECTION 4: The "Technical Pushback"
        # ================================================================
        {
            "section_id": "technical_pushback",
            "section_number": 4,
            "title": "The \"Technical Pushback\"",
            "description": "Seniors protect the codebase. The AI needs an example of you being a \u201cGuardian of Quality.\u201d",
            "questions": [
                {
                    "question_id": "s4_q1",
                    "question_text": "Describe a time you pushed back on a Product Manager or Design requirement because it was technically unsafe or too costly. What alternative solution did you propose?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "What was the original ask? Why was it problematic (security, performance, maintainability, cost)? How did you frame the pushback constructively? What was the outcome?",
                },
            ],
        },
        # ================================================================
        # SECTION 5: The "Production War Story"
        # ================================================================
        {
            "section_id": "production_war_story",
            "section_number": 5,
            "title": "The \"Production War Story\"",
            "description": "Shows operational maturity.",
            "questions": [
                {
                    "question_id": "s5_q1",
                    "question_text": "Describe a production incident where you led the debugging. How did you find the root cause (logs, metrics, intuition) and what did you do to stabilize the system immediately?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Walk through the incident timeline: alert/discovery, triage, investigation, root cause, immediate fix, and long-term prevention. Mention specific tools (Datadog, Splunk, Grafana, etc.) and your role in the incident.",
                },
            ],
        },
        # ================================================================
        # SECTION 6: The "Force Multiplier" (Mentorship)
        # ================================================================
        {
            "section_id": "force_multiplier_mentorship",
            "section_number": 6,
            "title": "The \"Force Multiplier\" (Mentorship)",
            "description": "Seniors must scale themselves. The AI needs a \u201cTeam Player\u201d story.",
            "questions": [
                {
                    "question_id": "s6_q1",
                    "question_text": "Give an example of how you helped a junior engineer get unblocked or grow. Did you do code reviews, pair programming, or write documentation?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Be specific: what was the junior stuck on, what approach did you take (pairing, review, doc, 1:1), and what was the result? How did this scale your team\u2019s output?",
                },
            ],
        },
        # ================================================================
        # SECTION 7: The Stack & Tools
        # ================================================================
        {
            "section_id": "stack_and_tools",
            "section_number": 7,
            "title": "The Stack & Tools",
            "description": "Establishes your \u201cDefault\u201d toolkit for System Design rounds.",
            "questions": [
                {
                    "question_id": "s7_q1",
                    "question_text": "What is your 'Go-To' stack for building a microservice quickly? Which testing framework or CI/CD tool do you insist on using?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "List your default choices: language, framework, database, message queue, containerization, CI/CD, testing framework, monitoring. Explain why you reach for these tools first.",
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


async def seed_senior_swe_l3l5_questionnaire():
    """Seed the Senior SWE (L3-L5) questionnaire into MongoDB."""
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
    role_key = "senior_swe_l3_l5"
    existing = await collection.find_one({"role_type": role_key})
    if existing:
        print(f"\nSenior SWE (L3-L5) questionnaire already exists (version {existing.get('version', '?')})")
        response = input("Do you want to replace it? (y/N): ").strip().lower()
        if response == "y":
            await collection.delete_one({"role_type": role_key})
            print("Existing questionnaire deleted.")
        else:
            print("Skipping. Exiting.")
            client.close()
            return

    # Set total question count
    total = _count_questions(SENIOR_SWE_L3L5_QUESTIONNAIRE)
    SENIOR_SWE_L3L5_QUESTIONNAIRE["total_questions"] = total

    # Insert
    print(f"\nInserting Senior SWE (L3-L5) questionnaire ({total} questions across {len(SENIOR_SWE_L3L5_QUESTIONNAIRE['sections'])} sections)...")
    result = await collection.insert_one(SENIOR_SWE_L3L5_QUESTIONNAIRE)
    print(f"Inserted with ID: {result.inserted_id}")

    # Print summary
    print("\nSections:")
    for section in SENIOR_SWE_L3L5_QUESTIONNAIRE["sections"]:
        q_count = len(section["questions"])
        required = sum(1 for q in section["questions"] if q["is_required"])
        print(f"  {section['section_number']:2d}. {section['title']} ({q_count} questions, {required} required)")

    print(f"\nTotal: {total} questions")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_senior_swe_l3l5_questionnaire())
