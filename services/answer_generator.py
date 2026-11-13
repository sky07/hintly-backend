"""Answer generation service using user's configured LLM provider.

Generates resume-grounded answers for interview questions.
Uses the same provider dispatch pattern as the main LLM clients.
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)

# Framework mapping by round type
FRAMEWORK_MAP = {
    "behavioral": "STAR",
    "screening": "STAR",
    "technical": "Problem-Approach-Solution",
    "system_design": "Requirements-Architecture-Tradeoffs",
    "culture_fit": "Values-Experience-Alignment",
}

ANSWER_GENERATION_PROMPT = """You are an interview preparation expert. Generate a structured answer for the following interview question.

CRITICAL RULES:
- ONLY reference projects, companies, and metrics that appear in the provided resume/experience
- If the resume doesn't contain a relevant example, honestly acknowledge the gap and suggest how to frame it
- NEVER fabricate project names, company names, metrics, or achievements
- Be specific: reference actual technologies, team sizes, and outcomes from the resume

CONTEXT:
- Company: {company_name}
- Interview Round: {round_type}
- Job Description: {jd_text}
- Candidate Resume: {resume_text}
- Additional Work Experience: {work_experience}

QUESTION: {question_text}

FRAMEWORK TO USE: {framework}

Generate a JSON response with this EXACT structure:
{{
    "core_message": "The main point to convey in 1-2 sentences",
    "example_reference": "A specific example from the resume that supports the answer",
    "impact_metrics": "Quantifiable results or metrics from the resume (or 'N/A' if none relevant)",
    "talking_points": ["Point 1 to mention", "Point 2 to mention", "Point 3 to mention"]
}}

Respond ONLY with valid JSON, no other text."""


async def _generate_via_groq(
    prompt: str,
    api_key: str,
) -> Optional[Dict[str, Any]]:
    """Generate answer using Groq API."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.groq.com/openai/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "llama-3.3-70b-versatile",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                    "response_format": {"type": "json_object"},
                },
            )
            if response.status_code != 200:
                logger.error(f"[ANSWER-GEN] Groq error: {response.status_code} {response.text}")
                return None

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as e:
        logger.error(f"[ANSWER-GEN] Groq generation failed: {e}")
        return None


async def _generate_via_openai(
    prompt: str,
    api_key: str,
) -> Optional[Dict[str, Any]]:
    """Generate answer using OpenAI API."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                "https://api.openai.com/v1/chat/completions",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": "gpt-4o-mini",
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.5,
                    "response_format": {"type": "json_object"},
                },
            )
            if response.status_code != 200:
                logger.error(f"[ANSWER-GEN] OpenAI error: {response.status_code} {response.text}")
                return None

            data = response.json()
            content = data["choices"][0]["message"]["content"]
            return json.loads(content)
    except Exception as e:
        logger.error(f"[ANSWER-GEN] OpenAI generation failed: {e}")
        return None


async def _generate_via_gemini(
    prompt: str,
    api_key: str,
) -> Optional[Dict[str, Any]]:
    """Generate answer using Gemini API."""
    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
                headers={"Content-Type": "application/json"},
                json={
                    "contents": [{"parts": [{"text": prompt}]}],
                    "generationConfig": {
                        "temperature": 0.5,
                        "responseMimeType": "application/json",
                    },
                },
            )
            if response.status_code != 200:
                logger.error(f"[ANSWER-GEN] Gemini error: {response.status_code} {response.text}")
                return None

            data = response.json()
            text = data["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
    except Exception as e:
        logger.error(f"[ANSWER-GEN] Gemini generation failed: {e}")
        return None


async def generate_answer(
    question_text: str,
    resume_text: str,
    jd_text: str,
    work_experience: str,
    company_name: str,
    round_type: str,
    provider: str,
    api_key: str,
) -> Optional[Dict[str, Any]]:
    """Generate a single answer for an interview question.

    Args:
        question_text: The interview question
        resume_text: Parsed resume text
        jd_text: Job description text
        work_experience: Additional work experience
        company_name: Target company name
        round_type: Interview round type
        provider: LLM provider (groq, openai, gemini)
        api_key: User's API key for the provider

    Returns:
        Answer dict with core_message, example_reference, impact_metrics, talking_points
    """
    framework = FRAMEWORK_MAP.get(round_type.lower(), "STAR")

    prompt = ANSWER_GENERATION_PROMPT.format(
        company_name=company_name or "Unknown",
        round_type=round_type or "General",
        jd_text=jd_text or "(Not provided)",
        resume_text=resume_text or "(Not provided)",
        work_experience=work_experience or "(Not provided)",
        question_text=question_text,
        framework=framework,
    )

    # Dispatch to appropriate provider
    provider_lower = provider.lower()
    if provider_lower in ("groq", "adaptive"):
        return await _generate_via_groq(prompt, api_key)
    elif provider_lower == "openai":
        return await _generate_via_openai(prompt, api_key)
    elif provider_lower == "gemini":
        return await _generate_via_gemini(prompt, api_key)
    else:
        logger.error(f"[ANSWER-GEN] Unknown provider: {provider}")
        return None


async def generate_answers_batch(
    questions: List[Dict[str, Any]],
    resume_text: str,
    jd_text: str,
    work_experience: str,
    company_name: str,
    round_type: str,
    provider: str,
    api_key: str,
) -> List[Dict[str, Any]]:
    """Generate answers for a batch of questions sequentially.

    Sequential processing to respect rate limits.

    Args:
        questions: List of question dicts with 'question_text' field
        Other args same as generate_answer

    Returns:
        List of {question_id, question_text, answer_data} dicts
    """
    results = []

    for q in questions:
        q_text = q.get("question_text", "")
        q_id = q.get("id", "")

        logger.info(f"[ANSWER-GEN] Generating answer for: {q_text[:60]}...")
        answer = await generate_answer(
            question_text=q_text,
            resume_text=resume_text,
            jd_text=jd_text,
            work_experience=work_experience,
            company_name=company_name,
            round_type=round_type,
            provider=provider,
            api_key=api_key,
        )

        results.append({
            "question_id": q_id,
            "question_text": q_text,
            "answer_data": answer or {
                "core_message": "Unable to generate answer",
                "example_reference": "",
                "impact_metrics": "",
                "talking_points": [],
            },
        })

    logger.info(f"[ANSWER-GEN] Generated {len(results)} answers")
    return results
