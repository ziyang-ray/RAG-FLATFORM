"""REST API server for Agent Evaluation Harness.

Follows the qms_agent_backend pattern: BaseHTTPRequestHandler + ThreadingHTTPServer.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse, parse_qs

from .config import EvalConfig
from .models import EvalStore
from .runner import EvalRunner, import_suite_from_yaml
from .reporter import generate_single_run_report, generate_comparison_report, export_results_csv

logger = logging.getLogger(__name__)


class EvalHandler(BaseHTTPRequestHandler):
    """HTTP request handler for evaluation API."""

    config: EvalConfig
    store: EvalStore

    def log_message(self, fmt, *args):
        logger.info(fmt, *args)

    def _json_response(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _ok(self, data: Any = None) -> None:
        self._json_response({"ok": True, "data": data})

    def _err(self, message: str, status: int = 400) -> None:
        self._json_response({"ok": False, "message": message}, status)

    def _read_json(self) -> dict | None:
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except json.JSONDecodeError:
            self._err("Invalid JSON body")
            return None

    def _path_parts(self) -> list[str]:
        return [p for p in urlparse(self.path).path.split("/") if p]

    def _query_params(self) -> dict[str, str]:
        params = parse_qs(urlparse(self.path).query)
        return {k: v[0] if len(v) == 1 else v for k, v in params.items()}

    # ==================== Routing ====================

    def do_GET(self) -> None:
        parts = self._path_parts()
        params = self._query_params()

        if len(parts) == 1 and parts[0] == "healthz":
            return self._ok({"status": "healthy"})

        # /v1/eval/suites
        if parts == ["v1", "eval", "suites"]:
            return self._list_suites()

        # /v1/eval/suites/<id>
        if len(parts) == 4 and parts[:3] == ["v1", "eval", "suites"]:
            return self._get_suite(parts[3])

        # /v1/eval/runs
        if parts == ["v1", "eval", "runs"]:
            return self._list_runs(params)

        # /v1/eval/runs/<id>
        if len(parts) == 4 and parts[:3] == ["v1", "eval", "runs"]:
            return self._get_run(parts[3])

        # /v1/eval/runs/<id>/results
        if len(parts) == 5 and parts[:3] == ["v1", "eval", "runs"] and parts[4] == "results":
            return self._get_run_results(parts[3])

        # /v1/eval/runs/<id>/export
        if len(parts) == 5 and parts[:3] == ["v1", "eval", "runs"] and parts[4] == "export":
            return self._export_run(parts[3], params)

        # /v1/eval/agents
        if parts == ["v1", "eval", "agents"]:
            return self._list_agents()

        self._err("Not found", 404)

    def do_POST(self) -> None:
        parts = self._path_parts()

        # /v1/eval/suites
        if parts == ["v1", "eval", "suites"]:
            return self._create_suite()

        # /v1/eval/suites/<id>/cases
        if len(parts) == 5 and parts[:3] == ["v1", "eval", "suites"] and parts[4] == "cases":
            return self._add_case(parts[3])

        # /v1/eval/runs
        if parts == ["v1", "eval", "runs"]:
            return self._start_run()

        # /v1/eval/compare
        if parts == ["v1", "eval", "compare"]:
            return self._compare_runs()

        self._err("Not found", 404)

    def do_DELETE(self) -> None:
        parts = self._path_parts()

        # /v1/eval/suites/<id>
        if len(parts) == 4 and parts[:3] == ["v1", "eval", "suites"]:
            return self._delete_suite(parts[3])

        # /v1/eval/runs/<id>
        if len(parts) == 4 and parts[:3] == ["v1", "eval", "runs"]:
            return self._delete_run(parts[3])

        self._err("Not found", 404)

    # ==================== Handlers ====================

    def _list_suites(self) -> None:
        self._ok(self.store.list_suites())

    def _get_suite(self, suite_id: str) -> None:
        suite = self.store.get_suite(suite_id)
        if not suite:
            return self._err("Suite not found", 404)
        suite["cases"] = self.store.get_cases(suite_id)
        self._ok(suite)

    def _create_suite(self) -> None:
        body = self._read_json()
        if body is None:
            return

        # From YAML path
        yaml_path = body.get("yaml_path")
        if yaml_path:
            try:
                suite_id = import_suite_from_yaml(self.store, yaml_path)
                self._ok({"suite_id": suite_id})
            except Exception as e:
                self._err(str(e))
            return

        # From JSON body
        name = body.get("name")
        if not name:
            return self._err("'name' or 'yaml_path' is required")
        try:
            suite_id = self.store.create_suite(
                name=name,
                description=body.get("description", ""),
                version=body.get("version", "1.0"),
                tags=body.get("tags"),
            )
            self._ok({"suite_id": suite_id})
        except Exception as e:
            self._err(str(e))

    def _delete_suite(self, suite_id: str) -> None:
        if self.store.delete_suite(suite_id):
            self._ok({"suite_id": suite_id})
        else:
            self._err("Suite not found", 404)

    def _add_case(self, suite_id: str) -> None:
        body = self._read_json()
        if body is None:
            return
        question = body.get("question")
        if not question:
            return self._err("'question' is required")
        try:
            case_id = self.store.add_case(
                suite_id=suite_id,
                question=question,
                reference_answer=body.get("reference_answer"),
                expected_output_contains=body.get("expected_output_contains"),
                expected_output_regex=body.get("expected_output_regex"),
                expected_tool_calls=body.get("expected_tool_calls"),
                reference_chunk_ids=body.get("reference_chunk_ids"),
                tags=body.get("tags"),
                metadata=body.get("metadata"),
            )
            self._ok({"case_id": case_id})
        except Exception as e:
            self._err(str(e))

    def _list_runs(self, params: dict) -> None:
        runs = self.store.list_runs(suite_id=params.get("suite_id"))
        self._ok(runs)

    def _get_run(self, run_id: str) -> None:
        run = self.store.get_run(run_id)
        if not run:
            return self._err("Run not found", 404)
        run["results"] = self.store.get_results(run_id)
        self._ok(run)

    def _get_run_results(self, run_id: str) -> None:
        results = self.store.get_results(run_id)
        self._ok(results)

    def _export_run(self, run_id: str, params: dict) -> None:
        fmt = params.get("format", "json")
        try:
            if fmt == "csv":
                content = export_results_csv(self.store, run_id)
                body = content.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/csv; charset=utf-8")
                self.send_header("Content-Disposition", f'attachment; filename="{run_id[:8]}.csv"')
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            else:
                report = generate_single_run_report(self.store, run_id)
                self._ok(report)
        except Exception as e:
            self._err(str(e))

    def _start_run(self) -> None:
        body = self._read_json()
        if body is None:
            return
        suite_id = body.get("suite_id")
        if not suite_id:
            return self._err("'suite_id' is required")

        # Run in background thread
        runner = EvalRunner(self.config, self.store)

        def _run():
            try:
                runner.run(
                    suite_id=suite_id,
                    agent_title=body.get("agent_title"),
                    agent_id=body.get("agent_id"),
                    dialog_id=body.get("dialog_id"),
                    name=body.get("name", ""),
                    tag_filter=body.get("tag_filter"),
                    timeout_sec=body.get("timeout_sec"),
                    skip_judge=body.get("skip_judge", False),
                )
            except Exception as e:
                logger.error("Background run failed: %s", e)

        # Create run first to get the ID, then let the runner update it
        # Actually, the runner creates the run itself, so we need a different approach
        # We'll return a "started" message and let the user poll
        t = threading.Thread(target=_run, daemon=True)
        t.start()
        self._ok({"message": "Evaluation started. Poll GET /v1/eval/runs to check status."})

    def _delete_run(self, run_id: str) -> None:
        if self.store.delete_run(run_id):
            self._ok({"run_id": run_id})
        else:
            self._err("Run not found", 404)

    def _compare_runs(self) -> None:
        body = self._read_json()
        if body is None:
            return
        run_ids = body.get("run_ids")
        if not run_ids or len(run_ids) < 2:
            return self._err("'run_ids' needs at least 2 IDs")
        try:
            report = generate_comparison_report(self.store, run_ids)
            self._ok(report)
        except Exception as e:
            self._err(str(e))

    def _list_agents(self) -> None:
        try:
            from ragflow_sdk.ragflow import RAGFlow
            rag = RAGFlow(api_key=self.config.ragflow_api_key,
                          base_url=self.config.ragflow_base_url)
            agents = rag.list_agents(page_size=200)
            self._ok([
                {"id": a.id, "title": getattr(a, "title", ""), "description": getattr(a, "description", "")}
                for a in agents
            ])
        except Exception as e:
            self._err(str(e), 500)


def start_server(config: EvalConfig, host: str = "0.0.0.0", port: int = 9391) -> None:
    """Start the evaluation harness REST API server."""
    store = EvalStore(config.db_path)

    # Inject config and store into handler class
    handler_class = type("ConfiguredEvalHandler", (EvalHandler,), {
        "config": config,
        "store": store,
    })

    server = ThreadingHTTPServer((host, port), handler_class)
    logger.info("Eval harness server listening on %s:%d", host, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        server.shutdown()
