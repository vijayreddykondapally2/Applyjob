import os
from typing import List

from dotenv import load_dotenv

from app.job_types import JobCard
from app.linkedin_agent import LinkedInApplyAgent
from app.profile_store import prompt_profile_if_missing


def _bool_env(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def run() -> None:
    load_dotenv()

    email = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    keywords = os.getenv("JOB_KEYWORDS", "etl testing")
    location = os.getenv("JOB_LOCATION", "India")
    max_jobs = int(os.getenv("MAX_JOBS", "25"))
    headless = _bool_env(os.getenv("HEADLESS", "false"))

    if not email or not password:
        raise RuntimeError("Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env before running.")

    profile = prompt_profile_if_missing()
    print(f"Loaded profile for: {profile.get('full_name', 'unknown')}")

    with LinkedInApplyAgent(email=email, password=password, headless=headless) as agent:
        print("Logging into LinkedIn...")
        agent.login()

        print(f"Searching jobs for '{keywords}' in '{location}'...")
        jobs: List[JobCard] = agent.find_jobs(keywords=keywords, location=location, max_jobs=max_jobs)
        if not jobs:
            print("No jobs found for this query.")
            return

        easy = sum(1 for j in jobs if j.is_easy_apply)
        external = len(jobs) - easy
        print(f"Discovered {len(jobs)} jobs -> Easy Apply: {easy}, External: {external}")

        results = agent.process_jobs(jobs)
        agent.save_results(results)
        print("Run completed. Results saved to data/results.json")
