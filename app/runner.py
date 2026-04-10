import os
import time
import json
import re
from pathlib import Path
from typing import List

from dotenv import load_dotenv

from app.ai_answerer import AIAnswerer
from app.job_types import JobCard
from app.linkedin_agent import LinkedInApplyAgent
from app.profile_store import prompt_profile_if_missing

DEBUG_LOG_PATH = "/Users/apple/Projects/linkedin-apply-agent/.cursor/debug-786398.log"
DEBUG_SESSION_ID = "786398"


def _dbg(run_id: str, hypothesis_id: str, location: str, message: str, data: dict) -> None:
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
        Path(DEBUG_LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
        with open(DEBUG_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
    except Exception:
        pass


def _bool_env(value: str, default: bool = False) -> bool:
    if not value:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(value: str, default: int) -> int:
    if not value:
        return default
    try:
        return int(value.strip())
    except ValueError:
        return default


def _split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _job_matches_keywords(job: JobCard, keywords: List[str]) -> bool:
    if not keywords:
        return True
    haystack = f"{job.title} {job.company} {job.location}".lower()
    return any(re.search(rf"\b{re.escape(k.lower())}\b", haystack) for k in keywords)


def run() -> None:
    load_dotenv(override=True)

    email = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    keywords = os.getenv("JOB_KEYWORDS", "etl testing")
    location = os.getenv("JOB_LOCATION", "India")
    job_search_url = os.getenv("JOB_SEARCH_URL", "").strip()
    max_jobs = _int_env(os.getenv("MAX_JOBS", "25"), 25)
    headless = _bool_env(os.getenv("HEADLESS", "false"))
    auto_apply = _bool_env(os.getenv("AUTO_APPLY", "true"), default=True)
    keep_browser_open = _bool_env(os.getenv("KEEP_BROWSER_OPEN", "true"), default=True)
    allow_manual_checkpoint = _bool_env(os.getenv("ALLOW_MANUAL_CHECKPOINT", "true"), default=True)
    manual_checkpoint_timeout = _int_env(os.getenv("MANUAL_CHECKPOINT_TIMEOUT", "300"), 300)
    manual_login_submit = _bool_env(os.getenv("MANUAL_LOGIN_SUBMIT", "true"), default=True)
    user_data_dir = os.getenv("BROWSER_PROFILE_DIR", "data/browser-profile").strip() or "data/browser-profile"
    enable_ai_answering = _bool_env(os.getenv("ENABLE_AI_ANSWERING", "false"), default=False)
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant"
    default_experience_years = os.getenv("DEFAULT_EXPERIENCE_YEARS", "10").strip() or "10"
    default_notice_days = os.getenv("DEFAULT_NOTICE_PERIOD_DAYS", "90").strip() or "90"
    portal_email = os.getenv("PORTAL_EMAIL", "").strip()
    portal_password = os.getenv("PORTAL_PASSWORD", "").strip()
    continuous_loop = _bool_env(os.getenv("CONTINUOUS_LOOP", "true"), default=True)
    loop_wait_seconds = _int_env(os.getenv("LOOP_WAIT_SECONDS", "30"), 30)
    max_cycles = _int_env(os.getenv("MAX_CYCLES", "0"), 0)
    easy_apply_only = _bool_env(os.getenv("EASY_APPLY_ONLY", "false"), default=False)
    ai_job_matching = _bool_env(os.getenv("AI_JOB_MATCHING", "true"), default=True)
    ai_job_max_select = _int_env(os.getenv("AI_JOB_MAX_SELECT", "10"), 10)
    strict_keyword_filter = _bool_env(os.getenv("STRICT_KEYWORD_FILTER", "true"), default=True)
    strict_keywords = _split_csv(os.getenv("STRICT_KEYWORDS", "etl,data quality,qa,testing"))
    run_id = f"runner_{int(time.time())}"
    # region agent log
    _dbg(
        run_id,
        "H1",
        "runner.py:run:config_flags",
        "Runtime flags loaded for apply loop.",
        {
            "easy_apply_only_raw": os.getenv("EASY_APPLY_ONLY"),
            "easy_apply_only": easy_apply_only,
            "continuous_loop": continuous_loop,
            "max_cycles": max_cycles,
            "max_jobs": max_jobs,
        },
    )
    # endregion

    if not email or not password:
        raise RuntimeError("Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env before running.")

    profile = prompt_profile_if_missing()
    if portal_email:
        profile["portal_email"] = portal_email
    if portal_password:
        profile["portal_password"] = portal_password
    if not str(profile.get("years_experience") or "").strip():
        profile["years_experience"] = default_experience_years
    if not str(profile.get("notice_period_days") or "").strip():
        profile["notice_period_days"] = default_notice_days
    print(f"Loaded profile for: {profile.get('full_name', 'unknown')}")
    ai_answerer = AIAnswerer(
        enabled=enable_ai_answering,
        api_key=groq_api_key,
        model=groq_model,
    )

    prompted_close = False
    stopped_by_user = False
    with LinkedInApplyAgent(
        email=email,
        password=password,
        headless=headless,
        profile=profile,
        auto_apply=auto_apply,
        user_data_dir=user_data_dir,
        ai_answerer=ai_answerer,
    ) as agent:
        try:
            print("Logging into LinkedIn...")
            agent.login(
                allow_manual_checkpoint=allow_manual_checkpoint,
                manual_timeout_seconds=manual_checkpoint_timeout,
                manual_login_submit=manual_login_submit,
            )

            cycle = 1
            while True:
                print(f"\nCycle {cycle}: searching jobs for '{keywords}' in '{location}'...")
                jobs: List[JobCard] = agent.find_jobs(
                    keywords=keywords,
                    location=location,
                    max_jobs=max_jobs,
                    easy_apply_only=easy_apply_only,
                    direct_search_url=job_search_url,
                )
                # region agent log
                _dbg(
                    run_id,
                    "H2",
                    "runner.py:run:cycle_jobs_count",
                    "Cycle job list obtained.",
                    {
                        "cycle": cycle,
                        "jobs_count": len(jobs),
                        "easy_count": sum(1 for j in jobs if j.is_easy_apply),
                        "non_easy_count": sum(1 for j in jobs if not j.is_easy_apply),
                    },
                )
                # endregion
                if not jobs:
                    if easy_apply_only:
                        print("No Easy Apply jobs found in this cycle.")
                    else:
                        print("No jobs found in this cycle.")
                else:
                    if strict_keyword_filter:
                        before_count = len(jobs)
                        jobs = [j for j in jobs if _job_matches_keywords(j, strict_keywords)]
                        print(
                            f"Strict keyword filter kept {len(jobs)}/{before_count} jobs "
                            f"for keywords: {', '.join(strict_keywords)}"
                        )
                        if not jobs:
                            print("No jobs passed strict keyword filter in this cycle.")
                            if not continuous_loop:
                                break
                            if max_cycles > 0 and cycle >= max_cycles:
                                print(f"Reached MAX_CYCLES={max_cycles}. Stopping loop.")
                                break
                            print(f"No actionable button or cycle complete. Waiting {loop_wait_seconds}s before next search...")
                            time.sleep(loop_wait_seconds)
                            cycle += 1
                            continue
                    easy = sum(1 for j in jobs if j.is_easy_apply)
                    external = len(jobs) - easy
                    print(f"Discovered {len(jobs)} jobs -> Easy Apply: {easy}, External: {external}")
                    if easy_apply_only:
                        print("EASY_APPLY_ONLY enabled -> using LinkedIn easy-apply filter in search URL.")
                    if ai_job_matching and ai_answerer.enabled:
                        job_payload = [
                            {
                                "title": j.title,
                                "company": j.company,
                                "location": j.location,
                                "url": j.url,
                                "is_easy_apply": j.is_easy_apply,
                            }
                            for j in jobs
                        ]
                        selected_urls = ai_answerer.select_relevant_job_urls(
                            jobs=job_payload,
                            profile=profile,
                            query=keywords,
                            max_select=ai_job_max_select,
                        )
                        if selected_urls:
                            selected_set = set(selected_urls)
                            jobs = [j for j in jobs if j.url in selected_set]
                            print(f"AI shortlisted {len(jobs)} jobs for applying this cycle.")
                        else:
                            print("AI shortlist empty; continuing with discovered jobs.")
                    results = agent.process_jobs(jobs)
                    agent.save_results(results)
                    print("Cycle completed. Results appended to data/results.json")

                if not continuous_loop:
                    break
                if max_cycles > 0 and cycle >= max_cycles:
                    print(f"Reached MAX_CYCLES={max_cycles}. Stopping loop.")
                    break
                print(f"No actionable button or cycle complete. Waiting {loop_wait_seconds}s before next search...")
                time.sleep(loop_wait_seconds)
                cycle += 1
        except KeyboardInterrupt:
            print("\nStopped by user.")
            stopped_by_user = True
        except Exception:
            if keep_browser_open and not headless:
                print("\nRun hit an error. Browser kept open for inspection.")
                input("Press Enter after you are done reviewing to close browser...")
                prompted_close = True
            raise
        finally:
            if keep_browser_open and not headless and not prompted_close and not stopped_by_user:
                input("\nPress Enter to close browser and finish run...")
