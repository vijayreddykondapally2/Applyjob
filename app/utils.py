import json
import os
import time
from pathlib import Path
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
                    }}).filter(x => x).slice(0, 300).join('\\n');
                }}""",
                selector
            )
        )
    except Exception:
        return ""


def bool_env(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def int_env(value: str, default: int) -> int:
    if not value:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default
