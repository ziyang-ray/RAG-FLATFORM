from __future__ import annotations

import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from timeit import default_timer as timer
from typing import Any

# Inject SDK path
_SDK_PATH = str(Path(__file__).resolve().parents[2] / "sdk" / "python")
if _SDK_PATH not in sys.path:
    sys.path.insert(0, _SDK_PATH)

import yaml
from ragflow_sdk.ragflow import RAGFlow

from .config import EvalConfig
from .models import EvalStore
from .metrics.deterministic import compute_deterministic
from .metrics.retrieval import compute_retrieval
from .metrics.llm_judge import compute_llm_judge
from .metrics.composite import compute_composite, compute_summary

logger = logging.getLogger(__name__)


def load_suite_from_yaml(path: str) -> dict[str, Any]:
    """Load a test suite definition from a YAML file."""
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if not isinstance(data, dict):
        raise ValueError(f"Invalid YAML structure in {path}")

    # Validate
    if "cases" not in data or not isinstance(data["cases"], list):
        raise ValueError(f"Suite must have a 'cases' list: {path}")

    for i, case in enumerate(data["cases"]):
        if not case.get("question"):
            raise ValueError(f"Case {i} must have a 'question' field: {path}")

    return data


def import_suite_from_yaml(store: EvalStore, path: str) -> str:
    """Import a test suite from YAML into the store. Returns suite_id."""
    data = load_suite_from_yaml(path)
    suite_id = store.create_suite(
        name=data["name"],
        description=data.get("description", ""),
        version=data.get("version", "1.0"),
        tags=data.get("tags"),
        source_path=path,
    )
    defaults = data.get("defaults") or {}
    default_tags = defaults.get("tags", [])

    for case_data in data["cases"]:
        store.add_case(
            suite_id=suite_id,
            question=case_data["question"],
            reference_answer=case_data.get("reference_answer"),
            expected_output_contains=case_data.get("expected_output_contains"),
            expected_output_regex=case_data.get("expected_output_regex"),
            expected_tool_calls=case_data.get("expected_tool_calls"),
            reference_chunk_ids=case_data.get("reference_chunk_ids"),
            tags=case_data.get("tags") or default_tags,
            metadata=case_data.get("metadata"),
            case_id=case_data.get("id"),
        )

    return suite_id


class RunResult:
    """Result of an evaluation run."""

    def __init__(self, run_id: str, suite_id: str, status: str,
                 results: list[dict], metrics_summary: dict):
        self.run_id = run_id
        self.suite_id = suite_id
        self.status = status
        self.results = results
        self.metrics_summary = metrics_summary


class EvalRunner:
    """Core evaluation execution engine."""

    def __init__(self, config: EvalConfig, store: EvalStore):
        self.config = config
        self.store = store
        self.rag = RAGFlow(api_key=config.ragflow_api_key, base_url=config.ragflow_base_url)

    def run(self, suite_id: str, agent_title: str | None = None,
            agent_id: str | None = None, dialog_id: str | None = None,
            name: str = "", tag_filter: list[str] | None = None,
            timeout_sec: int | None = None, skip_judge: bool = False) -> RunResult:
        """Execute an evaluation run.

        Args:
            suite_id: ID of the test suite to run
            agent_title: Find agent by title (takes precedence if agent_id not set)
            agent_id: RAGFlow agent ID directly
            dialog_id: Dialog ID for backward compat
            name: Human-readable run name
            tag_filter: Only run cases matching these tags
            timeout_sec: Per-case timeout (default from config)
            skip_judge: Skip LLM-as-judge metrics

        Returns:
            RunResult with all per-case results and summary metrics
        """
        timeout = timeout_sec or self.config.default_timeout_sec

        # Load suite
        suite = self.store.get_suite(suite_id)
        if not suite:
            raise ValueError(f"Suite not found: {suite_id}")

        # Resolve target
        target_agent = None
        target_agent_id = agent_id
        target_agent_title = agent_title

        if not agent_id and not dialog_id:
            # Try suite default target
            cases = self.store.get_cases(suite_id)
            if not cases:
                raise ValueError(f"No cases in suite: {suite_id}")
        else:
            cases = self.store.get_cases(suite_id)

        if agent_title and not agent_id:
            agents = self.rag.list_agents(title=agent_title, page_size=1)
            if not agents:
                raise ValueError(f"Agent not found by title: {agent_title}")
            target_agent = agents[0]
            target_agent_id = target_agent.id
            target_agent_title = getattr(target_agent, "title", agent_title)

        # Filter by tags
        if tag_filter:
            tag_set = set(tag_filter)
            cases = [c for c in cases if tag_set & set(c.get("tags") or [])]

        if not cases:
            raise ValueError("No cases to evaluate after filtering")

        # DSL snapshot
        dsl_snapshot = None
        if target_agent:
            dsl_snapshot = json.dumps(target_agent.dsl) if hasattr(target_agent, "dsl") else None

        # Create run record
        run_id = self.store.create_run(
            suite_id=suite_id,
            name=name,
            agent_id=target_agent_id,
            agent_title=target_agent_title,
            dialog_id=dialog_id,
            dsl_snapshot=dsl_snapshot,
            config={"timeout_sec": timeout, "skip_judge": skip_judge, "tag_filter": tag_filter},
        )

        all_results: list[dict] = []
        error_msg = None

        try:
            logger.info("Starting eval run %s with %d cases", run_id[:8], len(cases))

            for i, case in enumerate(cases):
                logger.info("  [%d/%d] case=%s", i + 1, len(cases), case.get("case_id", "")[:8])
                result = self._evaluate_case(
                    run_id=run_id,
                    case=case,
                    target_agent=target_agent,
                    target_agent_id=target_agent_id,
                    dialog_id=dialog_id,
                    timeout=timeout,
                    skip_judge=skip_judge,
                )
                all_results.append(result)

                # Store individual result
                self.store.add_result(
                    run_id=run_id,
                    case_id=case["case_id"],
                    generated_answer=result.get("generated_answer", ""),
                    retrieved_chunks=result.get("retrieved_chunks"),
                    execution_time_sec=result.get("execution_time_sec", 0),
                    error=result.get("error"),
                    metrics=result.get("metrics"),
                )

        except Exception as e:
            error_msg = str(e)
            logger.error("Eval run failed: %s", e)

        # Compute summary
        summary = compute_summary(all_results, self.config.default_pass_threshold)

        # Update run
        status = "COMPLETED" if not error_msg else "FAILED"
        self.store.update_run(
            run_id,
            status=status,
            metrics_summary=summary,
            completed_at=datetime.now(timezone.utc).isoformat(),
            error_message=error_msg,
        )

        return RunResult(
            run_id=run_id,
            suite_id=suite_id,
            status=status,
            results=all_results,
            metrics_summary=summary,
        )

    def _evaluate_case(self, run_id: str, case: dict, target_agent: Any,
                       target_agent_id: str | None, dialog_id: str | None,
                       timeout: int, skip_judge: bool) -> dict:
        """Evaluate a single test case."""
        case_id = case["case_id"]
        question = case["question"]
        result: dict[str, Any] = {
            "case_id": case_id,
            "question": question,
            "generated_answer": "",
            "retrieved_chunks": [],
            "execution_time_sec": 0,
            "error": None,
            "metrics": {},
        }

        start_time = timer()

        try:
            # Get or create agent
            agent = target_agent
            if not agent and target_agent_id:
                agents = self.rag.list_agents(id=target_agent_id, page_size=1)
                if not agents:
                    result["error"] = f"Agent not found: {target_agent_id}"
                    return result
                agent = agents[0]

            if agent:
                # Agent mode: create fresh session per case
                session_name = f"eval-{run_id[:8]}-{case_id[:8]}"
                session = agent.create_session(name=session_name)
                msg = next(session.ask(question, stream=False))
                result["generated_answer"] = msg.content or ""
                if msg.reference:
                    result["retrieved_chunks"] = msg.reference if isinstance(msg.reference, list) else []
            elif dialog_id:
                # Dialog mode (backward compat) - not implemented yet
                result["error"] = "Dialog evaluation not yet implemented"
                return result
            else:
                result["error"] = "No agent or dialog target specified"
                return result

        except Exception as e:
            result["error"] = str(e)
            logger.warning("Case %s failed: %s", case_id[:8], e)

        finally:
            result["execution_time_sec"] = timer() - start_time

        # Compute metrics
        answer = result["generated_answer"]
        chunks = result["retrieved_chunks"]

        # Deterministic metrics
        det_metrics = compute_deterministic(question, answer, case)
        result["metrics"].update(det_metrics)

        # Retrieval metrics
        ref_chunk_ids = case.get("reference_chunk_ids")
        if ref_chunk_ids:
            ret_metrics = compute_retrieval(chunks, ref_chunk_ids)
            result["metrics"].update(ret_metrics)

        # LLM-as-judge metrics
        if not skip_judge and not result["error"]:
            try:
                judge_metrics = compute_llm_judge(question, answer, chunks, self.store, self.config)
                result["metrics"].update(judge_metrics)
            except Exception as e:
                logger.warning("Judge metrics failed for case %s: %s", case_id[:8], e)

        # Composite score
        comp = compute_composite(result["metrics"])
        if comp is not None:
            result["metrics"]["composite_score"] = comp

        return result
