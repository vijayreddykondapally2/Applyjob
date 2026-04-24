"""
Multi-user task manager for ApplyJob AI.

Each user can launch automation agents in background threads.
Agents run in isolated subprocesses with per-user environment variables.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from app.database import (
    create_job_run,
    get_active_runs,
    get_credentials,
    get_profile,
    get_settings,
    update_job_run,
    user_browser_profile_dir,
    user_data_dir,
    user_log_path,
)

# Global registry of running processes: { user_id: { run_id: subprocess.Popen } }
_active_processes: Dict[int, Dict[int, subprocess.Popen]] = {}
_lock = threading.Lock()


def get_user_env(user_id: int, portals: List[str]) -> Dict[str, str]:
    """Build a complete environment dict for a user's automation run."""
    env = os.environ.copy()

    # Load user settings
    settings = get_settings(user_id)
    profile = get_profile(user_id)
    creds = get_credentials(user_id)

    # Groq / AI
    env["GROQ_API_KEY"] = settings.get("groq_api_key", "")
    env["GROQ_MODEL"] = settings.get("groq_model", "llama-3.1-8b-instant")
    env["ENABLE_AI_ANSWERING"] = "true" if settings.get("groq_api_key") else "false"

    # Job search
    is_on_huggingface = os.getenv("SPACE_ID") is not None
    has_display = os.getenv("DISPLAY") is not None
    is_linux = sys.platform.startswith("linux")
    
    env["JOB_KEYWORDS"] = settings.get("job_keywords", "ETL Testing")
    env["JOB_LOCATION"] = settings.get("job_location", "India")
    env["MAX_JOBS"] = str(settings.get("max_jobs", 25))
    
    # Force headless if on Hugging Face or on Linux without an X server (no GUI)
    if is_on_huggingface or (is_linux and not has_display):
        env["HEADLESS"] = "true"
    else:
        env["HEADLESS"] = "true" if settings.get("headless") else "false"
        
    env["EASY_APPLY_ONLY"] = "true" if settings.get("easy_apply_only") else "false"

    # Per-user browser profiles
    env["BROWSER_PROFILE_DIR"] = user_browser_profile_dir(user_id, "linkedin")
    env["NAUKRI_PROFILE_DIR"] = user_browser_profile_dir(user_id, "naukri")
    env["FOUNDIT_PROFILE_DIR"] = user_browser_profile_dir(user_id, "foundit")
    env["MONSTER_PROFILE_DIR"] = user_browser_profile_dir(user_id, "monster")

    # Portal credentials
    linkedin_creds = creds.get("linkedin", {})
    env["LINKEDIN_EMAIL"] = linkedin_creds.get("email", "")
    env["LINKEDIN_PASSWORD"] = linkedin_creds.get("password", "")

    naukri_creds = creds.get("naukri", {})
    env["NAUKARI_EMAIL"] = naukri_creds.get("email", "")
    env["NAUKARI_PASSWORD"] = naukri_creds.get("password", "")

    foundit_creds = creds.get("foundit", {})
    env["FOUNDIT_EMAIL"] = foundit_creds.get("email", "")
    env["FOUNDIT_PASSWORD"] = foundit_creds.get("password", "")
    env["FOUNDIT_MOBILE"] = foundit_creds.get("mobile", "")

    monster_creds = creds.get("monster", {})
    env["MONSTER_EMAIL"] = monster_creds.get("email", "")
    env["MONSTER_PASSWORD"] = monster_creds.get("password", "")

    # Per-user profile file
    profile_dir = user_data_dir(user_id)
    profile_path = profile_dir / "profile.json"
    profile_path.write_text(json.dumps(profile, ensure_ascii=False, indent=2), encoding="utf-8")
    env["APPLYJOB_PROFILE_PATH"] = str(profile_path)

    # Per-user log and data
    env["APPLYJOB_USER_ID"] = str(user_id)
    env["APPLYJOB_DATA_DIR"] = str(profile_dir)

    # Parallel mode settings
    env["PARALLEL_MODE"] = "true"
    env["CONTINUOUS_LOOP"] = "true"
    env["KEEP_BROWSER_OPEN"] = "false"
    
    # In headless mode, the agent MUST auto-click buttons (no human present).
    # MANUAL_LOGIN_SUBMIT=true means "wait for human to click Submit" — fatal in headless.
    if env.get("HEADLESS") == "true":
        env["MANUAL_LOGIN_SUBMIT"] = "false"
        env["ALLOW_MANUAL_CHECKPOINT"] = "false"
    else:
        env["MANUAL_LOGIN_SUBMIT"] = "true"
        env["ALLOW_MANUAL_CHECKPOINT"] = "true"

    return env


def start_run(user_id: int, portals: List[str]) -> Dict[str, Any]:
    """
    Launch automation agents for a user.
    Returns {"run_id": ..., "status": "started"} or error info.
    """
    with _lock:
        # Check if user already has an active run
        if user_id in _active_processes:
            active = {k: v for k, v in _active_processes[user_id].items()
                      if v.poll() is None}
            if active:
                return {"status": "already_running", "run_id": list(active.keys())[0]}
            else:
                del _active_processes[user_id]

    # Validate portals
    valid_portals = {"linkedin", "naukri", "foundit", "monster"}
    portals = [p.lower() for p in portals if p.lower() in valid_portals]
    if not portals:
        return {"status": "error", "message": "No valid portals selected"}

    # Create DB record
    run_id = create_job_run(user_id, portals)

    # Build environment
    env = get_user_env(user_id, portals)

    # Prepare log file
    log_path = user_log_path(user_id)
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    with open(log_path, "w") as f:
        f.write(f"--- Session started at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')} ---\n")
        f.write(f"--- User ID: {user_id} | Portals: {portals} ---\n")

    # Determine which script to run
    portal_arg = "parallel" if len(portals) > 1 else portals[0]

    try:
        python_exe = sys.executable
        proc = subprocess.Popen(
            [python_exe, "-u", "main.py", portal_arg],
            stdout=open(log_path, "a"),
            stderr=subprocess.STDOUT,
            env=env,
            cwd=os.getcwd(),
            preexec_fn=os.setsid,
        )

        update_job_run(run_id, pid=proc.pid, status="running")

        with _lock:
            _active_processes.setdefault(user_id, {})[run_id] = proc

        # Start monitoring thread
        t = threading.Thread(target=_monitor_process, args=(user_id, run_id, proc), daemon=True)
        t.start()

        return {"status": "started", "run_id": run_id, "pid": proc.pid}

    except Exception as e:
        update_job_run(run_id, status="error", finished_at=datetime.now().isoformat())
        return {"status": "error", "message": str(e)}


def stop_run(user_id: int, run_id: Optional[int] = None) -> Dict[str, str]:
    """Stop a running automation for a user."""
    with _lock:
        if user_id not in _active_processes:
            return {"status": "not_running"}

        procs = _active_processes[user_id]
        stopped = []

        targets = [run_id] if run_id else list(procs.keys())

        for rid in targets:
            proc = procs.get(rid)
            if proc and proc.poll() is None:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
                    stopped.append(rid)
                except Exception:
                    try:
                        proc.terminate()
                        stopped.append(rid)
                    except Exception:
                        pass

                update_job_run(rid, status="stopped", finished_at=datetime.now().isoformat())

        # Clean up
        for rid in stopped:
            procs.pop(rid, None)
        if not procs:
            del _active_processes[user_id]

        return {"status": "stopped", "stopped_runs": stopped}


def get_user_status(user_id: int) -> Dict[str, Any]:
    """Get current automation status for a user."""
    is_running = False
    active_run_id = None

    with _lock:
        if user_id in _active_processes:
            for rid, proc in list(_active_processes[user_id].items()):
                if proc.poll() is None:
                    is_running = True
                    active_run_id = rid
                    break

    # Read recent logs
    log_lines = []
    log_path = user_log_path(user_id)
    if os.path.exists(log_path):
        try:
            with open(log_path, "r", encoding="utf-8", errors="replace") as f:
                all_lines = f.read().splitlines()
                log_lines = all_lines[-30:]  # Last 30 lines
        except Exception:
            pass

    from app.database import get_application_stats, get_applications
    
    stats = get_application_stats(user_id)
    recent_apps = get_applications(user_id, limit=1)
    latest_app = recent_apps[0] if recent_apps else None

    # Flatten active portals into a single list for the UI
    active_runs = get_active_runs(user_id)
    active_portal_names = []
    for run in active_runs:
        portals_val = run.get("portals", "")
        if isinstance(portals_val, str):
            if portals_val.startswith("["): # JSON array
                try:
                    p_list = json.loads(portals_val)
                    active_portal_names.extend([p.lower() for p in p_list])
                except: pass
            else: # Comma separated
                p_list = portals_val.split(",")
                active_portal_names.extend([p.strip().lower() for p in p_list if p.strip()])
        elif isinstance(portals_val, list):
            active_portal_names.extend([p.lower() for p in portals_val])

    return {
        "is_running": is_running,
        "active_run_id": active_run_id,
        "logs": log_lines,
        "active_runs": list(set(active_portal_names)), # Unique list
        "stats": stats,
        "latest_app": latest_app,
    }


def _monitor_process(user_id: int, run_id: int, proc: subprocess.Popen):
    """Background thread that waits for a process to finish and updates the DB."""
    proc.wait()
    update_job_run(run_id, status="finished", finished_at=datetime.now().isoformat())

    with _lock:
        if user_id in _active_processes:
            _active_processes[user_id].pop(run_id, None)
            if not _active_processes[user_id]:
                del _active_processes[user_id]
