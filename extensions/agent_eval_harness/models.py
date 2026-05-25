from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional


def _uuid() -> str:
    return uuid.uuid4().hex


class EvalStore:
    """SQLite storage for evaluation suites, runs, and results."""

    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS eval_suites (
                    suite_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    version TEXT DEFAULT '1.0',
                    tags TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    source_path TEXT
                );

                CREATE TABLE IF NOT EXISTS eval_cases (
                    case_id TEXT PRIMARY KEY,
                    suite_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    reference_answer TEXT,
                    expected_output_contains TEXT,
                    expected_output_regex TEXT,
                    expected_tool_calls TEXT,
                    reference_chunk_ids TEXT,
                    tags TEXT,
                    metadata TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (suite_id) REFERENCES eval_suites(suite_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_cases_suite ON eval_cases(suite_id);

                CREATE TABLE IF NOT EXISTS eval_runs (
                    run_id TEXT PRIMARY KEY,
                    suite_id TEXT NOT NULL,
                    agent_id TEXT,
                    agent_title TEXT,
                    dialog_id TEXT,
                    dsl_snapshot TEXT,
                    name TEXT,
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    metrics_summary TEXT,
                    config TEXT,
                    created_at TEXT NOT NULL,
                    completed_at TEXT,
                    error_message TEXT,
                    FOREIGN KEY (suite_id) REFERENCES eval_suites(suite_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_runs_suite ON eval_runs(suite_id, created_at);

                CREATE TABLE IF NOT EXISTS eval_results (
                    result_id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL,
                    case_id TEXT NOT NULL,
                    generated_answer TEXT,
                    retrieved_chunks TEXT,
                    execution_time_sec REAL NOT NULL DEFAULT 0,
                    error TEXT,
                    metrics TEXT,
                    raw_response TEXT,
                    created_at TEXT NOT NULL,
                    FOREIGN KEY (run_id) REFERENCES eval_runs(run_id) ON DELETE CASCADE,
                    FOREIGN KEY (case_id) REFERENCES eval_cases(case_id) ON DELETE CASCADE
                );
                CREATE INDEX IF NOT EXISTS idx_results_run ON eval_results(run_id);

                CREATE TABLE IF NOT EXISTS eval_comparisons (
                    comparison_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    run_ids TEXT NOT NULL,
                    results TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS eval_judge_cache (
                    cache_key TEXT PRIMARY KEY,
                    metric_name TEXT NOT NULL,
                    score REAL NOT NULL,
                    reasoning TEXT,
                    model TEXT,
                    created_at TEXT NOT NULL
                );
            """)

    # ==================== Suite CRUD ====================

    def create_suite(self, name: str, description: str = "", version: str = "1.0",
                     tags: list[str] | None = None, source_path: str = "") -> str:
        suite_id = _uuid()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_suites (suite_id, name, description, version, tags, created_at, updated_at, source_path) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (suite_id, name, description, version, json.dumps(tags or []), now, now, source_path),
            )
        return suite_id

    def get_suite(self, suite_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM eval_suites WHERE suite_id = ?", (suite_id,)).fetchone()
            return dict(row) if row else None

    def list_suites(self) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute("SELECT * FROM eval_suites ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_suite(self, suite_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM eval_suites WHERE suite_id = ?", (suite_id,))
            return cur.rowcount > 0

    # ==================== Case CRUD ====================

    def add_case(self, suite_id: str, question: str, reference_answer: str | None = None,
                 expected_output_contains: list[str] | None = None,
                 expected_output_regex: list[str] | None = None,
                 expected_tool_calls: list[str] | None = None,
                 reference_chunk_ids: list[str] | None = None,
                 tags: list[str] | None = None,
                 metadata: dict | None = None,
                 case_id: str | None = None) -> str:
        case_id = case_id or _uuid()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_cases (case_id, suite_id, question, reference_answer, "
                "expected_output_contains, expected_output_regex, expected_tool_calls, "
                "reference_chunk_ids, tags, metadata, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (case_id, suite_id, question, reference_answer,
                 json.dumps(expected_output_contains), json.dumps(expected_output_regex),
                 json.dumps(expected_tool_calls), json.dumps(reference_chunk_ids),
                 json.dumps(tags or []), json.dumps(metadata or {}), now),
            )
        return case_id

    def get_cases(self, suite_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_cases WHERE suite_id = ? ORDER BY created_at", (suite_id,)
            ).fetchall()
            return [self._parse_case(r) for r in rows]

    def get_case(self, case_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM eval_cases WHERE case_id = ?", (case_id,)).fetchone()
            return self._parse_case(row) if row else None

    def delete_case(self, case_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM eval_cases WHERE case_id = ?", (case_id,))
            return cur.rowcount > 0

    @staticmethod
    def _parse_case(row: sqlite3.Row) -> dict:
        d = dict(row)
        for field in ("expected_output_contains", "expected_output_regex",
                       "expected_tool_calls", "reference_chunk_ids", "tags", "metadata"):
            if d.get(field) and isinstance(d[field], str):
                d[field] = json.loads(d[field])
        return d

    # ==================== Run CRUD ====================

    def create_run(self, suite_id: str, name: str = "", agent_id: str | None = None,
                   agent_title: str | None = None, dialog_id: str | None = None,
                   dsl_snapshot: str | None = None, config: dict | None = None) -> str:
        run_id = _uuid()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_runs (run_id, suite_id, agent_id, agent_title, dialog_id, "
                "dsl_snapshot, name, status, config, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?, ?)",
                (run_id, suite_id, agent_id, agent_title, dialog_id,
                 dsl_snapshot, name or f"Run {now[:19]}", json.dumps(config or {}), now),
            )
        return run_id

    def update_run(self, run_id: str, **kwargs) -> bool:
        allowed = {"status", "metrics_summary", "completed_at", "error_message", "dsl_snapshot"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields:
            return False
        # Serialize dict fields
        for k in ("metrics_summary",):
            if k in fields and isinstance(fields[k], dict):
                fields[k] = json.dumps(fields[k])
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        values = list(fields.values()) + [run_id]
        with self._connect() as conn:
            cur = conn.execute(f"UPDATE eval_runs SET {set_clause} WHERE run_id = ?", values)
            return cur.rowcount > 0

    def get_run(self, run_id: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM eval_runs WHERE run_id = ?", (run_id,)).fetchone()
            if not row:
                return None
            d = dict(row)
            if d.get("metrics_summary") and isinstance(d["metrics_summary"], str):
                d["metrics_summary"] = json.loads(d["metrics_summary"])
            if d.get("config") and isinstance(d["config"], str):
                d["config"] = json.loads(d["config"])
            return d

    def list_runs(self, suite_id: str | None = None) -> list[dict]:
        with self._connect() as conn:
            if suite_id:
                rows = conn.execute(
                    "SELECT * FROM eval_runs WHERE suite_id = ? ORDER BY created_at DESC",
                    (suite_id,),
                ).fetchall()
            else:
                rows = conn.execute("SELECT * FROM eval_runs ORDER BY created_at DESC").fetchall()
            return [dict(r) for r in rows]

    def delete_run(self, run_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute("DELETE FROM eval_runs WHERE run_id = ?", (run_id,))
            return cur.rowcount > 0

    # ==================== Result CRUD ====================

    def add_result(self, run_id: str, case_id: str, generated_answer: str = "",
                   retrieved_chunks: list | None = None, execution_time_sec: float = 0,
                   error: str | None = None, metrics: dict | None = None,
                   raw_response: str | None = None) -> str:
        result_id = _uuid()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_results (result_id, run_id, case_id, generated_answer, "
                "retrieved_chunks, execution_time_sec, error, metrics, raw_response, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result_id, run_id, case_id, generated_answer,
                 json.dumps(retrieved_chunks or []), execution_time_sec, error,
                 json.dumps(metrics or {}), raw_response, now),
            )
        return result_id

    def get_results(self, run_id: str) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM eval_results WHERE run_id = ? ORDER BY created_at", (run_id,)
            ).fetchall()
            return [self._parse_result(r) for r in rows]

    @staticmethod
    def _parse_result(row: sqlite3.Row) -> dict:
        d = dict(row)
        for field in ("retrieved_chunks", "metrics"):
            if d.get(field) and isinstance(d[field], str):
                d[field] = json.loads(d[field])
        return d

    # ==================== Judge Cache ====================

    def get_judge_cache(self, cache_key: str) -> dict | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM eval_judge_cache WHERE cache_key = ?", (cache_key,)
            ).fetchone()
            return dict(row) if row else None

    def set_judge_cache(self, cache_key: str, metric_name: str, score: float,
                        reasoning: str, model: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO eval_judge_cache "
                "(cache_key, metric_name, score, reasoning, model, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (cache_key, metric_name, score, reasoning, model, now),
            )

    # ==================== Comparison ====================

    def save_comparison(self, name: str, run_ids: list[str], results: dict) -> str:
        comp_id = _uuid()
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            conn.execute(
                "INSERT INTO eval_comparisons (comparison_id, name, run_ids, results, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (comp_id, name, json.dumps(run_ids), json.dumps(results), now),
            )
        return comp_id
