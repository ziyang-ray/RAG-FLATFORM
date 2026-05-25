from __future__ import annotations

import re
from typing import Any


def compute_deterministic(question: str, answer: str, case: dict[str, Any]) -> dict[str, float]:
    """Compute deterministic metrics: string matching, regex, basic quality checks."""
    metrics: dict[str, float] = {}

    # has_answer: 1.0 if non-empty, 0.0 otherwise
    metrics["has_answer"] = 1.0 if answer and answer.strip() else 0.0

    # answer_length: character count
    metrics["answer_length"] = float(len(answer)) if answer else 0.0

    # contains_check: fraction of expected substrings found
    expected = case.get("expected_output_contains") or []
    if expected:
        answer_lower = (answer or "").lower()
        found = sum(1 for s in expected if s.lower() in answer_lower)
        metrics["contains_score"] = found / len(expected)

    # regex_check: fraction of expected patterns matched
    patterns = case.get("expected_output_regex") or []
    if patterns:
        matched = sum(1 for p in patterns if re.search(p, answer or "", re.IGNORECASE))
        metrics["regex_score"] = matched / len(patterns)

    return metrics
