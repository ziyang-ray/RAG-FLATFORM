from __future__ import annotations

import os
import re
import sys
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from .memory_store import MemoryItem, MemoryStore

# Make local SDK importable without touching global env.
SDK_PATH = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from ragflow_sdk.ragflow import RAGFlow  # type: ignore  # noqa: E402


@dataclass
class QMSAgentConfig:
    api_key: str
    base_url: str = "http://127.0.0.1:9380"
    chat_id: str = ""
    chat_name: str = "QMS Assistant"
    dataset_ids: list[str] | None = None
    db_path: str = "./extensions/qms_agent_backend/data/qms_memory.sqlite3"


class QMSAgentService:
    """Backend-first service for scenario 1 & 2.

    Scenario 1: process QA + template hints
    Scenario 2: structured learning + deep follow-up
    """

    def __init__(self, cfg: QMSAgentConfig):
        self.cfg = cfg
        self.rag = RAGFlow(api_key=cfg.api_key, base_url=cfg.base_url)
        self.memory = MemoryStore(cfg.db_path)
        self._session_map: dict[str, Any] = {}
        self.chat = self._ensure_chat()

    def _ensure_chat(self):
        expected_dataset_ids = self.cfg.dataset_ids or []

        def _drop_invalid_dataset_id(err_text: str, ds_ids: list[str]) -> list[str]:
            m = re.search(r"The dataset\s+([0-9a-f]{32})\s+doesn't own parsed file", err_text)
            if not m:
                return ds_ids
            bad_id = m.group(1)
            return [x for x in ds_ids if x != bad_id]

        def _create_chat_with_valid_datasets(name: str, ds_ids: list[str]):
            cur = list(ds_ids)
            while True:
                try:
                    return self.rag.create_chat(name=name, dataset_ids=cur)
                except Exception as e:
                    nxt = _drop_invalid_dataset_id(str(e), cur)
                    if nxt == cur:
                        raise
                    cur = nxt

        def _bind_datasets_if_needed(chat_obj: Any):
            if not expected_dataset_ids:
                return chat_obj
            current = getattr(chat_obj, "dataset_ids", None) or []
            if set(current) != set(expected_dataset_ids):
                try:
                    chat_obj.update({"dataset_ids": expected_dataset_ids})
                    refreshed = self.rag.get_chat(chat_obj.id)
                    return refreshed
                except Exception as e:
                    # If some datasets have no parsed files, keep only valid ones.
                    cur = list(expected_dataset_ids)
                    while True:
                        nxt = _drop_invalid_dataset_id(str(e), cur)
                        if nxt == cur:
                            return chat_obj
                        cur = nxt
                        try:
                            chat_obj.update({"dataset_ids": cur})
                            return self.rag.get_chat(chat_obj.id)
                        except Exception as e2:
                            e = e2
            return chat_obj

        if self.cfg.chat_id:
            return _bind_datasets_if_needed(self.rag.get_chat(self.cfg.chat_id))

        def _find_existing_chat_by_name() -> Any | None:
            # Be tolerant to different backend response shapes.
            try:
                res = self.rag.get(
                    "/chats",
                    {
                        "page": 1,
                        "page_size": 200,
                        "orderby": "create_time",
                        "desc": True,
                        "keywords": self.cfg.chat_name,
                    },
                ).json()
                if res.get("code") != 0:
                    return None
                data = res.get("data")
                chats = data.get("chats", []) if isinstance(data, dict) else (data or [])
                for c in chats:
                    if c.get("name") == self.cfg.chat_name and c.get("id"):
                        return self.rag.get_chat(c["id"])
            except Exception:
                return None
            return None

        existed = _find_existing_chat_by_name()
        if existed is not None:
            return _bind_datasets_if_needed(existed)

        dataset_ids = expected_dataset_ids
        try:
            return _bind_datasets_if_needed(_create_chat_with_valid_datasets(self.cfg.chat_name, dataset_ids))
        except Exception as e:
            # Common case: duplicate name created earlier by another session.
            if "Duplicated chat name" in str(e):
                existed = _find_existing_chat_by_name()
                if existed is not None:
                    return _bind_datasets_if_needed(existed)
                # Last fallback: create a unique chat name to avoid startup failure.
                unique_name = f"{self.cfg.chat_name}-{datetime.now().strftime('%Y%m%d%H%M%S')}"
                return _bind_datasets_if_needed(_create_chat_with_valid_datasets(unique_name, dataset_ids))
            raise

    def _get_or_create_session(self, user_id: str):
        if user_id in self._session_map:
            return self._session_map[user_id]
        session = self.chat.create_session(name=f"qms-{user_id}")
        self._session_map[user_id] = session
        return session

    def _extract_typed_memories(self, user_id: str, question: str, answer: str) -> list[MemoryItem]:
        text = f"{question}\n{answer}"
        items: list[MemoryItem] = []

        # profile
        for p in re.findall(r"(?:我是|我在|我负责)([^，。\n]{2,40})", text):
            items.append(MemoryItem(user_id, "profile", p.strip(), 1.5, ""))

        # preference
        pref_patterns = [
            r"(请.*?简短.*?)",
            r"(请.*?详细.*?)",
            r"(给我.*?步骤.*?)",
            r"(用.*?表格.*?)",
            r"(中英双语.*?)",
            r"(先给步骤.*?)",
            r"(尽量简短.*?)",
            r"(先.*?再.*?)",
            r"(要点式.*?)",
            r"(分点回答.*?)",
        ]
        for pat in pref_patterns:
            for p in re.findall(pat, text):
                items.append(MemoryItem(user_id, "preference", p.strip(), 1.2, ""))

        # objective
        for o in re.findall(r"(?:目标|希望|我要|需要)([^，。\n]{3,60})", question):
            items.append(MemoryItem(user_id, "objective", o.strip(), 1.3, ""))

        # glossary
        if "NCM" in text:
            items.append(MemoryItem(user_id, "glossary", "NCM=不合格品管理流程", 1.0, ""))
        if "管理评审" in text:
            items.append(MemoryItem(user_id, "glossary", "管理评审=管理层定期体系有效性评估", 1.0, ""))
        if "风险管理" in text:
            items.append(MemoryItem(user_id, "glossary", "风险管理=识别-评估-控制-监测闭环", 1.0, ""))

        # history summary
        items.append(MemoryItem(user_id, "history", question[:120], 0.8, ""))
        return items

    def _build_memory_context(self, user_id: str, session_id: str) -> str:
        memories = self.memory.get_top_memories(user_id, limit=10)
        history = self.memory.recent_history_all_sessions(user_id, limit=6)
        profile = self.memory.get_user_profile(user_id, auto_build=True)

        def _join_items(items: list[str], max_n: int = 5) -> str:
            if not items:
                return "暂无"
            return "；".join(items[:max_n])

        lines = ["[长期用户画像]"]
        lines.append(f"- persona: {_join_items(profile.get('persona', []), 4)}")
        lines.append(f"- preferences: {_join_items(profile.get('preferences', []), 5)}")
        lines.append(f"- objectives: {_join_items(profile.get('long_term_objectives', []), 4)}")
        topics = profile.get("recurring_topics", [])
        lines.append(f"- recurring_topics: {('、'.join(topics[:6]) if topics else '暂无')}")

        lines.append("[用户记忆]")
        if memories:
            for m in memories:
                lines.append(f"- ({m.memory_type}) {m.content}")
        else:
            lines.append("- 暂无")

        lines.append("[跨会话最近对话]")
        if history:
            for h in history:
                lines.append(f"- Q: {h['question'][:100]}")
        else:
            lines.append("- 暂无")
        return "\n".join(lines)

    @staticmethod
    def _extract_negative_keywords_from_feedback(text: str) -> list[str]:
        """Extract terms likely marked as wrong in correction text."""
        kws: list[str] = []
        patterns = [
            r"([A-Za-z0-9\u4e00-\u9fa5_\-]{2,30})\s*改为",
            r"不是\s*([A-Za-z0-9\u4e00-\u9fa5_\-]{2,30})",
            r"不要\s*([A-Za-z0-9\u4e00-\u9fa5_\-]{2,30})",
            r"错误[:：]\s*([A-Za-z0-9\u4e00-\u9fa5_\-]{2,30})",
        ]
        for pat in patterns:
            for m in re.findall(pat, text):
                kw = (m or "").strip()
                if len(kw) >= 2:
                    kws.append(kw)
        # de-dup while preserving order
        seen = set()
        result = []
        for k in kws:
            if k not in seen:
                seen.add(k)
                result.append(k)
        return result[:12]

    @staticmethod
    def _build_feedback_eval_signal(question: str, original_answer: str, corrected_answer: str, note: str = "") -> str:
        """Create structured learning signal from user correction."""
        q = question.strip()[:180]
        o = original_answer.strip()[:260]
        c = corrected_answer.strip()[:260]
        n = note.strip()[:160]

        issue_tags = []
        raw = f"{corrected_answer}\n{note}".lower()
        if any(x in raw for x in ["引用", "证据", "来源", "条款"]):
            issue_tags.append("evidence")
        if any(x in raw for x in ["步骤", "流程", "顺序", "环节"]):
            issue_tags.append("workflow")
        if any(x in raw for x in ["术语", "定义", "概念"]):
            issue_tags.append("terminology")
        if any(x in raw for x in ["格式", "简短", "详细", "分点", "表格"]):
            issue_tags.append("style")
        if not issue_tags:
            issue_tags.append("general")

        return (
            f"[feedback_eval] tags={','.join(issue_tags)} | "
            f"question={q} | original={o} | corrected={c} | note={n}"
        )

    @staticmethod
    def _extract_reference_docs(reference_chunks: list[dict] | None) -> list[dict]:
        if not reference_chunks:
            return []
        docs: dict[str, dict] = {}
        for c in reference_chunks:
            doc_id = (
                c.get("document_id")
                or c.get("doc_id")
                or c.get("id")
                or "unknown"
            )
            doc_name = (
                c.get("document_name")
                or c.get("docnm_kwd")
                or c.get("doc_name")
                or "unknown"
            )
            if doc_id not in docs:
                docs[doc_id] = {"document_id": doc_id, "document_name": doc_name}
        return list(docs.values())

    @staticmethod
    def _pick_template_hints(reference_docs: list[dict]) -> list[dict]:
        hints = []
        for d in reference_docs:
            name = (d.get("document_name") or "").lower()
            if any(k in name for k in ["template", "tpl", "form", "表单", "模板"]):
                hints.append(d)
        return hints

    def ask_process_qa(self, user_id: str, question: str) -> dict:
        session = self._get_or_create_session(user_id)
        mem_ctx = self._build_memory_context(user_id, session.id)

        prompt = f"""
你是SSME DX & SSME US QMS流程助手。
回答目标：
1) 直接给出可执行步骤（编号）
2) 给出关键控制点（至少3条）
3) 给出常见错误与避免建议
4) 若检索结果中有模板/表单文件，单独列出“相关模板”
5) 仅使用可追溯到知识库的内容，不编造条款编号

{mem_ctx}

用户问题：{question}
""".strip()

        answer_message = next(session.ask(prompt, stream=False))
        answer = answer_message.content
        refs = self._extract_reference_docs(answer_message.reference)
        template_hints = self._pick_template_hints(refs)

        self.memory.log_interaction(user_id, session.id, question, answer, refs)
        self.memory.upsert_memories(self._extract_typed_memories(user_id, question, answer))
        self.memory.refresh_user_profile(user_id)

        return {
            "session_id": session.id,
            "answer": answer,
            "references": refs,
            "template_hints": template_hints,
        }

    def ask_learning_qa(self, user_id: str, module: str, question: str) -> dict:
        session = self._get_or_create_session(user_id)
        mem_ctx = self._build_memory_context(user_id, session.id)

        prompt = f"""
你是QMS培训专家，面向员工做体系化教学。
主题模块：{module}
教学要求：
1) 先给“模块总览”
2) 再给“核心原则/关键术语”
3) 再给“流程框架（输入-活动-输出）”
4) 再给“岗位落地建议（执行/记录/证据）”
5) 最后给“3道自测题（含参考答案）”
并在回答末尾给出“下一轮可深入问题建议”。

{mem_ctx}

用户补充问题：{question}
""".strip()

        answer_message = next(session.ask(prompt, stream=False))
        answer = answer_message.content
        refs = self._extract_reference_docs(answer_message.reference)

        self.memory.log_interaction(user_id, session.id, f"[{module}] {question}", answer, refs)
        self.memory.upsert_memories(self._extract_typed_memories(user_id, question, answer))
        self.memory.refresh_user_profile(user_id)

        return {
            "session_id": session.id,
            "module": module,
            "answer": answer,
            "references": refs,
        }

    def get_user_long_term_memory(self, user_id: str) -> dict:
        """Return current persisted profile + latest memories for debugging/inspection."""
        profile = self.memory.get_user_profile(user_id, auto_build=True)
        top_memories = self.memory.get_top_memories(user_id, limit=20)
        return {
            "profile": profile,
            "typed_memories": [
                {
                    "memory_type": m.memory_type,
                    "content": m.content,
                    "weight": m.weight,
                    "last_seen": m.last_seen,
                }
                for m in top_memories
            ],
        }

    def submit_user_feedback(
        self,
        user_id: str,
        question: str,
        original_answer: str,
        corrected_answer: str,
        score: float = 1.0,
        note: str = "",
        session_id: str | None = None,
    ) -> dict:
        """Learn from user correction and update memory weights."""
        if not question.strip():
            raise ValueError("question is required")
        if not original_answer.strip():
            raise ValueError("original_answer is required")
        if not corrected_answer.strip():
            raise ValueError("corrected_answer is required")

        session = self._get_or_create_session(user_id)
        sid = session_id or session.id
        score = max(0.1, min(2.0, float(score)))

        eval_signal = self._build_feedback_eval_signal(question, original_answer, corrected_answer, note)
        neg_keywords = self._extract_negative_keywords_from_feedback(f"{corrected_answer}\n{note}")
        decayed = self.memory.decay_memories_by_keywords(user_id=user_id, keywords=neg_keywords, decay_factor=0.82)

        learned_items = self._extract_typed_memories(user_id, question, corrected_answer)
        amplified_items = [
            MemoryItem(
                user_id=i.user_id,
                memory_type=i.memory_type,
                content=i.content,
                weight=min(4.0, i.weight * (1.2 + 0.6 * score)),
                last_seen=i.last_seen,
            )
            for i in learned_items
        ]
        amplified_items.append(
            MemoryItem(
                user_id=user_id,
                memory_type="history",
                content=f"反馈修正: {eval_signal[:220]}",
                weight=min(3.5, 1.5 + score),
                last_seen="",
            )
        )

        self.memory.upsert_memories(amplified_items)
        self.memory.log_feedback(
            user_id=user_id,
            session_id=sid,
            question=question,
            original_answer=original_answer,
            corrected_answer=corrected_answer,
            eval_signal=eval_signal,
            score=score,
        )
        self.memory.log_interaction(
            user_id=user_id,
            session_id=sid,
            question=f"[USER_FEEDBACK] {question}",
            answer=f"{corrected_answer}\n\n{note}".strip(),
            references=[],
        )
        profile = self.memory.refresh_user_profile(user_id)

        return {
            "session_id": sid,
            "eval_signal": eval_signal,
            "negative_keywords": neg_keywords,
            "decayed_memories": decayed,
            "learned_items": len(amplified_items),
            "profile_updated_at": profile.get("updated_at"),
        }


def load_config_from_env() -> QMSAgentConfig:
    dataset_ids_raw = os.getenv("QMS_DATASET_IDS", "").strip()
    dataset_ids = [x.strip() for x in dataset_ids_raw.split(",") if x.strip()] if dataset_ids_raw else None
    api_key = os.getenv("RAGFLOW_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RAGFLOW_API_KEY is required")
    if api_key in {"你的token", "你的RAGFlow_API_TOKEN", "your_token", "your_api_token"}:
        raise RuntimeError("RAGFLOW_API_KEY 不能使用占位符，请替换为真实 API Token。")
    try:
        api_key.encode("latin-1")
    except UnicodeEncodeError as e:
        raise RuntimeError("RAGFLOW_API_KEY 包含非 ASCII/Latin-1 字符，请检查是否填了中文或全角符号。") from e

    return QMSAgentConfig(
        api_key=api_key,
        base_url=os.getenv("RAGFLOW_BASE_URL", "http://127.0.0.1:9380").strip(),
        chat_id=os.getenv("QMS_CHAT_ID", "").strip(),
        chat_name=os.getenv("QMS_CHAT_NAME", "QMS Assistant").strip(),
        dataset_ids=dataset_ids,
        db_path=os.getenv("QMS_MEMORY_DB", "./extensions/qms_agent_backend/data/qms_memory.sqlite3").strip(),
    )
