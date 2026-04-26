from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict
import os


PROFILE_PATH = Path(os.getenv("APPLYJOB_PROFILE_PATH", "data/profile.json"))


def ensure_profile_dir() -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_profile() -> Dict[str, Any]:
    ensure_profile_dir()
    if not PROFILE_PATH.exists():
        return {}
    try:
        return json.loads(PROFILE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}


def save_profile(profile: Dict[str, Any]) -> None:
    ensure_profile_dir()
    PROFILE_PATH.write_text(json.dumps(profile, indent=2, ensure_ascii=False), encoding="utf-8")


def prompt_profile_if_missing() -> Dict[str, Any]:
    """Load profile.json; interactively create one only if it doesn't exist."""
    profile = load_profile()
    if profile:
        print(f"Profile loaded: {profile.get('full_name', 'unknown')} ({profile.get('email', '')})")
        return profile

    from app.utils import should_run_headless
    if should_run_headless():
        print("\nERROR: No saved profile found and cannot prompt in headless mode.")
        print("Please ensure your profile is configured in the web dashboard.")
        return {}

    print("\nNo saved profile found at data/profile.json.")
    print("You can copy your profile.json there and re-run, or fill in basics now.\n")
    profile = {
        "full_name": input("Full name: ").strip(),
        "email": input("Email: ").strip(),
        "phone": input("Phone: ").strip(),
        "linkedin_url": input("LinkedIn URL: ").strip(),
        "work_authorization": input("Work authorization (e.g. Yes): ").strip(),
        "current_title": input("Current title: ").strip(),
        "years_experience": input("Years of experience: ").strip(),
        "notice_period_days": input("Notice period (days): ").strip(),
        "school": input("School/College: ").strip(),
        "resume_path": input("Resume file path: ").strip(),
        "current_ctc": input("Current CTC: ").strip(),
        "expected_ctc": input("Expected CTC: ").strip(),
        "current_city": input("Current city: ").strip(),
    }
    save_profile(profile)
    print("\nProfile saved to data/profile.json\n")
    return profile
