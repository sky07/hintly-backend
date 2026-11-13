"""Seed MongoDB with Product Manager role questionnaire.

Run with: cd backend && python -m scripts.seed_pm_questionnaire

Creates/updates the mid-level PM intake questionnaire in `role_questionnaires`.
"""

import asyncio
import sys
import os
from datetime import datetime

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


PM_QUESTIONNAIRE = {
    "role_type": "product_manager",
    "version": 1,
    "title": "Product Manager Interview AI – User Intake Question Set (Mid-Level)",
    "description": "Comprehensive questionnaire to build your PM digital twin. Covers product sense, execution, metrics, tradeoffs, stakeholder management, strategy, and behavioral questions. Designed for 2–5 years PM experience across B2B/B2C/SaaS/Fintech.",
    "ground_rules": "Answer only based on products you actually worked on. If you didn't own a decision, say so. I will not invent launches, metrics, or impact.",
    "sections": [
        # ================================================================
        # SECTION 1: PM Identity & Career Snapshot
        # ================================================================
        {
            "section_id": "pm_identity",
            "section_number": 1,
            "title": "PM Identity & Career Snapshot",
            "description": "Understanding your PM career trajectory and working style.",
            "questions": [
                {
                    "question_id": "s1_q1",
                    "question_text": "Walk me through your PM career.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Company (or anonymized)",
                        "Years in each role",
                        "Product/domain",
                        "Team size",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Cover each role separately. Be specific about what you owned vs. what others owned.",
                },
                {
                    "question_id": "s1_q2",
                    "question_text": "What type of PM are you closer to?",
                    "question_type": "single_select",
                    "options": [
                        "Execution-focused",
                        "Customer-focused",
                        "Data-focused",
                        "Technical PM",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Pick the one that best describes your natural strength. This shapes how the AI positions your answers.",
                },
                {
                    "question_id": "s1_q3",
                    "question_text": "What part of PM work do you enjoy the most?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Be honest — discovery, shipping, strategy, stakeholder management, data analysis, user research, etc.",
                },
                {
                    "question_id": "s1_q4",
                    "question_text": "What part of PM work is hardest for you?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Self-awareness is a signal. What do you find draining or struggle with?",
                },
            ],
        },
        # ================================================================
        # SECTION 2: Products You've Owned (Core Section)
        # ================================================================
        {
            "section_id": "products_owned",
            "section_number": 2,
            "title": "Products You've Owned (Core Section)",
            "description": "Deep dive into your main products or features. Minimum 2, ideal 4–6. Repeat for each major product.",
            "questions": [
                {
                    "question_id": "s2_q1",
                    "question_text": "Describe one product or feature you owned.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Who was the user?",
                        "What problem were you solving?",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "This is the MOST important section. Be as detailed as possible. Repeat for 2-6 products.",
                    "repeatable": True,
                    "min_entries": 2,
                    "max_entries": 6,
                },
                {
                    "question_id": "s2_q2",
                    "question_text": "How did you identify this problem?",
                    "question_type": "multi_select",
                    "options": [
                        "Customer feedback",
                        "Data analysis",
                        "Leadership direction",
                        "Competitive analysis",
                        "User research",
                    ],
                    "sub_prompts": ["Explain briefly how the insight surfaced."],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Select all that apply. Interviewers care about how you find problems, not just solve them.",
                },
                {
                    "question_id": "s2_q3",
                    "question_text": "What was the success metric?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "How did you know this feature was successful? Be specific — adoption %, revenue lift, NPS change, etc.",
                },
                {
                    "question_id": "s2_q4",
                    "question_text": "What was your exact ownership?",
                    "question_type": "multi_select",
                    "options": [
                        "Discovery / problem definition",
                        "PRD / spec writing",
                        "Prioritization / roadmap",
                        "Launch / go-to-market",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Draw clear lines. Interviewers test for ownership clarity — what YOU did vs. what the team did.",
                },
                {
                    "question_id": "s2_q5",
                    "question_text": "What constraints did you have?",
                    "question_type": "multi_select",
                    "options": [
                        "Time / deadline pressure",
                        "Technical limitations",
                        "Legal / compliance",
                        "Team bandwidth",
                        "Budget",
                    ],
                    "sub_prompts": ["Briefly describe the biggest constraint and how it shaped your decisions."],
                    "is_required": True,
                    "order": 5,
                    "help_text": "Constraints show how you think. Every real product has them.",
                },
                {
                    "question_id": "s2_q6",
                    "question_text": "Final outcome?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Shipped / delayed / killed?",
                        "Impact on users or business (with numbers if possible)",
                    ],
                    "is_required": True,
                    "order": 6,
                    "help_text": "Numbers and outcomes make stories credible. Even estimates help.",
                },
                {
                    "question_id": "s2_q7",
                    "question_text": "What would you do differently if you built this again?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 7,
                    "help_text": "Shows self-awareness and growth mindset. Great closer for product stories.",
                },
            ],
        },
        # ================================================================
        # SECTION 3: Product Thinking & Decision Making
        # ================================================================
        {
            "section_id": "product_thinking",
            "section_number": 3,
            "title": "Product Thinking & Decision Making",
            "description": "How you make product decisions, prioritize, and handle ambiguity.",
            "questions": [
                {
                    "question_id": "s3_q1",
                    "question_text": "How do you decide what to build vs not build?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Walk through your prioritization framework — RICE, ICE, gut + data, opportunity cost thinking, etc.",
                },
                {
                    "question_id": "s3_q2",
                    "question_text": "Give an example of a tough prioritization decision you made.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "What were the options? What data did you use? Who disagreed? What happened?",
                },
                {
                    "question_id": "s3_q3",
                    "question_text": "A feature you said 'no' to and why.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Saying no is a PM superpower. How did you frame the rejection? How did stakeholders react?",
                },
                {
                    "question_id": "s3_q4",
                    "question_text": "How do you handle ambiguous requirements?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "What's your process when you get a vague ask from leadership or customers?",
                },
                {
                    "question_id": "s3_q5",
                    "question_text": "What tradeoffs do you usually think about?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Speed vs quality",
                        "Scope vs impact",
                        "Short-term vs long-term",
                    ],
                    "is_required": True,
                    "order": 5,
                    "help_text": "Give a real example for each tradeoff you've navigated. Specifics > theory.",
                },
            ],
        },
        # ================================================================
        # SECTION 4: Metrics & Data (Mid-Level PM Signal)
        # ================================================================
        {
            "section_id": "metrics_data",
            "section_number": 4,
            "title": "Metrics & Data (Mid-Level PM Signal)",
            "description": "How you use data to make decisions — a key differentiator for mid-level PMs.",
            "questions": [
                {
                    "question_id": "s4_q1",
                    "question_text": "Metrics you've used in real work.",
                    "question_type": "multi_select",
                    "options": [
                        "Adoption / activation",
                        "Retention / churn",
                        "Conversion",
                        "Revenue / ARPU",
                        "NPS / CSAT",
                        "Engagement (DAU/MAU, session time)",
                    ],
                    "sub_prompts": ["For each metric selected, briefly describe how you used it in a real decision."],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Only check metrics you've actually tracked and acted on. Describe real usage, not textbook definitions.",
                },
                {
                    "question_id": "s4_q2",
                    "question_text": "How do you define success for a new feature?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Walk through your process — do you set OKRs, define a north star metric, set guardrail metrics?",
                },
                {
                    "question_id": "s4_q3",
                    "question_text": "A time metrics told a different story than intuition.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "What did you expect? What did the data show? What did you do about it?",
                },
                {
                    "question_id": "s4_q4",
                    "question_text": "How do you handle missing or noisy data?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Real PM life has imperfect data. How do you make decisions when data is incomplete or contradictory?",
                },
            ],
        },
        # ================================================================
        # SECTION 5: Customer & User Understanding
        # ================================================================
        {
            "section_id": "customer_understanding",
            "section_number": 5,
            "title": "Customer & User Understanding",
            "description": "How you develop customer empathy and use insights to shape products.",
            "questions": [
                {
                    "question_id": "s5_q1",
                    "question_text": "How do you normally collect customer insights?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "User interviews, surveys, support tickets, analytics, sales calls, dogfooding — what's your go-to method?",
                },
                {
                    "question_id": "s5_q2",
                    "question_text": "Example where customer feedback changed your roadmap.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "What was the feedback? How did you validate it? What changed in your plan?",
                },
                {
                    "question_id": "s5_q3",
                    "question_text": "How do you balance loud customers vs the silent majority?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "A common PM tension. How do you avoid building for the vocal few?",
                },
                {
                    "question_id": "s5_q4",
                    "question_text": "A time you shipped something customers didn't like.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "What happened? How did you find out? What did you do next? This shows resilience and learning.",
                },
            ],
        },
        # ================================================================
        # SECTION 6: Execution & Delivery
        # ================================================================
        {
            "section_id": "execution_delivery",
            "section_number": 6,
            "title": "Execution & Delivery",
            "description": "How you get things shipped — PRDs, working with engineering, handling scope creep.",
            "questions": [
                {
                    "question_id": "s6_q1",
                    "question_text": "How do you write PRDs?",
                    "question_type": "single_select",
                    "options": [
                        "Detailed and comprehensive",
                        "Lightweight / one-pager",
                        "Depends on the feature size",
                    ],
                    "sub_prompts": ["Describe what a typical PRD from you looks like."],
                    "is_required": True,
                    "order": 1,
                    "help_text": "There's no right answer — what matters is you have a system and can explain why.",
                },
                {
                    "question_id": "s6_q2",
                    "question_text": "How do you work with engineering day to day?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Standups, async Slack, 1:1s, sprint planning — describe your actual working rhythm.",
                },
                {
                    "question_id": "s6_q3",
                    "question_text": "How do you handle delays or scope creep?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Give a real example. How did you detect it, communicate it, and resolve it?",
                },
                {
                    "question_id": "s6_q4",
                    "question_text": "How do you prepare for product launches?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Launch checklist, comms plan, rollback plan, monitoring — what's your process?",
                },
            ],
        },
        # ================================================================
        # SECTION 7: Stakeholder Management
        # ================================================================
        {
            "section_id": "stakeholder_management",
            "section_number": 7,
            "title": "Stakeholder Management",
            "description": "How you work with cross-functional teams and navigate disagreements.",
            "questions": [
                {
                    "question_id": "s7_q1",
                    "question_text": "Teams you work closely with.",
                    "question_type": "multi_select",
                    "options": [
                        "Engineering",
                        "Design",
                        "Sales",
                        "Support / CS",
                        "Marketing",
                        "Data / Analytics",
                        "Legal / Compliance",
                    ],
                    "sub_prompts": ["For the top 2-3, describe what your collaboration looks like."],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Select all that you regularly partner with. Describe the working relationship, not just the team name.",
                },
                {
                    "question_id": "s7_q2",
                    "question_text": "A time you had conflict with engineering.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "What was the disagreement? How did you resolve it? What was the outcome?",
                },
                {
                    "question_id": "s7_q3",
                    "question_text": "A time you pushed back on leadership.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Shows backbone. How did you frame your pushback? What data or reasoning did you use?",
                },
                {
                    "question_id": "s7_q4",
                    "question_text": "How do you align stakeholders on priorities?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "When multiple teams want different things, what's your playbook for alignment?",
                },
            ],
        },
        # ================================================================
        # SECTION 8: Product Sense & Design Questions
        # ================================================================
        {
            "section_id": "product_sense",
            "section_number": 8,
            "title": "Product Sense & Design Questions",
            "description": "Preparing for product design and product sense interview rounds.",
            "questions": [
                {
                    "question_id": "s8_q1",
                    "question_text": "Products you can comfortably talk about in interviews.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "List 3-5 products (yours or well-known ones) you can analyze deeply — user, problem, competitors, improvements.",
                },
                {
                    "question_id": "s8_q2",
                    "question_text": "How do you approach a product design question?",
                    "question_type": "single_select",
                    "options": [
                        "User → Problem → Solution",
                        "Business goal → User → Solution",
                        "Start with constraints, then explore",
                        "Depends on the question",
                    ],
                    "sub_prompts": ["Walk through your typical framework step by step."],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Your natural approach, not a memorized framework. Show your thinking style.",
                },
                {
                    "question_id": "s8_q3",
                    "question_text": "What assumptions do you usually validate first?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "When given a product problem, what do you check or ask about before jumping to solutions?",
                },
                {
                    "question_id": "s8_q4",
                    "question_text": "Example of simplifying a complex product.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "A time you took something complex and made it simpler for users. What was your approach?",
                },
            ],
        },
        # ================================================================
        # SECTION 9: Go-To-Market & Launch
        # ================================================================
        {
            "section_id": "gtm_launch",
            "section_number": 9,
            "title": "Go-To-Market & Launch",
            "description": "Your experience with product launches and go-to-market strategies.",
            "questions": [
                {
                    "question_id": "s9_q1",
                    "question_text": "Your involvement in launches.",
                    "question_type": "multi_select",
                    "options": [
                        "Beta / private preview",
                        "Phased rollout (feature flags, % rollout)",
                        "Full launch (GA)",
                        "Internal-only launch",
                    ],
                    "sub_prompts": ["Describe your role in the most significant launch you've done."],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Select all launch types you've been involved in. Describe your specific role, not just the team's.",
                },
                {
                    "question_id": "s9_q2",
                    "question_text": "How did you communicate changes to users?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "In-app messaging, emails, changelogs, sales enablement, docs — what was your approach?",
                },
                {
                    "question_id": "s9_q3",
                    "question_text": "A launch that didn't go as planned.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "What went wrong? How did you detect it? What did you do? This shows resilience and operational maturity.",
                },
            ],
        },
        # ================================================================
        # SECTION 10: Behavioral Story Bank
        # ================================================================
        {
            "section_id": "behavioral_stories",
            "section_number": 10,
            "title": "Behavioral Story Bank (Reusable Ammo)",
            "description": "These answers will be reused across dozens of interview questions. Use STAR format: Situation, Task, Action, Result.",
            "questions": [
                {
                    "question_id": "s10_q1",
                    "question_text": "A time you handled ambiguity.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "STAR format: Situation, Task, Action, Result. Be specific about what was ambiguous and how you created clarity.",
                },
                {
                    "question_id": "s10_q2",
                    "question_text": "A time you influenced without authority.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Classic PM question. Who did you need to influence? What was at stake? How did you persuade them?",
                },
                {
                    "question_id": "s10_q3",
                    "question_text": "A time you failed as a PM.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Be real. The behavioral change afterward is what interviewers look for, not the failure itself.",
                },
                {
                    "question_id": "s10_q4",
                    "question_text": "A time you improved a process or product quality.",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 4,
                    "help_text": "Before/after. What was broken, what did you change, what improved? Include metrics if possible.",
                },
            ],
        },
        # ================================================================
        # SECTION 11: Collaboration & Leadership Growth
        # ================================================================
        {
            "section_id": "collaboration_leadership",
            "section_number": 11,
            "title": "Collaboration & Leadership Growth",
            "description": "How you build trust and grow as a PM leader.",
            "questions": [
                {
                    "question_id": "s11_q1",
                    "question_text": "How do you build trust with engineers?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Specific behaviors and habits, not platitudes. What do you do in the first 30 days with a new eng team?",
                },
                {
                    "question_id": "s11_q2",
                    "question_text": "How do you mentor junior PMs or teammates?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 2,
                    "help_text": "If applicable. Coaching, pairing, sharing frameworks — what does mentorship look like for you?",
                },
                {
                    "question_id": "s11_q3",
                    "question_text": "How do you handle feedback on your product decisions?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "When someone disagrees with your decision, how do you evaluate their feedback vs. sticking to your plan?",
                },
            ],
        },
        # ================================================================
        # SECTION 12: Scale, Impact & Credibility Anchors
        # ================================================================
        {
            "section_id": "scale_impact",
            "section_number": 12,
            "title": "Scale, Impact & Credibility Anchors",
            "description": "Concrete numbers and scope that make your answers credible.",
            "questions": [
                {
                    "question_id": "s12_q1",
                    "question_text": "Approximate scale you've worked at.",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Users (MAU, DAU, or total)",
                        "Revenue impact (ARR, MRR, or deal size)",
                        "Team size (eng + design + data)",
                    ],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Even rough numbers are better than none. Approximations are fine — interviewers just want a sense of scale.",
                },
                {
                    "question_id": "s12_q2",
                    "question_text": "What kind of products are you strongest at?",
                    "question_type": "multi_select",
                    "options": [
                        "B2B / Enterprise",
                        "B2C / Consumer",
                        "SaaS / Platform",
                        "Internal tools",
                        "Marketplace / multi-sided",
                        "Fintech / regulated",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Select the product types where you have the most credible experience. This shapes which stories the AI leads with.",
                },
            ],
        },
        # ================================================================
        # SECTION 13: Interview Persona & Constraints
        # ================================================================
        {
            "section_id": "persona_constraints",
            "section_number": 13,
            "title": "Interview Persona & Constraints",
            "description": "How you want the AI to sound when answering as you.",
            "questions": [
                {
                    "question_id": "s13_q1",
                    "question_text": "How do you want to sound in interviews?",
                    "question_type": "multi_select",
                    "options": [
                        "Structured and methodical",
                        "Conversational and storytelling",
                        "Data-driven and analytical",
                        "Customer-first and empathetic",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 1,
                    "help_text": "Pick 1-2 that feel most natural. This directly shapes the AI's tone when acting as your digital twin.",
                },
                {
                    "question_id": "s13_q2",
                    "question_text": "Do you prefer asking clarifying questions before answering?",
                    "question_type": "single_select",
                    "options": [
                        "Yes, always — I like to scope the question first",
                        "Sometimes — depends on how clear the question is",
                        "Rarely — I prefer to dive in and adjust",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 2,
                    "help_text": "Should the AI suggest clarifying questions before answering, or jump straight to a response?",
                },
                {
                    "question_id": "s13_q3",
                    "question_text": "Any topics the AI should avoid or be cautious on?",
                    "question_type": "structured",
                    "sub_prompts": [
                        "Topics to avoid entirely",
                        "Companies to anonymize",
                        "Areas to be careful about (NDA, sensitive data)",
                    ],
                    "is_required": False,
                    "order": 3,
                    "help_text": "Safety rails for the digital twin. The AI will respect these boundaries.",
                },
            ],
        },
        # ================================================================
        # SECTION 14: Integrity & Safety Checks
        # ================================================================
        {
            "section_id": "integrity_safety",
            "section_number": 14,
            "title": "Integrity & Safety Checks",
            "description": "Ensuring the AI knows what it doesn't know about you.",
            "questions": [
                {
                    "question_id": "s14_q1",
                    "question_text": "Anything on your resume you're not confident defending?",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 1,
                    "help_text": "The AI will be careful around these topics and won't overstate your experience.",
                },
                {
                    "question_id": "s14_q2",
                    "question_text": "Areas where the AI should say: 'I don't have direct experience, but here's how I'd approach it.'",
                    "question_type": "long_text",
                    "sub_prompts": [],
                    "is_required": False,
                    "order": 2,
                    "help_text": "Honesty > fabrication. This makes the digital twin trustworthy and interview-safe.",
                },
                {
                    "question_id": "s14_q3",
                    "question_text": "How conservative should the AI be when unsure?",
                    "question_type": "single_select",
                    "options": [
                        "Very conservative — only speak from confirmed experience",
                        "Moderate — can infer reasonable answers from context",
                        "Flexible — can extrapolate from related experience",
                    ],
                    "sub_prompts": [],
                    "is_required": True,
                    "order": 3,
                    "help_text": "Controls how much the AI can generalize vs. strictly stick to what you told it.",
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


async def seed_pm_questionnaire():
    """Seed the Product Manager questionnaire into MongoDB."""
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

    # Create indexes (idempotent)
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

    # Check if PM questionnaire already exists
    existing = await collection.find_one({"role_type": "product_manager"})
    if existing:
        print(f"\nPM questionnaire already exists (version {existing.get('version', '?')})")
        response = input("Do you want to replace it? (y/N): ").strip().lower()
        if response == "y":
            await collection.delete_one({"role_type": "product_manager"})
            print("Existing questionnaire deleted.")
        else:
            print("Skipping. Exiting.")
            client.close()
            return

    # Set total question count
    total = _count_questions(PM_QUESTIONNAIRE)
    PM_QUESTIONNAIRE["total_questions"] = total

    # Insert
    print(f"\nInserting PM questionnaire ({total} questions across {len(PM_QUESTIONNAIRE['sections'])} sections)...")
    result = await collection.insert_one(PM_QUESTIONNAIRE)
    print(f"Inserted with ID: {result.inserted_id}")

    # Print summary
    print("\nSections:")
    for section in PM_QUESTIONNAIRE["sections"]:
        q_count = len(section["questions"])
        required = sum(1 for q in section["questions"] if q["is_required"])
        print(f"  {section['section_number']:2d}. {section['title']} ({q_count} questions, {required} required)")

    print(f"\nTotal: {total} questions")
    print("Seed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_pm_questionnaire())
