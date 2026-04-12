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
from app.utils import debug_log, bool_env, int_env




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
    max_jobs = int_env(os.getenv("MAX_JOBS", "25"), 25)
    headless = bool_env(os.getenv("HEADLESS", "false"))
    auto_apply = bool_env(os.getenv("AUTO_APPLY", "true"), default=True)
    keep_browser_open = bool_env(os.getenv("KEEP_BROWSER_OPEN", "true"), default=True)
    allow_manual_checkpoint = bool_env(os.getenv("ALLOW_MANUAL_CHECKPOINT", "true"), default=True)
    manual_checkpoint_timeout = int_env(os.getenv("MANUAL_CHECKPOINT_TIMEOUT", "300"), 300)
    manual_login_submit = bool_env(os.getenv("MANUAL_LOGIN_SUBMIT", "true"), default=True)
    user_data_dir = os.getenv("BROWSER_PROFILE_DIR", "data/browser-profile").strip() or "data/browser-profile"
    enable_ai_answering = bool_env(os.getenv("ENABLE_AI_ANSWERING", "false"), default=False)
    groq_api_key = os.getenv("GROQ_API_KEY", "").strip()
    groq_model = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant"
    default_experience_years = os.getenv("DEFAULT_EXPERIENCE_YEARS", "10").strip() or "10"
    default_notice_days = os.getenv("DEFAULT_NOTICE_PERIOD_DAYS", "90").strip() or "90"
    portal_email = os.getenv("PORTAL_EMAIL", "").strip()
    portal_password = os.getenv("PORTAL_PASSWORD", "").strip()
    continuous_loop = bool_env(os.getenv("CONTINUOUS_LOOP", "true"), default=True)
    loop_wait_seconds = int_env(os.getenv("LOOP_WAIT_SECONDS", "30"), 30)
    max_cycles = int_env(os.getenv("MAX_CYCLES", "0"), 0)
    easy_apply_only = bool_env(os.getenv("EASY_APPLY_ONLY", "false"), default=False)
    ai_job_matching = bool_env(os.getenv("AI_JOB_MATCHING", "true"), default=True)
    ai_job_max_select = int_env(os.getenv("AI_JOB_MAX_SELECT", "10"), 10)
    strict_keyword_filter = bool_env(os.getenv("STRICT_KEYWORD_FILTER", "true"), default=True)
    strict_keywords = _split_csv(os.getenv("STRICT_KEYWORDS", "etl,data quality,qa,testing"))
    
    # Pre-split search keywords for rotation if search fails
    search_keyword_list = _split_csv(keywords)
    if not search_keyword_list:
        search_keyword_list = [keywords.strip()]
    
    run_id = f"runner_{int(time.time())}"
    # region agent log
    debug_log(
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
                # Use keyword rotation to avoid getting stuck with 0 results for complex queries
                current_search_keyword = search_keyword_list[(cycle - 1) % len(search_keyword_list)]
                print(f"\n[{time.strftime('%H:%M:%S')}] Cycle {cycle}: searching jobs for '{current_search_keyword}' in '{location}'...")
                
                # Pagination loop for the current search
                page_num = 1
                max_pages = 10 # Reasonable limit per keyword
                
                while page_num <= max_pages:
                    print(f"[{time.strftime('%H:%M:%S')}] Processing Page {page_num}...")
                    jobs: List[JobCard] = agent.find_jobs(
                        keywords=current_search_keyword,
                        location=location,
                        max_jobs=max_jobs,
                        easy_apply_only=easy_apply_only,
                        direct_search_url=job_search_url,
                    )
                    
                    # region agent log
                    debug_log(
                        run_id,
                        "H2",
                        "runner.py:run:cycle_jobs_count",
                        "Cycle job list obtained.",
                        {
                            "cycle": cycle,
                            "page": page_num,
                            "jobs_count": len(jobs),
                            "easy_count": sum(1 for j in jobs if j.is_easy_apply),
                            "non_easy_count": sum(1 for j in jobs if not j.is_easy_apply),
                        },
                    )
                    # endregion
                    
                    if not jobs:
                        print(f"No jobs found on Page {page_num} using standard or AI selectors.")
                        break
                    
                    if strict_keyword_filter:
                        before_count = len(jobs)
                        jobs = [j for j in jobs if _job_matches_keywords(j, strict_keywords)]
                        if not jobs:
                            print(f"Page {page_num}: All {before_count} jobs were filtered out by STRICT_KEYWORDS.")
                        else:
                            print(
                                f"Page {page_num}: Strict keyword filter kept {len(jobs)}/{before_count} jobs "
                                f"for keywords: {', '.join(strict_keywords)}"
                            )
                    
                    if jobs:
                        # AI selection if enabled
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
                                query=current_search_keyword,
                                max_select=ai_job_max_select,
                            )
                            if selected_urls:
                                selected_set = set(selected_urls)
                                before_ai_count = len(jobs)
                                jobs = [j for j in jobs if j.url in selected_set]
                                print(f"AI shortlisted {len(jobs)}/{before_ai_count} jobs for applying.")
                            else:
                                print("AI shortlist returned no results or was disabled; continuing with discovered jobs.")
                        
                        results = agent.process_jobs(jobs)
                        agent.save_results(results)
                        print(f"Page {page_num} processing completed.")
                    
                    # Try to go to next page
                    if agent.click_next_page():
                        page_num += 1
                        print("Navigating to next page...")
                        time.sleep(2) # Brief wait for page load
                    else:
                        print("Reached last page.")
                        break

                if not continuous_loop:
                    break
                if max_cycles > 0 and cycle >= max_cycles:
                    print(f"Reached MAX_CYCLES={max_cycles}. Stopping loop.")
                    break
                print(f"Cycle {cycle} complete. Waiting {loop_wait_seconds}s before next cycle...")
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
