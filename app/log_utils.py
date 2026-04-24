"""
Structured logging for the ApplyJob UI dashboard.

Every log line is prefixed with a tag so the frontend can color-code them:

  [LINKEDIN]  [INFO]   Some message        → default white
  [LINKEDIN]  [OK]     Login successful     → green
  [LINKEDIN]  [FAIL]   Login timed out      → red
  [LINKEDIN]  [APPLY]  Submitted to Google  → bright cyan/purple
  [LINKEDIN]  [SKIP]   Already applied      → dim/grey
  [LINKEDIN]  [WAIT]   Waiting 3m...        → yellow
"""

import sys

# Portal tag widths for alignment
_PORTAL_WIDTH = 10


def _emit(portal: str, level: str, msg: str):
    """Format and print a structured log line."""
    tag = f"[{portal.upper():<{_PORTAL_WIDTH}}]"
    lvl = f"[{level:<5}]"
    line = f"{tag} {lvl}  {msg}"
    print(line, flush=True)


def log_info(portal: str, msg: str):
    _emit(portal, "INFO", msg)

def log_ok(portal: str, msg: str):
    _emit(portal, "OK", f"✅ {msg}")

def log_fail(portal: str, msg: str):
    _emit(portal, "FAIL", f"❌ {msg}")

def log_apply(portal: str, msg: str):
    _emit(portal, "APPLY", f"📝 {msg}")

def log_skip(portal: str, msg: str):
    _emit(portal, "SKIP", f"⏭️  {msg}")

def log_wait(portal: str, msg: str):
    _emit(portal, "WAIT", f"⏳ {msg}")

def log_step(portal: str, msg: str):
    """For sub-steps like 'Filling field X'."""
    _emit(portal, "STEP", f"   → {msg}")
