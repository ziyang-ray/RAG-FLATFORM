from __future__ import annotations

import statistics
from typing import Any

# Default weights for composite scoring
DEFAULT_WEIGHTS = {
    "contains_score": 0.20,
    "regex_score": 0.10,
    "faithfulness": 0.25,
    "answer_relevance": 0.25,
    "context_relevance": 0.10,
    "coherence": 0.10,
}

# LLM judge metrics use 1-5 scale
_JUDGE_METRICS = {"faithfulness", "answer_relevance", "context_relevance", "coherence"}


def compute_composite(all_metrics: dict[str, Any],
                      weights: dict[str, float] | None = None) -> float | None:
    """Compute weighted average score (0-1 scale) from individual metrics.

    Returns None if no weighted metrics are available.
    """
    weights = weights or DEFAULT_WEIGHTS
    total_weight = 0.0
    weighted_sum = 0.0

    for metric_name, weight in weights.items():
        val = all_metrics.get(metric_name)
        if val is None:
            continue
        val = float(val)
        # Normalize LLM judge scores from 1-5 to 0-1
        if metric_name in _JUDGE_METRICS:
            val = (val - 1.0) / 4.0
        weighted_sum += val * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else None


def compute_summary(results: list[dict[str, Any]],
                    pass_threshold: float = 0.6) -> dict[str, Any]:
    """Compute summary metrics across all case results.

    Each result dict should have a 'metrics' key with per-case metrics.
    """
    if not results:
        return {}

    # Collect all metric values
    metric_values: dict[str, list[float]] = {}
    composite_scores: list[float] = []
    execution_times: list[float] = []
    error_count = 0

    for r in results:
        if r.get("error"):
            error_count += 1
            continue
        metrics = r.get("metrics") or {}
        for k, v in metrics.items():
            if v is not None:
                metric_values.setdefault(k, []).append(float(v))
        comp = metrics.get("composite_score")
        if comp is not None:
            composite_scores.append(float(comp))
        execution_times.append(float(r.get("execution_time_sec", 0)))

    summary: dict[str, Any] = {}

    # Per-metric averages
    for k, vals in metric_values.items():
        if vals:
            summary[f"avg_{k}"] = statistics.mean(vals)

    # Composite score stats
    if composite_scores:
        summary["avg_composite_score"] = statistics.mean(composite_scores)
        sorted_cs = sorted(composite_scores)
        n = len(sorted_cs)
        summary["p50_composite_score"] = sorted_cs[n // 2]
        summary["p90_composite_score"] = sorted_cs[int(n * 0.9)] if n > 1 else sorted_cs[0]

    # Pass rate
    total = len(results)
    passed = sum(1 for cs in composite_scores if cs >= pass_threshold)
    summary["pass_rate"] = passed / total if total > 0 else 0.0
    summary["total_cases"] = total
    summary["passed"] = passed
    summary["errors"] = error_count

    # Execution time
    if execution_times:
        summary["avg_execution_time_sec"] = statistics.mean(execution_times)

    return summary
