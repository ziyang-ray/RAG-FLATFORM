from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable


@dataclass
class MemoryItem:
    user_id: str
    memory_type: str
    content: str
    weight: float
    last_seen: str


class MemoryStore:
    """Local typed-memory store for multi-turn preference adaptation.

    Memory types:
    - profile: user's role/team/system context
    - preference: answer style preference
    - objective: current work goal
    - glossary: terminology mapping
    - history: recent key interactions
    """

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS typed_memory (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    memory_type TEXT NOT NULL,
                    content TEXT NOT NULL,
                    weight REAL NOT NULL DEFAULT 1.0,
                    last_seen TEXT NOT NULL,
                    UNIQUE(user_id, memory_type, content)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    references_json TEXT NOT NULL,
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_profile (
                    user_id TEXT PRIMARY KEY,
                    profile_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS feedback_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    session_id TEXT NOT NULL,
                    question TEXT NOT NULL,
                    original_answer TEXT NOT NULL,
                    corrected_answer TEXT NOT NULL,
                    eval_signal TEXT NOT NULL,
                    score REAL NOT NULL DEFAULT 1.0,
                    created_at TEXT NOT NULL
                )
                """
            )

    def upsert_memories(self, items: Iterable[MemoryItem]) -> None:
        now = datetime.now(timezone.utc).isoformat()
        with self._connect() as conn:
            for item in items:
                conn.execute(
                    """
                    INSERT INTO typed_memory(user_id, memory_type, content, weight, last_seen)
                    VALUES(?, ?, ?, ?, ?)
                    ON CONFLICT(user_id, memory_type, content)
                    DO UPDATE SET
                        weight = MIN(10.0, typed_memory.weight + excluded.weight * 0.2),
                        last_seen = excluded.last_seen
                    """,
                    (item.user_id, item.memory_type, item.content.strip(), item.weight, now),
                )

    def get_top_memories(self, user_id: str, limit: int = 12) -> list[MemoryItem]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, memory_type, content, weight, last_seen
                FROM typed_memory
                WHERE user_id = ?
                ORDER BY weight DESC, last_seen DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        return [MemoryItem(**dict(r)) for r in rows]

    def log_interaction(
        self,
        user_id: str,
        session_id: str,
        question: str,
        answer: str,
        references: list[dict],
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO conversation_log(user_id, session_id, question, answer, references_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    question,
                    answer,
                    json.dumps(references, ensure_ascii=False),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def recent_history(self, user_id: str, session_id: str, limit: int = 5) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT question, answer, references_json, created_at
                FROM conversation_log
                WHERE user_id = ? AND session_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, session_id, limit),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            result.append(
                {
                    "question": row["question"],
                    "answer": row["answer"],
                    "references": json.loads(row["references_json"]),
                    "created_at": row["created_at"],
                }
            )
        return result

    def recent_history_all_sessions(self, user_id: str, limit: int = 8) -> list[dict]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT session_id, question, answer, references_json, created_at
                FROM conversation_log
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (user_id, limit),
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            result.append(
                {
                    "session_id": row["session_id"],
                    "question": row["question"],
                    "answer": row["answer"],
                    "references": json.loads(row["references_json"]),
                    "created_at": row["created_at"],
                }
            )
        return result

    def refresh_user_profile(self, user_id: str) -> dict:
        """Rebuild and persist long-term profile from all historical memories."""
        with self._connect() as conn:
            mem_rows = conn.execute(
                """
                SELECT memory_type, content, weight, last_seen
                FROM typed_memory
                WHERE user_id = ?
                ORDER BY weight DESC, last_seen DESC
                """,
                (user_id,),
            ).fetchall()
            log_rows = conn.execute(
                """
                SELECT question, answer, created_at
                FROM conversation_log
                WHERE user_id = ?
                ORDER BY id DESC
                LIMIT 200
                """,
                (user_id,),
            ).fetchall()

            profile_items = [r["content"] for r in mem_rows if r["memory_type"] == "profile"]
            preference_items = [r["content"] for r in mem_rows if r["memory_type"] == "preference"]
            objective_items = [r["content"] for r in mem_rows if r["memory_type"] == "objective"]
            glossary_items = [r["content"] for r in mem_rows if r["memory_type"] == "glossary"]

            glossary_map: dict[str, str] = {}
            for g in glossary_items:
                if "=" in g:
                    k, v = g.split("=", 1)
                    k = k.strip()
                    v = v.strip()
                    if k and v:
                        glossary_map[k] = v

            token_freq: dict[str, int] = {}
            stop_words = {
                "请", "帮", "一下", "这个", "那个", "我们", "你们", "以及", "还有", "如何", "什么", "根据", "关于",
                "the", "and", "for", "with", "that", "this", "from", "what", "how", "please",
            }
            for row in log_rows:
                text = f"{row['question']} {row['answer'][:200]}"
                tokens = re.findall(r"[A-Za-z]{3,}|[\u4e00-\u9fa5]{2,}", text)
                for t in tokens:
                    tk = t.lower()
                    if tk in stop_words:
                        continue
                    token_freq[tk] = token_freq.get(tk, 0) + 1
            top_topics = [k for k, _ in sorted(token_freq.items(), key=lambda x: x[1], reverse=True)[:8]]

            profile = {
                "user_id": user_id,
                "persona": profile_items[:6],
                "preferences": preference_items[:8],
                "long_term_objectives": objective_items[:6],
                "glossary": glossary_map,
                "recurring_topics": top_topics,
                "memory_count": len(mem_rows),
                "conversation_count": len(log_rows),
                "updated_at": datetime.now(timezone.utc).isoformat(),
            }

            conn.execute(
                """
                INSERT INTO user_profile(user_id, profile_json, updated_at)
                VALUES(?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    profile_json = excluded.profile_json,
                    updated_at = excluded.updated_at
                """,
                (user_id, json.dumps(profile, ensure_ascii=False), profile["updated_at"]),
            )

        return profile

    def get_user_profile(self, user_id: str, auto_build: bool = True) -> dict:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT profile_json
                FROM user_profile
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if row:
            return json.loads(row["profile_json"])
        if auto_build:
            return self.refresh_user_profile(user_id)
        return {}

    def decay_memories_by_keywords(
        self,
        user_id: str,
        keywords: list[str],
        decay_factor: float = 0.85,
    ) -> int:
        """Downweight stale/incorrect memories containing feedback-negative keywords."""
        clean_keywords = [k.strip() for k in keywords if k and k.strip()]
        if not clean_keywords:
            return 0

        affected = 0
        factor = min(0.99, max(0.1, decay_factor))
        with self._connect() as conn:
            for kw in clean_keywords:
                cur = conn.execute(
                    """
                    UPDATE typed_memory
                    SET weight = MAX(0.1, weight * ?),
                        last_seen = ?
                    WHERE user_id = ?
                      AND content LIKE ?
                    """,
                    (
                        factor,
                        datetime.now(timezone.utc).isoformat(),
                        user_id,
                        f"%{kw}%",
                    ),
                )
                affected += cur.rowcount or 0
        return affected

    def log_feedback(
        self,
        user_id: str,
        session_id: str,
        question: str,
        original_answer: str,
        corrected_answer: str,
        eval_signal: str,
        score: float,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO feedback_log(
                    user_id, session_id, question, original_answer, corrected_answer,
                    eval_signal, score, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    session_id,
                    question,
                    original_answer,
                    corrected_answer,
                    eval_signal,
                    float(score),
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
