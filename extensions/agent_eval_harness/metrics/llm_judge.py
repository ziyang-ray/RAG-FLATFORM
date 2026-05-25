from __future__ import annotations

import hashlib
import json
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

JUDGE_METRICS = ("faithfulness", "answer_relevance", "context_relevance", "coherence")

PROMPTS = {
    "faithfulness": (
        "You are an expert evaluator assessing the faithfulness of an AI-generated answer.\n\n"
        "TASK: Determine whether every claim in the answer is supported by the provided context.\n\n"
        "CONTEXT (retrieved chunks):\n{context}\n\n"
        "QUESTION: {question}\n\n"
        "ANSWER: {answer}\n\n"
        "SCORING RUBRIC:\n"
        "1 - Most claims are NOT supported by the context (major hallucination)\n"
        "2 - Several claims lack support; some are fabricated\n"
        "3 - About half the claims are supported; minor unsupported additions\n"
        "4 - Most claims are supported; only trivial unsupported details\n"
        "5 - Every claim is directly supported by the context\n\n"
        'Respond in JSON format ONLY:\n{{"score": <1-5>, "reasoning": "<brief explanation citing specific claims>"}}'
    ),
    "answer_relevance": (
        "You are an expert evaluator assessing how well an answer addresses the question.\n\n"
        "TASK: Score how relevant and complete the answer is to the question asked.\n\n"
        "QUESTION: {question}\n\n"
        "ANSWER: {answer}\n\n"
        "SCORING RUBRIC:\n"
        "1 - Answer is completely off-topic or nonsensical\n"
        "2 - Answer is partially related but misses the main point\n"
        "3 - Answer addresses the question partially; key aspects missing\n"
        "4 - Answer is relevant and covers most aspects of the question\n"
        "5 - Answer directly and comprehensively addresses the question\n\n"
        'Respond in JSON format ONLY:\n{{"score": <1-5>, "reasoning": "<brief explanation>"}}'
    ),
    "context_relevance": (
        "You are an expert evaluator assessing the relevance of retrieved context.\n\n"
        "TASK: Score how relevant the retrieved chunks are to answering the question.\n\n"
        "QUESTION: {question}\n\n"
        "RETRIEVED CHUNKS:\n{context}\n\n"
        "SCORING RUBRIC:\n"
        "1 - Chunks are completely irrelevant to the question\n"
        "2 - Chunks are tangentially related at best\n"
        "3 - Some chunks are relevant but mixed with irrelevant ones\n"
        "4 - Most chunks are relevant and useful for answering\n"
        "5 - All chunks are highly relevant and directly support answering\n\n"
        'Respond in JSON format ONLY:\n{{"score": <1-5>, "reasoning": "<brief explanation>"}}'
    ),
    "coherence": (
        "You are an expert evaluator assessing the coherence and quality of an answer.\n\n"
        "TASK: Score the answer's coherence, clarity, and logical organization.\n\n"
        "QUESTION: {question}\n\n"
        "ANSWER: {answer}\n\n"
        "SCORING RUBRIC:\n"
        "1 - Incoherent, contradictory, or unreadable\n"
        "2 - Poorly organized; hard to follow\n"
        "3 - Somewhat organized but could be clearer\n"
        "4 - Well-structured and easy to follow\n"
        "5 - Excellent organization, clear, professional quality\n\n"
        'Respond in JSON format ONLY:\n{{"score": <1-5>, "reasoning": "<brief explanation>"}}'
    ),
}


def _build_context(retrieved_chunks: list[dict[str, Any]], max_chars: int = 4000) -> str:
    parts = []
    total = 0
    for chunk in retrieved_chunks:
        content = chunk.get("content", "")
        if total + len(content) > max_chars:
            remaining = max_chars - total
            if remaining > 100:
                parts.append(content[:remaining] + "...")
            break
        parts.append(content)
        total += len(content)
    return "\n---\n".join(parts) if parts else "(no context available)"


def _cache_key(question: str, answer: str, context: str, metric_name: str) -> str:
    raw = f"{question}|||{answer}|||{context}|||{metric_name}"
    return hashlib.sha256(raw.encode()).hexdigest()


def _parse_judge_response(text: str) -> tuple[float | None, str]:
    """Extract score and reasoning from judge LLM response."""
    # Try direct JSON parse
    try:
        data = json.loads(text.strip())
        score = float(data.get("score", 0))
        reasoning = data.get("reasoning", "")
        if 1.0 <= score <= 5.0:
            return score, reasoning
    except (json.JSONDecodeError, ValueError, TypeError):
        pass

    # Try extracting JSON from markdown code fences
    match = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    if match:
        try:
            data = json.loads(match.group(1))
            score = float(data.get("score", 0))
            reasoning = data.get("reasoning", "")
            if 1.0 <= score <= 5.0:
                return score, reasoning
        except (json.JSONDecodeError, ValueError, TypeError):
            pass

    # Try finding score pattern in text
    match = re.search(r'"score"\s*:\s*(\d)', text)
    if match:
        score = float(match.group(1))
        if 1.0 <= score <= 5.0:
            return score, ""

    return None, ""


def compute_llm_judge(question: str, answer: str, retrieved_chunks: list[dict[str, Any]],
                      store: Any, config: Any) -> dict[str, float | None]:
    """Compute LLM-as-judge metrics. Returns dict with scores or None if unavailable."""
    if not config.judge_enabled or not config.judge_api_key:
        return {m: None for m in JUDGE_METRICS}

    context = _build_context(retrieved_chunks)
    results: dict[str, float | None] = {}

    for metric_name in JUDGE_METRICS:
        key = _cache_key(question, answer, context, metric_name)

        # Check cache
        cached = store.get_judge_cache(key)
        if cached:
            results[metric_name] = cached["score"]
            continue

        # Call judge LLM
        prompt = PROMPTS[metric_name].format(
            question=question, answer=answer, context=context
        )
        try:
            score, reasoning = _call_judge(prompt, config)
            if score is not None:
                store.set_judge_cache(key, metric_name, score, reasoning, config.judge_model)
            results[metric_name] = score
        except Exception as e:
            logger.warning("Judge LLM call failed for %s: %s", metric_name, e)
            results[metric_name] = None

    return results


def _call_judge(prompt: str, config: Any) -> tuple[float | None, str]:
    """Call judge LLM via OpenAI-compatible API."""
    import requests

    resp = requests.post(
        f"{config.judge_base_url.rstrip('/')}/chat/completions",
        headers={
            "Authorization": f"Bearer {config.judge_api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": config.judge_model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 300,
        },
        timeout=60,
    )
    resp.raise_for_status()
    data = resp.json()
    text = data["choices"][0]["message"]["content"]
    return _parse_judge_response(text)
