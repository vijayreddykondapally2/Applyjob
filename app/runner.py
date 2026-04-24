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
from app.profile_store import prompt_profile_if_missing, PROFILE_PATH
from app.utils import bool_env, int_env, should_run_headless
from app.log_utils import log_info, log_ok, log_fail, log_apply, log_skip, log_wait, log_step


def _split_csv(value: str) -> List[str]:
    return [x.strip() for x in (value or "").split(",") if x.strip()]


def _job_matches_keywords(job: JobCard, keywords: List[str]) -> bool:
    if not keywords:
        return True
    haystack = f"{job.title} {job.company} {job.location}".lower()
    return any(re.search(rf"\b{re.escape(k.lower())}\b", haystack) for k in keywords)


def run() -> None:
    # Environment variables are managed by the Task Manager

    email    = os.getenv("LINKEDIN_EMAIL", "").strip()
    password = os.getenv("LINKEDIN_PASSWORD", "").strip()
    keywords = os.getenv("JOB_KEYWORDS", "etl testing")
    location = os.getenv("JOB_LOCATION", "India")
    job_search_url        = os.getenv("JOB_SEARCH_URL", "").strip()
    max_jobs              = int_env(os.getenv("MAX_JOBS", "25"), 25)
    headless              = should_run_headless()
    auto_apply            = bool_env(os.getenv("AUTO_APPLY", "true"), default=True)
    keep_browser_open     = bool_env(os.getenv("KEEP_BROWSER_OPEN", "true"), default=True)
    manual_login_submit   = bool_env(os.getenv("MANUAL_LOGIN_SUBMIT", "true"), default=True)
    
    # CRITICAL: In headless mode, we MUST auto-submit. No human can click the button.
    if headless:
        manual_login_submit = False
        allow_manual_ckpt = False
    user_data_dir         = os.getenv("BROWSER_PROFILE_DIR", "data/browser-profile").strip() or "data/browser-profile"
    enable_ai             = bool_env(os.getenv("ENABLE_AI_ANSWERING", "true"), default=True)
    groq_api_key          = os.getenv("GROQ_API_KEY", "").strip()
    groq_model            = os.getenv("GROQ_MODEL", "llama-3.1-8b-instant").strip() or "llama-3.1-8b-instant"
    default_exp_years     = os.getenv("DEFAULT_EXPERIENCE_YEARS", "10").strip() or "10"
    default_notice_days   = os.getenv("DEFAULT_NOTICE_PERIOD_DAYS", "90").strip() or "90"
    portal_email          = os.getenv("PORTAL_EMAIL", "").strip()
    portal_password       = os.getenv("PORTAL_PASSWORD", "").strip()
    continuous_loop       = bool_env(os.getenv("CONTINUOUS_LOOP", "true"), default=True)
    loop_wait_seconds     = int_env(os.getenv("LOOP_WAIT_SECONDS", "30"), 30)
    max_cycles            = int_env(os.getenv("MAX_CYCLES", "0"), 0)
    easy_apply_only       = bool_env(os.getenv("EASY_APPLY_ONLY", "true"), default=True)
    ai_job_matching       = bool_env(os.getenv("AI_JOB_MATCHING", "true"), default=True)
    ai_job_max_select     = int_env(os.getenv("AI_JOB_MAX_SELECT", "10"), 10)
    strict_keyword_filter = bool_env(os.getenv("STRICT_KEYWORD_FILTER", "true"), default=True)
    strict_keywords       = _split_csv(os.getenv("STRICT_KEYWORDS", "etl,data quality,qa,testing"))
    max_pages             = int_env(os.getenv("MAX_PAGES", "5"), 5)

    # Use all keywords in a single search bar entry for better filtering
    search_keyword_list = [keywords.strip()]

    if not email or not password:
        raise RuntimeError("Set LINKEDIN_EMAIL and LINKEDIN_PASSWORD in .env before running.")

    # ── Load profile ───────────────────────────────────────────────────────────
    # This now automatically uses APPLYJOB_PROFILE_PATH if set
    profile = prompt_profile_if_missing()

    # Apply overrides from .env
    if portal_email:
        profile["portal_email"] = portal_email
    if portal_password:
        profile["portal_password"] = portal_password
    if not str(profile.get("years_experience") or "").strip():
        profile["years_experience"] = default_exp_years
    if not str(profile.get("notice_period_days") or "").strip():
        profile["notice_period_days"] = default_notice_days

    print(f"✓ Profile: {profile.get('full_name', 'unknown')} ({profile.get('email', '')})")

    # ── Build AI Answerer ──────────────────────────────────────────────────────
    ai_answerer = AIAnswerer(
        enabled=enable_ai,
        api_key=groq_api_key,
        model=groq_model,
        full_profile=profile,        # Pass the FULL profile so Groq has every field
    )
    if ai_answerer.enabled:
        print(f"✓ Groq AI enabled (model: {groq_model})")
    else:
        print("⚠ Groq AI disabled (no API key or ENABLE_AI_ANSWERING=false)")

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
            log_info("linkedin", "Starting login...")
            agent.login(
                allow_manual_checkpoint=allow_manual_ckpt,
                manual_timeout_seconds=manual_ckpt_timeout,
                manual_login_submit=manual_login_submit,
            )

            cycle = 1
            session_start_time = time.time()
            while True:
                kw = search_keyword_list[(cycle - 1) % len(search_keyword_list)]
                log_info("linkedin", f"Cycle {cycle} | Searching: '{kw}' in '{location}'")

                # ── Session Timeout Check ──────────────────────────────────
                if time.time() - session_start_time > 900:
                    log_wait("linkedin", "Session limit reached (15 min). Closing.")
                    break

                # ── Page 1: navigate to search URL ─────────────────────────
                ok = agent.navigate_to_search(
                    keywords=kw,
                    location=location,
                    easy_apply_only=easy_apply_only,
                    direct_search_url=job_search_url if job_search_url else "",
                )
                if not ok:
                    log_fail("linkedin", "Could not load search page. Retrying next cycle.")
                else:
                    page_num = 1
                    while page_num <= max_pages:
                        # ── Session Timeout Check ──────────────────────────────
                        if time.time() - session_start_time > 900:
                            print("\n⏳ Reached maximum session runtime of 15 minutes. Stopping pagination.")
                            break

                        print(f"\n  ── Page {page_num} ──────────────────────────────────────")

                        # Scrape cards from the CURRENT page (no navigation)
                        jobs: List[JobCard] = agent._scrape_cards(max_jobs)

                        if not jobs:
                            print(f"  No jobs on page {page_num}.")
                            break

                        # Strict keyword filter
                        if strict_keyword_filter:
                            before = len(jobs)
                            jobs = [j for j in jobs if _job_matches_keywords(j, strict_keywords)]
                            print(f"  Strict filter: {len(jobs)}/{before} kept (keywords: {strict_keywords}).")

                        # AI job selection
                        if jobs and ai_job_matching and ai_answerer.enabled:
                            job_payload = [
                                {"title": j.title, "company": j.company,
                                 "location": j.location, "url": j.url,
                                 "is_easy_apply": j.is_easy_apply}
                                for j in jobs
                            ]
                            selected_urls = ai_answerer.select_relevant_job_urls(
                                jobs=job_payload, profile=profile,
                                query=kw, max_select=ai_job_max_select,
                            )
                            if selected_urls:
                                sel_set = set(selected_urls)
                                before = len(jobs)
                                jobs = [j for j in jobs if j.url in sel_set]
                                print(f"  AI shortlist: {len(jobs)}/{before} jobs selected.")

                        if jobs:
                            # Process jobs one-by-one WITHOUT reloading the page
                            results = agent.process_jobs(jobs)
                            agent.save_results(results)
                            submitted = sum(1 for r in results if r.status == "submitted")
                            skipped   = sum(1 for r in results if r.status == "skipped")
                            log_ok("linkedin", f"Page {page_num}: {submitted} applied, {skipped} skipped")

                        # Navigate to next page (no find_jobs call → no page.goto())
                        if agent.click_next_page():
                            page_num += 1
                            print(f"  → Next page loaded.")
                        else:
                            print("  → Last page reached.")
                            break

                # ── Cycle control ──────────────────────────────────────────
                if not continuous_loop:
                    break
                if max_cycles > 0 and cycle >= max_cycles:
                    print(f"\nReached MAX_CYCLES={max_cycles}. Stopping.")
                    break

                print(f"\n  Cycle {cycle} complete. Waiting {loop_wait_seconds}s…")
                time.sleep(loop_wait_seconds)
                cycle += 1

        except KeyboardInterrupt:
            print("\nStopped by user (Ctrl+C).")
            stopped_by_user = True
        except Exception:
            if keep_browser_open and not headless:
                print("\nError occurred. Browser kept open for inspection.")
                input("Press Enter to close browser and exit…")
                prompted_close = True
            raise
        finally:
            if keep_browser_open and not headless and not prompted_close and not stopped_by_user:
                if os.getenv("PARALLEL_MODE") == "true":
                    print(f"\n[Parallel] LinkedIn task finished. Terminal input disabled in parallel mode. Browser will stay open for a few minutes...")
                    time.sleep(300) 
                else:
                    input("\nAll done. Press Enter to close browser")

