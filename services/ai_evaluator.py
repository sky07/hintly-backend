"""AI Confidence Evaluator for questionnaire responses.

Evaluates user's questionnaire answers holistically to determine
if the AI has enough context to act as their digital twin.

Supports multiple LLM providers (anthropic, groq, openai, gemini).
"""

import json
import logging
from typing import Any, Dict, List, Optional

import httpx

from services.ai_agent_prompts import (
    get_evaluator_prompt,
    EVALUATION_OUTPUT_FORMAT,
)

logger = logging.getLogger(__name__)


def _format_answers_for_evaluation(
    questionnaire: Dict,
    answers: Dict[str, Any],
    follow_up_answers: Dict[str, str],
) -> str:
    """Format questionnaire + answers into a readable text block for the AI.

    Args:
        questionnaire: The questionnaire template
        answers: User's answers keyed by question_id
        follow_up_answers: Answers to follow-up questions (if re-evaluating)

    Returns:
        Formatted text block.
    """
    lines = []
    lines.append(f"# Candidate Intake: {questionnaire.get('title', 'Unknown Role')}")
    lines.append("")

    for section in questionnaire.get("sections", []):
        lines.append(f"## Section {section['section_number']}: {section['title']}")
        lines.append(f"_{section.get('description', '')}_")
        lines.append("")

        for question in section.get("questions", []):
            q_id = question["question_id"]
            q_text = question["question_text"]
            answer = answers.get(q_id, {})
            answer_text = answer.get("answer_text", "").strip() if isinstance(answer, dict) else ""

            lines.append(f"**Q: {q_text}**")

            if question.get("sub_prompts"):
                for sp in question["sub_prompts"]:
                    lines.append(f"  - _{sp}_")

            if answer_text:
                lines.append(f"**A:** {answer_text}")
            else:
                lines.append("**A:** _(No answer provided)_")
            lines.append("")

    # Include follow-up answers if any
    if follow_up_answers:
        lines.append("## Follow-Up Question Answers")
        lines.append("")
        for fu_id, fu_answer in follow_up_answers.items():
            lines.append(f"**{fu_id}:** {fu_answer}")
            lines.append("")

    return "\n".join(lines)


async def _call_groq(
    system_prompt: str,
    user_message: str,
    api_key: str,
) -> Dict:
    """Call Groq API for evaluation."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.groq.com/openai/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "llama-3.3-70b-versatile",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


async def _call_openai(
    system_prompt: str,
    user_message: str,
    api_key: str,
) -> Dict:
    """Call OpenAI API for evaluation."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": "gpt-4o-mini",
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message},
                ],
                "temperature": 0.3,
                "max_tokens": 2000,
                "response_format": {"type": "json_object"},
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["choices"][0]["message"]["content"]
        return json.loads(content)


async def _call_gemini(
    system_prompt: str,
    user_message: str,
    api_key: str,
) -> Dict:
    """Call Gemini API for evaluation."""
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "contents": [
                    {
                        "parts": [
                            {"text": f"{system_prompt}\n\n{user_message}"}
                        ]
                    }
                ],
                "generationConfig": {
                    "temperature": 0.3,
                    "maxOutputTokens": 2000,
                    "responseMimeType": "application/json",
                },
            },
        )
        response.raise_for_status()
        data = response.json()
        content = data["candidates"][0]["content"]["parts"][0]["text"]
        return json.loads(content)


async def _call_anthropic(
    system_prompt: str,
    user_message: str,
    api_key: str,
) -> Dict:
    """Call Anthropic API for evaluation using Claude Sonnet 5.5."""
    async with httpx.AsyncClient(timeout=90.0) as client:
        response = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "anthropic-beta": "server-side-fallback-2026-07-01",
                "content-type": "application/json",
            },
            json={
                "model": "claude-sonnet-5-5",
                # Sonnet 5.5 always thinks (counted in max_tokens) and rejects
                # a custom temperature, so neither is set as before.
                "max_tokens": 16000,
                "fallbacks": "default",
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": user_message},
                ],
            },
        )
        response.raise_for_status()
        data = response.json()
        if data.get("stop_reason") == "refusal":
            raise RuntimeError("Claude declined this request (stop_reason=refusal)")
        # Read the answer by block type: the response opens with thinking blocks
        text_out = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        content = text_out
        # Strip markdown code block wrapper if present
        stripped = content.strip()
        if stripped.startswith("```"):
            # Remove opening ```json or ``` line and closing ```
            lines = stripped.split("\n")
            lines = lines[1:]  # drop opening ```json
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            stripped = "\n".join(lines)
        return json.loads(stripped)


async def evaluate_questionnaire(
    role_type: str,
    questionnaire: Dict,
    answers: Dict[str, Any],
    follow_up_answers: Dict[str, str],
    provider: str,
    api_key: str,
) -> Dict:
    """Evaluate questionnaire answers and return confidence score.

    Args:
        role_type: Role type string
        questionnaire: The questionnaire template
        answers: User's answers keyed by question_id
        follow_up_answers: Follow-up answers (empty dict if first evaluation)
        provider: LLM provider ('groq', 'openai', 'gemini', 'adaptive')
        api_key: Decrypted API key

    Returns:
        Evaluation result with confidence_score, strengths, gaps, follow_up_questions.
    """
    # Build the system prompt
    evaluator_prompt = get_evaluator_prompt(role_type)
    system_prompt = f"{evaluator_prompt}\n\n{EVALUATION_OUTPUT_FORMAT}"

    # Format answers into readable text
    formatted_answers = _format_answers_for_evaluation(
        questionnaire, answers, follow_up_answers,
    )

    is_re_evaluation = bool(follow_up_answers)
    user_message = (
        f"{'Re-evaluate after follow-up answers were provided. ' if is_re_evaluation else ''}"
        f"Please evaluate the following candidate intake questionnaire and provide your confidence score.\n\n"
        f"{formatted_answers}"
    )

    logger.info(
        f"[EVAL] Starting evaluation for role={role_type}, provider={provider}, "
        f"re_eval={is_re_evaluation}, answers_length={len(formatted_answers)}"
    )

    # Route to appropriate provider
    provider_lower = provider.lower()
    try:
        if provider_lower == "anthropic":
            result = await _call_anthropic(system_prompt, user_message, api_key)
        elif provider_lower in ("groq", "adaptive"):
            result = await _call_groq(system_prompt, user_message, api_key)
        elif provider_lower == "openai":
            result = await _call_openai(system_prompt, user_message, api_key)
        elif provider_lower == "gemini":
            result = await _call_gemini(system_prompt, user_message, api_key)
        else:
            raise ValueError(f"Unsupported provider: {provider}")
    except httpx.HTTPStatusError as e:
        logger.error(f"[EVAL] API error: {e.response.status_code} - {e.response.text[:200]}")
        raise RuntimeError(f"LLM API error ({e.response.status_code}): {e.response.text[:200]}")
    except json.JSONDecodeError as e:
        logger.error(f"[EVAL] Failed to parse JSON response: {e}")
        raise RuntimeError("Failed to parse AI evaluation response as JSON")

    # Validate required fields
    if "confidence_score" not in result:
        result["confidence_score"] = 0
    if "strengths" not in result:
        result["strengths"] = []
    if "gaps" not in result:
        result["gaps"] = []
    if "follow_up_questions" not in result:
        result["follow_up_questions"] = []
    if "summary" not in result:
        result["summary"] = "Evaluation completed."

    # Ensure confidence_score is a number
    try:
        result["confidence_score"] = float(result["confidence_score"])
    except (TypeError, ValueError):
        result["confidence_score"] = 0

    # Remove follow-up questions if confidence is >= 70%
    if result["confidence_score"] >= 70:
        result["follow_up_questions"] = []

    logger.info(
        f"[EVAL] Evaluation complete: confidence={result['confidence_score']}%, "
        f"strengths={len(result['strengths'])}, gaps={len(result['gaps'])}, "
        f"follow_ups={len(result['follow_up_questions'])}"
    )

    return result
