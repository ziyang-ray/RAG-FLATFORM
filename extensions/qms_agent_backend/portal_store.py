from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass
class PortalUser:
    user_id: str
    login_id: str
    display_name: str
    is_active: bool


class PortalStore:
    """Portal auth/RBAC/audit storage (SQLite, local-first)."""

    @staticmethod
    def normalize_dept_id(dept_id: str) -> str:
        raw = (dept_id or "").strip()
        mapping = {
            "dept_q": "dept_mp_q",
            "dept_plm": "dept_mp_plm",
            "dept_ap": "dept_mp_ap",
            "dept_mc": "dept_mp_mc",
            "dept_ai": "dept_mp_ap",
            "dept_md": "dept_mp_mc",
            "dept_at": "dept_mp_at",
            "dept_usdx": "dept_mp_usdx",
        }
        return mapping.get(raw, raw)

    @staticmethod
    def dept_code(dept_id: str) -> str:
        n = PortalStore.normalize_dept_id(dept_id)
        code_map = {
            "dept_mp": "MP",
            "dept_mp_q": "MP-Q",
            "dept_mp_plm": "MP-PLM",
            "dept_mp_ap": "MP-AP",
            "dept_mp_mc": "MP-MC",
            "dept_mp_usdx": "MP-US&DX",
            "dept_mp_at": "MP-AT",
        }
        return code_map.get(n, n.replace("dept_", "").upper())

    def __init__(self, db_path: str | Path):
        self.db_path = str(db_path)
        self._init_db()
        self._seed_defaults()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            # Migration: detect old schema and rebuild
            rp_cols = {r[1] for r in conn.execute("PRAGMA table_info(resource_policies)").fetchall()}
            if rp_cols and "allow_user_ids_json" in rp_cols:
                conn.execute("""
                    CREATE TABLE resource_policies_new AS
                    SELECT policy_id, resource_type, resource_id, owner_dept_id, owner_user_id,
                           visibility, allow_roles_json, allow_dept_ids_json, deny_dept_ids_json,
                           is_active, created_by, created_at, updated_at, category
                    FROM resource_policies
                """)
                conn.execute("DROP TABLE resource_policies")
                conn.execute("ALTER TABLE resource_policies_new RENAME TO resource_policies")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_policy_type_id ON resource_policies(resource_type, resource_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_policy_owner ON resource_policies(owner_dept_id, visibility)")

            ks_cols = {r[1] for r in conn.execute("PRAGMA table_info(kb_shares)").fetchall()}
            if ks_cols and "permission_mask" not in ks_cols:
                conn.execute("""
                    CREATE TABLE kb_shares_new AS
                    SELECT id, kb_id, target_user_id,
                           CASE WHEN permission='write' THEN 7 ELSE 4 END as permission_mask,
                           shared_by, NULL as approved_by, NULL as parent_share_id,
                           'approved' as status, created_at, created_at as updated_at
                    FROM kb_shares
                """)
                conn.execute("DROP TABLE kb_shares")
                conn.execute("ALTER TABLE kb_shares_new RENAME TO kb_shares")
                conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_kb_share_unique ON kb_shares(kb_id, target_user_id)")

            sr_cols = {r[1] for r in conn.execute("PRAGMA table_info(kb_share_requests)").fetchall()}
            if sr_cols and ("target_user_login" in sr_cols or "requested_permission" not in sr_cols):
                conn.execute("""
                    CREATE TABLE kb_share_requests_new AS
                    SELECT request_id, kb_id, owner_dept_id, requester_user_id, target_scope,
                           target_dept_ids_json, target_user_id, reason, status, reviewer_user_id,
                           review_comment, created_at, updated_at, 4 as requested_permission,
                           NULL as parent_share_id
                    FROM kb_share_requests
                """)
                conn.execute("DROP TABLE kb_share_requests")
                conn.execute("ALTER TABLE kb_share_requests_new RENAME TO kb_share_requests")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_share_req_kb ON kb_share_requests(kb_id)")
                conn.execute("CREATE INDEX IF NOT EXISTS idx_share_req_status ON kb_share_requests(status, updated_at)")
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    login_id TEXT NOT NULL UNIQUE,
                    password_hash TEXT NOT NULL,
                    display_name TEXT NOT NULL,
                    email TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS departments (
                    dept_id TEXT PRIMARY KEY,
                    dept_code TEXT NOT NULL UNIQUE,
                    dept_name TEXT NOT NULL UNIQUE,
                    parent_dept_id TEXT,
                    is_public INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_departments (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT NOT NULL,
                    dept_id TEXT NOT NULL,
                    role TEXT NOT NULL DEFAULT 'member',
                    is_default INTEGER NOT NULL DEFAULT 0,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(user_id, dept_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS resource_policies (
                    policy_id TEXT PRIMARY KEY,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    owner_dept_id TEXT NOT NULL,
                    owner_user_id TEXT,
                    visibility TEXT NOT NULL DEFAULT 'dept',
                    allow_roles_json TEXT NOT NULL DEFAULT '[]',
                    allow_dept_ids_json TEXT NOT NULL DEFAULT '[]',
                    deny_dept_ids_json TEXT NOT NULL DEFAULT '[]',
                    is_active INTEGER NOT NULL DEFAULT 1,
                    created_by TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    category TEXT NOT NULL DEFAULT '',
                    UNIQUE(resource_type, resource_id)
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_logs (
                    audit_id TEXT PRIMARY KEY,
                    request_id TEXT NOT NULL,
                    event_time TEXT NOT NULL,
                    user_id TEXT,
                    dept_id TEXT,
                    action TEXT NOT NULL,
                    resource_type TEXT,
                    resource_id TEXT,
                    session_id TEXT,
                    status TEXT NOT NULL,
                    error_code TEXT,
                    error_message TEXT,
                    payload_digest TEXT
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_sessions (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    dept_id TEXT NOT NULL,
                    agent_id TEXT NOT NULL,
                    kb_ids_json TEXT NOT NULL DEFAULT '[]',
                    is_private INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    is_active INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS portal_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    references_json TEXT NOT NULL DEFAULT '[]',
                    created_at TEXT NOT NULL
                )
                """
            )
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kb_share_requests (
                    request_id TEXT PRIMARY KEY,
                    kb_id TEXT NOT NULL,
                    owner_dept_id TEXT NOT NULL,
                    requester_user_id TEXT NOT NULL,
                    target_scope TEXT NOT NULL,
                    target_dept_ids_json TEXT NOT NULL DEFAULT '[]',
                    target_user_id TEXT,
                    reason TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    reviewer_user_id TEXT,
                    review_comment TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    requested_permission INTEGER NOT NULL DEFAULT 4,
                    parent_share_id INTEGER
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ud_user ON user_departments(user_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_ud_dept ON user_departments(dept_id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_policy_owner ON resource_policies(owner_dept_id, visibility)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_event ON audit_logs(event_time)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portal_sessions_user ON portal_sessions(user_id, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_portal_messages_session ON portal_messages(session_id, id)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_share_req_status ON kb_share_requests(status, updated_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_share_req_kb ON kb_share_requests(kb_id, status)")

            # Backward-compatible migration for old DB files.
            cols = conn.execute("PRAGMA table_info(resource_policies)").fetchall()
            col_names = {r[1] for r in cols}
            if "category" not in col_names:
                conn.execute("ALTER TABLE resource_policies ADD COLUMN category TEXT NOT NULL DEFAULT ''")
            if "owner_user_id" not in col_names:
                conn.execute("ALTER TABLE resource_policies ADD COLUMN owner_user_id TEXT")

            sess_cols = conn.execute("PRAGMA table_info(portal_sessions)").fetchall()
            sess_col_names = {r[1] for r in sess_cols}
            if "kb_ids_json" not in sess_col_names:
                conn.execute("ALTER TABLE portal_sessions ADD COLUMN kb_ids_json TEXT NOT NULL DEFAULT '[]'")
            if "is_private" not in sess_col_names:
                conn.execute("ALTER TABLE portal_sessions ADD COLUMN is_private INTEGER NOT NULL DEFAULT 0")
            if "kb_overridden" not in sess_col_names:
                conn.execute("ALTER TABLE portal_sessions ADD COLUMN kb_overridden INTEGER NOT NULL DEFAULT 0")

            share_cols = conn.execute("PRAGMA table_info(kb_share_requests)").fetchall()
            share_col_names = {r[1] for r in share_cols}
            if "target_user_id" not in share_col_names:
                conn.execute("ALTER TABLE kb_share_requests ADD COLUMN target_user_id TEXT")

            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS kb_shares (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kb_id TEXT NOT NULL,
                    target_user_id TEXT NOT NULL,
                    permission_mask INTEGER NOT NULL DEFAULT 4,
                    shared_by TEXT NOT NULL,
                    approved_by TEXT,
                    parent_share_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'approved',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(kb_id, target_user_id)
                )
                """)

            user_cols = conn.execute("PRAGMA table_info(users)").fetchall()
            user_col_names = {r[1] for r in user_cols}
            if "ragflow_user_id" not in user_col_names:
                conn.execute("ALTER TABLE users ADD COLUMN ragflow_user_id TEXT")
            if "ragflow_token" not in user_col_names:
                conn.execute("ALTER TABLE users ADD COLUMN ragflow_token TEXT")

    @staticmethod
    def _utc_now() -> str:
        return datetime.now(timezone.utc).isoformat()

    @staticmethod
    def _new_id(prefix: str = "") -> str:
        import uuid

        return f"{prefix}{uuid.uuid4().hex}"

    @staticmethod
    def make_password_hash(password: str, salt: str | None = None) -> str:
        real_salt = salt or os.urandom(16).hex()
        digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), real_salt.encode("utf-8"), 120_000).hex()
        return f"pbkdf2_sha256${real_salt}${digest}"

    @staticmethod
    def verify_password(password: str, password_hash: str) -> bool:
        try:
            algo, salt, digest = password_hash.split("$", 2)
            if algo != "pbkdf2_sha256":
                return False
            calc = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt.encode("utf-8"), 120_000).hex()
            return calc == digest
        except Exception:
            return False

    def _seed_defaults(self) -> None:
        now = self._utc_now()
        depts = [
            ("dept_mp", "MP", "MP总部", None, 1),
            ("dept_mp_q", "MP-Q", "MP质量部门", "dept_mp", 0),
            ("dept_mp_plm", "MP-PLM", "MP产品生命周期部门", "dept_mp", 0),
            ("dept_mp_ap", "MP-AP", "MP AP部门", "dept_mp", 0),
            ("dept_mp_mc", "MP-MC", "MP MC部门", "dept_mp", 0),
            ("dept_mp_usdx", "MP-US&DX", "MP US&DX部门", "dept_mp", 0),
        ]
        with self._connect() as conn:
            for dept_id, code, name, parent, is_public in depts:
                conn.execute(
                    """
                    INSERT INTO departments(dept_id, dept_code, dept_name, parent_dept_id, is_public, is_active, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                    ON CONFLICT(dept_id) DO NOTHING
                    """,
                    (dept_id, code, name, parent, is_public, now, now),
                )

            default_user = conn.execute("SELECT user_id FROM users WHERE login_id = ?", ("mp@example.com",)).fetchone()
            if not default_user:
                user_id = "u_mp_default"
                conn.execute(
                    """
                    INSERT INTO users(user_id, login_id, password_hash, display_name, email, is_active, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                    """,
                    (
                        user_id,
                        "mp@example.com",
                        self.make_password_hash("12345678"),
                        "MP管理员",
                        "mp@example.com",
                        now,
                        now,
                    ),
                )
                conn.execute(
                    """
                    INSERT INTO user_departments(user_id, dept_id, role, is_default, is_active, created_at, updated_at)
                    VALUES(?, ?, 'admin', 1, 1, ?, ?)
                    ON CONFLICT(user_id, dept_id) DO NOTHING
                    """,
                    (user_id, "dept_mp", now, now),
                )

            # Ensure MP default user can manage all sub-depts.
            mp_row = conn.execute("SELECT user_id FROM users WHERE login_id = ?", ("mp@example.com",)).fetchone()
            mp_user_id = mp_row["user_id"] if mp_row else "u_mp_default"
            for dept_id in ["dept_mp_q", "dept_mp_plm", "dept_mp_ap", "dept_mp_mc", "dept_mp_usdx"]:
                conn.execute(
                    """
                    INSERT INTO user_departments(user_id, dept_id, role, is_default, is_active, created_at, updated_at)
                    VALUES(?, ?, 'admin', 0, 1, ?, ?)
                    ON CONFLICT(user_id, dept_id) DO NOTHING
                    """,
                    (mp_user_id, dept_id, now, now),
                )

            # Seed demo staff users.
            dept_demo_users = [
                ("u_mp_q_staff", "mp-q-staff@example.com", "MP-Q-staff01", "dept_mp_q"),
                ("u_mp_plm_staff", "mp-plm-staff@example.com", "MP-PLM-staff01", "dept_mp_plm"),
                ("u_mp_ap_staff", "mp-ap-staff@example.com", "MP-AP-staff01", "dept_mp_ap"),
                ("u_mp_mc_staff", "mp-mc-staff@example.com", "MP-MC-staff01", "dept_mp_mc"),
                ("u_mp_usdx_staff", "mp-usdx-staff@example.com", "MP-USDX-staff01", "dept_mp_usdx"),
            ]
            for uid, login_id, display_name, dept_id in dept_demo_users:
                row = conn.execute("SELECT user_id FROM users WHERE login_id = ?", (login_id,)).fetchone()
                if not row:
                    conn.execute(
                        """
                        INSERT INTO users(user_id, login_id, password_hash, display_name, email, is_active, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            uid,
                            login_id,
                            self.make_password_hash("12345678"),
                            display_name,
                            f"{login_id.lower().replace('&', '')}" if "@" in login_id else f"{login_id.lower().replace('&', '')}@example.com",
                            now,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE users SET display_name = ?, updated_at = ?
                        WHERE login_id = ?
                        """,
                        (display_name, now, login_id),
                    )
                conn.execute(
                    """
                    INSERT INTO user_departments(user_id, dept_id, role, is_default, is_active, created_at, updated_at)
                    VALUES(?, ?, 'member', 1, 1, ?, ?)
                    ON CONFLICT(user_id, dept_id) DO NOTHING
                    """,
                    (uid, dept_id, now, now),
                )

            # Seed demo admin users.
            dept_admin_users = [
                ("u_mp_q_admin", "mp-q-admin@example.com", "MP-Q-admin", "dept_mp_q"),
                ("u_mp_plm_admin", "mp-plm-admin@example.com", "MP-PLM-admin", "dept_mp_plm"),
                ("u_mp_ap_admin", "mp-ap-admin@example.com", "MP-AP-admin", "dept_mp_ap"),
                ("u_mp_mc_admin", "mp-mc-admin@example.com", "MP-MC-admin", "dept_mp_mc"),
                ("u_mp_usdx_admin", "mp-usdx-admin@example.com", "MP-USDX-admin", "dept_mp_usdx"),
            ]
            for uid, login_id, display_name, dept_id in dept_admin_users:
                row = conn.execute("SELECT user_id FROM users WHERE login_id = ?", (login_id,)).fetchone()
                if not row:
                    conn.execute(
                        """
                        INSERT INTO users(user_id, login_id, password_hash, display_name, email, is_active, created_at, updated_at)
                        VALUES(?, ?, ?, ?, ?, 1, ?, ?)
                        """,
                        (
                            uid,
                            login_id,
                            self.make_password_hash("12345678"),
                            display_name,
                            f"{login_id.lower().replace('&', '')}" if "@" in login_id else f"{login_id.lower().replace('&', '')}@example.com",
                            now,
                            now,
                        ),
                    )
                else:
                    conn.execute(
                        """
                        UPDATE users SET display_name = ?, updated_at = ?
                        WHERE login_id = ?
                        """,
                        (display_name, now, login_id),
                    )
                conn.execute(
                    """
                    INSERT INTO user_departments(user_id, dept_id, role, is_default, is_active, created_at, updated_at)
                    VALUES(?, ?, 'admin', 1, 1, ?, ?)
                    ON CONFLICT(user_id, dept_id) DO NOTHING
                    """,
                    (uid, dept_id, now, now),
                )

    def authenticate_user(self, login_id: str, password: str) -> PortalUser | None:
        with self._connect() as conn:
            # Try login_id first, then fall back to email
            row = conn.execute(
                """
                SELECT user_id, login_id, password_hash, display_name, is_active
                FROM users WHERE UPPER(login_id) = UPPER(?)
                """,
                (login_id,),
            ).fetchone()
            if not row:
                row = conn.execute(
                    """
                    SELECT user_id, login_id, password_hash, display_name, is_active
                    FROM users WHERE UPPER(email) = UPPER(?)
                    """,
                    (login_id,),
                ).fetchone()
        if not row:
            return None
        if int(row["is_active"]) != 1:
            return None
        if not self.verify_password(password, row["password_hash"]):
            return None
        return PortalUser(
            user_id=row["user_id"],
            login_id=row["login_id"],
            display_name=row["display_name"],
            is_active=True,
        )

    def register_user(
        self,
        login_id: str,
        password: str,
        display_name: str,
        dept_id: str,
        role: str = "member",
        ragflow_user_id: str = "",
        ragflow_token: str = "",
    ) -> dict[str, Any]:
        """Create a portal user and link to a department. Returns user dict or raises ValueError."""
        now = self._utc_now()
        with self._connect() as conn:
            existing = conn.execute(
                "SELECT user_id FROM users WHERE UPPER(login_id) = UPPER(?)", (login_id,)
            ).fetchone()
            if existing:
                raise ValueError(f"用户名 {login_id} 已存在")

            user_id = self._new_id("u_")
            conn.execute(
                """
                INSERT INTO users(user_id, login_id, password_hash, display_name, email, is_active,
                    ragflow_user_id, ragflow_token, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, 1, ?, ?, ?, ?)
                """,
                (
                    user_id,
                    login_id,
                    self.make_password_hash(password),
                    display_name,
                    f"{login_id.lower()}@portal.local",
                    ragflow_user_id,
                    ragflow_token,
                    now,
                    now,
                ),
            )

            dept_row = conn.execute("SELECT dept_id FROM departments WHERE dept_id = ?", (dept_id,)).fetchone()
            if not dept_row:
                raise ValueError(f"部门 {dept_id} 不存在")

            conn.execute(
                """
                INSERT INTO user_departments(user_id, dept_id, role, is_default, is_active, created_at, updated_at)
                VALUES(?, ?, ?, 1, 1, ?, ?)
                ON CONFLICT(user_id, dept_id) DO NOTHING
                """,
                (user_id, dept_id, role, now, now),
            )

        return {
            "user_id": user_id,
            "login_id": login_id,
            "display_name": display_name,
            "dept_id": dept_id,
            "role": role,
            "ragflow_user_id": ragflow_user_id,
        }

    def get_user_ragflow_token(self, user_id: str) -> str:
        """Get the RAGFlow API token for a portal user."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ragflow_token FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row and row["ragflow_token"]:
            return row["ragflow_token"]
        return ""

    def get_all_users_with_ragflow_token(self) -> list[tuple[str, str, str]]:
        """Returns [(user_id, ragflow_token, default_dept_id), ...] for all users with RAGFlow tokens.
        Admin users (MP, ADMIN) are returned first so they claim resource ownership before regular users.
        """
        with self._connect() as conn:
            rows = conn.execute("""
                SELECT u.user_id, u.ragflow_token,
                    COALESCE((SELECT ud.dept_id FROM user_departments ud
                              WHERE ud.user_id = u.user_id AND ud.is_default = 1
                              LIMIT 1), 'dept_mp') as default_dept_id
                FROM users u
                WHERE u.ragflow_token IS NOT NULL AND u.ragflow_token != '' AND u.is_active = 1
                ORDER BY CASE WHEN UPPER(u.login_id) LIKE '%ADMIN%' OR UPPER(u.login_id) = 'MP' THEN 0 ELSE 1 END,
                         u.created_at ASC
            """).fetchall()
        return [(r["user_id"], r["ragflow_token"], r["default_dept_id"]) for r in rows]

    def get_all_users_without_ragflow(self) -> list[tuple[str, str]]:
        """Returns [(user_id, login_id)] for active users without RAGFlow accounts."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT user_id, login_id FROM users
                WHERE is_active = 1
                  AND (ragflow_user_id IS NULL OR ragflow_user_id = '')
                """
            ).fetchall()
        return [(r["user_id"], r["login_id"]) for r in rows]

    def update_user_ragflow(self, user_id: str, ragflow_user_id: str, ragflow_token: str) -> None:
        """Update RAGFlow linkage for a user."""
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                "UPDATE users SET ragflow_user_id = ?, ragflow_token = ?, updated_at = ? WHERE user_id = ?",
                (ragflow_user_id, ragflow_token, now, user_id),
            )

    def get_user_ragflow_user_id(self, user_id: str) -> str:
        """Get the RAGFlow user_id for a portal user."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT ragflow_user_id FROM users WHERE user_id = ?", (user_id,)
            ).fetchone()
        if row and row["ragflow_user_id"]:
            return row["ragflow_user_id"]
        return ""

    def delete_user(self, user_id: str) -> bool:
        """Delete a portal user and all related data."""
        with self._connect() as conn:
            row = conn.execute("SELECT user_id, login_id FROM users WHERE user_id = ?", (user_id,)).fetchone()
            if not row:
                return False
            conn.execute("DELETE FROM user_departments WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM resource_policies WHERE owner_user_id = ?", (user_id,))
            conn.execute("DELETE FROM portal_sessions WHERE user_id = ?", (user_id,))
            conn.execute("DELETE FROM users WHERE user_id = ?", (user_id,))
        return True

    def get_user_by_login(self, login_id: str) -> dict[str, Any] | None:
        if not login_id:
            return None
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT user_id, login_id, display_name, is_active
                FROM users WHERE UPPER(login_id) = UPPER(?)
                """,
                (login_id,),
            ).fetchone()
        return dict(row) if row else None

    def search_users(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        """Fuzzy search users by login_id or display_name."""
        q = f"%{query}%"
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.user_id, u.login_id, u.display_name,
                    COALESCE((SELECT d.dept_code FROM user_departments ud
                              JOIN departments d ON ud.dept_id = d.dept_id
                              WHERE ud.user_id = u.user_id AND ud.is_default = 1
                              LIMIT 1), '') as dept_code
                FROM users u
                WHERE u.is_active = 1
                  AND (UPPER(u.login_id) LIKE UPPER(?) OR UPPER(u.display_name) LIKE UPPER(?))
                ORDER BY u.login_id
                LIMIT ?
                """,
                (q, q, limit),
            ).fetchall()
        return [{"user_id": r["user_id"], "login_id": r["login_id"], "display_name": r["display_name"], "dept_code": r["dept_code"]} for r in rows]

    # 权限位掩码常量
    PERM_READ = 4
    PERM_WRITE = 2
    PERM_SHARE = 1
    PERM_FULL = 7  # read + write + share

    @staticmethod
    def has_read(mask: int) -> bool:
        return bool(mask & PortalStore.PERM_READ)

    @staticmethod
    def has_write(mask: int) -> bool:
        return bool(mask & PortalStore.PERM_WRITE)

    @staticmethod
    def has_share(mask: int) -> bool:
        return bool(mask & PortalStore.PERM_SHARE)

    @staticmethod
    def perm_label(mask: int) -> str:
        parts = []
        if mask & PortalStore.PERM_READ:
            parts.append("可读")
        if mask & PortalStore.PERM_WRITE:
            parts.append("可改写")
        if mask & PortalStore.PERM_SHARE:
            parts.append("可分享")
        return "、".join(parts) if parts else "无权限"

    def add_kb_share(self, kb_id: str, target_user_id: str, permission_mask: int, shared_by: str,
                     parent_share_id: int | None = None, approved_by: str = "", status: str = "approved") -> None:
        """Add or update a KB share record with bitmask permission."""
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kb_shares(kb_id, target_user_id, permission_mask, shared_by, approved_by,
                    parent_share_id, status, created_at, updated_at)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(kb_id, target_user_id) DO UPDATE SET
                    permission_mask = ?, shared_by = ?, approved_by = ?,
                    parent_share_id = ?, status = ?, updated_at = ?
                """,
                (kb_id, target_user_id, permission_mask, shared_by, approved_by,
                 parent_share_id, status, now, now,
                 permission_mask, shared_by, approved_by, parent_share_id, status, now),
            )

    def remove_kb_share(self, kb_id: str, target_user_id: str) -> bool:
        """Revoke a KB share record."""
        now = self._utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE kb_shares SET status = 'revoked', updated_at = ? WHERE kb_id = ? AND target_user_id = ?",
                (now, kb_id, target_user_id),
            )
        return cur.rowcount > 0

    def get_kb_permission(self, kb_id: str, user_id: str) -> int:
        """Get permission bitmask for a user on a KB. Returns 0 if no access."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT permission_mask FROM kb_shares WHERE kb_id = ? AND target_user_id = ? AND status = 'approved'",
                (kb_id, user_id),
            ).fetchone()
        return row["permission_mask"] if row else 0

    def has_kb_access(self, kb_id: str, user_id: str) -> int:
        """Returns permission_mask if user has access to KB, 0 otherwise.
        Checks both owner status and kb_shares."""
        with self._connect() as conn:
            # Check if user is the owner
            policy = conn.execute(
                "SELECT owner_user_id FROM resource_policies WHERE resource_id = ? AND resource_type = 'kb'",
                (kb_id,),
            ).fetchone()
            if policy and policy["owner_user_id"] == user_id:
                return self.PERM_FULL  # Owner has full access
        return self.get_kb_permission(kb_id, user_id)

    def list_kb_shares(self, kb_id: str) -> list[dict[str, Any]]:
        """List all approved shares for a KB."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ks.id, ks.target_user_id, ks.permission_mask, ks.shared_by,
                    ks.approved_by, ks.parent_share_id, ks.status, ks.created_at,
                    u.login_id as target_login, u.display_name as target_name,
                    u2.login_id as sharer_login
                FROM kb_shares ks
                JOIN users u ON ks.target_user_id = u.user_id
                LEFT JOIN users u2 ON ks.shared_by = u2.user_id
                WHERE ks.kb_id = ? AND ks.status = 'approved'
                ORDER BY ks.created_at
                """,
                (kb_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_share_chain(self, kb_id: str, target_user_id: str) -> list[dict[str, Any]]:
        """Get the sharing chain for a user's access to a KB."""
        with self._connect() as conn:
            # Get the user's share record first
            share = conn.execute(
                "SELECT id, shared_by, parent_share_id, permission_mask FROM kb_shares WHERE kb_id = ? AND target_user_id = ? AND status = 'approved'",
                (kb_id, target_user_id),
            ).fetchone()
            if not share:
                return []
            chain = [dict(share)]
            parent_id = share["parent_share_id"]
            while parent_id:
                parent = conn.execute(
                    "SELECT id, shared_by, parent_share_id, permission_mask FROM kb_shares WHERE id = ?",
                    (parent_id,),
                ).fetchone()
                if parent:
                    chain.append(dict(parent))
                    parent_id = parent["parent_share_id"]
                else:
                    break
        return chain

    def get_users_in_dept(self, dept_id: str) -> list[dict[str, Any]]:
        """Get all active users in a department."""
        dept_id = self.normalize_dept_id(dept_id)
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT u.user_id, u.login_id, u.display_name
                FROM users u
                JOIN user_departments ud ON u.user_id = ud.user_id
                WHERE ud.dept_id = ? AND ud.is_active = 1 AND u.is_active = 1
                ORDER BY u.login_id
                """,
                (dept_id,),
            ).fetchall()
        return [{"user_id": r["user_id"], "login_id": r["login_id"], "display_name": r["display_name"]} for r in rows]

    def get_user_departments(self, user_id: str) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT d.dept_id, d.dept_code, d.dept_name, ud.role, ud.is_default, d.is_public
                FROM user_departments ud
                JOIN departments d ON d.dept_id = ud.dept_id
                WHERE ud.user_id = ? AND ud.is_active = 1 AND d.is_active = 1
                ORDER BY ud.is_default DESC, d.dept_code ASC
                """,
                (user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def get_all_departments(self) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT dept_id, dept_code, dept_name, parent_dept_id, is_public
                FROM departments
                WHERE is_active = 1
                ORDER BY dept_code ASC
                """
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_policy(
        self,
        resource_type: str,
        resource_id: str,
        owner_dept_id: str,
        visibility: str,
        created_by: str = "system",
        owner_user_id: str | None = None,
        allow_dept_ids: list[str] | None = None,
        deny_dept_ids: list[str] | None = None,
        allow_roles: list[str] | None = None,
        category: str = "",
    ) -> None:
        now = self._utc_now()
        allow_dept_ids_json = json.dumps(allow_dept_ids or [], ensure_ascii=False)
        deny_dept_ids_json = json.dumps(deny_dept_ids or [], ensure_ascii=False)
        allow_roles_json = json.dumps(allow_roles or [], ensure_ascii=False)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT policy_id FROM resource_policies WHERE resource_type = ? AND resource_id = ?",
                (resource_type, resource_id),
            ).fetchone()
            if row:
                conn.execute(
                    """
                    UPDATE resource_policies
                    SET owner_dept_id = ?, owner_user_id = ?, visibility = ?,
                        allow_roles_json = ?, allow_dept_ids_json = ?, deny_dept_ids_json = ?,
                        category = ?,
                        updated_at = ?, is_active = 1
                    WHERE policy_id = ?
                    """,
                    (
                        owner_dept_id,
                        owner_user_id,
                        visibility,
                        allow_roles_json,
                        allow_dept_ids_json,
                        deny_dept_ids_json,
                        category,
                        now,
                        row["policy_id"],
                    ),
                )
            else:
                conn.execute(
                    """
                    INSERT INTO resource_policies
                    (policy_id, resource_type, resource_id, owner_dept_id, owner_user_id, visibility,
                     allow_roles_json, allow_dept_ids_json, deny_dept_ids_json, category,
                     is_active, created_by, created_at, updated_at)
                    VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        self._new_id("pol_"),
                        resource_type, resource_id,
                        owner_dept_id, owner_user_id, visibility,
                        allow_roles_json, allow_dept_ids_json, deny_dept_ids_json,
                        category, created_by, now, now,
                    ),
                )

    def list_policies(
        self,
        resource_type: str | None = None,
        owner_dept_id: str | None = None,
        visibility: str | None = None,
        category: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        where = ["is_active = 1"]
        params: list[Any] = []
        if resource_type:
            where.append("resource_type = ?")
            params.append(resource_type)
        if owner_dept_id:
            where.append("owner_dept_id = ?")
            params.append(owner_dept_id)
        if visibility:
            where.append("visibility = ?")
            params.append(visibility)
        if category is not None and category != "":
            where.append("category = ?")
            params.append(category)

        sql = f"""
            SELECT *
            FROM resource_policies
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([page_size, max(0, (page - 1) * page_size)])

        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def deactivate_policy(self, resource_type: str, resource_id: str) -> None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE resource_policies
                SET is_active = 0, updated_at = ?
                WHERE resource_type = ? AND resource_id = ?
                """,
                (now, resource_type, resource_id),
            )

    def cleanup_stale_kb_policies(self, valid_ids: set[str]) -> int:
        """Remove policies for KBs that no longer exist in RAGFlow."""
        now = self._utc_now()
        with self._connect() as conn:
            all_kb_policies = conn.execute(
                "SELECT resource_id FROM resource_policies WHERE resource_type = 'kb' AND is_active = 1"
            ).fetchall()
            stale = [r[0] for r in all_kb_policies if r[0] not in valid_ids]
            if stale:
                conn.executemany(
                    "UPDATE resource_policies SET is_active = 0, updated_at = ? WHERE resource_type = 'kb' AND resource_id = ?",
                    [(now, sid) for sid in stale],
                )
                conn.executemany(
                    "DELETE FROM kb_shares WHERE kb_id = ?",
                    [(sid,) for sid in stale],
                )
                print(f"[PORTAL] Cleaned {len(stale)} stale KB policies")
            return len(stale)

    def get_policy(self, resource_type: str, resource_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM resource_policies
                WHERE resource_type = ? AND resource_id = ? AND is_active = 1
                """,
                (resource_type, resource_id),
            ).fetchone()
        return dict(row) if row else None

    def can_access_resource(
        self,
        user_dept_ids: set[str],
        selected_dept_id: str,
        policy: dict[str, Any] | None,
        user_id: str | None = None,
    ) -> bool:
        """Check dept-level/visibility access. User-level access is checked separately via has_kb_access()."""
        if not policy:
            return False
        normalized_user_depts = {self.normalize_dept_id(x) for x in (user_dept_ids or set())}
        selected = self.normalize_dept_id(selected_dept_id)
        if selected not in normalized_user_depts:
            return False
        visibility = (policy.get("visibility") or "dept").lower()
        owner_user_id = policy.get("owner_user_id")
        owner_dept = self.normalize_dept_id(policy.get("owner_dept_id") or "")
        allow_depts = {self.normalize_dept_id(x) for x in json.loads(policy.get("allow_dept_ids_json") or "[]")}
        deny_depts = {self.normalize_dept_id(x) for x in json.loads(policy.get("deny_dept_ids_json") or "[]")}

        if visibility == "private":
            return bool(user_id) and owner_user_id == user_id
        if selected in deny_depts:
            return False
        if selected in allow_depts:
            return True

        if visibility == "public":
            return True
        return owner_dept == selected

    def effective_kb_permission(
        self,
        user_dept_ids: set[str],
        selected_dept_id: str,
        kb_id: str,
        user_id: str,
        policy: dict[str, Any] | None = None,
    ) -> int:
        """Compute effective permission bitmask for a user on a KB.

        Rules (Portal-side):
        - Owner: full (read/write/share)
        - Dept/policy visibility grants READ only
        - kb_shares grants the stored bitmask (may include write/share)

        Notes:
        - This is a Portal authorization model; RAGFlow tenant access is handled separately.
        """
        kb_id = (kb_id or "").strip()
        user_id = (user_id or "").strip()
        if not kb_id or not user_id:
            return 0

        p = policy or self.get_policy("kb", kb_id)
        owner_user_id = (p or {}).get("owner_user_id") or ""
        if owner_user_id and owner_user_id == user_id:
            return int(self.PERM_FULL)

        mask = 0
        if p and self.can_access_resource(user_dept_ids, selected_dept_id, p, user_id=user_id):
            mask |= int(self.PERM_READ)

        # user-level shares
        mask |= int(self.get_kb_permission(kb_id, user_id) or 0)

        # Defensive: ensure READ if any other bit exists
        if mask and (mask & int(self.PERM_READ)) == 0:
            mask |= int(self.PERM_READ)
        return int(mask)

    def can_read_kb(self, user_dept_ids: set[str], selected_dept_id: str, kb_id: str, user_id: str) -> bool:
        return self.has_read(self.effective_kb_permission(user_dept_ids, selected_dept_id, kb_id, user_id))

    def can_write_kb(self, user_dept_ids: set[str], selected_dept_id: str, kb_id: str, user_id: str) -> bool:
        return self.has_write(self.effective_kb_permission(user_dept_ids, selected_dept_id, kb_id, user_id))

    def can_share_kb(self, user_dept_ids: set[str], selected_dept_id: str, kb_id: str, user_id: str) -> bool:
        return self.has_share(self.effective_kb_permission(user_dept_ids, selected_dept_id, kb_id, user_id))

    def cascade_cleanup_kb(self, kb_id: str) -> dict[str, Any]:
        """Cascade-clean Portal-side records related to a KB.

        Intended to be called after the KB is deleted in RAGFlow, to avoid dangling
        shares/requests/sessions/messages.
        """
        kb_id = (kb_id or "").strip()
        if not kb_id:
            return {"kb_id": kb_id, "deleted_kb_shares": 0, "deleted_share_requests": 0, "deactivated_sessions": 0, "deleted_messages": 0, "session_ids": []}

        now = self._utc_now()
        affected_session_ids: list[str] = []
        deleted_messages = 0
        with self._connect() as conn:
            # Remove KB shares and share-requests outright (KB no longer exists)
            cur1 = conn.execute("DELETE FROM kb_shares WHERE kb_id = ?", (kb_id,))
            cur2 = conn.execute("DELETE FROM kb_share_requests WHERE kb_id = ?", (kb_id,))

            # Deactivate portal sessions that referenced this KB and delete their messages
            rows = conn.execute(
                "SELECT session_id, kb_ids_json FROM portal_sessions WHERE is_active = 1 AND kb_ids_json IS NOT NULL AND kb_ids_json != '[]'"
            ).fetchall()
            for r in rows:
                sid = r["session_id"]
                try:
                    kb_ids = json.loads(r["kb_ids_json"] or "[]")
                except Exception:
                    kb_ids = []
                if kb_id in set(str(x) for x in (kb_ids or [])):
                    affected_session_ids.append(sid)

            for sid in affected_session_ids:
                curm = conn.execute("DELETE FROM portal_messages WHERE session_id = ?", (sid,))
                deleted_messages += int(curm.rowcount or 0)
                conn.execute(
                    "UPDATE portal_sessions SET is_active = 0, updated_at = ? WHERE session_id = ?",
                    (now, sid),
                )

        return {
            "kb_id": kb_id,
            "deleted_kb_shares": int(cur1.rowcount or 0),
            "deleted_share_requests": int(cur2.rowcount or 0),
            "deactivated_sessions": len(affected_session_ids),
            "deleted_messages": int(deleted_messages),
            "session_ids": affected_session_ids,
        }

    def write_audit(
        self,
        request_id: str,
        action: str,
        status: str,
        user_id: str | None = None,
        dept_id: str | None = None,
        resource_type: str | None = None,
        resource_id: str | None = None,
        session_id: str | None = None,
        error_code: str | None = None,
        error_message: str | None = None,
        payload_digest: str | None = None,
    ) -> None:
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO audit_logs(
                    audit_id, request_id, event_time, user_id, dept_id, action,
                    resource_type, resource_id, session_id, status, error_code,
                    error_message, payload_digest
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    self._new_id("aud_"),
                    request_id,
                    self._utc_now(),
                    user_id,
                    dept_id,
                    action,
                    resource_type,
                    resource_id,
                    session_id,
                    status,
                    error_code,
                    error_message,
                    payload_digest,
                ),
            )

    def list_audit_logs(
        self,
        dept_id: str | None = None,
        user_id: str | None = None,
        action: str | None = None,
        status: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if dept_id:
            where.append("dept_id = ?")
            params.append(dept_id)
        if user_id:
            where.append("user_id = ?")
            params.append(user_id)
        if action:
            where.append("action = ?")
            params.append(action)
        if status:
            where.append("status = ?")
            params.append(status)

        sql = f"""
            SELECT *
            FROM audit_logs
            WHERE {' AND '.join(where)}
            ORDER BY event_time DESC
            LIMIT ? OFFSET ?
        """
        params.extend([page_size, max(0, (page - 1) * page_size)])

        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
        return [dict(r) for r in rows]

    def create_portal_session(
        self,
        session_id: str,
        user_id: str,
        dept_id: str,
        agent_id: str,
        kb_ids: list[str] | None = None,
        is_private: bool = False,
        kb_overridden: bool = False,
    ) -> None:
        now = self._utc_now()
        kb_ids_json = json.dumps(kb_ids or [], ensure_ascii=False)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portal_sessions(session_id, user_id, dept_id, agent_id, kb_ids_json, is_private, kb_overridden, created_at, updated_at, is_active)
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, 1)
                ON CONFLICT(session_id)
                DO UPDATE SET
                    user_id = excluded.user_id,
                    dept_id = excluded.dept_id,
                    agent_id = excluded.agent_id,
                    kb_ids_json = excluded.kb_ids_json,
                    is_private = excluded.is_private,
                    kb_overridden = excluded.kb_overridden,
                    updated_at = excluded.updated_at,
                    is_active = 1
                """,
                (session_id, user_id, dept_id, agent_id, kb_ids_json, int(bool(is_private)), int(bool(kb_overridden)), now, now),
            )

    def get_portal_session(self, session_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT * FROM portal_sessions
                WHERE session_id = ? AND is_active = 1
                """,
                (session_id,),
            ).fetchone()
        if not row:
            return None
        data = dict(row)
        try:
            data["kb_ids"] = json.loads(data.get("kb_ids_json") or "[]")
        except Exception:
            data["kb_ids"] = []
        data["kb_overridden"] = bool(data.get("kb_overridden", 0))
        return data

    def append_portal_message(self, session_id: str, role: str, content: str, references: list[dict] | None = None) -> None:
        now = self._utc_now()
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO portal_messages(session_id, role, content, references_json, created_at)
                VALUES(?, ?, ?, ?, ?)
                """,
                (session_id, role, content, json.dumps(references or [], ensure_ascii=False), now),
            )
            conn.execute(
                """
                UPDATE portal_sessions
                SET updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )

    def list_portal_messages(self, session_id: str, limit: int = 200) -> list[dict[str, Any]]:
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT role, content, references_json, created_at
                FROM portal_messages
                WHERE session_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (session_id, limit),
            ).fetchall()
        return [
            {
                "role": r["role"],
                "content": r["content"],
                "references": json.loads(r["references_json"] or "[]"),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    def is_admin(self, user_id: str) -> bool:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM user_departments
                WHERE user_id = ?
                  AND is_active = 1
                  AND role IN ('admin', 'owner')
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()
        return bool(row)

    def list_user_sessions(self, user_id: str, dept_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        where = ["user_id = ?", "is_active = 1"]
        params: list[Any] = [user_id]
        if dept_id:
            where.append("dept_id = ?")
            params.append(dept_id)

        sql = f"""
            SELECT session_id, user_id, dept_id, agent_id, kb_ids_json, is_private, created_at, updated_at
            FROM portal_sessions
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(max(1, limit))

        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        result: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["kb_ids"] = json.loads(item.get("kb_ids_json") or "[]")
            except Exception:
                item["kb_ids"] = []
            result.append(item)
        return result

    def list_dept_sessions(
        self,
        dept_id: str,
        exclude_user_id: str | None = None,
        include_private: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        where = ["dept_id = ?", "is_active = 1"]
        params: list[Any] = [self.normalize_dept_id(dept_id)]
        if exclude_user_id:
            where.append("user_id <> ?")
            params.append(exclude_user_id)
        if not include_private:
            where.append("is_private = 0")

        sql = f"""
            SELECT session_id, user_id, dept_id, agent_id, kb_ids_json, is_private, created_at, updated_at
            FROM portal_sessions
            WHERE {' AND '.join(where)}
            ORDER BY updated_at DESC
            LIMIT ?
        """
        params.append(limit)

        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        result: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["kb_ids"] = json.loads(item.get("kb_ids_json") or "[]")
            except Exception:
                item["kb_ids"] = []
            result.append(item)
        return result

    def is_dept_admin(self, user_id: str, dept_id: str) -> bool:
        dept_id = self.normalize_dept_id(dept_id)
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT 1
                FROM user_departments
                WHERE user_id = ?
                  AND dept_id = ?
                  AND is_active = 1
                  AND role IN ('admin', 'dept_admin', 'owner')
                LIMIT 1
                """,
                (user_id, dept_id),
            ).fetchone()
        return bool(row)

    def deactivate_portal_session(self, session_id: str, user_id: str) -> bool:
        now = self._utc_now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id
                FROM portal_sessions
                WHERE session_id = ? AND user_id = ? AND is_active = 1
                """,
                (session_id, user_id),
            ).fetchone()
            if not row:
                return False

            conn.execute(
                """
                UPDATE portal_sessions
                SET is_active = 0, updated_at = ?
                WHERE session_id = ?
                """,
                (now, session_id),
            )
        return True

    def update_portal_session_privacy(self, session_id: str, user_id: str, is_private: bool) -> bool:
        now = self._utc_now()
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT session_id
                FROM portal_sessions
                WHERE session_id = ? AND user_id = ? AND is_active = 1
                """,
                (session_id, user_id),
            ).fetchone()
            if not row:
                return False
            conn.execute(
                """
                UPDATE portal_sessions
                SET is_private = ?, updated_at = ?
                WHERE session_id = ?
                """,
                (int(bool(is_private)), now, session_id),
            )
        return True

    def create_kb_share_request(
        self,
        kb_id: str,
        owner_dept_id: str,
        requester_user_id: str,
        target_scope: str,
        target_dept_ids: list[str] | None = None,
        target_user_id: str | None = None,
        reason: str = "",
        requested_permission: int | None = None,
        parent_share_id: int | None = None,
    ) -> dict[str, Any]:
        now = self._utc_now()
        req_id = self._new_id("shr_")
        target_ids = [self.normalize_dept_id(x) for x in (target_dept_ids or []) if str(x).strip()]
        perm = int(requested_permission) if requested_permission is not None else int(self.PERM_READ)
        # Ensure at least READ permission for any share request.
        if (perm & self.PERM_READ) == 0:
            perm |= int(self.PERM_READ)
        with self._connect() as conn:
            conn.execute(
                """
                INSERT INTO kb_share_requests(
                    request_id, kb_id, owner_dept_id, requester_user_id,
                    target_scope, target_dept_ids_json, target_user_id, reason, status,
                    reviewer_user_id, review_comment, created_at, updated_at,
                    requested_permission, parent_share_id
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', NULL, NULL, ?, ?, ?, ?)
                """,
                (
                    req_id, kb_id,
                    self.normalize_dept_id(owner_dept_id),
                    requester_user_id,
                    target_scope,
                    json.dumps(target_ids, ensure_ascii=False),
                    target_user_id,
                    reason,
                    now, now,
                    perm,
                    parent_share_id,
                ),
            )
        return self.get_kb_share_request(req_id) or {}

    def get_kb_share_request(self, request_id: str) -> dict[str, Any] | None:
        with self._connect() as conn:
            row = conn.execute(
                """
                SELECT *
                FROM kb_share_requests
                WHERE request_id = ?
                """,
                (request_id,),
            ).fetchone()
        if not row:
            return None
        item = dict(row)
        try:
            item["target_dept_ids"] = [self.normalize_dept_id(x) for x in json.loads(item.get("target_dept_ids_json") or "[]")]
        except Exception:
            item["target_dept_ids"] = []
        item["owner_dept_id"] = self.normalize_dept_id(item.get("owner_dept_id") or "")
        return item

    def list_kb_share_requests(
        self,
        status: str | None = None,
        owner_dept_id: str | None = None,
        requester_user_id: str | None = None,
        page: int = 1,
        page_size: int = 50,
    ) -> list[dict[str, Any]]:
        where = ["1=1"]
        params: list[Any] = []
        if status:
            where.append("status = ?")
            params.append(status)
        if owner_dept_id:
            where.append("owner_dept_id = ?")
            params.append(self.normalize_dept_id(owner_dept_id))
        if requester_user_id:
            where.append("requester_user_id = ?")
            params.append(requester_user_id)

        sql = f"""
            SELECT sr.*, u.login_id as target_user_login
            FROM kb_share_requests sr
            LEFT JOIN users u ON sr.target_user_id = u.user_id
            WHERE {' AND '.join(where)}
            ORDER BY sr.updated_at DESC
            LIMIT ? OFFSET ?
        """
        params.extend([max(1, page_size), max(0, (page - 1) * page_size)])

        with self._connect() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()

        items: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            try:
                item["target_dept_ids"] = [self.normalize_dept_id(x) for x in json.loads(item.get("target_dept_ids_json") or "[]")]
            except Exception:
                item["target_dept_ids"] = []
            item["owner_dept_id"] = self.normalize_dept_id(item.get("owner_dept_id") or "")
            items.append(item)
        return items

    def review_kb_share_request(self, request_id: str, reviewer_user_id: str, approved: bool, review_comment: str = "") -> dict[str, Any] | None:
        now = self._utc_now()
        status = "approved" if approved else "rejected"
        with self._connect() as conn:
            conn.execute(
                """
                UPDATE kb_share_requests
                SET status = ?, reviewer_user_id = ?, review_comment = ?, updated_at = ?
                WHERE request_id = ? AND status = 'pending'
                """,
                (status, reviewer_user_id, review_comment, now, request_id),
            )
        return self.get_kb_share_request(request_id)

    def delete_kb_share_request(self, request_id: str) -> bool:
        with self._connect() as conn:
            cur = conn.execute(
                """
                DELETE FROM kb_share_requests
                WHERE request_id = ?
                """,
                (request_id,),
            )
        return cur.rowcount > 0

    # ---- Recall Dashboard: list & selective revoke ----

    def list_dept_shares_for_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        """Return dept-level shares for all KBs owned by owner_user_id.

        Each entry represents one department the KB has been shared to,
        derived from resource_policies.allow_dept_ids_json.
        """
        with self._connect() as conn:
            policies = conn.execute(
                """
                SELECT resource_id, allow_dept_ids_json
                FROM resource_policies
                WHERE resource_type = 'kb' AND owner_user_id = ? AND is_active = 1
                """,
                (owner_user_id,),
            ).fetchall()

        results: list[dict[str, Any]] = []
        for p in policies:
            kb_id = p["resource_id"]
            allow_depts = []
            try:
                allow_depts = json.loads(p["allow_dept_ids_json"] or "[]")
            except Exception:
                pass
            if not allow_depts:
                continue
            # Resolve dept info and permission mask
            with self._connect() as conn:
                for dept_id in allow_depts:
                    dept_row = conn.execute(
                        "SELECT dept_code, dept_name FROM departments WHERE dept_id = ?",
                        (dept_id,),
                    ).fetchone()
                    # Get permission mask from any kb_shares record for users in this dept
                    mask_row = conn.execute(
                        """
                        SELECT ks.permission_mask
                        FROM kb_shares ks
                        JOIN user_departments ud ON ks.target_user_id = ud.user_id AND ud.dept_id = ? AND ud.is_active = 1
                        WHERE ks.kb_id = ? AND ks.status = 'approved'
                        LIMIT 1
                        """,
                        (dept_id, kb_id),
                    ).fetchone()
                    results.append({
                        "kb_id": kb_id,
                        "dept_id": dept_id,
                        "dept_code": (dept_row["dept_code"] if dept_row else dept_id),
                        "dept_name": (dept_row["dept_name"] if dept_row else dept_id),
                        "permission_mask": (mask_row["permission_mask"] if mask_row else self.PERM_READ),
                    })
        return results

    def list_user_shares_for_owner(self, owner_user_id: str) -> list[dict[str, Any]]:
        """Return individual user shares for all KBs owned by owner_user_id."""
        with self._connect() as conn:
            rows = conn.execute(
                """
                SELECT ks.id, ks.kb_id, ks.target_user_id, ks.permission_mask, ks.status,
                       u.login_id AS target_login, u.display_name AS target_name
                FROM kb_shares ks
                JOIN users u ON ks.target_user_id = u.user_id
                JOIN resource_policies rp ON rp.resource_id = ks.kb_id
                    AND rp.resource_type = 'kb' AND rp.is_active = 1
                WHERE rp.owner_user_id = ? AND ks.status = 'approved'
                ORDER BY ks.created_at DESC
                """,
                (owner_user_id,),
            ).fetchall()
        return [dict(r) for r in rows]

    def revoke_dept_share(self, kb_id: str, dept_id: str) -> bool:
        """Revoke a specific department share: remove dept from allow_dept_ids and delete kb_shares for dept users."""
        dept_id = self.normalize_dept_id(dept_id)
        now = self._utc_now()
        with self._connect() as conn:
            row = conn.execute(
                "SELECT policy_id, allow_dept_ids_json FROM resource_policies WHERE resource_id = ? AND resource_type = 'kb' AND is_active = 1",
                (kb_id,),
            ).fetchone()
            if not row:
                return False
            allow_depts: list[str] = []
            try:
                allow_depts = json.loads(row["allow_dept_ids_json"] or "[]")
            except Exception:
                pass
            if dept_id in allow_depts:
                allow_depts.remove(dept_id)
            conn.execute(
                "UPDATE resource_policies SET allow_dept_ids_json = ?, updated_at = ? WHERE policy_id = ?",
                (json.dumps(allow_depts, ensure_ascii=False), now, row["policy_id"]),
            )
            # Remove kb_shares for users in that department
            dept_users = conn.execute(
                "SELECT user_id FROM user_departments WHERE dept_id = ? AND is_active = 1",
                (dept_id,),
            ).fetchall()
            for u in dept_users:
                conn.execute(
                    "DELETE FROM kb_shares WHERE kb_id = ? AND target_user_id = ?",
                    (kb_id, u["user_id"]),
                )
        return True

    def revoke_user_share(self, share_id: int) -> bool:
        """Revoke a single user share by its primary key ID."""
        now = self._utc_now()
        with self._connect() as conn:
            cur = conn.execute(
                "UPDATE kb_shares SET status = 'revoked', updated_at = ? WHERE id = ? AND status = 'approved'",
                (now, share_id),
            )
        return cur.rowcount > 0
