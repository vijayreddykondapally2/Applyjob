"""
SQLite database layer for multi-user ApplyJob AI.

Tables:
  - users         : authentication + basic info
  - profiles      : per-user candidate profile (JSON blob)
  - credentials   : per-user portal credentials
  - job_runs      : tracks active/finished automation runs
  - applications  : per-user job application history
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

import bcrypt

DB_PATH = Path(os.getenv("APPLYJOB_DB", "data/applyjob.db"))


def _ensure_db_dir():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db():
    """Thread-safe connection context manager."""
    _ensure_db_dir()
    conn = sqlite3.connect(str(DB_PATH), timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db():
    """Create all tables if they don't exist."""
    with get_db() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS users (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                email       TEXT    UNIQUE NOT NULL,
                password_hash TEXT  NOT NULL,
                full_name   TEXT    NOT NULL DEFAULT '',
                is_admin    INTEGER NOT NULL DEFAULT 0,
                created_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS profiles (
                user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                profile_json TEXT NOT NULL DEFAULT '{}',
                updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS credentials (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                portal      TEXT    NOT NULL,  -- linkedin, naukri, foundit, monster
                cred_key    TEXT    NOT NULL,  -- e.g. 'email', 'password', 'mobile'
                cred_value  TEXT    NOT NULL DEFAULT '',
                UNIQUE(user_id, portal, cred_key)
            );

            CREATE TABLE IF NOT EXISTS job_runs (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                portals     TEXT    NOT NULL DEFAULT '[]',  -- JSON list of portal names
                status      TEXT    NOT NULL DEFAULT 'pending',  -- pending, running, stopped, finished, error
                pid         INTEGER,
                started_at  TEXT,
                finished_at TEXT,
                log_tail    TEXT    NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS applications (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                portal      TEXT    NOT NULL,
                title       TEXT    NOT NULL DEFAULT '',
                company     TEXT    NOT NULL DEFAULT '',
                url         TEXT    NOT NULL DEFAULT '',
                status      TEXT    NOT NULL DEFAULT 'submitted',
                applied_at  TEXT    NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS settings (
                user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                groq_api_key TEXT   NOT NULL DEFAULT '',
                groq_model  TEXT    NOT NULL DEFAULT 'llama-3.1-8b-instant',
                job_keywords TEXT   NOT NULL DEFAULT 'ETL Testing',
                job_location TEXT   NOT NULL DEFAULT 'India',
                max_jobs    INTEGER NOT NULL DEFAULT 25,
                headless    INTEGER NOT NULL DEFAULT 0,
                easy_apply_only INTEGER NOT NULL DEFAULT 1
            );
        """)


# ═══════════════════════════════════════════════════════════════════════════════
# User CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def create_user(email: str, password: str, full_name: str = "") -> Optional[int]:
    """Register a new user. Returns user id or None if email exists."""
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        with get_db() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)",
                (email.lower().strip(), pw_hash, full_name.strip()),
            )
            user_id = cur.lastrowid
            # Create empty profile and settings rows
            conn.execute("INSERT INTO profiles (user_id) VALUES (?)", (user_id,))
            conn.execute("INSERT INTO settings (user_id) VALUES (?)", (user_id,))
            return user_id
    except sqlite3.IntegrityError:
        return None


def verify_user(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Check credentials. Returns user dict or None."""
    with get_db() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        ).fetchone()
    if row and bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return dict(row)
    return None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone()
    return dict(row) if row else None


def get_all_users() -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute("SELECT id, email, full_name, is_admin, created_at FROM users ORDER BY id").fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Profile CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def get_profile(user_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT profile_json FROM profiles WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        try:
            return json.loads(row["profile_json"])
        except json.JSONDecodeError:
            return {}
    return {}


def save_profile(user_id: int, profile: Dict[str, Any]):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO profiles (user_id, profile_json, updated_at) VALUES (?, ?, datetime('now')) "
            "ON CONFLICT(user_id) DO UPDATE SET profile_json = excluded.profile_json, updated_at = excluded.updated_at",
            (user_id, json.dumps(profile, ensure_ascii=False, indent=2)),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Credentials CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def get_credentials(user_id: int) -> Dict[str, Dict[str, str]]:
    """Returns {portal: {key: value, ...}, ...}"""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT portal, cred_key, cred_value FROM credentials WHERE user_id = ?",
            (user_id,),
        ).fetchall()
    result: Dict[str, Dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["portal"], {})[r["cred_key"]] = r["cred_value"]
    return result


def save_credential(user_id: int, portal: str, key: str, value: str):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO credentials (user_id, portal, cred_key, cred_value) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, portal, cred_key) DO UPDATE SET cred_value = excluded.cred_value",
            (user_id, portal.lower(), key, value),
        )


def save_credentials_bulk(user_id: int, creds: Dict[str, Dict[str, str]]):
    """Save multiple portal credentials at once. creds = {portal: {key: val}}"""
    with get_db() as conn:
        for portal, pairs in creds.items():
            for key, value in pairs.items():
                conn.execute(
                    "INSERT INTO credentials (user_id, portal, cred_key, cred_value) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, portal, cred_key) DO UPDATE SET cred_value = excluded.cred_value",
                    (user_id, portal.lower(), key, value),
                )


# ═══════════════════════════════════════════════════════════════════════════════
# Settings CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def get_settings(user_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        row = conn.execute("SELECT * FROM settings WHERE user_id = ?", (user_id,)).fetchone()
    if row:
        return dict(row)
    return {
        "user_id": user_id, "groq_api_key": "", "groq_model": "llama-3.1-8b-instant",
        "job_keywords": "ETL Testing", "job_location": "India", "max_jobs": 25,
        "headless": 0, "easy_apply_only": 1,
    }


def save_settings(user_id: int, settings: Dict[str, Any]):
    with get_db() as conn:
        conn.execute(
            """INSERT INTO settings (user_id, groq_api_key, groq_model, job_keywords, job_location,
               max_jobs, headless, easy_apply_only)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(user_id) DO UPDATE SET
                 groq_api_key=excluded.groq_api_key, groq_model=excluded.groq_model,
                 job_keywords=excluded.job_keywords, job_location=excluded.job_location,
                 max_jobs=excluded.max_jobs, headless=excluded.headless,
                 easy_apply_only=excluded.easy_apply_only""",
            (
                user_id,
                settings.get("groq_api_key", ""),
                settings.get("groq_model", "llama-3.1-8b-instant"),
                settings.get("job_keywords", "ETL Testing"),
                settings.get("job_location", "India"),
                int(settings.get("max_jobs", 25)),
                int(settings.get("headless", 0)),
                int(settings.get("easy_apply_only", 1)),
            ),
        )


# ═══════════════════════════════════════════════════════════════════════════════
# Job Runs
# ═══════════════════════════════════════════════════════════════════════════════

def create_job_run(user_id: int, portals: List[str]) -> int:
    with get_db() as conn:
        cur = conn.execute(
            "INSERT INTO job_runs (user_id, portals, status, started_at) VALUES (?, ?, 'running', datetime('now'))",
            (user_id, json.dumps(portals)),
        )
        return cur.lastrowid


def update_job_run(run_id: int, **kwargs):
    allowed = {"status", "pid", "finished_at", "log_tail"}
    sets = []
    vals = []
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = ?")
            vals.append(v)
    if not sets:
        return
    vals.append(run_id)
    with get_db() as conn:
        conn.execute(f"UPDATE job_runs SET {', '.join(sets)} WHERE id = ?", vals)


def get_active_runs(user_id: int) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM job_runs WHERE user_id = ? AND status = 'running' ORDER BY id DESC",
            (user_id,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_run_history(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM job_runs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Application History
# ═══════════════════════════════════════════════════════════════════════════════

def log_application(user_id: int, portal: str, title: str, company: str, url: str, status: str = "submitted"):
    with get_db() as conn:
        conn.execute(
            "INSERT INTO applications (user_id, portal, title, company, url, status) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, portal, title, company, url, status),
        )


def get_applications(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        ).fetchall()
    return [dict(r) for r in rows]


def get_application_stats(user_id: int) -> Dict[str, int]:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT portal, COUNT(*) as cnt FROM applications WHERE user_id = ? GROUP BY portal",
            (user_id,),
        ).fetchall()
    stats = {r["portal"]: r["cnt"] for r in rows}
    stats["total"] = sum(stats.values())
    return stats


# ═══════════════════════════════════════════════════════════════════════════════
# User data directory helpers
# ═══════════════════════════════════════════════════════════════════════════════

def user_data_dir(user_id: int) -> Path:
    """Return per-user data directory, creating if needed."""
    p = Path(f"data/users/{user_id}")
    p.mkdir(parents=True, exist_ok=True)
    return p


def user_browser_profile_dir(user_id: int, portal: str) -> str:
    """Return per-user browser profile directory for a specific portal."""
    p = user_data_dir(user_id) / f"browser-profile-{portal}"
    p.mkdir(parents=True, exist_ok=True)
    return str(p)


def user_log_path(user_id: int) -> str:
    return str(user_data_dir(user_id) / "app_output.log")
