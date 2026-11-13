"""Seed MongoDB with TPM role questionnaire.

Run with: cd backend && python -m scripts.seed_tpm_questionnaire

Creates the `role_questionnaires` collection and inserts the TPM intake questionnaire.
"""

import asyncio
import sys
import os
from datetime import datetime

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


TPM_QUESTIONNAIRE = {
    "role_type": "technical_program_manager",
    "version": 1,
    "title": "TPM Interview AI \u2013 User Intake Question Set",
    "description": "Comprehensive questionnaire to build your TPM digital twin. Answer honestly based only on what you've actually done.",
    "ground_rules": "Answer honestly based only on what you've actually done. If you haven't done something, say so. I won't make things up\u2014I'll adapt answers to your real experience.",
    "sections": [
        # --- SECTION 1: Career Snapshot ---
        {
            "section_id": "career_snapshot",
            "section_number": 1,
            "title": "Career Snapshot (Identity Anchor)",
            "description": "Understanding your career trajectory and TPM identity.",
            "questions": [
                {
                    "question_id": "s1_q1",
                    "question_text": "Walk me through your career as a TPM, role by role.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Company (or anonymized)",
                        "Years/months in each role",
                        "Product/domain",
                        "Team size you worked with",
                        "Your core ownership",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Cover each role separately. Be specific about what you owned vs. what others owned.",
                },
                {
                    "question_id": "s1_q2",
                    "question_text": "Which role best represents your current TPM skill level? Why?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Pick the role where you felt most effective and explain what made it your best fit.",
                },
                {
                    "question_id": "s1_q3",
                    "question_text": "What kind of TPM are you closer to?",
                    "question_type": "multi_select",
                    "options": [
                        "Execution-heavy",
                        "Technical/architecture-heavy",
                        "Stakeholder/strategy-heavy",
                    ],
                    "sub_prompts": ["You can pick more than one, but rank them."],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Rank from strongest to least strong. This shapes how the AI positions your answers.",
                },
            ],
        },
        # --- SECTION 2: Program Ownership ---
        {
            "section_id": "program_ownership",
            "section_number": 2,
            "title": "Program Ownership (Most Important)",
            "description": "Deep dive into your major programs. Minimum 3, ideal 5\u20137 programs.",
            "questions": [
                {
                    "question_id": "s2_q1",
                    "question_text": "Describe one major program you owned end-to-end.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "What problem were you solving?",
                        "Why was it important to the business?",
                        "Timeline & constraints",
                        "Teams involved",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "This is the MOST important section. Be as detailed as possible. Repeat for 3-7 programs.",
                    "repeatable": True,
                    "min_entries": 3,
                    "max_entries": 7,
                },
                {
                    "question_id": "s2_q2",
                    "question_text": "What exactly was your responsibility vs engineering/product?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Draw clear lines. Interviewers test for ownership clarity.",
                },
                {
                    "question_id": "s2_q3",
                    "question_text": "What were the biggest dependencies?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Internal teams",
                        "External vendors",
                        "Compliance/legal/security",
                    ],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Dependencies are a TPM's bread and butter. Be specific.",
                },
                {
                    "question_id": "s2_q4",
                    "question_text": "What were the top 3 risks at the start? Which ones materialized?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Risk identification and management is core TPM. Show you anticipated issues.",
                },
                {
                    "question_id": "s2_q5",
                    "question_text": "How did you track progress day to day and week to week?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 5,
                    "help_text": "Tools, rituals, dashboards, reports \u2013 what was your system?",
                },
                {
                    "question_id": "s2_q6",
                    "question_text": "What was the final outcome?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "On time / delayed?",
                        "Scope cut?",
                        "Metrics or impact if available",
                    ],
                    "is_required": True,
                    "order": 6,
                    "help_text": "Numbers and outcomes make stories credible. Even estimates help.",
                },
                {
                    "question_id": "s2_q7",
                    "question_text": "What would you do differently if you ran this again?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 7,
                    "help_text": "Shows self-awareness and growth mindset.",
                },
            ],
        },
        # --- SECTION 3: Failures, Delays & Hard Lessons ---
        {
            "section_id": "failures_lessons",
            "section_number": 3,
            "title": "Failures, Delays & Hard Lessons (Credibility Booster)",
            "description": "Your failures and how you handled them. This builds credibility.",
            "questions": [
                {
                    "question_id": "s3_q1",
                    "question_text": "Tell me about a program that missed its deadline.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Why did it slip?",
                        "What signals did you miss early on?",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Everyone has misses. What matters is the analysis and learning.",
                },
                {
                    "question_id": "s3_q2",
                    "question_text": "Describe a time you had to push back on leadership.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Shows backbone and influence skills. How did you frame it?",
                },
                {
                    "question_id": "s3_q3",
                    "question_text": "Describe a time engineering disagreed with your plan. How did you resolve it?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "TPM-Engineering tension is a classic interview topic.",
                },
                {
                    "question_id": "s3_q4",
                    "question_text": "Biggest mistake you've made as a TPM? What changed in your behavior afterward?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Be real. The behavioral change afterward is what they're looking for.",
                },
            ],
        },
        # --- SECTION 4: Execution & Delivery Style ---
        {
            "section_id": "execution_style",
            "section_number": 4,
            "title": "Execution & Delivery Style",
            "description": "How you run programs day-to-day.",
            "questions": [
                {
                    "question_id": "s4_q1",
                    "question_text": "How do you typically break down a vague goal into an executable plan?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Walk through your process step by step.",
                },
                {
                    "question_id": "s4_q2",
                    "question_text": "What artifacts do you usually create?",
                    "question_type": "multi_select",
                    "options": [
                        "PRDs",
                        "Tech specs",
                        "Jira epics",
                        "Gantt charts",
                        "Release docs",
                        "RACI matrices",
                        "Risk registers",
                        "Status reports",
                        "Other",
                    ],
                    "sub_prompts": ["Select all that you regularly create and describe how you use them."],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Mention the ones you actually use, not what sounds impressive.",
                },
                {
                    "question_id": "s4_q3",
                    "question_text": "How do you run weekly program reviews and daily syncs (if any)?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Structure, attendees, cadence, what you track.",
                },
                {
                    "question_id": "s4_q4",
                    "question_text": "How do you detect a program is going off track before leadership notices?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Early warning signals, leading indicators, gut checks.",
                },
                {
                    "question_id": "s4_q5",
                    "question_text": "When do you escalate vs try to resolve quietly?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 5,
                    "help_text": "This is a judgment question. Share your framework.",
                },
            ],
        },
        # --- SECTION 5: Technical Depth Calibration ---
        {
            "section_id": "technical_depth",
            "section_number": 5,
            "title": "Technical Depth Calibration",
            "description": "Calibrating how deep your technical knowledge goes.",
            "questions": [
                {
                    "question_id": "s5_q1",
                    "question_text": "Which of these have you hands-on experience with?",
                    "question_type": "multi_select",
                    "options": [
                        "APIs & microservices",
                        "Distributed systems",
                        "Data pipelines",
                        "Cloud (AWS/GCP/Azure)",
                        "CI/CD & release pipelines",
                        "Databases (SQL / NoSQL)",
                        "Observability & incident response",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Only check what you've actually worked with. Honest calibration prevents interview surprises.",
                },
                {
                    "question_id": "s5_q2",
                    "question_text": "For 2\u20133 areas above, explain what you actually worked on and decisions you influenced.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Go deep on your strongest 2-3 areas. Specific examples > broad claims.",
                },
                {
                    "question_id": "s5_q3",
                    "question_text": "What technical topics do you intentionally stay high-level on?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 3,
                    "help_text": "It's okay to not go deep everywhere. Knowing your limits is a strength.",
                },
                {
                    "question_id": "s5_q4",
                    "question_text": "When engineers explain something complex, how do you validate you truly understand it?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Your process for understanding without pretending.",
                },
            ],
        },
        # --- SECTION 6: System/Design Interview Readiness ---
        {
            "section_id": "system_design",
            "section_number": 6,
            "title": "System / Design Interview Readiness",
            "description": "Preparing for system design interview rounds.",
            "questions": [
                {
                    "question_id": "s6_q1",
                    "question_text": "Have you ever designed or co-designed a system? If yes, describe it.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "What was the system?",
                        "Scale (users, services, data)",
                        "Tradeoffs discussed",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Even co-designing counts. The goal is to see if you can talk architecture.",
                },
                {
                    "question_id": "s6_q2",
                    "question_text": "What kind of design questions scare you the most?",
                    "question_type": "multi_select",
                    "options": [
                        "Architecture",
                        "Scalability",
                        "Data modeling",
                        "Reliability",
                    ],
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 2,
                    "help_text": "Honest assessment helps the AI know when to hedge vs. go deep.",
                },
                {
                    "question_id": "s6_q3",
                    "question_text": "In design interviews, do you prefer starting broad and refining, or anchoring to a real system you've built?",
                    "question_type": "single_select",
                    "options": [
                        "Starting broad and refining",
                        "Anchoring to a real system I've built",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "This shapes how the AI structures design answers for you.",
                },
            ],
        },
        # --- SECTION 7: Coding & Problem-Solving Style ---
        {
            "section_id": "coding_style",
            "section_number": 7,
            "title": "Coding & Problem-Solving Style (TPM-Level)",
            "description": "Your coding interview approach and comfort level.",
            "questions": [
                {
                    "question_id": "s7_q1",
                    "question_text": "Which languages are you comfortable writing in during interviews?",
                    "question_type": "text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "List languages you can code in under pressure, not just ones you've used.",
                },
                {
                    "question_id": "s7_q2",
                    "question_text": "How do you usually approach a coding/puzzle question?",
                    "question_type": "single_select",
                    "options": [
                        "Talk first, code later",
                        "Jump into brute force",
                        "Draw it out first",
                        "Ask clarifying questions then plan",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Your natural approach, not what you think sounds best.",
                },
                {
                    "question_id": "s7_q3",
                    "question_text": "Are you comfortable explaining time complexity, space complexity, and edge cases?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Be honest about your comfort level with Big-O analysis.",
                },
                {
                    "question_id": "s7_q4",
                    "question_text": "What level of correctness do you aim for in TPM coding interviews?",
                    "question_type": "single_select",
                    "options": [
                        "Fully optimal",
                        "Mostly correct with clear reasoning",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "TPM coding bars are different from SWE bars. Be realistic.",
                },
            ],
        },
        # --- SECTION 8: Behavioral Story Bank ---
        {
            "section_id": "behavioral_stories",
            "section_number": 8,
            "title": "Behavioral Story Bank (Reusable Ammo)",
            "description": "These answers will be reused across dozens of interview questions.",
            "questions": [
                {
                    "question_id": "s8_q1",
                    "question_text": "A time you handled extreme ambiguity.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "STAR format: Situation, Task, Action, Result. Be specific.",
                },
                {
                    "question_id": "s8_q2",
                    "question_text": "A time you influenced without authority.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Classic TPM question. Who, what, how did you convince them?",
                },
                {
                    "question_id": "s8_q3",
                    "question_text": "A time you had to make a tradeoff under pressure.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "What were the options? Why did you choose what you chose?",
                },
                {
                    "question_id": "s8_q4",
                    "question_text": "A time you improved a broken process.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Before/after. What was broken, what did you change, what improved?",
                },
                {
                    "question_id": "s8_q5",
                    "question_text": "A time you handled a production incident or crisis.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 5,
                    "help_text": "How you handled the chaos, communicated, and resolved it.",
                },
            ],
        },
        # --- SECTION 9: Metrics, Scale & Numbers ---
        {
            "section_id": "metrics_scale",
            "section_number": 9,
            "title": "Metrics, Scale & Numbers (Make Answers Real)",
            "description": "Concrete numbers make your answers credible.",
            "questions": [
                {
                    "question_id": "s9_q1",
                    "question_text": "Rough scale you've worked at.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Team size",
                        "Number of services",
                        "Release frequency",
                        "Users or transactions (even approximations)",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Even rough numbers are better than none. Approximations are fine.",
                },
                {
                    "question_id": "s9_q2",
                    "question_text": "What metrics do you naturally track?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Delivery metrics",
                        "Quality metrics",
                        "Business impact",
                    ],
                    "is_required": True,
                    "order": 2,
                    "help_text": "What dashboards do you check? What numbers do you report?",
                },
            ],
        },
        # --- SECTION 10: Interview Persona & Tone ---
        {
            "section_id": "persona_tone",
            "section_number": 10,
            "title": "Interview Persona & Tone",
            "description": "How you want the AI to sound when answering as you.",
            "questions": [
                {
                    "question_id": "s10_q1",
                    "question_text": "How do you want to sound in interviews?",
                    "question_type": "multi_select",
                    "options": [
                        "Calm and structured",
                        "Conversational",
                        "Deeply technical",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "This directly shapes the AI's tone when acting as your digital twin.",
                },
                {
                    "question_id": "s10_q2",
                    "question_text": "Do you prefer asking clarifying questions out loud?",
                    "question_type": "single_select",
                    "options": ["Yes, always", "Sometimes", "Rarely"],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Should the AI suggest clarifying questions before answering?",
                },
                {
                    "question_id": "s10_q3",
                    "question_text": "Any phrases you naturally use in interviews?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 3,
                    "help_text": "E.g., 'In my last role...', 'What we realized was...'. Makes the AI sound like you.",
                },
                {
                    "question_id": "s10_q4",
                    "question_text": "Any hard boundaries?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Topics you don't want the AI to answer",
                        "Companies you want anonymized",
                    ],
                    "is_required": False,
                    "order": 4,
                    "help_text": "Safety rails for the digital twin.",
                },
            ],
        },
        # --- SECTION 11: Reality Check (AI Safety Valve) ---
        {
            "section_id": "reality_check",
            "section_number": 11,
            "title": "Reality Check (AI Safety Valve)",
            "description": "Ensuring the AI knows what it doesn't know about you.",
            "questions": [
                {
                    "question_id": "s11_q1",
                    "question_text": "Is there anything on your resume you feel underprepared to defend?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 1,
                    "help_text": "The AI will be careful around these topics and won't overstate your experience.",
                },
                {
                    "question_id": "s11_q2",
                    "question_text": "Are there areas where you want the AI to explicitly say: 'I don't have direct experience, but here's how I'd approach it'?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 2,
                    "help_text": "Honesty > fabrication. This makes the digital twin trustworthy.",
                },
            ],
        },
    ],
    "created_at": datetime.utcnow(),
    "updated_at": datetime.utcnow(),
}


def _count_questions(questionnaire: dict) -> int:
    """Count total questions across all sections."""
    count = 0
    for section in questionnaire["sections"]:
        count += len(section["questions"])
    return count


async def seed_tpm_questionnaire():
    """Seed the TPM questionnaire into MongoDB."""
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

    # Create indexes
    print("\nCreating indexes on role_questionnaires...")
    await collection.create_index("role_type", unique=True)
    await collection.create_index("version")
    print("Indexes created: role_type (unique), version")

    # Also create indexes on questionnaire_responses
    responses_collection = db["questionnaire_responses"]
    print("\nCreating indexes on questionnaire_responses...")
    await responses_collection.create_index(
        [("user_id", 1), ("role_type", 1)],
        unique=True,
    )
    await responses_collection.create_index("status")
    print("Indexes created: (user_id, role_type) unique, status")

    # Check if TPM questionnaire already exists
    existing = await collection.find_one({"role_type": "technical_program_manager"})
    if existing:
        print(f"\nTPM questionnaire already exists (version {existing.get('version', '?')})")
        response = input("Do you want to replace it? (y/N): ").strip().lower()
        if response == "y":
            await collection.delete_one({"role_type": "technical_program_manager"})
            print("Existing questionnaire deleted.")
        else:
            print("Skipping. Exiting.")
            client.close()
            return

    # Set total question count
    total = _count_questions(TPM_QUESTIONNAIRE)
    TPM_QUESTIONNAIRE["total_questions"] = total

    # Insert
    print(f"\nInserting TPM questionnaire ({total} questions across {len(TPM_QUESTIONNAIRE['sections'])} sections)...")
    result = await collection.insert_one(TPM_QUESTIONNAIRE)
    print(f"Inserted with ID: {result.inserted_id}")

    # Print summary
    print("\nSections:")
    for section in TPM_QUESTIONNAIRE["sections"]:
        q_count = len(section["questions"])
        required = sum(1 for q in section["questions"] if q["is_required"])
        print(f"  {section['section_number']:2d}. {section['title']} ({q_count} questions, {required} required)")

    print(f"\nTotal: {total} questions")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_tpm_questionnaire())
