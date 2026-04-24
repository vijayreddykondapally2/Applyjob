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
import psycopg2
from psycopg2.extras import RealDictCursor

# Configuration: Use DATABASE_URL for Postgres (Supabase), otherwise fallback to SQLite
DB_URL = os.getenv("DATABASE_URL")
DB_PATH = Path(os.getenv("APPLYJOB_DB", "data/applyjob.db"))


def _ensure_db_dir():
    if not DB_URL:
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)


@contextmanager
def get_db():
    """Thread-safe connection context manager for SQLite or PostgreSQL."""
    _ensure_db_dir()
    
    conn = None
    is_postgres = DB_URL is not None
    
    try:
        if is_postgres:
            # Connect to PostgreSQL (Supabase)
            conn = psycopg2.connect(DB_URL, connect_timeout=10)
        else:
            # Connect to local SQLite
            conn = sqlite3.connect(str(DB_PATH), timeout=10)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA foreign_keys=ON")
            
        yield conn
        conn.commit()
    except Exception as e:
        if conn:
            conn.rollback()
        raise e
    finally:
        if conn:
            conn.close()


def _get_cursor(conn):
    """Helper to get a dictionary-friendly cursor."""
    if DB_URL:
        return conn.cursor(cursor_factory=RealDictCursor)
    return conn.cursor()


def init_db():
    """Create all tables if they don't exist."""
    is_postgres = DB_URL is not None
    
    # SQL types and defaults differ between SQLite and Postgres
    SERIAL_TYPE = "SERIAL" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    PK_SERIAL = "SERIAL PRIMARY KEY" if is_postgres else "INTEGER PRIMARY KEY AUTOINCREMENT"
    NOW = "CURRENT_TIMESTAMP" if is_postgres else "(datetime('now'))"
    TEXT_TYPE = "TEXT"
    
    with get_db() as conn:
        if is_postgres:
            conn.set_isolation_level(0)  # Autocommit mode for initialization
        
        cur = conn.cursor()
        print("🛠️ Starting Database Initialization...")
        
        queries = [
            f"""CREATE TABLE IF NOT EXISTS users (
                id          {PK_SERIAL},
                email       {TEXT_TYPE} UNIQUE NOT NULL,
                password_hash {TEXT_TYPE} NOT NULL,
                full_name   {TEXT_TYPE} NOT NULL DEFAULT '',
                is_admin    INTEGER NOT NULL DEFAULT 0,
                created_at  TIMESTAMP NOT NULL DEFAULT {NOW}
            )""",
            f"""CREATE TABLE IF NOT EXISTS profiles (
                user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                profile_json {TEXT_TYPE} NOT NULL DEFAULT '{{}}',
                updated_at  TIMESTAMP NOT NULL DEFAULT {NOW}
            )""",
            f"""CREATE TABLE IF NOT EXISTS credentials (
                id          {PK_SERIAL},
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                portal      {TEXT_TYPE} NOT NULL,
                cred_key    {TEXT_TYPE} NOT NULL,
                cred_value  {TEXT_TYPE} NOT NULL DEFAULT '',
                UNIQUE(user_id, portal, cred_key)
            )""",
            f"""CREATE TABLE IF NOT EXISTS job_runs (
                id          {PK_SERIAL},
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                portals     {TEXT_TYPE} NOT NULL DEFAULT '[]',
                status      {TEXT_TYPE} NOT NULL DEFAULT 'pending',
                pid         INTEGER,
                started_at  {TEXT_TYPE},
                finished_at {TEXT_TYPE},
                log_tail    {TEXT_TYPE} NOT NULL DEFAULT ''
            )""",
            f"""CREATE TABLE IF NOT EXISTS applications (
                id          {PK_SERIAL},
                user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
                portal      {TEXT_TYPE} NOT NULL,
                title       {TEXT_TYPE} NOT NULL DEFAULT '',
                company     {TEXT_TYPE} NOT NULL DEFAULT '',
                url         {TEXT_TYPE} NOT NULL DEFAULT '',
                status      {TEXT_TYPE} NOT NULL DEFAULT 'submitted',
                applied_at  TIMESTAMP NOT NULL DEFAULT {NOW}
            )""",
            f"""CREATE TABLE IF NOT EXISTS settings (
                user_id     INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
                groq_api_key {TEXT_TYPE} NOT NULL DEFAULT '',
                groq_model  {TEXT_TYPE} NOT NULL DEFAULT 'llama-3.1-8b-instant',
                job_keywords {TEXT_TYPE} NOT NULL DEFAULT 'ETL Testing',
                job_location {TEXT_TYPE} NOT NULL DEFAULT 'India',
                max_jobs    INTEGER NOT NULL DEFAULT 25,
                headless    INTEGER NOT NULL DEFAULT 0,
                easy_apply_only INTEGER NOT NULL DEFAULT 1
            )"""
        ]

        for q in queries:
            table_name = q.split("IF NOT EXISTS ")[1].split(" ")[0].strip()
            try:
                cur.execute(q)
                print(f"✅ Table check/creation: {table_name}")
            except Exception as e:
                # In autocommit mode, we can just continue
                if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                    print(f"ℹ️ Table {table_name} already exists.")
                else:
                    print(f"⚠️ Warning on {table_name}: {e}")
        
        cur.close()
        print("✨ Database Initialization Complete.")


# ═══════════════════════════════════════════════════════════════════════════════
# User CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def create_user(email: str, password: str, full_name: str = "") -> Optional[int]:
    """Register a new user. Returns user id or None if email exists."""
    pw_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
    try:
        with get_db() as conn:
            cur = conn.cursor()
            if DB_URL:
                # PostgreSQL way: use RETURNING id
                cur.execute(
                    "INSERT INTO users (email, password_hash, full_name) VALUES (%s, %s, %s) RETURNING id",
                    (email.lower().strip(), pw_hash, full_name.strip()),
                )
                user_id = cur.fetchone()[0]
            else:
                # SQLite way
                cur.execute(
                    "INSERT INTO users (email, password_hash, full_name) VALUES (?, ?, ?)",
                    (email.lower().strip(), pw_hash, full_name.strip()),
                )
                user_id = cur.lastrowid
                
            cur.execute(
                "INSERT INTO profiles (user_id) VALUES (%s)" if DB_URL else
                "INSERT INTO profiles (user_id) VALUES (?)", (user_id,)
            )
            cur.execute(
                "INSERT INTO settings (user_id) VALUES (%s)" if DB_URL else
                "INSERT INTO settings (user_id) VALUES (?)", (user_id,)
            )
            cur.close()
            return user_id
    except (sqlite3.IntegrityError, psycopg2.IntegrityError) as e:
        print(f"❌ Signup Integrity Error: {e}")
        return None
    except Exception as e:
        print(f"❌ Signup Error: {e}")
        return None


def verify_user(email: str, password: str) -> Optional[Dict[str, Any]]:
    """Check credentials. Returns user dict or None."""
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT * FROM users WHERE email = %s" if DB_URL else
            "SELECT * FROM users WHERE email = ?", (email.lower().strip(),)
        )
        row = cur.fetchone()
        cur.close()
    if row and bcrypt.checkpw(password.encode("utf-8"), row["password_hash"].encode("utf-8")):
        return dict(row)
    return None


def get_user_by_id(user_id: int) -> Optional[Dict[str, Any]]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT * FROM users WHERE id = %s" if DB_URL else
            "SELECT * FROM users WHERE id = ?", (user_id,)
        )
        row = cur.fetchone()
        cur.close()
    return dict(row) if row else None


def get_all_users() -> List[Dict[str, Any]]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute("SELECT id, email, full_name, is_admin, created_at FROM users ORDER BY id")
        rows = cur.fetchall()
        cur.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Profile CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def get_profile(user_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT profile_json FROM profiles WHERE user_id = %s" if DB_URL else
            "SELECT profile_json FROM profiles WHERE user_id = ?", (user_id,)
        )
        row = cur.fetchone()
        cur.close()
    if row:
        try:
            return json.loads(row["profile_json"])
        except json.JSONDecodeError:
            return {}
    return {}


def save_profile(user_id: int, profile: Dict[str, Any]):
    now_sql = "CURRENT_TIMESTAMP" if DB_URL else "datetime('now')"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO profiles (user_id, profile_json, updated_at) VALUES (%s, %s, {now_sql}) "
            "ON CONFLICT(user_id) DO UPDATE SET profile_json = EXCLUDED.profile_json, updated_at = EXCLUDED.updated_at" if DB_URL else
            f"INSERT INTO profiles (user_id, profile_json, updated_at) VALUES (?, ?, {now_sql}) "
            "ON CONFLICT(user_id) DO UPDATE SET profile_json = excluded.profile_json, updated_at = excluded.updated_at",
            (user_id, json.dumps(profile, ensure_ascii=False, indent=2)),
        )
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Credentials CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def get_credentials(user_id: int) -> Dict[str, Dict[str, str]]:
    """Returns {portal: {key: value, ...}, ...}"""
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT portal, cred_key, cred_value FROM credentials WHERE user_id = %s" if DB_URL else
            "SELECT portal, cred_key, cred_value FROM credentials WHERE user_id = ?",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
    result: Dict[str, Dict[str, str]] = {}
    for r in rows:
        result.setdefault(r["portal"], {})[r["cred_key"]] = r["cred_value"]
    return result


def save_credential(user_id: int, portal: str, key: str, value: str):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO credentials (user_id, portal, cred_key, cred_value) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT(user_id, portal, cred_key) DO UPDATE SET cred_value = EXCLUDED.cred_value" if DB_URL else
            "INSERT INTO credentials (user_id, portal, cred_key, cred_value) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(user_id, portal, cred_key) DO UPDATE SET cred_value = excluded.cred_value",
            (user_id, portal.lower(), key, value),
        )
        cur.close()


def save_credentials_bulk(user_id: int, creds: Dict[str, Dict[str, str]]):
    """Save multiple portal credentials at once. creds = {portal: {key: val}}"""
    with get_db() as conn:
        cur = conn.cursor()
        for portal, pairs in creds.items():
            for key, value in pairs.items():
                cur.execute(
                    "INSERT INTO credentials (user_id, portal, cred_key, cred_value) VALUES (%s, %s, %s, %s) "
                    "ON CONFLICT(user_id, portal, cred_key) DO UPDATE SET cred_value = EXCLUDED.cred_value" if DB_URL else
                    "INSERT INTO credentials (user_id, portal, cred_key, cred_value) VALUES (?, ?, ?, ?) "
                    "ON CONFLICT(user_id, portal, cred_key) DO UPDATE SET cred_value = excluded.cred_value",
                    (user_id, portal.lower(), key, value),
                )
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Settings CRUD
# ═══════════════════════════════════════════════════════════════════════════════

def get_settings(user_id: int) -> Dict[str, Any]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT * FROM settings WHERE user_id = %s" if DB_URL else
            "SELECT * FROM settings WHERE user_id = ?", (user_id,)
        )
        row = cur.fetchone()
        cur.close()
    if row:
        return dict(row)
    return {
        "user_id": user_id, "groq_api_key": "", "groq_model": "llama-3.1-8b-instant",
        "job_keywords": "ETL Testing", "job_location": "India", "max_jobs": 25,
        "headless": 0, "easy_apply_only": 1,
    }


def save_settings(user_id: int, settings: Dict[str, Any]):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            """INSERT INTO settings (user_id, groq_api_key, groq_model, job_keywords, job_location,
               max_jobs, headless, easy_apply_only)
               VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
               ON CONFLICT(user_id) DO UPDATE SET
                 groq_api_key=EXCLUDED.groq_api_key, groq_model=EXCLUDED.groq_model,
                 job_keywords=EXCLUDED.job_keywords, job_location=EXCLUDED.job_location,
                 max_jobs=EXCLUDED.max_jobs, headless=EXCLUDED.headless,
                 easy_apply_only=EXCLUDED.easy_apply_only""" if DB_URL else
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
        cur.close()


# ═══════════════════════════════════════════════════════════════════════════════
# Job Runs
# ═══════════════════════════════════════════════════════════════════════════════

def create_job_run(user_id: int, portals: List[str]) -> int:
    now_sql = "CURRENT_TIMESTAMP" if DB_URL else "datetime('now')"
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            f"INSERT INTO job_runs (user_id, portals, status, started_at) VALUES (%s, %s, 'running', {now_sql})" if DB_URL else
            f"INSERT INTO job_runs (user_id, portals, status, started_at) VALUES (?, ?, 'running', {now_sql})",
            (user_id, json.dumps(portals)),
        )
        if DB_URL:
            cur.execute("SELECT LASTVAL()")
            run_id = cur.fetchone()[0]
        else:
            run_id = cur.lastrowid
        cur.close()
        return run_id


def update_job_run(run_id: int, **kwargs):
    allowed = {"status", "pid", "finished_at", "log_tail"}
    sets = []
    vals = []
    param_char = "%s" if DB_URL else "?"
    for k, v in kwargs.items():
        if k in allowed:
            sets.append(f"{k} = {param_char}")
            vals.append(v)
    if not sets:
        return
    vals.append(run_id)
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(f"UPDATE job_runs SET {', '.join(sets)} WHERE id = {param_char}", vals)
        cur.close()


def get_active_runs(user_id: int) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT * FROM job_runs WHERE user_id = %s AND status = 'running' ORDER BY id DESC" if DB_URL else
            "SELECT * FROM job_runs WHERE user_id = ? AND status = 'running' ORDER BY id DESC",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
    return [dict(r) for r in rows]


def get_run_history(user_id: int, limit: int = 20) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT * FROM job_runs WHERE user_id = %s ORDER BY id DESC LIMIT %s" if DB_URL else
            "SELECT * FROM job_runs WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cur.fetchall()
        cur.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════════════════════════
# Application History
# ═══════════════════════════════════════════════════════════════════════════════

def log_application(user_id: int, portal: str, title: str, company: str, url: str, status: str = "submitted"):
    with get_db() as conn:
        cur = conn.cursor()
        cur.execute(
            "INSERT INTO applications (user_id, portal, title, company, url, status) VALUES (%s, %s, %s, %s, %s, %s)" if DB_URL else
            "INSERT INTO applications (user_id, portal, title, company, url, status) VALUES (?, ?, ?, ?, ?, ?)",
            (user_id, portal, title, company, url, status),
        )
        cur.close()


def get_applications(user_id: int, limit: int = 100) -> List[Dict[str, Any]]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT * FROM applications WHERE user_id = %s ORDER BY id DESC LIMIT %s" if DB_URL else
            "SELECT * FROM applications WHERE user_id = ? ORDER BY id DESC LIMIT ?",
            (user_id, limit),
        )
        rows = cur.fetchall()
        cur.close()
    return [dict(r) for r in rows]


def get_application_stats(user_id: int) -> Dict[str, int]:
    with get_db() as conn:
        cur = _get_cursor(conn)
        cur.execute(
            "SELECT portal, COUNT(*) as cnt FROM applications WHERE user_id = %s GROUP BY portal" if DB_URL else
            "SELECT portal, COUNT(*) as cnt FROM applications WHERE user_id = ? GROUP BY portal",
            (user_id,),
        )
        rows = cur.fetchall()
        cur.close()
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
