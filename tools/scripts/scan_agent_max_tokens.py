"""Scan (and optionally fix) RAGFlow Agent DSL for max_tokens-like settings.

Usage (PowerShell):
  $env:RAGFLOW_API_KEY='...'
  $env:RAGFLOW_BASE_URL='http://ragflow.local'
  python tools/scripts/scan_agent_max_tokens.py --id <agent_id>
  python tools/scripts/scan_agent_max_tokens.py --title "MP小助手"

Fix (set any max_tokens > 16384 to 4096):
    python tools/scripts/scan_agent_max_tokens.py --id <agent_id> --fix --new-max 4096

It prints any paths containing token limits (e.g. max_tokens, maxTokensEnabled).
"""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Any, Iterable


def _ensure_sdk_importable() -> None:
    # When running from repo root (ragflow-main/ragflow-main)
    sdk_path = Path(__file__).resolve().parents[2] / "sdk" / "python"
    if str(sdk_path) not in os.sys.path:
        os.sys.path.insert(0, str(sdk_path))


def _is_primitive(value: Any) -> bool:
    return value is None or isinstance(value, (str, int, float, bool))


def _as_mapping(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        return value
    if hasattr(value, "__dict__"):
        # ragflow_sdk.modules.base.Base
        return value.__dict__  # type: ignore[return-value]
    return None


def _walk_for_keys(obj: Any, path: tuple[str, ...], keys: set[str], out: list[tuple[tuple[str, ...], Any]], seen: set[int]) -> None:
    oid = id(obj)
    if oid in seen:
        return

    if _is_primitive(obj):
        return

    seen.add(oid)

    mapping = _as_mapping(obj)
    if mapping is not None:
        for k, v in mapping.items():
            ks = str(k)
            p = path + (ks,)
            ksl = ks.lower()
            if ks in keys or ("token" in ksl and "max" in ksl):
                out.append((p, v))
            _walk_for_keys(v, p, keys, out, seen)
        return

    if isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _walk_for_keys(v, path + (f"[{i}]",), keys, out, seen)


def _iter_agent_candidates(rag, title: str | None, agent_id: str | None, page_size: int, max_pages: int) -> Iterable[Any]:
    if agent_id:
        # list_agents supports id filter
        for a in rag.list_agents(page=1, page_size=page_size, id=agent_id):
            yield a
        return

    if title:
        # Some deployments treat title as fuzzy, some as exact. Fallback to paging.
        for a in rag.list_agents(page=1, page_size=page_size, title=title):
            yield a
            return

        for page in range(1, max_pages + 1):
            for a in rag.list_agents(page=page, page_size=page_size):
                if (getattr(a, "title", "") or "").find(title) >= 0:
                    yield a
            # stop early if last page seems empty
            if len(rag.list_agents(page=page, page_size=page_size)) < page_size:
                break


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--id", dest="agent_id", help="agent id")
    parser.add_argument("--title", help="agent title (exact or substring)")
    parser.add_argument("--page-size", type=int, default=200)
    parser.add_argument("--max-pages", type=int, default=20)
    parser.add_argument("--only-interesting", action="store_true", help="only print values >= 16000 or enabled flags")
    parser.add_argument("--fix", action="store_true", help="update agent DSL: clamp over-limit max_tokens")
    parser.add_argument("--threshold", type=int, default=16384, help="treat max_tokens above this as invalid")
    parser.add_argument("--new-max", type=int, default=4096, help="replacement max_tokens when fixing")
    args = parser.parse_args()

    if not args.agent_id and not args.title:
        parser.error("Provide --id or --title")

    api_key = os.environ.get("RAGFLOW_API_KEY")
    base_url = os.environ.get("RAGFLOW_BASE_URL")
    if not api_key or not base_url:
        raise SystemExit("Missing env vars: RAGFLOW_API_KEY and/or RAGFLOW_BASE_URL")

    _ensure_sdk_importable()
    from ragflow_sdk.ragflow import RAGFlow  # type: ignore[import-not-found]

    rag = RAGFlow(api_key=api_key, base_url=base_url)

    agents = list(_iter_agent_candidates(rag, args.title, args.agent_id, args.page_size, args.max_pages))
    if not agents:
        raise SystemExit("Agent not found")
    if len(agents) > 1 and args.agent_id is None:
        # Prefer exact title match
        exact = [a for a in agents if getattr(a, "title", None) == args.title]
        if len(exact) == 1:
            agents = exact

    agent = agents[0]

    # Prefer fetching by id via raw /agents endpoint so we always have title/description for PUT.
    agent_id = getattr(agent, "id", None) or args.agent_id
    if not agent_id:
        raise SystemExit("Agent id not available")

    res = rag.get(
        "/agents",
        params={"page": 1, "page_size": 30, "orderby": "update_time", "desc": True, "id": agent_id},
    ).json()
    if res.get("code") != 0 or not res.get("data"):
        raise SystemExit(f"Failed to fetch agent by id: {res.get('message')}")
    agent_record = res["data"][0]
    base_update_time = agent_record.get("update_time")
    dsl_dict = agent_record.get("dsl")
    if not isinstance(dsl_dict, dict):
        raise SystemExit("Unexpected DSL payload")

    keys = {
        "max_tokens",
        "maxTokensEnabled",
        "max_output_tokens",
        "max_completion_tokens",
        "max_new_tokens",
        "maxTokens",
        "max_tokens_limit",
    }

    hits: list[tuple[tuple[str, ...], Any]] = []
    _walk_for_keys(dsl_dict, ("dsl",), keys, hits, set())

    def try_int(v: Any) -> int | None:
        if isinstance(v, bool):
            return None
        if isinstance(v, (int, float)):
            return int(v)
        if isinstance(v, str) and v.isdigit():
            return int(v)
        return None

    print(f"agent_title= {agent_record.get('title')}")
    print(f"agent_id= {agent_id}")
    print(f"hits= {len(hits)}")

    for p, v in hits:
        if args.only_interesting:
            vi = try_int(v)
            if vi is not None and vi >= 16000:
                pass
            elif isinstance(v, bool) and p[-1].lower().endswith("enabled") and v is True:
                pass
            else:
                continue

        print("/".join(p), "=", v)

    if args.fix:
        def walk_patch(obj: Any) -> int:
            changed = 0
            if isinstance(obj, dict):
                for k, v in list(obj.items()):
                    if k == "max_tokens" and isinstance(v, (int, float)) and int(v) > args.threshold:
                        obj[k] = int(args.new_max)
                        changed += 1
                    changed += walk_patch(v)
            elif isinstance(obj, list):
                for v in obj:
                    changed += walk_patch(v)
            return changed

        # optimistic lock: refuse to overwrite if user saved meanwhile
        res_latest = rag.get(
            "/agents",
            params={"page": 1, "page_size": 30, "orderby": "update_time", "desc": True, "id": agent_id},
        ).json()
        if res_latest.get("code") != 0 or not res_latest.get("data"):
            raise SystemExit(f"Failed to refetch agent: {res_latest.get('message')}")
        latest_record = res_latest["data"][0]
        latest_update_time = latest_record.get("update_time")
        if base_update_time is not None and latest_update_time is not None and latest_update_time != base_update_time:
            raise SystemExit(
                "Refuse to fix: agent was updated meanwhile (please refresh canvas and retry)."
            )

        dsl_copy = dsl_dict  # already a plain dict
        changed = walk_patch(dsl_copy)
        print(f"fix_changed= {changed}")
        if changed:
            payload = {
                "title": latest_record.get("title"),
                "description": latest_record.get("description"),
                "dsl": dsl_copy,
            }
            put_res = rag.put(f"/agents/{agent_id}", payload).json()
            if put_res.get("code") != 0:
                raise SystemExit(f"Fix failed: {put_res.get('message')}")
            print("fix_result= success")
        else:
            print("fix_result= no-op")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
