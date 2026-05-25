from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any

from .models import EvalStore


def generate_single_run_report(store: EvalStore, run_id: str) -> dict[str, Any]:
    """Generate a report for a single evaluation run."""
    run = store.get_run(run_id)
    if not run:
        raise ValueError(f"Run not found: {run_id}")

    results = store.get_results(run_id)
    suite = store.get_suite(run["suite_id"])

    return {
        "report_type": "single_run",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "run": {
            "run_id": run["run_id"],
            "suite_name": suite["name"] if suite else "",
            "agent_title": run.get("agent_title", ""),
            "agent_id": run.get("agent_id", ""),
            "status": run["status"],
            "created_at": run["created_at"],
            "completed_at": run.get("completed_at"),
            "metrics_summary": run.get("metrics_summary") or {},
        },
        "results": [
            {
                "case_id": r["case_id"],
                "generated_answer": r.get("generated_answer", ""),
                "execution_time_sec": r.get("execution_time_sec", 0),
                "metrics": r.get("metrics") or {},
                "error": r.get("error"),
            }
            for r in results
        ],
    }


def generate_comparison_report(store: EvalStore, run_ids: list[str]) -> dict[str, Any]:
    """Generate an A/B comparison report across multiple runs."""
    if len(run_ids) < 2:
        raise ValueError("Need at least 2 runs to compare")

    runs_data = []
    for rid in run_ids:
        run = store.get_run(rid)
        if not run:
            raise ValueError(f"Run not found: {rid}")
        results = store.get_results(rid)
        runs_data.append({"run": run, "results": results})

    # Metric comparison
    metric_comparison: dict[str, dict] = {}
    all_metric_names: set[str] = set()

    for rd in runs_data:
        for r in rd["results"]:
            metrics = r.get("metrics") or {}
            all_metric_names.update(metrics.keys())

    for metric in sorted(all_metric_names):
        values = []
        for rd in runs_data:
            vals = [r["metrics"].get(metric) for r in rd["results"]
                    if r.get("metrics") and r["metrics"].get(metric) is not None]
            values.append(sum(vals) / len(vals) if vals else None)

        if all(v is not None for v in values):
            entry = {}
            for i, rd in enumerate(runs_data):
                entry[f"run{i+1}"] = round(values[i], 4)
            entry["delta"] = round(values[-1] - values[0], 4)
            entry["improved"] = entry["delta"] > 0
            metric_comparison[metric] = entry

    # Case-level comparison
    case_comparison = []
    # Group results by case_id
    case_map: dict[str, list[dict]] = {}
    for i, rd in enumerate(runs_data):
        for r in rd["results"]:
            cid = r["case_id"]
            if cid not in case_map:
                case_map[cid] = [None] * len(runs_data)
            case_map[cid][i] = r

    for cid, case_results in sorted(case_map.items()):
        entry = {"case_id": cid, "results": {}}
        scores = []
        for i, cr in enumerate(case_results):
            if cr:
                comp = (cr.get("metrics") or {}).get("composite_score")
                entry["results"][f"run{i+1}"] = {
                    "composite_score": comp,
                    "execution_time_sec": cr.get("execution_time_sec", 0),
                }
                if comp is not None:
                    scores.append(comp)
            else:
                entry["results"][f"run{i+1}"] = None

        # Detect regression (score dropped from first to last run)
        if len(scores) >= 2:
            entry["regression"] = scores[-1] < scores[0] * 0.9  # 10% drop threshold
        case_comparison.append(entry)

    regressions = [c["case_id"] for c in case_comparison if c.get("regression")]
    improvements = [c["case_id"] for c in case_comparison
                    if not c.get("regression") and len(c.get("results", {})) >= 2]

    return {
        "report_type": "comparison",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": [
            {
                "run_id": rd["run"]["run_id"],
                "agent_title": rd["run"].get("agent_title", ""),
                "status": rd["run"]["status"],
                "metrics_summary": rd["run"].get("metrics_summary") or {},
            }
            for rd in runs_data
        ],
        "metric_comparison": metric_comparison,
        "case_comparison": case_comparison,
        "regressions": regressions,
        "improvements": improvements,
    }


def export_results_csv(store: EvalStore, run_id: str) -> str:
    """Export run results as CSV string."""
    results = store.get_results(run_id)
    if not results:
        return ""

    # Collect all metric keys
    all_metrics: set[str] = set()
    for r in results:
        all_metrics.update((r.get("metrics") or {}).keys())
    metric_cols = sorted(all_metrics)

    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow(["case_id", "question", "generated_answer", "execution_time_sec", "error"] + metric_cols)

    # Rows
    for r in results:
        metrics = r.get("metrics") or {}
        writer.writerow([
            r.get("case_id", ""),
            r.get("question", ""),
            r.get("generated_answer", ""),
            r.get("execution_time_sec", 0),
            r.get("error", ""),
        ] + [metrics.get(m, "") for m in metric_cols])

    return output.getvalue()
