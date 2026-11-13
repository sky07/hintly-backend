"""Seed MongoDB with Software Engineer (SDE I-II) role questionnaire.

Run with: cd backend && python -m scripts.seed_sde_questionnaire

Creates/updates the SDE I-II intake questionnaire in `role_questionnaires`.
"""

import asyncio
import sys
import os
from datetime import datetime, UTC

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


SDE_QUESTIONNAIRE = {
    "role_type": "software_engineer",
    "version": 1,
    "title": "Software Engineer (SDE I-II) Interview AI – User Intake Question Set",
    "description": "Focused questionnaire to build your SDE I-II digital twin. Covers execution, code fluency, debugging, learning agility, collaboration, and code quality. Designed for L3/L4 engineers with 0–4 years of experience.",
    "ground_rules": "Answer only from your real experience. If you haven't done something, say so. I will not invent systems, scale, or impact.",
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
        # SECTION 2: The "Complex Feature"
        # ================================================================
        {
            "section_id": "complex_feature",
            "section_number": 2,
            "title": "The \"Complex Feature\"",
            "description": "Proves raw coding ability. The AI needs to know you can handle complexity.",
            "questions": [
                {
                    "question_id": "s2_q1",
                    "question_text": "What is the most complicated piece of logic (algorithm or feature) you have implemented personally? What made it hard (e.g., edge cases, race conditions, complex regex)?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Be specific about the complexity — edge cases, race conditions, algorithm choice, data structures used. This is your strongest coding story.",
                },
            ],
        },
        # ================================================================
        # SECTION 3: The "Impossible Bug"
        # ================================================================
        {
            "section_id": "impossible_bug",
            "section_number": 3,
            "title": "The \"Impossible Bug\"",
            "description": "Proves debugging grit and familiarity with tools.",
            "questions": [
                {
                    "question_id": "s3_q1",
                    "question_text": "Tell me about a bug that took you days to figure out. What was the specific tool or technique (e.g., git bisect, heap dump analysis) that finally revealed the issue?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Describe the symptoms, your debugging process step by step, and the 'aha' moment. Mention specific tools: debugger, profiler, git bisect, log analysis, etc.",
                },
            ],
        },
        # ================================================================
        # SECTION 4: The "Learning Curve"
        # ================================================================
        {
            "section_id": "learning_curve",
            "section_number": 4,
            "title": "The \"Learning Curve\"",
            "description": "Mid-level engineers are hired for potential and adaptability.",
            "questions": [
                {
                    "question_id": "s4_q1",
                    "question_text": "Describe a time you had to learn a new language, framework, or codebase completely from scratch to deliver a task. How did you approach the learning process?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Walk through your learning strategy: docs, tutorials, reading source code, pair programming, building small prototypes. How long did it take to become productive?",
                },
            ],
        },
        # ================================================================
        # SECTION 5: The "Collaboration"
        # ================================================================
        {
            "section_id": "collaboration",
            "section_number": 5,
            "title": "The \"Collaboration\"",
            "description": "Shows self-awareness and ability to work in a team (crucial for junior/mid roles).",
            "questions": [
                {
                    "question_id": "s5_q1",
                    "question_text": "Tell me about a time you misunderstood a requirement or blocked a teammate. How did you resolve the communication gap?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Be honest about the miscommunication. What went wrong, how you detected it, and what you did to fix it. Show self-awareness and growth.",
                },
            ],
        },
        # ================================================================
        # SECTION 6: The "Code Quality"
        # ================================================================
        {
            "section_id": "code_quality",
            "section_number": 6,
            "title": "The \"Code Quality\"",
            "description": "Shows you care about craftsmanship, not just \"making it work.\"",
            "questions": [
                {
                    "question_id": "s6_q1",
                    "question_text": "What is one specific thing you look for when reviewing someone else's code? (e.g., Variable naming, lack of tests, complexity).",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Pick ONE thing you genuinely care about in code reviews and explain why. This reveals your engineering values.",
                },
            ],
        },
        # ================================================================
        # SECTION 7: The "Preferred Language"
        # ================================================================
        {
            "section_id": "preferred_language",
            "section_number": 7,
            "title": "The \"Preferred Language\"",
            "description": "Safety. The AI needs to know never to suggest wrong syntax for your language.",
            "questions": [
                {
                    "question_id": "s7_q1",
                    "question_text": "Which one language are you 100% fluent in for LeetCode/Coding rounds? What are its standard libraries you use most?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Name your go-to interview language and the standard libraries/packages you rely on. E.g., 'Python — collections, itertools, heapq, defaultdict' or 'Java — HashMap, Arrays, Collections, Stream API'.",
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


async def seed_sde_questionnaire():
    """Seed the SDE I-II questionnaire into MongoDB."""
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

    # Check if SDE questionnaire already exists
    role_key = "software_engineer"
    existing = await collection.find_one({"role_type": role_key})
    if existing:
        print(f"\nSDE I-II questionnaire already exists (version {existing.get('version', '?')})")
        response = input("Do you want to replace it? (y/N): ").strip().lower()
        if response == "y":
            await collection.delete_one({"role_type": role_key})
            print("Existing questionnaire deleted.")
        else:
            print("Skipping. Exiting.")
            client.close()
            return

    # Set total question count
    total = _count_questions(SDE_QUESTIONNAIRE)
    SDE_QUESTIONNAIRE["total_questions"] = total

    # Insert
    print(f"\nInserting SDE I-II questionnaire ({total} questions across {len(SDE_QUESTIONNAIRE['sections'])} sections)...")
    result = await collection.insert_one(SDE_QUESTIONNAIRE)
    print(f"Inserted with ID: {result.inserted_id}")

    # Print summary
    print("\nSections:")
    for section in SDE_QUESTIONNAIRE["sections"]:
        q_count = len(section["questions"])
        required = sum(1 for q in section["questions"] if q["is_required"])
        print(f"  {section['section_number']:2d}. {section['title']} ({q_count} questions, {required} required)")

    print(f"\nTotal: {total} questions")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_sde_questionnaire())
