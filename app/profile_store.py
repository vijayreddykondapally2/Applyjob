import json
from pathlib import Path
from typing import Any, Dict


PROFILE_PATH = Path("data/profile.json")


def ensure_profile_dir() -> None:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)


def load_profile() -> Dict[str, Any]:
    ensure_profile_dir()
    if not PROFILE_PATH.exists():
        return {}
    return json.loads(PROFILE_PATH.read_text())


def save_profile(profile: Dict[str, Any]) -> None:
    ensure_profile_dir()
    PROFILE_PATH.write_text(json.dumps(profile, indent=2))


def prompt_profile_if_missing() -> Dict[str, Any]:
    profile = load_profile()
    if profile:
        return profile

    print("\nNo saved profile found. Enter your baseline details once.\n")
    profile = {
        "full_name": input("Full name: ").strip(),
        "email": input("Email: ").strip(),
        "phone": input("Phone: ").strip(),
        "linkedin_url": input("LinkedIn URL: ").strip(),
        "work_authorization": input("Work authorization (e.g. Yes): ").strip(),
        "current_title": input("Current title: ").strip(),
        "years_experience": input("Years of experience: ").strip(),
        "school": input("School/College: ").strip(),
        "resume_path": input("Resume file path: ").strip(),
    }
    save_profile(profile)
    print("\nProfile saved to data/profile.json\n")
    return profile
