from __future__ import annotations

import base64
import copy
import hashlib
import hmac
import io
import json
import os
import re
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

def _base_to_dict(obj, max_depth=10):
    """递归将 SDK Base 对象转换为普通 dict。"""
    if max_depth <= 0:
        return obj
    if hasattr(obj, '__dict__') and not isinstance(obj, dict):
        obj = obj.__dict__
    if isinstance(obj, dict):
        return {k: _base_to_dict(v, max_depth - 1) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_base_to_dict(item, max_depth - 1) for item in obj]
    return obj


def _extract_kb_ids_from_dsl(dsl, max_depth: int = 10) -> list[str]:
    """递归从 agent DSL 中提取所有 kb_ids / dataset_ids。"""
    dsl = _base_to_dict(dsl)
    result: list[str] = []
    if max_depth <= 0 or not isinstance(dsl, dict):
        return result
    # 直接匹配 kb_ids 或 dataset_ids 字段
    for key in ("kb_ids", "dataset_ids"):
        val = dsl.get(key)
        if isinstance(val, list) and val:
            result.extend(str(x) for x in val if x)
    # 递归搜索
    for v in dsl.values():
        if isinstance(v, dict):
            result.extend(_extract_kb_ids_from_dsl(v, max_depth - 1))
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    result.extend(_extract_kb_ids_from_dsl(item, max_depth - 1))
    return list(dict.fromkeys(result))  # deduplicate, preserve order


def _patch_dsl_kb_ids(dsl: dict, new_kb_ids: list[str]) -> dict:
    """深拷贝 DSL 并替换所有 Retrieval 组件中的 kb_ids / dataset_ids。"""
    patched = copy.deepcopy(dsl)
    components = patched.get("components", {})
    for comp in components.values():
        obj = comp.get("obj", {})
        params = obj.get("params", {})
        comp_name = obj.get("component_name", "")
        # Pattern A: 独立 Retrieval 组件
        if comp_name == "Retrieval":
            params["kb_ids"] = list(new_kb_ids)
            params["dataset_ids"] = list(new_kb_ids)
        # Pattern B: Agent 组件内嵌的 Retrieval tool
        tools = params.get("tools", [])
        if isinstance(tools, list):
            for tool in tools:
                if isinstance(tool, dict):
                    tool_params = tool.get("params", {})
                    tool_name = tool.get("component_name", "")
                    if tool_name == "Retrieval":
                        tool_params["kb_ids"] = list(new_kb_ids)
                        tool_params["dataset_ids"] = list(new_kb_ids)
    return patched


# RSA public key for RAGFlow user registration password encryption
_RAGFLOW_RSA_PUB_KEY = """-----BEGIN PUBLIC KEY-----
MIIBIjANBgkqhkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEArq9XTUSeYr2+N1h3Afl/z8Dse/2yD0ZGrKwx+EEEcdsBLca9Ynmx3nIB5obmLlSfmskLpBo0UACBmB5rEjBp2Q2f3AG3Hjd4B+gNCG6BDaawuDlgANIhGnaTLrIqWrrcm4EMzJOnAOI1fgzJRsOOUEfaS318Eq9OVO3apEyCCt0lOQK6PuksduOjVxtltDav+guVAA068NrPYmRNabVKRNLJpL8w4D44sfth5RvZ3q9t+6RTArpEtc5sh5ChzvqPOzKGMXW83C95TxmXqpbK6olN4RevSfVjEAgCydH6HN6OhtOQEcnrU97r9H0iZOWwbw3pVrZiUkuRD1R56Wzs2wIDAQAB
-----END PUBLIC KEY-----"""


def _encrypt_password_for_ragflow(password_plain: str) -> str:
    """RSA-encrypt password for RAGFlow /v1/user/register endpoint."""
    try:
        from Crypto.PublicKey import RSA  # type: ignore[import-not-found]
        from Crypto.Cipher import PKCS1_v1_5  # type: ignore[import-not-found]
    except ModuleNotFoundError:
        # Some environments use the pycryptodomex namespace.
        try:
            from Cryptodome.PublicKey import RSA  # type: ignore[import-not-found]
            from Cryptodome.Cipher import PKCS1_v1_5  # type: ignore[import-not-found]
        except ModuleNotFoundError as e:
            raise ModuleNotFoundError(
                "Missing dependency for registration password encryption. Install 'pycryptodome' (or 'pycryptodomex')."
            ) from e

    rsa_key = RSA.importKey(_RAGFLOW_RSA_PUB_KEY)
    cipher = PKCS1_v1_5.new(rsa_key)
    password_b64 = base64.b64encode(password_plain.encode("utf-8")).decode("utf-8")
    encrypted = cipher.encrypt(password_b64.encode())
    return base64.b64encode(encrypted).decode("utf-8")

from flask import Flask, jsonify, request, send_from_directory  # type: ignore[import-not-found]
import requests

from .portal_store import PortalStore

# Make local SDK importable without touching global env.
SDK_PATH = Path(__file__).resolve().parents[2] / "sdk" / "python"
if str(SDK_PATH) not in sys.path:
    sys.path.insert(0, str(SDK_PATH))

from ragflow_sdk.ragflow import RAGFlow  # type: ignore  # noqa: E402


def _find_reference(obj, max_depth=6):
    """递归查找包含 chunks 的 reference 对象。"""
    if max_depth <= 0 or not isinstance(obj, dict):
        return None
    if "chunks" in obj:
        chunks = obj["chunks"]
        # 空 chunks 等于没找到
        if isinstance(chunks, dict) and len(chunks) == 0:
            return None
        if isinstance(chunks, list) and len(chunks) == 0:
            return None
        return obj
    if "content" in obj and ("document_name" in obj or "docnm_kwd" in obj):
        return {"chunks": obj}
    for v in obj.values():
        if isinstance(v, dict):
            found = _find_reference(v, max_depth - 1)
            if found:
                return found
        elif isinstance(v, list):
            for item in v:
                if isinstance(item, dict):
                    found = _find_reference(item, max_depth - 1)
                    if found:
                        return found
    return None


@dataclass
class PortalConfig:
    api_key: str
    base_url: str
    db_path: str
    auth_secret: str
    direct_chat_url: str
    direct_chat_api_key: str
    direct_chat_model: str
    token_ttl_seconds: int = 3600 * 8


class PortalGateway:
    def __init__(self, cfg: PortalConfig):
        self.cfg = cfg
        self.store = PortalStore(cfg.db_path)
        self.rag = RAGFlow(api_key=cfg.api_key, base_url=cfg.base_url)
        self._session_registry: dict[str, dict[str, Any]] = {}
        self._ensure_ragflow_accounts()

    def _ensure_ragflow_accounts(self):
        """为没有 RAGFlow 账号的种子用户自动注册。"""
        users = self.store.get_all_users_without_ragflow()
        if not users:
            return
        for user_id, login_id in users:
            email = login_id.lower() if "@" in login_id else f"{login_id.lower()}@siemens-healthineers.com"
            try:
                enc_pwd = _encrypt_password_for_ragflow("12345678")
                resp = requests.post(
                    f"{self.cfg.base_url.rstrip('/')}/v1/user/register",
                    json={"nickname": login_id, "email": email, "password": enc_pwd},
                    timeout=30,
                )
                rdata = resp.json()
                rf_user_id = ""
                rf_token = ""
                if rdata.get("code") == 0:
                    rf_user_id = rdata.get("data", {}).get("id", "")
                elif "already registered" in rdata.get("message", "").lower():
                    pass  # will link via login below
                else:
                    print(f"[PORTAL] RAGFlow register failed for {login_id}: {rdata.get('message','')}")
                    continue

                # Login to get JWT, then create API key
                login_resp = requests.post(
                    f"{self.cfg.base_url.rstrip('/')}/v1/user/login",
                    json={"email": email, "password": enc_pwd},
                    timeout=30,
                )
                auth_jwt = login_resp.headers.get("Authorization", "")
                if not auth_jwt:
                    print(f"[PORTAL WARN] RAGFlow login failed for {login_id}")
                    continue
                login_result = login_resp.json()
                if not rf_user_id:
                    rf_user_id = login_result.get("data", {}).get("id", "")

                api_resp = requests.post(
                    f"{self.cfg.base_url.rstrip('/')}/v1/api/new_token",
                    json={"dialog_id": ""},
                    headers={"Authorization": auth_jwt},
                    cookies=login_resp.cookies,
                    timeout=30,
                )
                api_data = api_resp.json()
                if api_data.get("code") == 0 and isinstance(api_data.get("data"), dict):
                    rf_token = api_data["data"].get("token", "")

                if rf_user_id and rf_token:
                    self.store.update_user_ragflow(user_id, rf_user_id, rf_token)
                    print(f"[PORTAL] Linked {login_id} -> RAGFlow user {rf_user_id[:12]}...")
            except Exception as e:
                print(f"[PORTAL WARN] Failed to ensure RAGFlow account for {login_id}: {e}")

    def get_rag_client(self, user_id: str = "") -> RAGFlow:
        """获取用户的 RAGFlow 客户端。user_id 为空时使用共享管理员客户端。"""
        if user_id:
            token = self.store.get_user_ragflow_token(user_id)
            if token:
                return RAGFlow(api_key=token, base_url=self.cfg.base_url)
        return self.rag

    @staticmethod
    def _extract_chunks(reference):
        """从 reference 对象提取 chunks 列表。"""
        if not reference:
            return []
        chunks = reference.get("chunks", reference)
        if isinstance(chunks, dict):
            return list(chunks.values())
        if isinstance(chunks, list):
            return chunks
        return []

    def _direct_chat(self, messages: list[dict[str, str]]) -> str:
        base = self.cfg.direct_chat_url.rstrip("/")
        model = self.cfg.direct_chat_model
        api_key = self.cfg.direct_chat_api_key
        # Build candidate (url, headers) pairs
        candidates = []
        if base.endswith("/v1/chat/completions") or base.endswith("/chat/completions"):
            candidates.append((base, {"Authorization": f"Bearer {api_key}"}))
        else:
            # Azure OpenAI format (api-key header)
            azure_ver = os.getenv("AZURE_API_VERSION", "2024-12-01-preview").strip()
            candidates.append((
                f"{base}/openai/deployments/{model}/chat/completions?api-version={azure_ver}",
                {"api-key": api_key},
            ))
            # Standard OpenAI-compatible formats (Bearer token)
            for path in [
                "/v1/chat/completions",
                "/chat/completions",
                "/api/v1/chat/completions",
                "/v1/ai/chat/completions",
                "/openai/v1/chat/completions",
                "/api/openai/v1/chat/completions",
            ]:
                candidates.append((base + path, {"Authorization": f"Bearer {api_key}"}))
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
        }
        last_status = None
        last_body = ""
        for url, hdrs in candidates:
            headers = {"Content-Type": "application/json", **hdrs}
            res = requests.post(url, headers=headers, json=payload, timeout=60)
            if res.status_code >= 300:
                last_status = res.status_code
                last_body = res.text[:500]
                continue
            data = res.json()
            content = ((data.get("choices") or [{}])[0].get("message") or {}).get("content")
            if not content:
                content = ((data.get("choices") or [{}])[0].get("text") or "")
            return (content or "").strip()
        raise RuntimeError(f"direct chat failed: {last_status} {last_body}")

    @staticmethod
    def _b64url_encode(raw: bytes) -> str:
        return base64.urlsafe_b64encode(raw).decode("utf-8").rstrip("=")

    @staticmethod
    def _b64url_decode(raw: str) -> bytes:
        padding = "=" * (-len(raw) % 4)
        return base64.urlsafe_b64decode(raw + padding)

    def create_token(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode("utf-8")
        sig = hmac.new(self.cfg.auth_secret.encode("utf-8"), body, hashlib.sha256).digest()
        return f"{self._b64url_encode(body)}.{self._b64url_encode(sig)}"

    def parse_token(self, token: str) -> dict[str, Any] | None:
        try:
            body_b64, sig_b64 = token.split(".", 1)
            body = self._b64url_decode(body_b64)
            expected_sig = hmac.new(self.cfg.auth_secret.encode("utf-8"), body, hashlib.sha256).digest()
            if not hmac.compare_digest(expected_sig, self._b64url_decode(sig_b64)):
                return None
            payload = json.loads(body.decode("utf-8"))
            if int(payload.get("exp", 0)) < int(time.time()):
                return None
            return payload
        except Exception:
            return None

    @staticmethod
    @staticmethod
    def _normalize_dept_id(dept_id: str) -> str:
        return PortalStore.normalize_dept_id(dept_id)

    @staticmethod
    def _dept_code(dept_id: str) -> str:
        return PortalStore.dept_code(dept_id)

    @staticmethod
    def _infer_dept_by_title(title: str) -> str:
        t = (title or "").upper()
        if "PLM" in t:
            return "dept_mp_plm"
        if re.search(r"\bAP\b", t):
            return "dept_mp_ap"
        if re.search(r"\bMC\b", t):
            return "dept_mp_mc"
        if re.search(r"US.*DX|DX.*US", t):
            return "dept_mp_usdx"
        if re.search(r"\bQ\b", t):
            return "dept_mp_q"
        return "dept_mp"

    @staticmethod
    def _infer_visibility(title: str) -> str:
        t = title or ""
        if "公共" in t or "公用" in t or t.upper().startswith("MP") or "不选择" in t:
            return "public"
        return "dept"

    def _ensure_agent_policies(self) -> list[Any]:
        """Discover agents from all users' RAGFlow accounts and create policies."""
        all_agents: list[Any] = []
        seen_ids: set[str] = set()

        users_with_tokens = self.store.get_all_users_with_ragflow_token()
        if not users_with_tokens:
            return []

        for user_id, _token, dept_id in users_with_tokens:
            try:
                user_rag = self.get_rag_client(user_id)
                page = 1
                while True:
                    batch = user_rag.list_agents(page=page, page_size=200)
                    if not batch:
                        break
                    for a in batch:
                        if a.id in seen_ids:
                            continue
                        seen_ids.add(a.id)
                        all_agents.append(a)
                        policy = self.store.get_policy("agent", a.id)
                        if not policy:
                            self.store.upsert_policy(
                                "agent", a.id,
                                owner_dept_id=dept_id or "dept_mp",
                                visibility="public",
                                owner_user_id=user_id,
                            )
                        elif not policy.get("owner_user_id"):
                            self.store.upsert_policy(
                                "agent", a.id,
                                owner_dept_id=policy.get("owner_dept_id", dept_id or "dept_mp"),
                                visibility=policy.get("visibility", "public"),
                                owner_user_id=user_id,
                            )
                    if len(batch) < 200:
                        break
                    page += 1
            except Exception as e:
                print(f"[PORTAL WARN] Failed to list agents for user {user_id}: {e}")

        return all_agents

    def bootstrap_agents_to_dept_or_public(self, operator: str = "system") -> dict[str, int]:
        agents = self.rag.list_agents(page=1, page_size=500)
        total = 0
        updated = 0
        for a in agents:
            total += 1
            owner_dept = self._infer_dept_by_title(a.title)
            visibility = self._infer_visibility(a.title)
            self.store.upsert_policy(
                "agent",
                a.id,
                owner_dept_id=owner_dept,
                visibility=visibility,
                created_by=operator,
            )
            updated += 1
        return {"total": total, "updated": updated}

    def _ensure_kb_policies(self) -> list[Any]:
        """Discover KBs from ALL users' RAGFlow accounts, not just the admin."""
        all_datasets = []
        seen_ids = set()

        # Get all users with RAGFlow tokens
        users_with_tokens = self.store.get_all_users_with_ragflow_token()
        if not users_with_tokens:
            # Fallback: use shared admin client
            return self.rag.list_datasets(page=1, page_size=500)

        for user_id, _token, dept_id in users_with_tokens:
            try:
                user_rag = self.get_rag_client(user_id)
                datasets = user_rag.list_datasets(page=1, page_size=500)
                for ds in datasets:
                    if ds.id in seen_ids:
                        continue
                    seen_ids.add(ds.id)
                    all_datasets.append(ds)

                    policy = self.store.get_policy("kb", ds.id)
                    if policy:
                        # Update owner_user_id if missing
                        if not policy.get("owner_user_id"):
                            self.store.upsert_policy(
                                "kb", ds.id,
                                owner_dept_id=policy.get("owner_dept_id", dept_id or "dept_mp"),
                                visibility=policy.get("visibility", "dept"),
                                owner_user_id=user_id,
                                category=policy.get("category", ""),
                            )
                        continue
                    # New KB: create policy with proper owner, default to dept visibility
                    self.store.upsert_policy(
                        "kb", ds.id,
                        owner_dept_id=dept_id or "dept_mp",
                        visibility="dept",
                        owner_user_id=user_id,
                        category="D",
                    )
            except Exception as e:
                print(f"[PORTAL WARN] Failed to list datasets for user {user_id}: {e}")
                continue

        # Cleanup: remove policies for KBs that no longer exist in RAGFlow
        if seen_ids:
            self.store.cleanup_stale_kb_policies(seen_ids)

        return all_datasets

    def bootstrap_kbs_to_mp_public_a(self, operator: str = "system") -> dict[str, int]:
        datasets = self.rag.list_datasets(page=1, page_size=500)
        changed = 0
        total = 0
        for ds in datasets:
            total += 1
            self.store.upsert_policy(
                "kb",
                ds.id,
                owner_dept_id="dept_mp",
                visibility="public",
                created_by=operator,
                category="A",
            )
            changed += 1
        return {"total": total, "updated": changed}

    def is_admin(self, token_payload: dict[str, Any]) -> bool:
        user_id = token_payload.get("sub")
        if not user_id:
            return False
        return self.store.is_admin(user_id)

    def _get_runtime_session(self, session_id: str, agent_id: str):
        reg = self._session_registry.get(session_id)
        if reg and reg.get("session"):
            return reg["session"]

        agents = self.rag.list_agents(page=1, page_size=30, id=agent_id)
        if not agents:
            raise ValueError("agent not found")
        agent = agents[0]
        sessions = agent.list_sessions(page=1, page_size=30, id=session_id)
        if not sessions:
            raise ValueError("session not found in ragflow")
        runtime_session = sessions[0]
        self._session_registry[session_id] = {
            **(self._session_registry.get(session_id) or {}),
            "session": runtime_session,
        }
        return runtime_session

    def login(self, login_id: str, password: str) -> dict[str, Any] | None:
        user = self.store.authenticate_user(login_id=login_id, password=password)
        if not user:
            return None
        raw_depts = self.store.get_user_departments(user.user_id)
        depts = []
        for d in raw_depts:
            copied = dict(d)
            copied["dept_id"] = self._normalize_dept_id(copied.get("dept_id") or "")
            depts.append(copied)
        if not depts:
            return None
        default_dept = next((d for d in depts if int(d.get("is_default", 0)) == 1), depts[0])
        now = int(time.time())
        payload = {
            "sub": user.user_id,
            "login_id": user.login_id,
            "display_name": user.display_name,
            "dept_ids": [d["dept_id"] for d in depts],
            "default_dept_id": default_dept["dept_id"],
            "iat": now,
            "exp": now + self.cfg.token_ttl_seconds,
        }
        token = self.create_token(payload)
        return {
            "access_token": token,
            "expires_in": self.cfg.token_ttl_seconds,
            "user": {
                "user_id": user.user_id,
                "login_id": user.login_id,
                "display_name": user.display_name,
                "dept": self._dept_code(default_dept["dept_id"]),
            },
            "departments": depts,
            "default_dept_id": default_dept["dept_id"],
        }

    def list_resources(self, token_payload: dict[str, Any], dept_id: str | None = None, resource_type: str = "agent") -> list[dict[str, Any]]:
        depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        selected_dept = self._normalize_dept_id(dept_id or token_payload.get("default_dept_id") or "")
        if selected_dept and selected_dept not in depts:
            raise PermissionError("dept not allowed")
        user_id = token_payload.get("sub")

        items = []

        if resource_type in {"agent", "all"}:
            agents = self._ensure_agent_policies()
            for a in agents:
                policy = self.store.get_policy("agent", a.id)
                if not policy:
                    policy = {
                        "owner_dept_id": "dept_mp",
                        "visibility": "public",
                        "owner_user_id": None,
                        "allow_dept_ids_json": "[]",
                        "deny_dept_ids_json": "[]",
                        "allow_roles_json": "[]",
                        "category": "",
                    }
                if not self.store.can_access_resource(depts, selected_dept, policy, user_id=user_id):
                    continue
                items.append(
                    {
                        "resource_type": "agent",
                        "resource_id": a.id,
                        "title": a.title,
                        "description": getattr(a, "description", "") or "",
                        "owner_dept_id": (policy or {}).get("owner_dept_id", "dept_mp"),
                        "owner_user_id": (policy or {}).get("owner_user_id"),
                        "visibility": (policy or {}).get("visibility", "dept"),
                        "category": (policy or {}).get("category", ""),
                    }
                )

        if resource_type in {"kb", "all"}:
            datasets = self._ensure_kb_policies()
            for ds in datasets:
                policy = self.store.get_policy("kb", ds.id)
                if not policy:
                    policy = {
                        "owner_dept_id": "dept_mp",
                        "visibility": "public",
                        "owner_user_id": None,
                        "allow_dept_ids_json": "[]",
                        "deny_dept_ids_json": "[]",
                        "allow_roles_json": "[]",
                        "category": "A",
                    }
                my_perm = int(self.store.effective_kb_permission(depts, selected_dept, ds.id, str(user_id or ""), policy=policy))
                if not self.store.has_read(my_perm):
                    continue
                allow_depts = json.loads((policy or {}).get("allow_dept_ids_json") or "[]")
                owner_uid = (policy or {}).get("owner_user_id") or ""
                # Get shared users for chain display
                kb_shares = self.store.list_kb_shares(ds.id) if owner_uid == user_id else []
                items.append(
                    {
                        "resource_type": "kb",
                        "resource_id": ds.id,
                        "title": getattr(ds, "name", ds.id),
                        "description": getattr(ds, "description", "") or "",
                        "owner_dept_id": (policy or {}).get("owner_dept_id", "dept_mp"),
                        "owner_user_id": owner_uid,
                        "visibility": (policy or {}).get("visibility", "public"),
                        "category": (policy or {}).get("category", "A"),
                        "allow_dept_ids": allow_depts,
                        "my_permission": my_perm,
                        "shares": kb_shares,
                    }
                )

        items.sort(key=lambda x: (x["visibility"], x["title"]))
        return items

    def create_session(
        self,
        token_payload: dict[str, Any],
        dept_id: str,
        agent_id: str,
        kb_ids: list[str] | None = None,
        is_private: bool = False,
    ) -> dict[str, Any]:
        depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        dept_id = self._normalize_dept_id(dept_id)
        if dept_id not in depts:
            raise PermissionError("dept not allowed")
        user_id = token_payload.get("sub")

        if not agent_id:
            session_id = f"direct-{uuid.uuid4().hex}"
            # Validate user-selected kb_ids for direct mode
            valid_kb_ids: list[str] = []
            for kb_id in (kb_ids or []):
                p = self.store.get_policy("kb", kb_id)
                eff = int(self.store.effective_kb_permission(depts, dept_id, kb_id, str(user_id or ""), policy=p))
                if self.store.has_read(eff):
                    valid_kb_ids.append(kb_id)
            self._session_registry[session_id] = {
                "session": None,
                "user_id": user_id,
                "dept_id": dept_id,
                "agent_id": "direct",
                "kb_ids": valid_kb_ids,
                "agent_owner_id": "",
                "created_at": int(time.time()),
                "is_private": bool(is_private),
            }
            self.store.create_portal_session(
                session_id,
                user_id,
                dept_id,
                "direct",
                kb_ids=valid_kb_ids,
                is_private=bool(is_private),
            )
            return {
                "session_id": session_id,
                "agent_id": "direct",
                "dept_id": dept_id,
                "kb_ids": valid_kb_ids,
                "is_private": bool(is_private),
            }

        policy = self.store.get_policy("agent", agent_id)
        if not self.store.can_access_resource(depts, dept_id, policy, user_id=user_id):
            raise PermissionError("resource forbidden")

        # Find agent owner to use their RAGFlow client
        agent_owner_id = (policy or {}).get("owner_user_id") or ""
        agent_rag = self.get_rag_client(agent_owner_id) if agent_owner_id else self.rag

        agents = agent_rag.list_agents(page=1, page_size=30, id=agent_id)
        if not agents:
            raise ValueError("agent not found")
        agent = agents[0]

        # User-selected kb_ids take priority; fall back to agent DSL only when user selected none
        user_selected = bool(kb_ids)
        clean_kb_ids: list[str] = []
        if user_selected:
            for kb_id in kb_ids:
                p = self.store.get_policy("kb", kb_id)
                eff = int(self.store.effective_kb_permission(depts, dept_id, kb_id, str(user_id or ""), policy=p))
                can_read = self.store.has_read(eff)
                print(
                    f"[PORTAL DEBUG] KB access check: {kb_id[:12]}... policy={'YES' if p else 'NO'} eff_perm={eff}"
                )
                if can_read:
                    clean_kb_ids.append(kb_id)
        print(f"[PORTAL DEBUG] create_session: user_selected={user_selected}, input_kb_ids={len(kb_ids or [])}, clean_kb_ids={len(clean_kb_ids)}")

        # Only extract from agent DSL when user didn't select any KBs at all
        if not user_selected and not clean_kb_ids:
            try:
                agent_detail = agent_rag.list_agents(page=1, page_size=1, id=agent_id)
                if agent_detail:
                    dsl = getattr(agent_detail[0], 'dsl', {}) or {}
                    clean_kb_ids = _extract_kb_ids_from_dsl(dsl)
            except Exception as e:
                print(f"[PORTAL WARN] Failed to extract agent datasets: {e}")

        session = agent.create_session(name=f"portal-{token_payload['sub']}-{int(time.time())}")

        self._session_registry[session.id] = {
            "session": session,
            "user_id": user_id,
            "dept_id": dept_id,
            "agent_id": agent_id,
            "kb_ids": clean_kb_ids,
            "agent_owner_id": agent_owner_id,
            "kb_overridden": user_selected and bool(clean_kb_ids),
            "created_at": int(time.time()),
        }
        self.store.create_portal_session(
            session.id,
            user_id,
            dept_id,
            agent_id,
            kb_ids=clean_kb_ids,
            is_private=bool(is_private),
            kb_overridden=bool(user_selected and clean_kb_ids),
        )
        return {
            "session_id": session.id,
            "agent_id": agent_id,
            "dept_id": dept_id,
            "kb_ids": clean_kb_ids,
            "is_private": bool(is_private),
        }

    def list_sessions(self, token_payload: dict[str, Any], dept_id: str | None = None) -> list[dict[str, Any]]:
        user_id = token_payload.get("sub")
        if not user_id:
            return []
        depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        selected = self._normalize_dept_id(dept_id or "") if dept_id else None
        if selected and selected not in depts:
            raise PermissionError("dept not allowed")
        selected_dept = selected or self._normalize_dept_id(token_payload.get("default_dept_id") or "")
        if not selected_dept:
            return []
        own_sessions = self.store.list_user_sessions(user_id=user_id, dept_id=selected_dept, limit=300)
        if self.store.is_dept_admin(user_id, selected_dept):
            dept_sessions = self.store.list_dept_sessions(
                dept_id=selected_dept,
                exclude_user_id=user_id,
                include_private=False,
                limit=300,
            )
            items = own_sessions + dept_sessions
            items.sort(key=lambda x: x.get("updated_at") or "", reverse=True)
            return items
        return own_sessions

    def delete_session(self, token_payload: dict[str, Any], session_id: str) -> bool:
        user_id = token_payload.get("sub")
        if not user_id:
            return False
        ok = self.store.deactivate_portal_session(session_id=session_id, user_id=user_id)
        if ok:
            self._session_registry.pop(session_id, None)
        return ok

    def update_session_privacy(self, token_payload: dict[str, Any], session_id: str, is_private: bool) -> bool:
        user_id = token_payload.get("sub")
        if not user_id:
            return False
        ok = self.store.update_portal_session_privacy(session_id=session_id, user_id=user_id, is_private=is_private)
        if ok and session_id in self._session_registry:
            self._session_registry[session_id]["is_private"] = bool(is_private)
        return ok

    def upload_kb_for_dept(
        self,
        token_payload: dict[str, Any],
        dept_id: str,
        kb_name: str,
        files: list[Any],
        description: str = "",
        is_private: bool = False,
    ) -> dict[str, Any]:
        depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        dept_id = self._normalize_dept_id(dept_id)
        if dept_id not in depts:
            raise PermissionError("dept not allowed")
        if not kb_name.strip():
            raise ValueError("kb_name is required")
        if not files:
            raise ValueError("files are required")

        # Use per-user RAGFlow client if available, otherwise shared client
        user_id = token_payload.get("sub") or ""
        rag_client = self.get_rag_client(user_id)

        dept_code = self._dept_code(dept_id)
        full_name = f"{dept_code}-{kb_name.strip()}"

        # Check if a dataset with the same name already exists
        # Match both with prefix ("MP-Q-xxx") and without prefix ("xxx")
        ds = None
        raw_name = kb_name.strip()
        try:
            existing = rag_client.list_datasets(page=1, page_size=500)
            for e in existing:
                ename = getattr(e, "name", "")
                if ename == full_name or ename == raw_name:
                    # Check write permission before appending
                    existing_policy = self.store.get_policy("kb", e.id)
                    if existing_policy:
                        existing_owner = existing_policy.get("owner_user_id") or ""
                        if existing_owner and existing_owner != user_id:
                            perm = self.store.get_kb_permission(e.id, user_id)
                            if not self.store.has_write(perm):
                                # Also allow if user's dept has dept-level access
                                vis = (existing_policy.get("visibility") or "").lower()
                                if vis != "public":
                                    raise PermissionError("该知识库只允许所有者或改写权限用户追加文件")
                    ds = e
                    print(f"[PORTAL] KB '{full_name}' already exists, appending files to it")
                    break
        except PermissionError:
            raise
        except Exception:
            pass

        if not ds:
            ds = rag_client.create_dataset(
                name=full_name,
                description=description or f"{dept_code} private kb",
                permission="me",
            )

        docs_payload = []
        for f in files:
            filename = (getattr(f, "filename", "") or "").strip() or f"upload-{uuid.uuid4().hex}.txt"
            blob = f.read()
            if not isinstance(blob, (bytes, bytearray)):
                blob = str(blob).encode("utf-8")
            docs_payload.append({"display_name": filename, "blob": io.BytesIO(blob)})

        docs = ds.upload_documents(docs_payload)
        doc_ids = [d.id for d in docs if getattr(d, "id", "")]
        if doc_ids:
            try:
                ds.async_parse_documents(doc_ids)
            except Exception:
                pass

        self.store.upsert_policy(
            resource_type="kb",
            resource_id=ds.id,
            owner_dept_id=dept_id,
            visibility="private" if is_private else "dept",
            created_by=token_payload.get("sub") or "system",
            owner_user_id=token_payload.get("sub") or None,
            category="D",
        )

        return {
            "kb_id": ds.id,
            "kb_name": ds.name,
            "dept_id": dept_id,
            "is_private": bool(is_private),
            "uploaded_files": len(docs_payload),
            "queued_docs": len(doc_ids),
        }

    def request_kb_share(
        self,
        token_payload: dict[str, Any],
        kb_id: str,
        target_scope: str,
        target_dept_ids: list[str] | None = None,
        target_user_login: str | None = None,
        reason: str = "",
        permission: str = "read",
    ) -> dict[str, Any]:
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")

        owner_dept = self._normalize_dept_id(policy.get("owner_dept_id") or "")
        user_depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        if owner_dept not in user_depts:
            raise PermissionError("only owner dept can request sharing")
        if not self.store.is_dept_admin(user_id, owner_dept):
            raise PermissionError("dept admin required")
        if (policy.get("visibility") or "").lower() == "private" and policy.get("owner_user_id") != user_id:
            raise PermissionError("only owner can share private kb")

        scope = (target_scope or "").strip().lower()
        if scope not in {"mp", "user", "dept"}:
            raise ValueError("target_scope must be mp, user, or dept")

        normalized_targets: list[str] = []
        target_user_id = None
        requested_permission = self.store.PERM_READ
        if scope == "user":
            if not target_user_login:
                raise ValueError("target_user_login is required for user scope")
            user_row = self.store.get_user_by_login(target_user_login)
            if not user_row or int(user_row.get("is_active") or 0) != 1:
                raise ValueError("target_user_login not found")
            target_user_id = user_row.get("user_id")
            perm = (permission or "read").strip().lower()
            if perm == "write":
                requested_permission = self.store.PERM_READ | self.store.PERM_WRITE | self.store.PERM_SHARE
            elif perm == "share":
                requested_permission = self.store.PERM_READ | self.store.PERM_SHARE
            else:
                requested_permission = self.store.PERM_READ
        elif scope == "dept":
            normalized_targets = [self._normalize_dept_id(x) for x in (target_dept_ids or []) if str(x).strip()]
            if not normalized_targets:
                raise ValueError("target_dept_ids is required for dept scope")

        return self.store.create_kb_share_request(
            kb_id=kb_id,
            owner_dept_id=owner_dept,
            requester_user_id=user_id,
            target_scope=scope,
            target_dept_ids=normalized_targets,
            target_user_id=target_user_id,
            reason=reason,
            requested_permission=int(requested_permission),
            parent_share_id=None,
        )

    def share_kb_internal(self, token_payload: dict[str, Any], kb_id: str) -> dict[str, Any]:
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")

        owner_dept = self._normalize_dept_id(policy.get("owner_dept_id") or "")
        user_depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        if owner_dept not in user_depts:
            raise PermissionError("only owner dept can share internal")
        if (policy.get("visibility") or "").lower() == "private" and policy.get("owner_user_id") != user_id:
            raise PermissionError("only owner can share private kb")

        self.store.upsert_policy(
            resource_type="kb",
            resource_id=kb_id,
            owner_dept_id=owner_dept,
            visibility="dept",
            created_by=user_id,
            owner_user_id=policy.get("owner_user_id"),
            allow_dept_ids=[],
            deny_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")],
            allow_roles=[str(x) for x in json.loads(policy.get("allow_roles_json") or "[]")],
            category=(policy.get("category") or "D"),
        )
        return self.store.get_policy("kb", kb_id) or {}

    def share_kb_to_user(self, token_payload: dict[str, Any], kb_id: str, target_user_login: str, permission: str = "read") -> dict[str, Any]:
        """Share a KB to a specific user.
        - Owner: can always share (private KB direct, dept KB via approval).
        - Write-permission holder: can re-share (same rules as owner).
        - Read-only holder: CANNOT re-share.
        permission: 'read' (read-only) or 'write' (read-write).
        """
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        if permission not in ("read", "write", "share"):
            raise ValueError("permission must be 'read', 'write', or 'share'")

        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")

        owner_user_id = policy.get("owner_user_id") or ""
        visibility = (policy.get("visibility") or "dept").lower()
        is_owner = owner_user_id == user_id

        # Private KB: only owner can share
        if visibility == "private" and not is_owner:
            raise PermissionError("仅知识库所有者可分享私密知识库")

        # Check re-share permission: only owner or share-permission holders can share
        if not is_owner:
            my_perm = self.store.get_kb_permission(kb_id, user_id)
            if not self.store.has_share(my_perm):
                raise PermissionError("需要可分享权限才能将知识库分享给他人")

        # Find target user
        target = self.store.get_user_by_login(target_user_login)
        if not target:
            raise ValueError(f"user {target_user_login} not found")
        target_user_id = target["user_id"]
        if target_user_id == user_id:
            raise ValueError("cannot share to yourself")

        # Non-owner + non-private KB → requires owner approval
        if not is_owner and visibility != "private":
            owner_dept = self._normalize_dept_id(policy.get("owner_dept_id") or "")
            # Convert permission string to bitmask
            perm_mask = self.store.PERM_READ
            if permission == "write":
                perm_mask = self.store.PERM_READ | self.store.PERM_WRITE | self.store.PERM_SHARE
            elif permission == "share":
                perm_mask = self.store.PERM_READ | self.store.PERM_SHARE

            parent_share_id = None
            with self.store._connect() as conn:
                parent_row = conn.execute(
                    "SELECT id FROM kb_shares WHERE kb_id = ? AND target_user_id = ? AND status = 'approved'",
                    (kb_id, user_id),
                ).fetchone()
                parent_share_id = parent_row["id"] if parent_row else None

            req = self.store.create_kb_share_request(
                kb_id=kb_id,
                owner_dept_id=owner_dept,
                requester_user_id=user_id,
                target_scope="user",
                target_user_id=target_user_id,
                reason=f"分享给 {target_user_login}（{permission}权限），需所有者审批",
                requested_permission=int(perm_mask),
                parent_share_id=parent_share_id,
            )
            return {"request_id": req.get("request_id"), "status": "pending", "message": "分享申请已提交，等待所有者审批"}

        # Find target user
        target = self.store.get_user_by_login(target_user_login)
        if not target:
            raise ValueError(f"user {target_user_login} not found")
        target_user_id = target["user_id"]

        if target_user_id == user_id:
            raise ValueError("cannot share to yourself")

        # Convert permission string to bitmask
        perm_mask = self.store.PERM_READ
        if permission == "write":
            perm_mask = self.store.PERM_READ | self.store.PERM_WRITE | self.store.PERM_SHARE
        elif permission == "share":
            perm_mask = self.store.PERM_READ | self.store.PERM_SHARE

        # Determine parent_share_id (for chain tracking)
        parent_share_id = None
        if owner_user_id != user_id:
            # Sharer is not the owner - get their share record as parent
            sharer_share = self.store.get_kb_permission(kb_id, user_id)
            if sharer_share > 0:
                with self.store._connect() as conn:
                    parent_row = conn.execute(
                        "SELECT id FROM kb_shares WHERE kb_id = ? AND target_user_id = ? AND status = 'approved'",
                        (kb_id, user_id),
                    ).fetchone()
                    parent_share_id = parent_row["id"] if parent_row else None

        # Record share (single source of truth - no dual write)
        self.store.add_kb_share(kb_id, target_user_id, perm_mask, user_id, parent_share_id=parent_share_id)
        return {"permission_mask": perm_mask, "target_user_id": target_user_id}

    def list_share_requests(
        self,
        token_payload: dict[str, Any],
        status: str | None = None,
        owner_dept_id: str | None = None,
    ) -> list[dict[str, Any]]:
        user_id = token_payload.get("sub") or ""
        if not user_id:
            return []
        # MP superadmin sees all; dept admins see their own dept's requests
        admin_depts = [
            self._normalize_dept_id(x)
            for x in (token_payload.get("dept_ids") or [])
            if self.store.is_dept_admin(user_id, x)
        ]
        if admin_depts:
            # Filter by specific dept if provided, otherwise show all depts where user is admin
            filter_dept = self._normalize_dept_id(owner_dept_id) if owner_dept_id else (admin_depts[0] if len(admin_depts) == 1 else None)
            rows = self.store.list_kb_share_requests(
                status=(status or "").strip() or None,
                owner_dept_id=filter_dept,
                page=1,
                page_size=200,
            )
            if len(admin_depts) == 1:
                return rows
            admin_set = set(admin_depts)
            return [r for r in rows if self._normalize_dept_id(r.get("owner_dept_id") or "") in admin_set]
        return self.store.list_kb_share_requests(
            status=(status or "").strip() or None,
            requester_user_id=user_id,
            page=1,
            page_size=200,
        )

    def review_share_request(self, token_payload: dict[str, Any], request_id: str, approved: bool, review_comment: str = "") -> dict[str, Any]:
        user_id = token_payload.get("sub") or ""
        req = self.store.get_kb_share_request(request_id)
        if not req:
            raise ValueError("share request not found")
        if req.get("status") != "pending":
            raise ValueError("share request is not pending")
        owner_dept = self._normalize_dept_id(req.get("owner_dept_id") or "")
        # Only MP superadmin or the owning department's admin can approve
        is_mp_superadmin = self.store.is_dept_admin(user_id, "dept_mp")
        is_owner_dept_admin = self.store.is_dept_admin(user_id, owner_dept)
        if not is_mp_superadmin and not is_owner_dept_admin:
            raise PermissionError("只有归属部门的管理员才能审批此申请")

        req = self.store.review_kb_share_request(
            request_id=request_id,
            reviewer_user_id=user_id,
            approved=approved,
            review_comment=review_comment,
        )
        if not req:
            raise ValueError("share request not found")

        if approved:
            policy = self.store.get_policy("kb", req["kb_id"])
            if not policy:
                raise ValueError("kb policy not found")
            owner = self._normalize_dept_id(policy.get("owner_dept_id") or req.get("owner_dept_id") or "")
            existing_allow = [self._normalize_dept_id(x) for x in json.loads(policy.get("allow_dept_ids_json") or "[]")]
            target_scope = (req.get("target_scope") or "").lower()

            if target_scope == "mp":
                self.store.upsert_policy(
                    resource_type="kb",
                    resource_id=req["kb_id"],
                    owner_dept_id=owner,
                    visibility="public",
                    created_by=user_id,
                    owner_user_id=policy.get("owner_user_id"),
                    allow_dept_ids=[],
                    deny_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")],
                    allow_roles=[str(x) for x in json.loads(policy.get("allow_roles_json") or "[]")],
                    category=(policy.get("category") or "D"),
                )
            elif target_scope == "dept":
                # Add target departments to allow_dept_ids
                target_dept_ids = [self._normalize_dept_id(x) for x in json.loads(req.get("target_dept_ids_json") or "[]")]
                merged_depts = sorted(set(existing_allow + target_dept_ids))
                self.store.upsert_policy(
                    resource_type="kb",
                    resource_id=req["kb_id"],
                    owner_dept_id=owner,
                    visibility="dept",
                    created_by=user_id,
                    owner_user_id=policy.get("owner_user_id"),
                    allow_dept_ids=merged_depts,
                    deny_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")],
                    allow_roles=[str(x) for x in json.loads(policy.get("allow_roles_json") or "[]")],
                    category=(policy.get("category") or "D"),
                )
                # Create kb_shares entries for all users in target departments
                for dept_id in target_dept_ids:
                    dept_users = self.store.get_users_in_dept(dept_id)
                    for dept_user in dept_users:
                        if dept_user["user_id"] != policy.get("owner_user_id"):
                            self.store.add_kb_share(
                                req["kb_id"], dept_user["user_id"],
                                self.store.PERM_READ,
                                policy.get("owner_user_id") or user_id,
                            )
            elif target_scope == "user":
                target_user_id = req.get("target_user_id")
                if target_user_id:
                    perm_mask = int(req.get("requested_permission", self.store.PERM_READ))
                    sharer_user_id = req.get("requester_user_id") or ""
                    sharer_cap = int(self.store.has_kb_access(req["kb_id"], sharer_user_id))
                    if sharer_cap <= 0:
                        # Dept-admin initiated shares may not have a share record; allow READ-only fallback.
                        sharer_cap = int(self.store.PERM_READ)
                    if (perm_mask & int(self.store.PERM_READ)) == 0:
                        perm_mask |= int(self.store.PERM_READ)
                    if (perm_mask | sharer_cap) != sharer_cap:
                        raise PermissionError("申请的分享权限超过了发起人可分享的权限范围")
                    parent_id = req.get("parent_share_id")
                    self.store.add_kb_share(
                        req["kb_id"], target_user_id, perm_mask,
                        policy.get("owner_user_id") or user_id,
                        parent_share_id=parent_id,
                        approved_by=user_id,
                    )
                self.store.upsert_policy(
                    resource_type="kb",
                    resource_id=req["kb_id"],
                    owner_dept_id=owner,
                    visibility=policy.get("visibility", "dept"),
                    created_by=user_id,
                    owner_user_id=policy.get("owner_user_id"),
                    allow_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("allow_dept_ids_json") or "[]")],
                    deny_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")],
                    allow_roles=[str(x) for x in json.loads(policy.get("allow_roles_json") or "[]")],
                    category=(policy.get("category") or "D"),
                )
            else:
                self.store.upsert_policy(
                    resource_type="kb",
                    resource_id=req["kb_id"],
                    owner_dept_id=owner,
                    visibility="dept",
                    created_by=user_id,
                    owner_user_id=policy.get("owner_user_id"),
                    allow_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("allow_dept_ids_json") or "[]")],
                    deny_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")],
                    allow_roles=[str(x) for x in json.loads(policy.get("allow_roles_json") or "[]")],
                    category=(policy.get("category") or "D"),
                )

        return req

    def unshare_kb(self, token_payload: dict[str, Any], kb_id: str) -> dict[str, Any]:
        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")
        user_id = token_payload.get("sub") or ""
        owner_user_id = policy.get("owner_user_id") or ""
        owner_dept = self._normalize_dept_id(policy.get("owner_dept_id") or "")

        # Permission check: owner, owner dept admin, or MP superadmin
        is_mp = self.store.is_dept_admin(user_id, "dept_mp")
        is_owner = owner_user_id == user_id
        is_owner_dept_admin = self.store.is_dept_admin(user_id, owner_dept)
        if not is_mp and not is_owner and not is_owner_dept_admin:
            raise PermissionError("只有所有者、归属部门管理员或超级管理员才能撤回分享")

        # Revert visibility
        new_visibility = "private" if is_owner else "dept"

        self.store.upsert_policy(
            resource_type="kb",
            resource_id=kb_id,
            owner_dept_id=owner_dept,
            visibility=new_visibility,
            created_by=user_id,
            owner_user_id=owner_user_id,
            allow_dept_ids=[],
            deny_dept_ids=[],
            allow_roles=[],
            category=(policy.get("category") or "D"),
        )
        # Remove all share records (pointers)
        for s in self.store.list_kb_shares(kb_id):
            self.store.remove_kb_share(kb_id, s["target_user_id"])
        row = self.store.get_policy("kb", kb_id)
        return row or {}

    # ---- Recall Dashboard: share-to-depts, list-all-shares, selective-revoke ----

    def share_kb_to_depts(self, token_payload: dict[str, Any], kb_id: str, dept_ids: list[str], permission_mask: int) -> dict[str, Any]:
        """Share a KB to multiple departments. Owner-only action."""
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")
        owner_user_id = policy.get("owner_user_id") or ""
        if owner_user_id != user_id:
            raise PermissionError("只有知识库所有者才能分享到部门")
        if not isinstance(permission_mask, int) or permission_mask < self.store.PERM_READ:
            raise ValueError("permission_mask must be an integer >= 4 (at least READ)")

        owner_dept = self._normalize_dept_id(policy.get("owner_dept_id") or "")
        existing_allow: list[str] = []
        try:
            existing_allow = json.loads(policy.get("allow_dept_ids_json") or "[]")
        except Exception:
            pass

        normalized_new: list[str] = []
        for d in (dept_ids or []):
            nd = self._normalize_dept_id(str(d).strip())
            if nd and nd not in existing_allow:
                normalized_new.append(nd)

        merged = sorted(set(existing_allow + normalized_new))

        # If sharing to own dept and KB is private, change visibility to "dept"
        new_visibility = policy.get("visibility") or "dept"
        if new_visibility == "private" and owner_dept in merged:
            new_visibility = "dept"

        # Update policy
        self.store.upsert_policy(
            resource_type="kb",
            resource_id=kb_id,
            owner_dept_id=owner_dept,
            visibility=new_visibility,
            created_by=user_id,
            owner_user_id=owner_user_id,
            allow_dept_ids=merged,
            deny_dept_ids=[self._normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")],
            allow_roles=[str(x) for x in json.loads(policy.get("allow_roles_json") or "[]")],
            category=(policy.get("category") or "D"),
        )

        # Create kb_shares for all users in new departments
        for dept_id in normalized_new:
            dept_users = self.store.get_users_in_dept(dept_id)
            for dept_user in dept_users:
                if dept_user["user_id"] != owner_user_id:
                    self.store.add_kb_share(
                        kb_id, dept_user["user_id"], permission_mask, owner_user_id,
                    )

        return self.store.get_policy("kb", kb_id) or {}

    def list_all_shares(self, token_payload: dict[str, Any]) -> dict[str, Any]:
        """Return both dept-level and individual user shares for the current user's owned KBs."""
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        dept_shares = self.store.list_dept_shares_for_owner(user_id)
        user_shares = self.store.list_user_shares_for_owner(user_id)
        return {"dept_shares": dept_shares, "user_shares": user_shares}

    def selective_revoke(self, token_payload: dict[str, Any], kb_id: str, revoke_type: str, target_id: str) -> dict[str, Any]:
        """Revoke a specific share: either a dept share or an individual user share."""
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")
        owner_user_id = policy.get("owner_user_id") or ""
        if owner_user_id != user_id:
            raise PermissionError("只有知识库所有者才能撤回分享")

        if revoke_type == "dept":
            ok = self.store.revoke_dept_share(kb_id, target_id)
            if not ok:
                raise ValueError("dept share not found")
        elif revoke_type == "user":
            try:
                share_id = int(target_id)
            except (ValueError, TypeError):
                raise ValueError("target_id must be a valid share ID")
            ok = self.store.revoke_user_share(share_id)
            if not ok:
                raise ValueError("user share not found")
        else:
            raise ValueError("revoke_type must be 'dept' or 'user'")

        # Check if any shares remain; if none, optionally revert visibility
        remaining_policy = self.store.get_policy("kb", kb_id) or {}
        remaining_allow: list[str] = []
        try:
            remaining_allow = json.loads(remaining_policy.get("allow_dept_ids_json") or "[]")
        except Exception:
            pass
        remaining_shares = self.store.list_kb_shares(kb_id)
        if not remaining_allow and not remaining_shares:
            owner_dept = self._normalize_dept_id(policy.get("owner_dept_id") or "")
            self.store.upsert_policy(
                resource_type="kb",
                resource_id=kb_id,
                owner_dept_id=owner_dept,
                visibility="private",
                created_by=user_id,
                owner_user_id=owner_user_id,
                allow_dept_ids=[],
                deny_dept_ids=[],
                allow_roles=[],
                category=(policy.get("category") or "D"),
            )

        return {"ok": True, "kb_id": kb_id, "revoke_type": revoke_type, "target_id": target_id}

    def delete_private_kb(self, token_payload: dict[str, Any], kb_id: str) -> bool:
        """Delete a private KB. Only the owner can delete."""
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        policy = self.store.get_policy("kb", kb_id)
        if not policy:
            raise ValueError("kb not found")
        visibility = (policy.get("visibility") or "").lower()
        if visibility != "private":
            raise PermissionError("only private kb can be deleted")
        owner_user_id = policy.get("owner_user_id") or ""
        if owner_user_id != user_id:
            raise PermissionError("only owner can delete private kb")

        # Use owner's RAGFlow token for deletion
        owner_token = self.store.get_user_ragflow_token(owner_user_id)
        requests.delete(
            f"{self.cfg.base_url}/api/v1/datasets",
            headers={"Authorization": f"Bearer {owner_token or self.cfg.api_key}", "Content-Type": "application/json"},
            json={"ids": [kb_id]},
            timeout=30,
        )

        cleanup = self.store.cascade_cleanup_kb(kb_id)
        # Clean in-memory sessions that referenced this KB
        for sid in cleanup.get("session_ids") or []:
            self._session_registry.pop(str(sid), None)
        self.store.deactivate_policy("kb", kb_id)
        return True

    def delete_kbs(self, token_payload: dict[str, Any], kb_ids: list[str]) -> dict[str, Any]:
        user_id = token_payload.get("sub") or ""
        if not user_id:
            raise PermissionError("unauthorized")
        is_mp = self.store.is_dept_admin(user_id, "dept_mp")
        deleted: list[str] = []
        cleanup_stats: list[dict[str, Any]] = []
        depts = {self._normalize_dept_id(x) for x in (token_payload.get("dept_ids") or [])}
        selected_dept = self._normalize_dept_id(token_payload.get("default_dept_id") or "")
        for kb_id in kb_ids:
            policy = self.store.get_policy("kb", kb_id)
            if not policy:
                raise ValueError("kb not found")

            owner_user_id = policy.get("owner_user_id") or ""
            is_owner = owner_user_id == user_id

            # Check permission: read-only → only owner; read-write → owner + write users
            if not is_mp and not is_owner:
                perm = int(self.store.effective_kb_permission(depts, selected_dept, kb_id, user_id, policy=policy))
                if not self.store.has_write(perm):
                    raise PermissionError("只有所有者或可改写权限用户才能删除")

            # Use owner's RAGFlow token for deletion
            owner_token = self.store.get_user_ragflow_token(owner_user_id) if owner_user_id else self.cfg.api_key
            requests.delete(
                f"{self.cfg.base_url}/api/v1/datasets",
                headers={"Authorization": f"Bearer {owner_token}", "Content-Type": "application/json"},
                json={"ids": [kb_id]},
                timeout=30,
            )

            cleanup = self.store.cascade_cleanup_kb(kb_id)
            cleanup_stats.append(cleanup)
            for sid in cleanup.get("session_ids") or []:
                self._session_registry.pop(str(sid), None)
            self.store.deactivate_policy("kb", kb_id)
            deleted.append(kb_id)
        return {"deleted": deleted, "cleanup": cleanup_stats}

    def _multi_tenant_retrieve(self, kb_ids: list[str], question: str, top_k: int = 8, agent_owner_id: str = "") -> list[dict[str, Any]]:
        """Retrieve chunks from multiple RAGFlow accounts based on KB ownership.
        agent_owner_id: the user who owns the agent; used as fallback for KBs without a specific owner.
        """
        if not kb_ids or not question.strip():
            return []

        # Group kb_ids by their owner's ragflow_token
        token_groups: dict[str, list[str]] = {}  # ragflow_token -> [kb_ids]
        for kb_id in kb_ids:
            policy = self.store.get_policy("kb", kb_id)
            if not policy:
                print(f"[PORTAL DEBUG] retrieve: kb_id={kb_id[:12]}... NO POLICY FOUND")
                continue
            owner_user_id = policy.get("owner_user_id") or ""
            if not owner_user_id and agent_owner_id:
                owner_user_id = agent_owner_id
            owner_token = ""
            if owner_user_id:
                owner_token = self.store.get_user_ragflow_token(owner_user_id)
            if not owner_token and agent_owner_id:
                owner_token = self.store.get_user_ragflow_token(agent_owner_id)
            if not owner_token:
                owner_token = self.cfg.api_key  # fallback to shared admin
            masked = (owner_token[:8] + "...") if owner_token else "EMPTY"
            print(f"[PORTAL DEBUG] retrieve: kb_id={kb_id[:12]}... owner={owner_user_id[:12] if owner_user_id else 'NONE'} token={masked}")
            token_groups.setdefault(owner_token, []).append(kb_id)

        # Retrieve from each tenant
        all_chunks = []
        for ragflow_token, ds_ids in token_groups.items():
            try:
                rag = RAGFlow(api_key=ragflow_token, base_url=self.cfg.base_url)
                chunks = rag.retrieve(
                    dataset_ids=ds_ids,
                    question=question,
                    page_size=top_k,
                )
                chunk_list = list(chunks) if chunks else []
                print(f"[PORTAL DEBUG] retrieve: token={ragflow_token[:8]}... ds_ids={len(ds_ids)} chunks={len(chunk_list)}")
                for c in chunk_list:
                    chunk_dict = {
                        "content": getattr(c, "content", "") or "",
                        "document_name": getattr(c, "document_name", "") or "",
                        "similarity": getattr(c, "similarity", None),
                        "chunk_id": getattr(c, "id", ""),
                    }
                    if chunk_dict["content"]:
                        all_chunks.append(chunk_dict)
            except Exception as e:
                print(f"[PORTAL ERROR] Retrieval failed for token={ragflow_token[:8]}... ds_ids={ds_ids}: {e}")
                continue

        # Sort by similarity and take top_k
        all_chunks.sort(key=lambda x: x.get("similarity") or 0, reverse=True)
        print(f"[PORTAL DEBUG] retrieve total: {len(all_chunks)} chunks from {len(kb_ids)} KBs")
        return all_chunks[:top_k]

    def _generate_answer(self, question: str, chunks: list[dict[str, Any]], history: list[dict[str, str]] | None = None) -> str:
        """Generate answer using LLM with retrieved context."""
        if not chunks:
            return "当前知识库中未找到相关信息。"

        # Load system prompt
        prompt_path = Path(__file__).parent / "agent_system_prompt.md"
        system_prompt = ""
        if prompt_path.exists():
            system_prompt = prompt_path.read_text(encoding="utf-8")

        # Format context from chunks
        context_parts = []
        for i, c in enumerate(chunks, 1):
            doc_name = c.get("document_name", "未知文件")
            content = c.get("content", "")
            context_parts.append(f"[{i}] 文件: {doc_name}\n{content}")
        context = "\n\n".join(context_parts)

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        if history:
            messages.extend(history[-6:])  # last 6 messages for context
        messages.append({
            "role": "user",
            "content": f"根据以下知识库检索结果回答问题。如果检索结果中没有相关信息，明确告知用户。\n\n检索结果:\n{context}\n\n用户问题: {question}"
        })

        try:
            answer = self._direct_chat(messages)
            return answer
        except Exception as e:
            print(f"[PORTAL ERROR] LLM generation failed: {e}")
            return f"**回答生成失败**: {str(e)}"

    def _recover_agent_session(self, agent_id: str, session_id: str, agent_owner_id: str = ""):
        """服务器重启后尝试恢复 RAGFlow Session 对象。"""
        try:
            agent_rag = self.get_rag_client(agent_owner_id) if agent_owner_id else self.rag
            agents = agent_rag.list_agents(page=1, page_size=1, id=agent_id)
            if not agents:
                return None
            sessions = agents[0].list_sessions(page=1, page_size=100, id=session_id)
            for s in sessions:
                if s.id == session_id:
                    return s
            return None
        except Exception as e:
            print(f"[PORTAL WARN] Session recovery failed for {session_id}: {e}")
            return None

    def _load_session(self, session_id: str) -> dict | None:
        """从内存或 SQLite 加载会话，确保 agent_owner_id 存在。"""
        reg = self._session_registry.get(session_id)
        if reg:
            return reg
        persisted = self.store.get_portal_session(session_id)
        if not persisted:
            return None
        agent_id = persisted["agent_id"]
        agent_policy = self.store.get_policy("agent", agent_id) if agent_id and agent_id != "direct" else None
        reg = {
            "session": None,
            "user_id": persisted["user_id"],
            "dept_id": persisted["dept_id"],
            "agent_id": agent_id,
            "kb_ids": persisted.get("kb_ids", []),
            "agent_owner_id": (agent_policy or {}).get("owner_user_id", ""),
            "kb_overridden": persisted.get("kb_overridden", False),
        }
        self._session_registry[session_id] = reg
        return reg

    def ask(self, token_payload: dict[str, Any], session_id: str, question: str) -> dict[str, Any]:
        reg = self._load_session(session_id)
        if not reg:
            raise ValueError("session not found or expired")
        if reg["user_id"] != token_payload["sub"]:
            raise PermissionError("session owner mismatch")
        if not question.strip():
            raise ValueError("question is required")

        if reg["agent_id"] == "direct":
            # Direct mode: use retrieval pipeline when user selected knowledge bases
            kb_ids = reg.get("kb_ids", [])
            if kb_ids:
                chunks = self._multi_tenant_retrieve(kb_ids, question)
                hist_msgs = self.store.list_portal_messages(session_id, limit=6)
                history = []
                for m in hist_msgs:
                    if m.get("role") in {"user", "assistant"}:
                        history.append({"role": m.get("role"), "content": m.get("content")})
                answer = self._generate_answer(question, chunks, history)
                references = chunks
                print(f"[PORTAL DEBUG] direct+kb: retrieved {len(chunks)} chunks from {len(kb_ids)} KBs")
            else:
                history = self.store.list_portal_messages(session_id, limit=12)
                messages = []
                for m in history:
                    if m.get("role") in {"user", "assistant"}:
                        messages.append({"role": m.get("role"), "content": m.get("content")})
                messages.append({"role": "user", "content": question})
                answer = self._direct_chat(messages)
                references = []
        else:
            # Agent 模式
            kb_ids = reg.get("kb_ids", [])
            kb_overridden = reg.get("kb_overridden", False)
            print(f"[PORTAL DEBUG] Agent mode: kb_overridden={kb_overridden}, kb_count={len(kb_ids)}")

            if kb_overridden and kb_ids:
                # 用户选了知识库 → 检索仍由 portal 完成，但回答走智能体工作流。
                print(f"[PORTAL DEBUG] Agent+KB: retrieving {len(kb_ids)} KBs then calling agent workflow")
                chunks = self._multi_tenant_retrieve(kb_ids, question, agent_owner_id=reg.get("agent_owner_id", ""))

                context_lines: list[str] = []
                for i, c in enumerate(chunks[:12]):
                    docnm = (c.get("docnm_kwd") or c.get("document_name") or "").strip()
                    content = (c.get("content_with_weight") or c.get("content") or "").strip()
                    if not content:
                        continue
                    head = f"[{i+1}]"
                    if docnm:
                        head += f" {docnm}"
                    context_lines.append(head)
                    context_lines.append(content)
                context_block = "\n".join(context_lines)
                if len(context_block) > 6000:
                    context_block = context_block[:6000] + "\n...(已截断)"

                injected_question = (
                    "请基于以下【知识库检索结果】回答用户问题。若资料不足请说明。\n\n"
                    f"【知识库检索结果】\n{context_block}\n\n"
                    f"【用户问题】\n{question}"
                )

                rag_session = reg.get("session")
                if rag_session is None:
                    rag_session = self._recover_agent_session(
                        reg["agent_id"], session_id, reg.get("agent_owner_id", "")
                    )
                    if rag_session:
                        reg["session"] = rag_session
                        self._session_registry[session_id] = reg

                if rag_session is not None:
                    try:
                        answer_obj = None
                        for ans in rag_session.ask(
                            question=injected_question,
                            stream=False,
                            inputs={"knowledge_context": {"value": context_block}},
                        ):
                            answer_obj = ans
                        answer = (answer_obj.content or "") if answer_obj else "Agent returned no response."
                    except Exception as e:
                        print(f"[PORTAL ERROR] Agent session.ask() failed (agent+kb): {e}")
                        answer = self._direct_chat([{"role": "user", "content": injected_question}])
                else:
                    answer = self._direct_chat([{"role": "user", "content": injected_question}])

                references = chunks
            else:
                # 用户没选知识库 → 走智能体原始工作流
                rag_session = reg.get("session")
                if rag_session is None:
                    rag_session = self._recover_agent_session(
                        reg["agent_id"], session_id, reg.get("agent_owner_id", "")
                    )
                    if rag_session:
                        reg["session"] = rag_session
                        self._session_registry[session_id] = reg

                if rag_session is not None:
                    try:
                        print(f"[PORTAL DEBUG] Calling session.ask() for agent {reg['agent_id']}")
                        answer_obj = None
                        for ans in rag_session.ask(question=question, stream=False):
                            answer_obj = ans
                        if answer_obj:
                            answer = answer_obj.content or ""
                            raw_ref = getattr(answer_obj, "reference", None) or {}
                            references = self._extract_chunks(raw_ref)
                        else:
                            answer = "Agent returned no response."
                            references = []
                    except Exception as e:
                        print(f"[PORTAL ERROR] Agent session.ask() failed: {e}")
                        answer = self._direct_chat([{"role": "user", "content": question}])
                        references = []
                else:
                    answer = self._direct_chat([{"role": "user", "content": question}])
                    references = []

        self.store.append_portal_message(session_id, "user", question, references=[])
        self.store.append_portal_message(session_id, "assistant", answer, references=references)

        return {
            "session_id": session_id,
            "answer": answer,
            "references": references,
            "agent_id": reg["agent_id"],
            "dept_id": reg["dept_id"],
        }

    def get_history(self, token_payload: dict[str, Any], session_id: str) -> list[dict[str, Any]]:
        reg = self._load_session(session_id)
        if not reg:
            return []
        if reg["user_id"] != token_payload["sub"]:
            raise PermissionError("session owner mismatch")
        return self.store.list_portal_messages(session_id, limit=500)


def load_portal_config_from_env() -> PortalConfig:
    api_key = os.getenv("RAGFLOW_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("RAGFLOW_API_KEY is required")

    secret = os.getenv("PORTAL_AUTH_SECRET", "").strip() or hashlib.sha256((api_key + "portal").encode("utf-8")).hexdigest()

    default_db_path = str((Path(__file__).resolve().parent / "data" / "portal_auth.sqlite3").resolve())
    return PortalConfig(
        api_key=api_key,
        base_url=os.getenv("RAGFLOW_BASE_URL", "http://127.0.0.1:9380").strip(),
        db_path=os.getenv("PORTAL_DB_PATH", default_db_path).strip(),
        auth_secret=secret,
        direct_chat_url=os.getenv("DIRECT_CHAT_URL", "https://apimgateway.siemens-healthineers.com").strip(),
        direct_chat_api_key=os.getenv("DIRECT_CHAT_API_KEY", "ab6b83c59c2f488e931287b66cadd124").strip(),
        direct_chat_model=os.getenv("DIRECT_CHAT_MODEL", "gpt-5.4").strip(),
        token_ttl_seconds=int(os.getenv("PORTAL_TOKEN_TTL_SECONDS", "28800")),
    )


def create_app() -> Flask:
    cfg = load_portal_config_from_env()
    gateway = PortalGateway(cfg)

    web_root = Path(__file__).resolve().parent / "portal_web"
    app = Flask(__name__, static_folder=str(web_root), static_url_path="/portal/static")

    def _request_id() -> str:
        return request.headers.get("X-Request-ID") or uuid.uuid4().hex

    def _ok(data: Any = None, code: int = 200):
        return jsonify({"ok": True, "data": data}), code

    def _err(message: str, code: int = 400, error_code: str = "bad_request"):
        return jsonify({"ok": False, "error_code": error_code, "message": message}), code

    def _auth_payload() -> dict[str, Any] | None:
        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[7:].strip()
        return gateway.parse_token(token)

    def _is_admin(payload: dict[str, Any]) -> bool:
        user_id = payload.get("sub") or ""
        return gateway.store.is_dept_admin(user_id, "dept_mp") if user_id else False

    @app.after_request
    def _cors(resp):
        resp.headers["Access-Control-Allow-Origin"] = "*"
        resp.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization, X-Request-ID"
        resp.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        return resp

    @app.route("/", methods=["GET"])
    def index():
        return send_from_directory(web_root, "index.html")

    @app.route("/healthz", methods=["GET"])
    def healthz():
        return _ok(
            {
                "service": "qms-portal-gateway",
                "status": "ok",
                "features": [
                    "resource_type_all",
                    "kb_policy_category",
                    "session_kb_binding",
                    "admin_bootstrap_agents",
                ],
            }
        )

    @app.route("/portal/v1/auth/login", methods=["POST", "OPTIONS"])
    def login():
        if request.method == "OPTIONS":
            return ("", 204)
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        login_id = (payload.get("username") or payload.get("login_id") or "").strip()
        password = (payload.get("password") or "").strip()
        if not login_id or not password:
            return _err("username/login_id and password are required", 400)

        out = gateway.login(login_id=login_id, password=password)
        if not out:
            gateway.store.write_audit(req_id, "auth.login", "denied", error_code="invalid_credentials")
            return _err("invalid username or password", 401, "invalid_credentials")

        gateway.store.write_audit(req_id, "auth.login", "success", user_id=out["user"]["user_id"], dept_id=out["default_dept_id"])
        return _ok(out)

    @app.route("/portal/v1/auth/register", methods=["POST", "OPTIONS"])
    def register():
        if request.method == "OPTIONS":
            return ("", 204)
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        login_id = (payload.get("login_id") or "").strip()
        password = (payload.get("password") or "").strip()
        display_name = (payload.get("display_name") or login_id).strip()
        dept_id = (payload.get("dept_id") or "").strip()

        if not login_id or not password:
            return _err("login_id and password are required", 400)
        if len(password) < 6:
            return _err("password must be at least 6 characters", 400)
        if not dept_id:
            return _err("dept_id is required", 400)

        # Normalize dept_id
        dept_id = gateway._normalize_dept_id(dept_id)
        dept_row = gateway.store._connect().execute(
            "SELECT dept_id FROM departments WHERE dept_id = ?", (dept_id,)
        ).fetchone()
        if not dept_row:
            return _err(f"department {dept_id} not found", 400)

        # 1. Register user in RAGFlow, then create an API key for them
        ragflow_user_id = ""
        ragflow_token = ""
        try:
            ragflow_base = gateway.cfg.base_url.rstrip("/")
            email = login_id.lower() if "@" in login_id else f"{login_id.lower()}@siemens-healthineers.com"
            try:
                enc_password = _encrypt_password_for_ragflow(password)
            except ModuleNotFoundError as e:
                # Misconfiguration of the portal runtime environment.
                return _err(str(e), 500, "dependency_missing")

            # Step 1a: Register
            resp = requests.post(
                f"{ragflow_base}/v1/user/register",
                json={"nickname": login_id, "email": email, "password": enc_password},
                timeout=30,
            )
            rdata = resp.json()
            if rdata.get("code") == 0:
                ragflow_user_id = rdata.get("data", {}).get("id", "")
                print(f"[PORTAL] RAGFlow user created: {login_id} -> {ragflow_user_id}")
            elif rdata.get("code") == 102:
                return _err("RAGFlow user registration is disabled", 503)
            else:
                msg = rdata.get("message", "unknown error")
                if "already registered" in msg.lower():
                    # Email already exists in RAGFlow (e.g. portal user was deleted but RAGFlow user remains)
                    print(f"[PORTAL] RAGFlow email already exists, will link to existing account")
                else:
                    print(f"[PORTAL WARN] RAGFlow registration failed: {msg}")
                    return _err(f"RAGFlow registration failed: {msg}", 502)

            # Step 1b: Login to get session token (JWT is in response Authorization header)
            login_resp = requests.post(
                f"{ragflow_base}/v1/user/login",
                json={"email": email, "password": enc_password},
                timeout=30,
            )
            login_result = login_resp.json()
            auth_jwt = login_resp.headers.get("Authorization", "")
            session_cookies = login_resp.cookies
            if not ragflow_user_id and login_result.get("code") == 0:
                ragflow_user_id = login_result.get("data", {}).get("id", "")
            if not auth_jwt:
                print(f"[PORTAL WARN] Could not get RAGFlow JWT for {login_id}")
            else:
                # Step 1c: Create API key using JWT + session cookie
                api_resp = requests.post(
                    f"{ragflow_base}/v1/api/new_token",
                    json={"dialog_id": ""},
                    headers={"Authorization": auth_jwt},
                    cookies=session_cookies,
                    timeout=30,
                )
                api_data = api_resp.json()
                if api_data.get("code") == 0:
                    ragflow_token = api_data.get("data", {}).get("token", "")
                    print(f"[PORTAL] RAGFlow API key created for {login_id}")
                else:
                    print(f"[PORTAL WARN] Failed to create API key: {api_data.get('message', '')}")

            print(f"[PORTAL] RAGFlow setup complete: user_id={ragflow_user_id}, api_key={'yes' if ragflow_token else 'no'}")
        except Exception as e:
            print(f"[PORTAL ERROR] RAGFlow registration request failed: {e}")
            return _err(f"failed to connect to RAGFlow: {e}", 502)

        # 2. Create user in portal SQLite
        try:
            role = "member"  # 注册默认为普通员工，管理员只通过种子数据创建
            result = gateway.store.register_user(
                login_id=login_id,
                password=password,
                display_name=display_name,
                dept_id=dept_id,
                role=role,
                ragflow_user_id=ragflow_user_id,
                ragflow_token=ragflow_token,
            )
        except ValueError as e:
            return _err(str(e), 409)
        except Exception as e:
            print(f"[PORTAL ERROR] portal user creation failed: {e}")
            return _err(f"failed to create portal user: {e}", 500)

        gateway.store.write_audit(
            req_id, "auth.register", "success",
            user_id=result["user_id"], dept_id=dept_id,
        )
        return _ok(result, 201)

    @app.route("/portal/v1/me", methods=["GET"])
    def me():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        default_dept_id = auth.get("default_dept_id") or ""
        user_id = auth.get("sub") or ""
        # is_admin = MP superadmin only (admin of dept_mp)
        is_admin = gateway.store.is_dept_admin(user_id, "dept_mp")
        is_dept_admin = gateway.store.is_dept_admin(user_id, default_dept_id) if user_id and default_dept_id else False
        return _ok(
            {
                "user_id": auth.get("sub"),
                "login_id": auth.get("login_id"),
                "display_name": auth.get("display_name"),
                "dept_ids": auth.get("dept_ids", []),
                "dept": (default_dept_id or "").replace("dept_", "").upper(),
                "default_dept_id": auth.get("default_dept_id"),
                "is_admin": bool(is_admin),
                "is_dept_admin": bool(is_dept_admin),
                "exp": auth.get("exp"),
            }
        )

    @app.route("/portal/v1/me/delete", methods=["DELETE", "OPTIONS"])
    def delete_my_account():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        user_id = auth.get("sub") or ""
        req_id = _request_id()

        # 1. Delete RAGFlow user if linked
        ragflow_user_id = gateway.store.get_user_ragflow_user_id(user_id)
        if ragflow_user_id:
            try:
                ragflow_base = gateway.cfg.base_url.rstrip("/")
                resp = requests.delete(
                    f"{ragflow_base}/v1/user/{ragflow_user_id}",
                    headers={"Authorization": f"Bearer {gateway.cfg.api_key}"},
                    timeout=30,
                )
                print(f"[PORTAL] RAGFlow user delete: status={resp.status_code} body={resp.text[:100]}")
            except Exception as e:
                print(f"[PORTAL WARN] Failed to delete RAGFlow user: {e}")

        # 2. Delete portal user
        gateway.store.delete_user(user_id)
        gateway.store.write_audit(req_id, "user.delete_account", "success", user_id=user_id)
        return _ok({"deleted": True})

    @app.route("/portal/v1/departments", methods=["GET"])
    def departments():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        show_all = request.args.get("all", "").lower() == "true"
        user_dept_ids = {PortalGateway._normalize_dept_id(x) for x in (auth.get("dept_ids") or [])}
        all_depts = gateway.store.get_all_departments()
        allowed_codes = {"MP", "MP-Q", "MP-PLM", "MP-AP", "MP-MC", "MP-US&DX"}
        seen: set[str] = set()
        items = []
        for d in all_depts:
            dept_id = PortalGateway._normalize_dept_id(d.get("dept_id") or "")
            dept_code = PortalGateway._dept_code(dept_id)
            if dept_code not in allowed_codes or dept_id in seen:
                continue
            if show_all or dept_id in user_dept_ids:
                copied = dict(d)
                copied["dept_id"] = dept_id
                copied["dept_code"] = dept_code
                items.append(copied)
                seen.add(dept_id)
        items.sort(key=lambda x: x.get("dept_code") or "")
        return _ok({"items": items, "total": len(items)})

    @app.route("/portal/v1/users/search", methods=["GET"])
    def search_users():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        q = (request.args.get("q") or "").strip()
        if len(q) < 1:
            return _ok({"items": []})
        users = gateway.store.search_users(q, limit=20)
        return _ok({"items": users})

    @app.route("/portal/v1/share-targets", methods=["GET"])
    def share_targets():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        all_depts = gateway.store.get_all_departments()
        allowed_codes = {"MP", "MP-Q", "MP-PLM", "MP-AP", "MP-MC", "MP-US&DX"}
        seen: set[str] = set()
        items = []
        for d in all_depts:
            dept_id = PortalGateway._normalize_dept_id(d.get("dept_id") or "")
            dept_code = PortalGateway._dept_code(dept_id)
            if dept_code in allowed_codes and dept_id not in seen:
                copied = dict(d)
                copied["dept_id"] = dept_id
                copied["dept_code"] = dept_code
                items.append(copied)
                seen.add(dept_id)
        items.sort(key=lambda x: x.get("dept_code") or "")
        return _ok({"items": items, "total": len(items)})

    @app.route("/portal/v1/resources", methods=["GET"])
    def resources():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")

        req_id = _request_id()
        dept_id = (request.args.get("dept_id") or "").strip() or None
        resource_type = (request.args.get("resource_type") or "all").strip().lower()
        if resource_type not in {"agent", "kb", "all"}:
            return _err("resource_type must be one of: agent, kb, all", 400)
        try:
            items = gateway.list_resources(auth, dept_id=dept_id, resource_type=resource_type)
            gateway.store.write_audit(req_id, "resource.list", "success", user_id=auth.get("sub"), dept_id=dept_id or auth.get("default_dept_id"))
            return _ok({"items": items, "total": len(items)})
        except PermissionError as e:
            gateway.store.write_audit(req_id, "resource.list", "denied", user_id=auth.get("sub"), dept_id=dept_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "resource.list", "error", user_id=auth.get("sub"), dept_id=dept_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/sessions", methods=["POST", "OPTIONS"])
    def create_session():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        dept_id = (payload.get("dept_id") or auth.get("default_dept_id") or "").strip()
        agent_id = (payload.get("agent_id") or "").strip()
        kb_ids = payload.get("kb_ids") or []
        is_private = bool(payload.get("is_private"))
        if not dept_id:
            return _err("dept_id is required", 400)

        try:
            data = gateway.create_session(
                auth,
                dept_id=dept_id,
                agent_id=agent_id,
                kb_ids=[str(x) for x in kb_ids if str(x).strip()],
                is_private=is_private,
            )
            gateway.store.write_audit(req_id, "session.create", "success", user_id=auth.get("sub"), dept_id=dept_id, resource_type="agent", resource_id=agent_id, session_id=data["session_id"])
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "session.create", "denied", user_id=auth.get("sub"), dept_id=dept_id, resource_type="agent", resource_id=agent_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "session.create", "error", user_id=auth.get("sub"), dept_id=dept_id, resource_type="agent", resource_id=agent_id, error_code="not_found", error_message=str(e))
            return _err(str(e), 404, "not_found")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "session.create", "error", user_id=auth.get("sub"), dept_id=dept_id, resource_type="agent", resource_id=agent_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/sessions", methods=["GET"])
    def list_sessions():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        dept_id = (request.args.get("dept_id") or "").strip() or None
        try:
            items = gateway.list_sessions(auth, dept_id=dept_id)
            return _ok({"items": items, "total": len(items)})
        except PermissionError as e:
            return _err(str(e), 403, "forbidden")

    @app.route("/portal/v1/sessions/<session_id>", methods=["DELETE", "OPTIONS"])
    def delete_session(session_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        ok = gateway.delete_session(auth, session_id=session_id)
        if not ok:
            gateway.store.write_audit(req_id, "session.delete", "denied", user_id=auth.get("sub"), session_id=session_id, error_code="not_found")
            return _err("session not found", 404, "not_found")
        gateway.store.write_audit(req_id, "session.delete", "success", user_id=auth.get("sub"), session_id=session_id)
        return _ok({"session_id": session_id, "deleted": True})

    @app.route("/portal/v1/sessions/<session_id>/privacy", methods=["POST", "OPTIONS"])
    def update_session_privacy(session_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        is_private = bool(payload.get("is_private"))
        ok = gateway.update_session_privacy(auth, session_id=session_id, is_private=is_private)
        if not ok:
            gateway.store.write_audit(req_id, "session.privacy", "denied", user_id=auth.get("sub"), session_id=session_id, error_code="forbidden")
            return _err("session not found", 404, "not_found")
        gateway.store.write_audit(req_id, "session.privacy", "success", user_id=auth.get("sub"), session_id=session_id)
        return _ok({"session_id": session_id, "is_private": is_private})

    @app.route("/portal/v1/chat", methods=["POST", "OPTIONS"])
    def chat():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        session_id = (payload.get("session_id") or "").strip()
        question = (payload.get("question") or "").strip()
        if not session_id or not question:
            return _err("session_id and question are required", 400)

        try:
            data = gateway.ask(auth, session_id=session_id, question=question)
            gateway.store.write_audit(req_id, "session.ask", "success", user_id=auth.get("sub"), dept_id=data.get("dept_id"), resource_type="agent", resource_id=data.get("agent_id"), session_id=session_id)
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "session.ask", "denied", user_id=auth.get("sub"), session_id=session_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "session.ask", "error", user_id=auth.get("sub"), session_id=session_id, error_code="invalid_session", error_message=str(e))
            return _err(str(e), 404, "invalid_session")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "session.ask", "error", user_id=auth.get("sub"), session_id=session_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/sessions/<session_id>/messages", methods=["GET"])
    def history(session_id: str):
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        try:
            items = gateway.get_history(auth, session_id=session_id)
            return _ok({"items": items, "total": len(items)})
        except PermissionError as e:
            return _err(str(e), 403, "forbidden")

    @app.route("/portal/v1/kbs/upload", methods=["POST", "OPTIONS"])
    def upload_kb():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()

        dept_id = (request.form.get("dept_id") or auth.get("default_dept_id") or "").strip()
        kb_name = (request.form.get("kb_name") or "").strip()
        description = (request.form.get("description") or "").strip()
        is_private = str(request.form.get("is_private") or "").strip().lower() in {"1", "true", "yes", "on"}
        files = request.files.getlist("files")
        try:
            data = gateway.upload_kb_for_dept(
                auth,
                dept_id=dept_id,
                kb_name=kb_name,
                files=files,
                description=description,
                is_private=is_private,
            )
            gateway.store.write_audit(req_id, "kb.upload", "success", user_id=auth.get("sub"), dept_id=data.get("dept_id"), resource_type="kb", resource_id=data.get("kb_id"))
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.upload", "denied", user_id=auth.get("sub"), dept_id=dept_id, resource_type="kb", error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.upload", "error", user_id=auth.get("sub"), dept_id=dept_id, resource_type="kb", error_code="bad_request", error_message=str(e))
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.upload", "error", user_id=auth.get("sub"), dept_id=dept_id, resource_type="kb", error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs", methods=["POST", "OPTIONS"])
    def create_kb_private():
        # alias of upload endpoint: create private kb within current dept
        return upload_kb()

    @app.route("/portal/v1/kbs/<kb_id>/share-requests", methods=["POST", "OPTIONS"])
    def create_kb_share_request(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        target_scope = (payload.get("target_scope") or "").strip().lower()
        target_dept_ids = payload.get("target_dept_ids") or []
        target_user_login = (payload.get("target_user_login") or "").strip()
        reason = (payload.get("reason") or "").strip()
        permission = (payload.get("permission") or "read").strip().lower()
        try:
            data = gateway.request_kb_share(
                auth,
                kb_id=kb_id,
                target_scope=target_scope,
                target_dept_ids=[str(x) for x in target_dept_ids if str(x).strip()],
                target_user_login=target_user_login,
                reason=reason,
                permission=permission,
            )
            gateway.store.write_audit(req_id, "kb.share.request", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id)
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.share.request", "denied", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.share.request", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="bad_request", error_message=str(e))
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.share.request", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs/<kb_id>/share-internal", methods=["POST", "OPTIONS"])
    def share_kb_internal(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        try:
            data = gateway.share_kb_internal(auth, kb_id=kb_id)
            gateway.store.write_audit(req_id, "kb.share.internal", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id)
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.share.internal", "denied", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.share.internal", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="bad_request", error_message=str(e))
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.share.internal", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs/<kb_id>/share-to-user", methods=["POST", "OPTIONS"])
    def share_kb_to_user(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        target_user_login = (payload.get("target_user_login") or "").strip()
        permission = (payload.get("permission") or "read").strip()
        if not target_user_login:
            return _err("target_user_login is required", 400)
        try:
            data = gateway.share_kb_to_user(auth, kb_id=kb_id, target_user_login=target_user_login, permission=permission)
            gateway.store.write_audit(req_id, "kb.share.to_user", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id)
            return _ok(data)
        except PermissionError as e:
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs/<kb_id>/shares", methods=["GET"])
    def list_kb_shares(kb_id: str):
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        user_id = auth.get("sub") or ""
        # Any user with access to the KB can view the share chain
        policy = gateway.store.get_policy("kb", kb_id)
        if not policy:
            return _err("kb not found", 404)
        depts = {PortalGateway._normalize_dept_id(x) for x in (auth.get("dept_ids") or [])}
        selected_dept = PortalGateway._normalize_dept_id(auth.get("default_dept_id") or "")
        perm = int(gateway.store.effective_kb_permission(depts, selected_dept, kb_id, user_id, policy=policy))
        if not gateway.store.has_read(perm):
            return _err("no access to this knowledge base", 403)
        shares = gateway.store.list_kb_shares(kb_id)
        return _ok({"items": shares})

    @app.route("/portal/v1/share-requests", methods=["GET"])
    def list_share_requests():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        status = (request.args.get("status") or "").strip() or None
        owner_dept_id = (request.args.get("owner_dept_id") or "").strip() or None
        try:
            items = gateway.list_share_requests(auth, status=status, owner_dept_id=owner_dept_id)
            return _ok({"items": items, "total": len(items)})
        except Exception as e:  # noqa: BLE001
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/share-requests/<request_id>/approve", methods=["POST", "OPTIONS"])
    def approve_share_request(request_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        review_comment = (payload.get("review_comment") or "").strip()
        try:
            data = gateway.review_share_request(auth, request_id=request_id, approved=True, review_comment=review_comment)
            gateway.store.write_audit(req_id, "kb.share.approve", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=data.get("kb_id"))
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.share.approve", "denied", user_id=auth.get("sub"), error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.share.approve", "error", user_id=auth.get("sub"), error_code="bad_request", error_message=str(e))
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.share.approve", "error", user_id=auth.get("sub"), error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/share-requests/<request_id>/reject", methods=["POST", "OPTIONS"])
    def reject_share_request(request_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        review_comment = (payload.get("review_comment") or "").strip()
        try:
            data = gateway.review_share_request(auth, request_id=request_id, approved=False, review_comment=review_comment)
            gateway.store.write_audit(req_id, "kb.share.reject", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=data.get("kb_id"))
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.share.reject", "denied", user_id=auth.get("sub"), error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.share.reject", "error", user_id=auth.get("sub"), error_code="bad_request", error_message=str(e))
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.share.reject", "error", user_id=auth.get("sub"), error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/share-requests/<request_id>/dismiss", methods=["POST", "OPTIONS"])
    def dismiss_share_request(request_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        try:
            req = gateway.store.get_kb_share_request(request_id)
            if not req:
                return _err("share request not found", 404, "not_found")
            user_id = auth.get("sub")
            owner_dept = PortalGateway._normalize_dept_id(req.get("owner_dept_id") or "")
            if not gateway.store.is_dept_admin(user_id, "dept_mp") and not gateway.store.is_dept_admin(user_id, owner_dept):
                return _err("forbidden", 403, "forbidden")
            ok = gateway.store.delete_kb_share_request(request_id)
            if not ok:
                return _err("share request not found", 404, "not_found")
            gateway.store.write_audit(req_id, "kb.share.dismiss", "success", user_id=user_id, resource_type="kb", resource_id=req.get("kb_id"))
            return _ok({"request_id": request_id, "dismissed": True})
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.share.dismiss", "error", user_id=auth.get("sub"), error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs/<kb_id>/unshare", methods=["POST", "OPTIONS"])
    def unshare_kb(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        try:
            data = gateway.unshare_kb(auth, kb_id=kb_id)
            gateway.store.write_audit(req_id, "kb.unshare", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id)
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.unshare", "denied", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.unshare", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="bad_request", error_message=str(e))
            return _err(str(e), 400, "bad_request")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.unshare", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs/<kb_id>/delete-private", methods=["POST", "OPTIONS"])
    def delete_private_kb(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        try:
            gateway.delete_private_kb(auth, kb_id=kb_id)
            gateway.store.write_audit(req_id, "kb.delete.private", "success", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id)
            return _ok({"kb_id": kb_id, "deleted": True})
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.delete.private", "denied", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.delete.private", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="not_found", error_message=str(e))
            return _err(str(e), 404, "not_found")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.delete.private", "error", user_id=auth.get("sub"), resource_type="kb", resource_id=kb_id, error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/kbs/delete", methods=["POST", "OPTIONS"])
    def delete_kbs():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        payload = request.get_json(silent=True) or {}
        kb_ids = [str(x) for x in (payload.get("kb_ids") or []) if str(x).strip()]
        if not kb_ids:
            return _err("kb_ids are required", 400, "bad_request")
        try:
            data = gateway.delete_kbs(auth, kb_ids=kb_ids)
            gateway.store.write_audit(req_id, "kb.delete.batch", "success", user_id=auth.get("sub"), resource_type="kb")
            return _ok(data)
        except PermissionError as e:
            gateway.store.write_audit(req_id, "kb.delete.batch", "denied", user_id=auth.get("sub"), resource_type="kb", error_code="forbidden", error_message=str(e))
            return _err(str(e), 403, "forbidden")
        except ValueError as e:
            gateway.store.write_audit(req_id, "kb.delete.batch", "error", user_id=auth.get("sub"), resource_type="kb", error_code="not_found", error_message=str(e))
            return _err(str(e), 404, "not_found")
        except Exception as e:  # noqa: BLE001
            gateway.store.write_audit(req_id, "kb.delete.batch", "error", user_id=auth.get("sub"), resource_type="kb", error_code="internal_error", error_message=str(e))
            return _err(str(e), 500, "internal_error")

    @app.route("/portal/v1/policies/resources", methods=["GET"])
    def list_resource_policies():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        if not _is_admin(auth):
            return _err("admin required", 403, "forbidden")

        resource_type = (request.args.get("resource_type") or "agent").strip().lower()
        if resource_type not in {"agent", "kb"}:
            return _err("resource_type must be agent or kb", 400)
        dept_id = (request.args.get("owner_dept_id") or "").strip() or None
        visibility = (request.args.get("visibility") or "").strip() or None
        category = (request.args.get("category") or "").strip() or None
        page = max(1, int(request.args.get("page") or "1"))
        page_size = min(200, max(1, int(request.args.get("page_size") or "50")))
        items = gateway.store.list_policies(
            resource_type=resource_type,
            owner_dept_id=dept_id,
            visibility=visibility,
            category=category,
            page=page,
            page_size=page_size,
        )
        return _ok({"items": items, "total": len(items)})

    @app.route("/portal/v1/policies/agents", methods=["GET"])
    def list_agent_policies_compat():
        # Backward compatibility alias.
        return list_resource_policies()

    @app.route("/portal/v1/policies/resources/<resource_type>/<resource_id>", methods=["PUT", "OPTIONS"])
    def upsert_resource_policy(resource_type: str, resource_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        if not _is_admin(auth):
            return _err("admin required", 403, "forbidden")

        resource_type = (resource_type or "").strip().lower()
        if resource_type not in {"agent", "kb"}:
            return _err("resource_type must be agent or kb", 400)

        payload = request.get_json(silent=True) or {}
        owner_dept_id = (payload.get("owner_dept_id") or "").strip()
        visibility = (payload.get("visibility") or "dept").strip().lower()
        category = (payload.get("category") or "").strip()
        allow_dept_ids = payload.get("allow_dept_ids") or []
        deny_dept_ids = payload.get("deny_dept_ids") or []
        allow_roles = payload.get("allow_roles") or []
        if not owner_dept_id:
            return _err("owner_dept_id is required", 400)
        if visibility not in {"public", "dept"}:
            return _err("visibility must be public or dept", 400)

        gateway.store.upsert_policy(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_dept_id=owner_dept_id,
            visibility=visibility,
            created_by=auth.get("sub") or "system",
            allow_dept_ids=[str(x) for x in allow_dept_ids],
            deny_dept_ids=[str(x) for x in deny_dept_ids],
            allow_roles=[str(x) for x in allow_roles],
            category=category,
        )
        row = gateway.store.get_policy(resource_type, resource_id)
        return _ok(row)

    @app.route("/portal/v1/policies/agents/<agent_id>", methods=["PUT", "OPTIONS"])
    def upsert_agent_policy_compat(agent_id: str):
        # Backward compatibility alias.
        return upsert_resource_policy("agent", agent_id)

    @app.route("/portal/v1/admin/bootstrap/public-kb-a", methods=["POST", "OPTIONS"])
    def bootstrap_public_kb_a():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        if not _is_admin(auth):
            return _err("admin required", 403, "forbidden")

        req_id = _request_id()
        data = gateway.bootstrap_kbs_to_mp_public_a(operator=auth.get("sub") or "system")
        gateway.store.write_audit(
            req_id,
            "kb.bootstrap.public_a",
            "success",
            user_id=auth.get("sub"),
            dept_id=auth.get("default_dept_id"),
            resource_type="kb",
            resource_id="*",
        )
        return _ok(data)

    @app.route("/portal/v1/admin/bootstrap/agents", methods=["POST", "OPTIONS"])
    def bootstrap_agents():
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        if not _is_admin(auth):
            return _err("admin required", 403, "forbidden")

        req_id = _request_id()
        data = gateway.bootstrap_agents_to_dept_or_public(operator=auth.get("sub") or "system")
        gateway.store.write_audit(
            req_id,
            "agent.bootstrap.policies",
            "success",
            user_id=auth.get("sub"),
            dept_id=auth.get("default_dept_id"),
            resource_type="agent",
            resource_id="*",
        )
        return _ok(data)

    # ---- Recall Dashboard routes ----

    @app.route("/portal/v1/kbs/<kb_id>/share-to-depts", methods=["POST", "OPTIONS"])
    def share_kb_to_depts(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        body = request.get_json(silent=True) or {}
        dept_ids = body.get("dept_ids") or []
        permission_mask = body.get("permission_mask")
        if not isinstance(permission_mask, int):
            # Fall back to string mapping
            perm_str = str(body.get("permission") or "read").strip().lower()
            if perm_str == "write":
                permission_mask = 7
            elif perm_str == "share":
                permission_mask = 5
            else:
                permission_mask = 4
        if not dept_ids:
            return _err("dept_ids is required", 400)
        try:
            data = gateway.share_kb_to_depts(auth, kb_id, [str(d) for d in dept_ids], int(permission_mask))
            gateway.store.write_audit(req_id, "kb.share_to_depts", "success", user_id=auth.get("sub"), dept_id=auth.get("default_dept_id"), resource_type="kb", resource_id=kb_id)
            return _ok(data)
        except PermissionError as exc:
            return _err(str(exc), 403, "forbidden")
        except ValueError as exc:
            return _err(str(exc), 400)

    @app.route("/portal/v1/kbs/my-shares", methods=["GET"])
    def list_my_shares():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        try:
            data = gateway.list_all_shares(auth)
            return _ok(data)
        except PermissionError as exc:
            return _err(str(exc), 403, "forbidden")

    @app.route("/portal/v1/kbs/<kb_id>/revoke-selective", methods=["POST", "OPTIONS"])
    def revoke_selective(kb_id: str):
        if request.method == "OPTIONS":
            return ("", 204)
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")
        req_id = _request_id()
        body = request.get_json(silent=True) or {}
        revoke_type = (body.get("revoke_type") or "").strip()
        target_id = str(body.get("target_id") or "").strip()
        if not revoke_type or not target_id:
            return _err("revoke_type and target_id are required", 400)
        try:
            data = gateway.selective_revoke(auth, kb_id, revoke_type, target_id)
            gateway.store.write_audit(req_id, "kb.revoke_selective", "success", user_id=auth.get("sub"), dept_id=auth.get("default_dept_id"), resource_type="kb", resource_id=kb_id)
            return _ok(data)
        except PermissionError as exc:
            return _err(str(exc), 403, "forbidden")
        except ValueError as exc:
            return _err(str(exc), 400)

    @app.route("/portal/v1/audit/logs", methods=["GET"])
    def list_audit_logs():
        auth = _auth_payload()
        if not auth:
            return _err("unauthorized", 401, "unauthorized")

        req_dept_id = (request.args.get("dept_id") or "").strip() or None
        user_id = (request.args.get("user_id") or "").strip() or None
        action = (request.args.get("action") or "").strip() or None
        status = (request.args.get("status") or "").strip() or None
        page = max(1, int(request.args.get("page") or "1"))
        page_size = min(200, max(1, int(request.args.get("page_size") or "50")))

        # non-admin users can only query their own dept(s)
        if not _is_admin(auth):
            allowed = set(auth.get("dept_ids") or [])
            if req_dept_id and req_dept_id not in allowed:
                return _err("dept not allowed", 403, "forbidden")
            dept_id = req_dept_id or auth.get("default_dept_id")
        else:
            dept_id = req_dept_id

        items = gateway.store.list_audit_logs(
            dept_id=dept_id,
            user_id=user_id,
            action=action,
            status=status,
            page=page,
            page_size=page_size,
        )
        return _ok({"items": items, "total": len(items)})

    return app


def run_server(host: str = "0.0.0.0", port: int = 9391):
    app = create_app()
    print(f"Portal gateway listening on http://{host}:{port}")
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run_server()
