import json
import os
import time
from pathlib import Path
from datetime import datetime
from typing import Any, Dict

# Default relative paths
DEFAULT_DEBUG_LOG_PATH = "data/debug.log"
DEBUG_SESSION_ID = "786398"


def debug_log(run_id: str, hypothesis_id: str, location: str, message: str, data: Dict[str, Any]) -> None:
    """
    Centralized debugging logger.
    """
    # Use environment variable for log path if provided, otherwise default to relative path
    log_path = os.getenv("DEBUG_LOG_PATH", DEFAULT_DEBUG_LOG_PATH)
    
    payload = {
        "sessionId": DEBUG_SESSION_ID,
        "runId": run_id,
        "hypothesisId": hypothesis_id,
        "location": location,
        "message": message,
        "data": data,
        "timestamp": int(time.time() * 1000),
    }
    
    try:
        p = Path(log_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        # Silently fail for debug logs to avoid interrupting the main flow
        pass


def get_compressed_dom(page, selector: str = "body") -> str:
    """
    Extracts a simplified, text-based representation of the DOM for AI analysis.
    Focuses on tags, classes, and IDs of interactive elements.
    """
    try:
        return str(
            page.evaluate(
                f"""(rootSelector) => {{
                    const root = document.querySelector(rootSelector) || document.body;
                    const items = Array.from(root.querySelectorAll('li, div, button, a, [role="button"], [data-job-id], [data-occludable-job-id]'));
                    return items.filter(el => {{
                        const style = window.getComputedStyle(el);
                        return style.display !== 'none' && style.visibility !== 'hidden';
                    }}).map(el => {{
                        const tag = el.tagName.toLowerCase();
                        const id = el.id ? `#${{el.id}}` : '';
                        const classes = el.className && typeof el.className === 'string' 
                            ? `.${{el.className.trim().split(/\\s+/).join('.')}}` 
                            : '';
                        const text = (el.innerText || el.value || '').substring(0, 80).trim().replace(/\\n/g, ' ');
                        const role = el.getAttribute('role') || '';
                        const jobId = el.getAttribute('data-job-id') || el.getAttribute('data-occludable-job-id') || '';
                        if (!text && !id && !jobId && tag !== 'button' && tag !== 'a') return null;
                        return `<${{tag}}${{id}}${{classes}}${{role ? ` role="${{role}}"` : ''}}${{jobId ? ` jobid="${{jobId}}"` : ''}}>${{text}}</${{tag}}>`;
                    }}).filter(x => x).slice(0, 80).join('\\n');
                }}""",
                selector
            )
        )
    except Exception:
        return ""


def log_application(portal: str, title: str, company: str, url: str, status: str = "submitted"):
    """
    Log a job application. In multi-user mode (APPLYJOB_USER_ID set),
    writes to the SQLite database. Always writes to the JSON file as fallback.
    """
    # ── Multi-user: write to database ─────────────────────────────────────
    user_id = os.getenv("APPLYJOB_USER_ID", "")
    if user_id:
        try:
            from app.database import log_application as db_log_application
            db_log_application(int(user_id), portal, title, company, url, status)
        except Exception:
            pass  # Fall through to JSON file

    # ── JSON file fallback (always, for backward compatibility) ────────────
    data_dir = os.getenv("APPLYJOB_DATA_DIR", "data")
    history_path = Path(data_dir) / "applied_jobs.json"
    history_path.parent.mkdir(parents=True, exist_ok=True)
    
    record = {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "portal": portal,
        "title": title,
        "company": company,
        "url": url,
        "status": status
    }
    
    data = []
    if history_path.exists():
        try:
            with open(history_path, "r", encoding="utf-8") as f:
                data = json.load(f)
        except Exception:
            data = []
            
    data.append(record)
    
    try:
        with open(history_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except Exception:
        pass

def should_run_headless() -> bool:
    """Determine if we should run in headless mode based on environment."""
    import sys
    
    platform = sys.platform
    display = os.getenv("DISPLAY")
    space_id = os.getenv("SPACE_ID")
    railway = os.getenv("RAILWAY") or os.getenv("RAILWAY_ENVIRONMENT")
    headless_env = os.getenv("HEADLESS", "")
    is_docker = os.path.exists("/.dockerenv") or os.path.exists("/run/.containerenv")
    is_root_user = os.path.expanduser("~") == "/root"
    
    print(f"[HEADLESS CHECK] platform={platform}, DISPLAY={display}, "
          f"SPACE_ID={space_id}, RAILWAY={railway}, HEADLESS={headless_env}, "
          f"docker={is_docker}, root_user={is_root_user}")
    
    # Force headless if on any cloud platform
    if space_id is not None:
        print("[HEADLESS CHECK] → HEADLESS=True (HuggingFace Space detected)")
        return True
    
    if railway is not None:
        print("[HEADLESS CHECK] → HEADLESS=True (Railway detected)")
        return True
    
    # Force headless if in a Docker container
    if is_docker or is_root_user:
        print("[HEADLESS CHECK] → HEADLESS=True (Docker/container detected)")
        return True
    
    # Force headless if on Linux with no DISPLAY
    if platform.startswith("linux") and display is None:
        print("[HEADLESS CHECK] → HEADLESS=True (Linux without DISPLAY)")
        return True
    
    # Check the explicit HEADLESS env var
    result = bool_env(headless_env, default=False)
    print(f"[HEADLESS CHECK] → HEADLESS={result} (from env var)")
    return result


def bool_env(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


def int_env(value: str, default: int) -> int:
    if not value:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default
    except TypeError:
        return default
