"""Seed MongoDB with sample interview questions.

Run with: cd backend && python -m scripts.seed_questions

Creates indexes and inserts 24 sample questions across multiple companies
and all 5 interview round types.
"""

import asyncio
import sys
import os
from datetime import datetime, timedelta
import random

# Add parent to path so we can import config
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import settings


SAMPLE_QUESTIONS = [
    # === Google ===
    {
        "question_text": "Tell me about a time you had to make a difficult technical decision with incomplete information.",
        "normalized_text": "tell me about a time you had to make a difficult technical decision with incomplete information",
        "company_name": "Google",
        "role": "software_engineer",
        "interview_round": "behavioral",
        "tags": ["decision-making", "ambiguity", "leadership", "trade-offs"],
        "difficulty": "hard",
        "verified_count": 12,
        "last_seen": datetime.utcnow() - timedelta(days=15),
    },
    {
        "question_text": "Design a URL shortener like bit.ly. How would you handle billions of URLs?",
        "normalized_text": "design a url shortener like bitly how would you handle billions of urls",
        "company_name": "Google",
        "role": "software_engineer",
        "interview_round": "system_design",
        "tags": ["distributed-systems", "hashing", "scalability", "database"],
        "difficulty": "hard",
        "verified_count": 18,
        "last_seen": datetime.utcnow() - timedelta(days=7),
    },
    {
        "question_text": "Given an array of integers, find two numbers that add up to a specific target.",
        "normalized_text": "given an array of integers find two numbers that add up to a specific target",
        "company_name": "Google",
        "role": "software_engineer",
        "interview_round": "technical",
        "tags": ["arrays", "hash-map", "two-sum", "algorithms"],
        "difficulty": "easy",
        "verified_count": 25,
        "last_seen": datetime.utcnow() - timedelta(days=3),
    },
    {
        "question_text": "Why Google? What about our mission resonates with you?",
        "normalized_text": "why google what about our mission resonates with you",
        "company_name": "Google",
        "role": "software_engineer",
        "interview_round": "culture_fit",
        "tags": ["motivation", "company-research", "values"],
        "difficulty": "medium",
        "verified_count": 8,
        "last_seen": datetime.utcnow() - timedelta(days=30),
    },
    # === Amazon ===
    {
        "question_text": "Tell me about a time when you had to deliver results under a tight deadline. How did you prioritize?",
        "normalized_text": "tell me about a time when you had to deliver results under a tight deadline how did you prioritize",
        "company_name": "Amazon",
        "role": "software_engineer",
        "interview_round": "behavioral",
        "tags": ["deliver-results", "prioritization", "leadership-principles", "time-management"],
        "difficulty": "medium",
        "verified_count": 20,
        "last_seen": datetime.utcnow() - timedelta(days=5),
    },
    {
        "question_text": "Design an e-commerce recommendation system. How would you handle real-time personalization?",
        "normalized_text": "design an ecommerce recommendation system how would you handle real-time personalization",
        "company_name": "Amazon",
        "role": "software_engineer",
        "interview_round": "system_design",
        "tags": ["machine-learning", "recommendation", "real-time", "personalization"],
        "difficulty": "hard",
        "verified_count": 14,
        "last_seen": datetime.utcnow() - timedelta(days=20),
    },
    {
        "question_text": "Implement an LRU cache with O(1) get and put operations.",
        "normalized_text": "implement an lru cache with o1 get and put operations",
        "company_name": "Amazon",
        "role": "software_engineer",
        "interview_round": "technical",
        "tags": ["data-structures", "cache", "linked-list", "hash-map"],
        "difficulty": "medium",
        "verified_count": 22,
        "last_seen": datetime.utcnow() - timedelta(days=10),
    },
    {
        "question_text": "Walk me through your resume. What's your biggest achievement?",
        "normalized_text": "walk me through your resume whats your biggest achievement",
        "company_name": "Amazon",
        "role": "software_engineer",
        "interview_round": "screening",
        "tags": ["resume", "achievements", "introduction", "career-story"],
        "difficulty": "easy",
        "verified_count": 30,
        "last_seen": datetime.utcnow() - timedelta(days=2),
    },
    # === Meta ===
    {
        "question_text": "Describe a situation where you disagreed with your manager. How did you handle it?",
        "normalized_text": "describe a situation where you disagreed with your manager how did you handle it",
        "company_name": "Meta",
        "role": "software_engineer",
        "interview_round": "behavioral",
        "tags": ["conflict-resolution", "communication", "leadership", "influence"],
        "difficulty": "medium",
        "verified_count": 15,
        "last_seen": datetime.utcnow() - timedelta(days=12),
    },
    {
        "question_text": "Design a news feed system like Facebook. How do you rank posts?",
        "normalized_text": "design a news feed system like facebook how do you rank posts",
        "company_name": "Meta",
        "role": "software_engineer",
        "interview_round": "system_design",
        "tags": ["news-feed", "ranking", "distributed-systems", "caching"],
        "difficulty": "hard",
        "verified_count": 16,
        "last_seen": datetime.utcnow() - timedelta(days=8),
    },
    {
        "question_text": "Given a binary tree, return the level order traversal of its nodes' values.",
        "normalized_text": "given a binary tree return the level order traversal of its nodes values",
        "company_name": "Meta",
        "role": "software_engineer",
        "interview_round": "technical",
        "tags": ["trees", "bfs", "queue", "algorithms"],
        "difficulty": "medium",
        "verified_count": 19,
        "last_seen": datetime.utcnow() - timedelta(days=6),
    },
    {
        "question_text": "How do you handle work-life balance? How do you deal with ambiguity at work?",
        "normalized_text": "how do you handle work-life balance how do you deal with ambiguity at work",
        "company_name": "Meta",
        "role": "software_engineer",
        "interview_round": "culture_fit",
        "tags": ["work-life-balance", "ambiguity", "self-awareness", "growth"],
        "difficulty": "easy",
        "verified_count": 10,
        "last_seen": datetime.utcnow() - timedelta(days=25),
    },
    # === Microsoft ===
    {
        "question_text": "Tell me about a project that failed. What did you learn from it?",
        "normalized_text": "tell me about a project that failed what did you learn from it",
        "company_name": "Microsoft",
        "role": "software_engineer",
        "interview_round": "behavioral",
        "tags": ["failure", "learning", "resilience", "growth-mindset"],
        "difficulty": "medium",
        "verified_count": 11,
        "last_seen": datetime.utcnow() - timedelta(days=18),
    },
    {
        "question_text": "Design a distributed file storage system like OneDrive. How do you handle sync conflicts?",
        "normalized_text": "design a distributed file storage system like onedrive how do you handle sync conflicts",
        "company_name": "Microsoft",
        "role": "software_engineer",
        "interview_round": "system_design",
        "tags": ["distributed-storage", "sync", "conflict-resolution", "cloud"],
        "difficulty": "hard",
        "verified_count": 9,
        "last_seen": datetime.utcnow() - timedelta(days=35),
    },
    {
        "question_text": "Find the longest substring without repeating characters.",
        "normalized_text": "find the longest substring without repeating characters",
        "company_name": "Microsoft",
        "role": "software_engineer",
        "interview_round": "technical",
        "tags": ["strings", "sliding-window", "hash-set", "algorithms"],
        "difficulty": "medium",
        "verified_count": 21,
        "last_seen": datetime.utcnow() - timedelta(days=4),
    },
    {
        "question_text": "What's your experience with our products? What would you improve?",
        "normalized_text": "whats your experience with our products what would you improve",
        "company_name": "Microsoft",
        "role": "software_engineer",
        "interview_round": "screening",
        "tags": ["product-sense", "company-knowledge", "feedback", "improvement"],
        "difficulty": "easy",
        "verified_count": 7,
        "last_seen": datetime.utcnow() - timedelta(days=40),
    },
    # === Netflix ===
    {
        "question_text": "Give an example of how you've driven innovation in your previous role.",
        "normalized_text": "give an example of how youve driven innovation in your previous role",
        "company_name": "Netflix",
        "role": "software_engineer",
        "interview_round": "behavioral",
        "tags": ["innovation", "initiative", "impact", "ownership"],
        "difficulty": "medium",
        "verified_count": 6,
        "last_seen": datetime.utcnow() - timedelta(days=22),
    },
    {
        "question_text": "Design a video streaming service. How do you handle adaptive bitrate streaming?",
        "normalized_text": "design a video streaming service how do you handle adaptive bitrate streaming",
        "company_name": "Netflix",
        "role": "software_engineer",
        "interview_round": "system_design",
        "tags": ["streaming", "cdn", "adaptive-bitrate", "microservices"],
        "difficulty": "hard",
        "verified_count": 13,
        "last_seen": datetime.utcnow() - timedelta(days=14),
    },
    # === Stripe ===
    {
        "question_text": "How do you ensure data consistency in a distributed payment processing system?",
        "normalized_text": "how do you ensure data consistency in a distributed payment processing system",
        "company_name": "Stripe",
        "role": "software_engineer",
        "interview_round": "technical",
        "tags": ["distributed-systems", "consistency", "payments", "transactions"],
        "difficulty": "hard",
        "verified_count": 10,
        "last_seen": datetime.utcnow() - timedelta(days=11),
    },
    {
        "question_text": "Design an API rate limiter. How would you handle distributed rate limiting?",
        "normalized_text": "design an api rate limiter how would you handle distributed rate limiting",
        "company_name": "Stripe",
        "role": "software_engineer",
        "interview_round": "system_design",
        "tags": ["rate-limiting", "distributed-systems", "api-design", "scalability"],
        "difficulty": "medium",
        "verified_count": 17,
        "last_seen": datetime.utcnow() - timedelta(days=9),
    },
    # === General (no specific company) ===
    {
        "question_text": "What are your strengths and weaknesses?",
        "normalized_text": "what are your strengths and weaknesses",
        "company_name": "General",
        "role": "software_engineer",
        "interview_round": "screening",
        "tags": ["self-awareness", "strengths", "weaknesses", "introduction"],
        "difficulty": "easy",
        "verified_count": 35,
        "last_seen": datetime.utcnow() - timedelta(days=1),
    },
    {
        "question_text": "Where do you see yourself in 5 years?",
        "normalized_text": "where do you see yourself in 5 years",
        "company_name": "General",
        "role": "software_engineer",
        "interview_round": "screening",
        "tags": ["career-goals", "ambition", "planning", "growth"],
        "difficulty": "easy",
        "verified_count": 28,
        "last_seen": datetime.utcnow() - timedelta(days=2),
    },
    {
        "question_text": "Tell me about a time you mentored someone or helped a teammate grow.",
        "normalized_text": "tell me about a time you mentored someone or helped a teammate grow",
        "company_name": "General",
        "role": "software_engineer",
        "interview_round": "behavioral",
        "tags": ["mentoring", "teamwork", "leadership", "coaching"],
        "difficulty": "medium",
        "verified_count": 14,
        "last_seen": datetime.utcnow() - timedelta(days=16),
    },
    {
        "question_text": "Explain the difference between REST and GraphQL. When would you use each?",
        "normalized_text": "explain the difference between rest and graphql when would you use each",
        "company_name": "General",
        "role": "software_engineer",
        "interview_round": "technical",
        "tags": ["api-design", "rest", "graphql", "architecture"],
        "difficulty": "medium",
        "verified_count": 16,
        "last_seen": datetime.utcnow() - timedelta(days=13),
    },
]


async def seed_questions():
    """Seed the MongoDB questions collection."""
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
        # Test connection
        await client.admin.command("ping")
        print(f"Connected to MongoDB: {settings.mongodb_db_name}")
    except Exception as e:
        print(f"ERROR: Cannot connect to MongoDB: {e}")
        sys.exit(1)

    collection = db["questions"]

    # Create indexes
    print("\nCreating indexes...")
    await collection.create_index("company_name")
    await collection.create_index("role")
    await collection.create_index("interview_round")
    await collection.create_index("tags")
    await collection.create_index("last_seen")
    await collection.create_index(
        [("normalized_text", "text")],
        name="text_search_index",
    )
    print("Indexes created: company_name, role, interview_round, tags, last_seen, text")

    # Check existing count
    existing = await collection.count_documents({})
    if existing > 0:
        print(f"\nCollection already has {existing} questions.")
        response = input("Do you want to drop and re-seed? (y/N): ").strip().lower()
        if response == "y":
            await collection.drop()
            print("Collection dropped.")
        else:
            print("Skipping seed. Exiting.")
            client.close()
            return

    # Insert questions
    print(f"\nInserting {len(SAMPLE_QUESTIONS)} questions...")
    result = await collection.insert_many(SAMPLE_QUESTIONS)
    print(f"Inserted {len(result.inserted_ids)} questions")

    # Print summary
    companies = {}
    rounds = {}
    for q in SAMPLE_QUESTIONS:
        c = q["company_name"]
        r = q["interview_round"]
        companies[c] = companies.get(c, 0) + 1
        rounds[r] = rounds.get(r, 0) + 1

    print("\nBy company:")
    for c, count in sorted(companies.items()):
        print(f"  {c}: {count}")

    print("\nBy round type:")
    for r, count in sorted(rounds.items()):
        print(f"  {r}: {count}")

    print("\nSeed complete!")
    client.close()


if __name__ == "__main__":
    asyncio.run(seed_questions())
