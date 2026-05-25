from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

_DEFAULT_DB_PATH = str(Path(__file__).resolve().parent / "data" / "eval.sqlite3")


@dataclass
class EvalConfig:
    # RAGFlow connection
    ragflow_api_key: str = ""
    ragflow_base_url: str = "http://127.0.0.1:9380"

    # Storage
    db_path: str = _DEFAULT_DB_PATH

    # LLM-as-judge
    judge_api_key: str = ""
    judge_base_url: str = "https://api.openai.com/v1"
    judge_model: str = "gpt-4o-mini"
    judge_enabled: bool = True

    # Defaults
    default_timeout_sec: int = 120
    default_pass_threshold: float = 0.6

    # Server
    server_host: str = "0.0.0.0"
    server_port: int = 9391


def load_config_from_env() -> EvalConfig:
    return EvalConfig(
        ragflow_api_key=os.getenv("RAGFLOW_API_KEY", ""),
        ragflow_base_url=os.getenv("RAGFLOW_BASE_URL", "http://127.0.0.1:9380"),
        db_path=os.getenv("EVAL_DB_PATH") or _DEFAULT_DB_PATH,
        judge_api_key=os.getenv("JUDGE_LLM_API_KEY", ""),
        judge_base_url=os.getenv("JUDGE_LLM_BASE_URL", "https://api.openai.com/v1"),
        judge_model=os.getenv("JUDGE_LLM_MODEL", "gpt-4o-mini"),
        judge_enabled=os.getenv("JUDGE_ENABLED", "true").lower() == "true",
        default_timeout_sec=int(os.getenv("EVAL_TIMEOUT_SEC", "120")),
        default_pass_threshold=float(os.getenv("EVAL_PASS_THRESHOLD", "0.6")),
        server_host=os.getenv("EVAL_SERVER_HOST", "0.0.0.0"),
        server_port=int(os.getenv("EVAL_SERVER_PORT", "9391")),
    )
