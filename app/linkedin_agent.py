from __future__ import annotations

import json
import os
import time
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import quote_plus

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from app.ai_answerer import AIAnswerer
from app.job_types import ApplyResult, JobCard
from app.question_memory import QuestionMemory
from app.utils import debug_log, get_compressed_dom




class LinkedInApplyAgent:
    def __init__(
        self,
        email: str,
        password: str,
        headless: bool = False,
        profile: Dict[str, Any] | None = None,
        auto_apply: bool = True,
        user_data_dir: str = "data/browser-profile",
        ai_answerer: AIAnswerer | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.headless = headless
        self.profile = profile or {}
        self.auto_apply = auto_apply
        self.user_data_dir = user_data_dir
        self.ai_answerer = ai_answerer
        self.question_memory = QuestionMemory()
        self.default_years_experience = str(self.profile.get("years_experience") or "10").strip() or "10"
        self.default_notice_days = str(self.profile.get("notice_period_days") or "90").strip() or "90"
        self.playwright: Playwright | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    def __enter__(self) -> "LinkedInApplyAgent":
        self.playwright = sync_playwright().start()
        os.makedirs(self.user_data_dir, exist_ok=True)
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=self.user_data_dir,
            headless=self.headless,
        )
        self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.context:
            try:
                self.context.close()
            except Exception:
                # Ignore close errors if browser was already interrupted.
                pass
        if self.playwright:
            self.playwright.stop()

    def login(
        self,
        allow_manual_checkpoint: bool = True,
        manual_timeout_seconds: int = 180,
        manual_login_submit: bool = True,
    ) -> None:
        assert self.page is not None
        # Reuse existing authenticated session when possible.
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)
        if "/feed" in self.page.url.lower():
            print("Reused existing LinkedIn session.")
            return

        self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        self.page.fill("#username", self.email)
        self.page.fill("#password", self.password)
        if manual_login_submit:
            print(
                "\nCredentials filled on login page.\n"
                "Please complete any anti-bot check and click Sign in manually.\n"
                "Waiting for successful login...\n"
            )
            self._wait_for_login(timeout_seconds=manual_timeout_seconds)
            return

        self.page.click('button[type="submit"]')
        self.page.wait_for_timeout(4000)
        if "feed" in self.page.url:
            return

        if "checkpoint" in self.page.url or "challenge" in self.page.url:
            if not allow_manual_checkpoint:
                raise RuntimeError("Checkpoint/captcha detected. Enable manual checkpoint handling.")
            print(
                "\nLinkedIn checkpoint/captcha detected.\n"
                "Complete verification in the opened browser window.\n"
                "Waiting for successful login...\n"
            )
            self._wait_for_login(timeout_seconds=manual_timeout_seconds)
            return

        # Fallback for unexpected redirects after submit.
        if allow_manual_checkpoint:
            print(
                "\nLogin did not reach feed automatically.\n"
                "Please complete login manually in browser if prompted.\n"
            )
            self._wait_for_login(timeout_seconds=manual_timeout_seconds)
            return

        raise RuntimeError("LinkedIn login failed; feed not reached after submit.")

    def _wait_for_login(self, timeout_seconds: int = 180) -> None:
        assert self.page is not None
        unlimited_wait = timeout_seconds <= 0
        deadline_ms = timeout_seconds * 1000
        waited_ms = 0
        step = 2000

        while unlimited_wait or waited_ms < deadline_ms:
            current_url = self.page.url.lower()
            # Consider login successful when user lands in authenticated LinkedIn areas.
            if "/feed" in current_url or "/jobs" in current_url or "/mynetwork" in current_url:
                print("Manual login completed.")
                return
            self.page.wait_for_timeout(step)
            waited_ms += step
            if waited_ms and waited_ms % 30000 == 0:
                waited_s = waited_ms // 1000
                print(f"Still waiting for manual login... ({waited_s}s elapsed)")
        raise RuntimeError(
            f"Manual login timeout after {timeout_seconds}s. "
            "Increase MANUAL_CHECKPOINT_TIMEOUT or set it to 0 for unlimited wait."
        )

    def find_jobs(
        self,
        keywords: str,
        location: str,
        max_jobs: int = 25,
        easy_apply_only: bool = False,
        direct_search_url: str = "",
    ) -> List[JobCard]:
        assert self.page is not None
        run_id = f"find_jobs_{int(time.time())}"
        print(f"TRACE: Entering find_jobs (keywords='{keywords}', location='{location}')")
        if direct_search_url.strip():
            search_url = direct_search_url.strip()
            if easy_apply_only and "f_AL=true" not in search_url:
                joiner = "&" if "?" in search_url else "?"
                search_url = f"{search_url}{joiner}f_AL=true"
        else:
            search_url = (
                "https://www.linkedin.com/jobs/search/"
                f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}"
            )
            if easy_apply_only:
                search_url += "&f_AL=true"
        # region agent log
        debug_log(
            run_id,
            "H1",
            "linkedin_agent.py:find_jobs:search_url",
            "Prepared LinkedIn jobs search URL.",
            {"easy_apply_only": easy_apply_only, "has_easy_filter_param": "f_AL=true" in search_url},
        )
        # endregion
        try:
            print(f"TRACE: Navigating to {search_url}")
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
            print("TRACE: Navigation completed.")
            
            # SESSION CHECK: Check if we are at a login wall or checkpoint
            curr_url = self.page.url.lower()
            if "login" in curr_url or "checkpoint" in curr_url or "authwall" in curr_url:
                print("!!! EXPIRED SESSION / AUTHWALL DETECTED !!!")
                print(f"The bot has been redirected to: {curr_url}")
                print("Please log in manually in the browser window or check your credentials.")
                # Save diagnostic screenshot immediately
                self.page.screenshot(path="data/authwall_detected.png")
                return []
            
            # Save immediate diagnostic snapshot
            try:
                diag_dom = get_compressed_dom(self.page, "body")
                Path("data/initial_search_load.json").write_text(json.dumps({"url": curr_url, "dom": diag_dom}, indent=2))
                print("TRACE: Saved initial search load snapshot.")
            except Exception:
                pass

        except Exception as e:
            print(f"TRACE: Navigation failed: {str(e)}")
            # region agent log
            debug_log(
                run_id,
                "H4",
                "linkedin_agent.py:find_jobs:goto_exception",
                "LinkedIn search navigation failed.",
                {"easy_apply_only": easy_apply_only},
            )
            # endregion
            # Keep loop alive if LinkedIn intermittently stalls.
            return []

        # 1. Check for Security Check / Verification
        security_selectors = ["#captcha-internal", ".challange-header", "iframe[src*='security']", "h1:has-text('Quick security check')"]
        for sel in security_selectors:
            if self.page.locator(sel).count() > 0:
                print("!!! SECURITY CHECK DETECTED !!!")
                print("Please complete the verification in the browser window manually.")
                self.page.wait_for_timeout(10000) # Give user a moment to see it

        # 2. Wait for either job cards to appear OR a "No results" indicator
        results_found = False
        wait_start = time.time()
        while time.time() - wait_start < 15: # 15s max wait for loading
            # Check for common skeleton loader or loading spinner
            if self.page.locator(".jobs-search-results-list__skeleton, .artdeco-loader").count() == 0:
                # Skeletons gone, check if results arrived
                if self.page.locator("li[data-job-id], li[data-occludable-job-id], .job-card-container").count() > 0:
                    results_found = True
                    break
                # Check for "No results" text
                if self.page.locator(":has-text('No matching jobs found'), :has-text('No jobs found')").count() > 0:
                    break
            self.page.wait_for_timeout(1000)
        print(f"TRACE: Wait loop finished. Results found indicator: {results_found}")
        
        if easy_apply_only:
            print("TRACE: Applying filters...")
            self._apply_top_jobs_and_easy_apply_filters()
            self.page.wait_for_timeout(1500)

        print("TRACE: Starting job card discovery...")
        # Updated selectors to handle both guest and logged-in UI patterns.
        possible_search_list_selectors = [
            "li[data-job-id]",
            "div[data-job-id]",
            "li[data-occludable-job-id]",
            "div[data-occludable-job-id]",
            ".jobs-search-results-list__list-item",
            "ul.jobs-search__results-list li",
            "li.jobs-search-results__list-item",
            "div.job-card-container",
            "div.base-card",
        ]
        
        # Priority: if currentJobId is in URL, try to find that specific card first
        target_job_id = None
        if "currentJobId=" in search_url:
            parsed = urlparse(search_url)
            qs = parse_qs(parsed.query)
            target_job_id = qs.get("currentJobId", [None])[0]
        
        if target_job_id:
            possible_search_list_selectors.insert(0, f"[data-job-id='{target_job_id}']")
            possible_search_list_selectors.insert(1, f"[data-occludable-job-id='{target_job_id}']")

        # Active Scrolling to find target IDs or populate list
        results_container = self.page.locator(".jobs-search-results-list, .scaffold-layout__list").first
        if results_container.count() > 0:
            # Scroll aggressively to trigger lazy loading of at least 25 jobs
            print("TRACE: Scrolling results sidebar for lazy loading...")
            for _ in range(6): # Increased from 3 to 6
                results_container.evaluate("el => el.scrollTop += 1200") # Increased distance
                self.page.wait_for_timeout(600)
            
            # If we have a target ID, specifically try to find it
            if target_job_id:
                target_sel = f"[data-job-id='{target_job_id}'], [data-occludable-job-id='{target_job_id}']"
                for _ in range(8): # Increased from 5 to 8
                    if self.page.locator(target_sel).count() > 0:
                        break
                    results_container.evaluate("el => el.scrollTop += 1500")
                    self.page.wait_for_timeout(800)

        cards = None
        for selector in possible_search_list_selectors:
            try:
                cards = self.page.locator(selector)
                if cards.count() > 0:
                    # scroll first card into view to be safe
                    cards.first.scroll_into_view_if_needed()
                    break
            except Exception:
                continue
        
        if not cards or cards.count() == 0:
            # Fallback: ask AI to find selectors for job cards if standard ones fail.
            if self.ai_answerer and self.ai_answerer.enabled:
                dom = get_compressed_dom(self.page, ".scaffold-layout__list, .jobs-search-results-list, body")
                ai_selectors = self.ai_answerer.analyze_dom_for_elements(dom, "Find the CSS selector for a single job card/item in the search results list.")
                for selector in ai_selectors:
                    try:
                        cards = self.page.locator(selector)
                        if cards.count() > 0:
                            print(f"AI discovered job card selector: {selector}")
                            # region agent log
                            debug_log(
                                run_id,
                                "H5",
                                "linkedin_agent.py:find_jobs:ai_selector_success",
                                "AI successfully identified Job Card selector.",
                                {"selector": selector, "count": cards.count()},
                            )
                            # endregion
                            break
                    except Exception:
                        continue
            else:
                # If no AI answerer or disabled, still log the issue
                print("DEBUG: AI Answerer disabled or missing; skipping AI selector discovery.")

        if not cards or cards.count() == 0:
            # Diagnostic Dump: save the DOM if search failed to find any cards.
            try:
                diag_dom = get_compressed_dom(self.page, "body")
                diag_path = Path("data/diagnostic_dom.json")
                diag_path.parent.mkdir(parents=True, exist_ok=True)
                diag_path.write_text(json.dumps({"url": self.page.url, "dom": diag_dom}, indent=2), encoding="utf-8")
                
                # Save screenshot for visual debugging
                screenshot_path = Path("data/diagnostic_screenshot.png")
                self.page.screenshot(path=str(screenshot_path))
                
                print(f"DEBUG: No jobs found. Diagnostics saved to {diag_path} and {screenshot_path}")
            except Exception:
                pass
            return []
        count = min(cards.count(), max_jobs)
        jobs: List[JobCard] = []

        for i in range(count):
            card = cards.nth(i)
            # Try multiple common selectors for title and company within the card
            title_loc = card.locator("h3, .job-card-list__title, .base-search-card__title, .artdeco-entity-lockup__title, span.strong").first
            company_loc = card.locator("h4, .job-card-container__company-name, .base-search-card__subtitle, .artdeco-entity-lockup__subtitle").first
            location_loc = card.locator(".job-search-card__location, .job-card-container__metadata-item, .base-search-card__metadata, .artdeco-entity-lockup__caption").first
            
            title = (title_loc.inner_text(timeout=1500) if title_loc.count() > 0 else "").strip()
            company = (company_loc.inner_text(timeout=1500) if company_loc.count() > 0 else "").strip()
            loc = (location_loc.inner_text(timeout=1500) if location_loc.count() > 0 else "").strip()
            
            # Find the most relevant Link (prioritizing title links)
            url = ""
            link_locators = [
                card.locator("a[href*='/jobs/view/']"),
                card.locator("a.job-card-list__title"),
                card.locator("a.base-card__full-link"),
                card.locator("a.job-card-container__link"),
                card.locator("a").first,
            ]
            for link_loc in link_locators:
                if link_loc.count() > 0:
                    url = link_loc.first.get_attribute("href") or ""
                    if url:
                        break

            easy_badge = card.locator("span:has-text('Easy Apply'), .job-card-container__apply-method, .job-card-list__footer-item").count() > 0
            # Double check already applied
            card_text = (card.inner_text(timeout=1500) or "").lower()
            already_applied = "applied" in card_text or card.locator(".job-card-container__footer-item:has-text('Applied'), .job-card-list__footer-item:has-text('Applied')").count() > 0
            
            if url and title:
                ukey = f"{title}_{company}_{loc}".lower().replace(" ", "_").strip()
                jobs.append(
                    JobCard(
                        title=title,
                        company=company,
                        location=loc,
                        url=url.split("?")[0],
                        is_easy_apply=easy_badge,
                        is_already_applied=already_applied,
                        unique_key=ukey,
                    )
                )

        if not jobs and cards.count() > 0:
            # If we matched cards but couldn't parse any jobs, save diagnostics
            try:
                diag_path = Path("data/diagnostic_dom.json")
                diag_dom = get_compressed_dom(self.page, "body")
                diag_path.write_text(json.dumps({"url": self.page.url, "dom": diag_dom, "cards_count": cards.count()}, indent=2), encoding="utf-8")
                self.page.screenshot(path="data/diagnostic_screenshot.png")
                print(f"DEBUG: Matched {cards.count()} elements but failed to parse job details. Diagnostics saved.")
            except Exception:
                pass

        # region agent log
        debug_log(
            run_id,
            "H2",
            "linkedin_agent.py:find_jobs:results_summary",
            "Collected jobs from results list.",
            {
                "total_jobs": len(jobs),
                "easy_jobs": sum(1 for j in jobs if j.is_easy_apply),
                "non_easy_jobs": sum(1 for j in jobs if not j.is_easy_apply),
            },
        )
        # endregion
        return jobs

    def _apply_top_jobs_and_easy_apply_filters(self) -> None:
        """
        Clicks top Jobs filter and Easy Apply chip under search bar.
        Skips if already active in URL to save time and avoid toggling.
        """
        assert self.page is not None
        
        # If the URL already shows filters are active, we can skip the heavy UI clicking.
        current_url = self.page.url.lower()
        if "f_al=true" in current_url:
            return

        jobs_candidates = [
            "div.search-reusables__filters-bar button:has-text('Jobs')",
            "div.search-reusables__filters-bar [role='button']:has-text('Jobs')",
            "button:has-text('Jobs')",
            "[role='button']:has-text('Jobs')",
        ]
        easy_candidates = [
            "div.search-reusables__filters-bar button:has-text('Easy Apply')",
            "div.search-reusables__filters-bar [role='button']:has-text('Easy Apply')",
            "button:has-text('Easy Apply')",
            "[role='button']:has-text('Easy Apply')",
            "label:has-text('Easy Apply')",
        ]

        # Try selecting top Jobs chip first (best effort).
        for selector in jobs_candidates:
            try:
                btn = self.page.locator(selector).first
                if btn.count() == 0:
                    continue
                btn.scroll_into_view_if_needed()
                btn.click(timeout=1500)
                self.page.wait_for_timeout(400)
                break
            except Exception:
                continue

        # Then force Easy Apply chip.
        clicked_easy = False
        for selector in easy_candidates:
            try:
                btn = self.page.locator(selector).first
                if btn.count() == 0:
                    continue
                btn.scroll_into_view_if_needed()
                
                # Check if already active before clicking
                is_active = btn.evaluate(
                    """el => {
                        const pressed = (el.getAttribute('aria-pressed') || '').toLowerCase() === 'true';
                        const checked = (el.getAttribute('aria-checked') || '').toLowerCase() === 'true';
                        const cls = el.className.toLowerCase();
                        return pressed || checked || cls.includes('selected') || cls.includes('active');
                    }"""
                )
                if is_active:
                    clicked_easy = True
                    break

                try:
                    btn.click(timeout=2000)
                except Exception:
                    btn.click(timeout=2000, force=True)
                clicked_easy = True
                break
            except Exception:
                continue

        # JS fallback: click exact top filter chip by visible text.
        if not clicked_easy:
            try:
                clicked_easy = bool(
                    self.page.evaluate(
                        """() => {
                          const root = document.querySelector('.search-reusables__filters-bar') || document;
                          const nodes = Array.from(root.querySelectorAll('button,[role="button"],label'));
                          const hit = nodes.find(n => (n.textContent || '').trim().toLowerCase() === 'easy apply');
                          if (!hit) return false;
                          hit.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true }));
                          return true;
                        }"""
                    )
                )
            except Exception:
                clicked_easy = False

        # Wait until UI reflects easy-apply filter.
        if clicked_easy:
            try:
                self.page.wait_for_function(
                    """() => {
                      const root = document.querySelector('.search-reusables__filters-bar') || document;
                      const nodes = Array.from(root.querySelectorAll('button,[role="button"],label'));
                      const chip = nodes.find(n => (n.textContent || '').toLowerCase().includes('easy apply'));
                      if (!chip) return false;
                      const pressed = (chip.getAttribute('aria-pressed') || '').toLowerCase();
                      const selected = (chip.getAttribute('aria-checked') || '').toLowerCase();
                      const cls = (chip.className || '').toLowerCase();
                      return pressed === 'true' || selected === 'true' || cls.includes('selected') || cls.includes('active');
                    }""",
                    timeout=6000,
                )
            except Exception:
                pass

        if not clicked_easy:
            # Fallback: hit URL filter even when UI chip is not interactable.
            if "f_AL=true" not in self.page.url:
                joiner = "&" if "?" in self.page.url else "?"
                self.page.goto(f"{self.page.url}{joiner}f_AL=true", wait_until="domcontentloaded")

    def _confirm(self, job: JobCard) -> bool:
        if self.auto_apply:
            return True
        print(
            f"\nJob: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
            f"Type: {'Easy Apply' if job.is_easy_apply else 'External Apply'}\nURL: {job.url}\n"
        )
        return input("Apply now? (yes/no): ").strip().lower() == "yes"

    def _try_easy_apply(self, job: JobCard) -> ApplyResult:
        assert self.page is not None
        if self._is_job_already_applied_page():
            return ApplyResult(job.title, job.company, job.url, "easy_apply", "skipped", "Already applied (detail page).")

        easy_btn = self.page.locator("button:has-text('Easy Apply')")
        if easy_btn.count() == 0 and self.ai_answerer and self.ai_answerer.enabled:
            dom = get_compressed_dom(self.page, ".jobs-search__job-details, .jobs-details, body")
            ai_selectors = self.ai_answerer.analyze_dom_for_elements(dom, "Find the 'Easy Apply' button.")
            for selector in ai_selectors:
                try:
                    easy_btn = self.page.locator(selector)
                    if easy_btn.count() > 0:
                        break
                except Exception:
                    continue

        if easy_btn.count() == 0:
            return ApplyResult(job.title, job.company, job.url, "easy_apply", "skipped", "Button not found")

        easy_btn.first.click(timeout=5000)
        self.page.wait_for_timeout(1200)
        
        # Scope form interactions to the dialog if possible
        dialog_selector = "div[role='dialog'], .jobs-easy-apply-modal"
        filled = self._autofill_external_form(scope_selector=dialog_selector)
        progressed = self._process_easy_apply_dialog(scope_selector=dialog_selector)
        if progressed == "submitted":
            return ApplyResult(
                job.title,
                job.company,
                job.url,
                "easy_apply",
                "submitted",
                f"Easy Apply submitted. Autofilled {filled} field(s).",
            )
        if progressed == "blocked":
            return ApplyResult(
                job.title,
                job.company,
                job.url,
                "easy_apply",
                "needs_user_input",
                "Easy Apply paused: required field needed manual input.",
            )
        return ApplyResult(
            job.title,
            job.company,
            job.url,
            "easy_apply",
            "needs_manual_review",
            f"Easy Apply dialog opened; auto-progress attempted, autofilled {filled} field(s).",
        )

    def _try_external_apply(self, job: JobCard) -> ApplyResult:
        assert self.page is not None
        apply_link = self.page.locator("a:has-text('Apply'), a:has-text('Apply on company website')")
        if apply_link.count() == 0:
            return ApplyResult(job.title, job.company, job.url, "external", "skipped", "External apply link not found")
        href = apply_link.first.get_attribute("href")
        if not href:
            return ApplyResult(job.title, job.company, job.url, "external", "failed", "Missing external link href")

        self.page.goto(href, wait_until="domcontentloaded")
        filled_count = self._autofill_external_form()
        required_missing = self._required_fields_missing_count()
        if required_missing > 0:
            print(f"External form still has {required_missing} required field(s) missing.")
            self._prompt_manual_required_fields()
        print(
            f"External site opened for '{job.company}'. "
            f"Auto-filled {filled_count} field(s). Review and continue manually."
        )
        return ApplyResult(
            job.title,
            job.company,
            href,
            "external",
            "needs_manual_review",
            f"Company portal opened; auto-filled {filled_count} field(s); finish manually.",
        )

    def _detect_apply_action(self, scope_selector_override: str = "") -> str:
        assert self.page is not None
        # Scoped container selectors for the right-hand details pane
        container_selectors = [
            ".jobs-search-results-list__detail-container",
            ".jobs-details",
            ".job-view-layout",
            ".scaffold-layout__detail",
            "#main"
        ]
        
        container = self.page
        found_container = "body"
        if not scope_selector_override:
            for sel in container_selectors:
                if self.page.locator(sel).count() > 0:
                    container = self.page.locator(sel).first
                    found_container = sel
                    break
        else:
            container = self.page.locator(scope_selector_override).first
            found_container = scope_selector_override

        print(f"TRACE: Detecting action within container: {found_container}")
        
        # Check for Applied status explicitly within this container
        pane_text = (container.inner_text(timeout=2000) or "").lower()
        if "already applied" in pane_text or "applied on" in pane_text:
            print("TRACE: 'Applied' status detected in details pane.")
            return "already"
        
        # Check for Easy Apply
        easy_candidates = [
            "button:has-text('Easy Apply')",
            "button.jobs-apply-button",
            "button[aria-label*='Easy Apply']",
            ".jobs-apply-button--top-card button"
        ]
        for sel in easy_candidates:
            if container.locator(sel).count() > 0:
                print(f"TRACE: 'Easy Apply' action detected via {sel}")
                return "easy"
                
        # Check for External Apply
        external_candidates = [
            "a:has-text('Apply')",
            "a:has-text('Apply on company website')",
            "button:has-text('Apply')",
            ".jobs-apply-button:not(:has-text('Easy Apply'))"
        ]
        for sel in external_candidates:
            if container.locator(sel).count() > 0:
                print(f"TRACE: 'External Apply' action detected via {sel}")
                return "external"
        
        return "none"

    def _apply_from_job_page(self, job: JobCard) -> ApplyResult:
        # Wait a moment for the specific job's details to be dominant
        self.page.wait_for_timeout(1000)
        action = self._detect_apply_action()
        # region agent log
        debug_log(
            f"apply_{int(time.time())}",
            "H3",
            "linkedin_agent.py:_apply_from_job_page:action_detected",
            "Detected action type on job detail page.",
            {"company": job.company[:80], "title": job.title[:120], "action": action},
        )
        # endregion
        if action == "already":
            return ApplyResult(job.title, job.company, job.url, "n/a", "skipped", "Already applied (detail page).")
        if action == "easy":
            return self._try_easy_apply(job)
        if action == "external":
            return self._try_external_apply(job)
        return ApplyResult(job.title, job.company, job.url, "n/a", "skipped", "No Easy Apply/Apply button found.")

    def _is_pane_showing_job(self, title: str, company: str) -> bool:
        """
        Verify that the right-hand detail pane is actually showing the job we just clicked.
        """
        assert self.page is not None
        pane = self.page.locator(".jobs-details, .jobs-search-results-list__detail-container, .scaffold-layout__detail").first
        if pane.count() == 0:
            return False
        text = (pane.inner_text() or "").lower()
        # Check if Title or Company is present in the pane
        return title.lower() in text or company.lower() in text

    def process_jobs(self, discovered_jobs: List[JobCard]) -> List[ApplyResult]:
        """
        New 'Direct Sidebar Crawler' logic: 
        Iterates through the visible sidebar elements and applies one-by-one.
        """
        assert self.page is not None
        results: List[ApplyResult] = []
        
        # 1. Reset history cache for this run if we want to be fresh
        # (Keeping history for now but the user can delete the file)
        historical = self._load_historical_results()
        historical_keys = {(r.get("unique_key") or "").strip().lower() for r in historical if r.get("unique_key")}

        # 2. Locate the cards in the sidebar
        sidebar_items = self.page.locator("li.jobs-search-results__list-item").all()
        print(f"TRACE: Sidebar Crawler started. Found {len(sidebar_items)} visible items.")
        
        for idx, card in enumerate(sidebar_items):
            try:
                card.scroll_into_view_if_needed()
                
                # Extract essential info from card to check history
                title_el = card.locator("a.job-card-list__title, a.job-card-container__link").first
                company_el = card.locator(".job-card-container__primary-description, .job-card-list__entity-lockup-title").first
                
                title = (title_el.inner_text() or "Unknown Title").split("\n")[0].strip()
                company = (company_el.inner_text() or "Unknown Company").split("\n")[0].strip()
                loc = "India" # Fallback
                
                ukey = f"{title}_{company}_{loc}".lower().replace(" ", "_").strip()
                
                print(f"TRACE: Crawler processing card #{idx+1}: {title} @ {company}")
                
                if ukey in historical_keys:
                    print(f"[{company}] Skip: Already processed '{title}' previously.")
                    continue
                
                # 3. CLICK THE CARD (Title Link)
                print(f"TRACE: Clicking title link for {title}...")
                title_el.click(force=True)
                
                # 4. WAIT FOR DETAILS PANE TO REFRESH
                pane_ready = False
                wait_start = time.time()
                while time.time() - wait_start < 5:
                    if self._is_pane_showing_job(title, company):
                        # Extra check: ensure Easy Apply button is found or "Applied" status is clear
                        if self._detect_apply_action() != "none":
                            pane_ready = True
                            break
                    self.page.wait_for_timeout(500)
                
                if not pane_ready:
                    print(f"TRACE: Detail pane for {title} didn't load action button in time. Skipping.")
                    continue
                
                # 5. DETECT AND APPLY
                action = self._detect_apply_action()
                if action == "already":
                    print(f"[{company}] Already applied (verified in details pane).")
                    results.append(ApplyResult(title, company, "n/a", "n/a", "skipped", "Already applied."))
                elif action == "easy":
                    print(f"[{company}] Action: Easy Apply!")
                    # Create a temporary job object for the apply method
                    tmp_job = JobCard(title, company, loc, "n/a", True, False, ukey)
                    result = self._try_easy_apply(tmp_job)
                    results.append(result)
                elif action == "external":
                    print(f"[{company}] Action: External Apply (Skipping as per config).")
                    results.append(ApplyResult(title, company, "n/a", "n/a", "skipped", "External link."))
                
                # 6. CLEANUP
                self._cleanup_dialogs()
                self.page.wait_for_timeout(1000)

            except Exception as e:
                print(f"TRACE: Error processing card #{idx+1}: {e}")
                continue
                
        return results

    def _click_job_card(self, job: JobCard) -> bool:
        """
        Tries to find and click the job card in the search results sidebar.
        """
        assert self.page is not None
        # Try finding by data-job-id or data-occludable-job-id first
        job_id = ""
        if "/view/" in job.url:
            job_id = job.url.split("/view/")[-1].split("/")[0].split("?")[0]
        
        selectors = []
        if job_id:
            selectors.append(f"[data-job-id='{job_id}'] a.job-card-list__title")
            selectors.append(f"[data-occludable-job-id='{job_id}'] a.job-card-container__link")
            selectors.append(f"[data-job-id='{job_id}']")
            selectors.append(f"[data-occludable-job-id='{job_id}']")
        
        # Fallback to title-based search if ID fails
        selectors.append(f"a:has-text('{job.title}')")
        selectors.append(f"div.job-card-container:has-text('{job.company}')")
        
        for selector in selectors:
            try:
                card = self.page.locator(selector).first
                if card.count() > 0:
                    card.scroll_into_view_if_needed()
                    print(f"TRACE: Clicking job card via {selector}")
                    # Try standard click
                    try:
                        card.click(timeout=2000)
                    except Exception:
                        # JS-based click if standard fails
                        card.evaluate("el => el.click ? el.click() : el.dispatchEvent(new MouseEvent('click', {bubbles: true}))")
                    return True
            except Exception:
                continue
        return False

    def click_next_page(self) -> bool:
        """
        Finds and clicks the 'Next' page button in pagination.
        """
        assert self.page is not None
        next_selectors = [
            "button[aria-label='Next']",
            "button[aria-label='Next page']",
            "li.artdeco-pagination__item--next button",
            "button:has-text('Next')",
        ]
        
        # Scroll to bottom first to ensure pagination is loaded
        try:
            results_list = self.page.locator(".jobs-search-results-list, .scaffold-layout__list").first
            if results_list.count() > 0:
                results_list.evaluate("el => el.scrollTop = el.scrollHeight")
            else:
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(1000)
        except Exception:
            pass

        for selector in next_selectors:
            try:
                btn = self.page.locator(selector).first
                if btn.count() > 0 and btn.is_enabled():
                    btn.click(timeout=5000)
                    self.page.wait_for_timeout(2000)
                    return True
            except Exception:
                continue
        return False

    def _cleanup_dialogs(self) -> None:
        """
        Closes any lingering dialogs, dismissals, or 'Got it' popups.
        """
        assert self.page is not None
        dismiss_selectors = [
            "button[aria-label='Dismiss']",
            "button[aria-label*='Dismiss']",
            "button[aria-label='Close']",
            "button:has-text('Got it')",
            "button:has-text('Dismiss')",
            ".artdeco-modal__dismiss",
            ".artdeco-toast-item__dismiss",
        ]
        for sel in dismiss_selectors:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    print(f"TRACE: Cleaning up UI (clicking {sel}).")
                    btn.click(timeout=1500)
                    self.page.wait_for_timeout(500)
            except Exception:
                continue

    @staticmethod
    def save_results(results: List[ApplyResult], path: str = "data/results.json") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        previous = LinkedInApplyAgent._load_historical_results(path)
        merged = previous + [asdict(r) for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

    def _autofill_external_form(self, scope_selector: str = "") -> int:
        """
        Generic assisted autofill for external ATS pages or Easy Apply dialogs.
        We only fill common text-like fields and skip password/file/hidden controls.
        """
        assert self.page is not None
        if not self.profile:
            return 0

        candidate_values = self._candidate_values()
        if not candidate_values:
            return 0

        filled = 0
        container = self.page.locator(scope_selector).first if scope_selector else self.page
        if scope_selector and container.count() == 0:
            # Fallback to page if scoped container not found
            container = self.page

        fields = container.locator("input, textarea, select")
        total = fields.count()

        for i in range(total):
            field = fields.nth(i)
            try:
                if not field.is_visible():
                    continue
                tag_name = (field.evaluate("el => el.tagName") or "").lower()
                field_type = (field.get_attribute("type") or "").lower()
                if field_type in {"hidden", "file", "checkbox", "radio", "submit", "button"}:
                    continue
                if tag_name == "select":
                    existing_value = (field.input_value() or "").strip()
                else:
                    existing_value = (field.input_value() or "").strip()
                if existing_value:
                    continue

                attrs = " ".join(
                    [
                        field.get_attribute("name") or "",
                        field.get_attribute("id") or "",
                        field.get_attribute("placeholder") or "",
                        field.get_attribute("aria-label") or "",
                        field.get_attribute("autocomplete") or "",
                    ]
                ).lower()
                self._fill_common_defaults(field, tag_name, attrs)
                existing_after_defaults = (field.input_value() or "").strip()
                if existing_after_defaults:
                    filled += 1
                    continue

                match_value = self._pick_value_for_field(attrs, candidate_values)
                question = self._field_question_text(field, attrs)
                if not match_value:
                    match_value = self.question_memory.lookup(question)
                if not match_value and self.ai_answerer:
                    if tag_name == "select":
                        options = self._select_options(field)
                        picked = self.ai_answerer.choose_option(question, options, candidate_values)
                        if picked:
                            try:
                                field.select_option(label=picked)
                                filled += 1
                                self.question_memory.remember(question, picked)
                                continue
                            except Exception:
                                pass
                    else:
                        match_value = self.ai_answerer.answer_text(question, candidate_values)

                if not match_value:
                    continue
                if tag_name == "select":
                    try:
                        field.select_option(label=match_value)
                        filled += 1
                        self.question_memory.remember(question, match_value)
                    except Exception:
                        continue
                else:
                    field.fill(match_value)
                    filled += 1
                    self.question_memory.remember(question, match_value)
            except Exception:
                continue

        return filled

    def _fill_common_defaults(self, field, tag_name: str, attrs: str) -> None:
        if "experience" in attrs and "year" in attrs:
            if tag_name == "select":
                try:
                    self._select_closest_number_option(field, self.default_years_experience)
                    return
                except Exception:
                    return
            field.fill(self.default_years_experience)
            return
        if "notice" in attrs and ("day" in attrs or "period" in attrs):
            if tag_name == "select":
                try:
                    self._select_closest_number_option(field, self.default_notice_days)
                    return
                except Exception:
                    return
            field.fill(self.default_notice_days)

    def _process_easy_apply_dialog(self, scope_selector: str = "") -> str:
        assert self.page is not None
        for _ in range(8):
            self._autofill_external_form(scope_selector=scope_selector)
            if self._required_fields_missing_count(scope_selector=scope_selector) > 0:
                self._prompt_manual_required_fields()
                if self._required_fields_missing_count(scope_selector=scope_selector) > 0:
                    return "blocked"

            submit_btn = self.page.locator("button:has-text('Submit application'), button[aria-label*='Submit application']")
            if submit_btn.count() > 0 and submit_btn.first.is_enabled():
                submit_btn.first.click()
                self.page.wait_for_timeout(1200)
                return "submitted"

            review_btn = self.page.locator("button:has-text('Review')")
            if review_btn.count() > 0 and review_btn.first.is_enabled():
                review_btn.first.click()
                self.page.wait_for_timeout(1000)
                continue

            next_btn = self.page.locator("button:has-text('Next')")
            if next_btn.count() > 0 and next_btn.first.is_enabled():
                next_btn.first.click()
                self.page.wait_for_timeout(1000)
                continue

            # Final Fallback: use AI to find any clickable progress button in the dialog
            if self.ai_answerer and self.ai_answerer.enabled:
                dom = get_compressed_dom(self.page, scope_selector or "body")
                ai_selectors = self.ai_answerer.analyze_dom_for_elements(dom, "Find the 'Next', 'Review', or 'Submit' button in this application dialog.")
                clicked = False
                for selector in ai_selectors:
                    try:
                        btn = self.page.locator(selector).first
                        if btn.count() > 0 and btn.is_enabled():
                            btn.click()
                            self.page.wait_for_timeout(1000)
                            clicked = True
                            break
                    except Exception:
                        continue
                if clicked:
                    continue
            break
        return "needs_manual_review"

    def _required_fields_missing_count(self, scope_selector: str = "") -> int:
        assert self.page is not None
        try:
            return int(
                self.page.evaluate(
                    """([selector]) => {
                        const root = selector ? document.querySelector(selector) : document;
                        if (!root) return 0;
                        const required = Array.from(root.querySelectorAll('input[required], textarea[required], select[required]'));
                        let missing = 0;
                        for (const el of required) {
                          if (!(el instanceof HTMLElement)) continue;
                          const style = window.getComputedStyle(el);
                          if (style.visibility === 'hidden' || style.display === 'none') continue;
                          const val = (el.value || '').toString().trim();
                          if (!val) missing += 1;
                        }
                        return missing;
                    }""",
                    [scope_selector]
                )
            )
        except Exception:
            return 0

    def _prompt_manual_required_fields(self) -> None:
        print("Please fill the highlighted required fields manually, then press Enter to continue...")
        input()

    def _is_job_already_applied_page(self) -> bool:
        assert self.page is not None
        body = (self.page.inner_text("body") or "").lower()
        return "applied" in body and "easy apply" in body

    @staticmethod
    def _field_question_text(field, attrs_fallback: str) -> str:
        try:
            label_text = field.evaluate(
                """el => {
                    const id = el.id || "";
                    if (id) {
                      const byFor = document.querySelector(`label[for="${id}"]`);
                      if (byFor && byFor.textContent) return byFor.textContent.trim();
                    }
                    const wrap = el.closest("label");
                    if (wrap && wrap.textContent) return wrap.textContent.trim();
                    return "";
                }"""
            )
            if label_text:
                return str(label_text)
        except Exception:
            pass
        return attrs_fallback

    @staticmethod
    def _select_options(field) -> List[str]:
        try:
            return field.evaluate(
                """el => Array.from(el.options || [])
                .map(o => (o.textContent || '').trim())
                .filter(t => t && !/^select/i.test(t))"""
            )
        except Exception:
            return []

    @staticmethod
    def _select_closest_number_option(field, target_value: str) -> None:
        options = LinkedInApplyAgent._select_options(field)
        if not options:
            return
        try:
            target = int("".join(ch for ch in target_value if ch.isdigit()) or "0")
        except ValueError:
            target = 0
        best = ""
        best_gap = 10**9
        for opt in options:
            digits = "".join(ch for ch in opt if ch.isdigit())
            if not digits:
                continue
            value = int(digits)
            gap = abs(value - target)
            if gap < best_gap:
                best_gap = gap
                best = opt
        if best:
            field.select_option(label=best)

    def _candidate_values(self) -> Dict[str, str]:
        full_name = (self.profile.get("full_name") or "").strip()
        first_name = full_name.split()[0] if full_name else ""
        last_name = full_name.split()[-1] if len(full_name.split()) > 1 else ""
        return {
            "full_name": full_name,
            "first_name": first_name,
            "last_name": last_name,
            "email": (self.profile.get("email") or "").strip(),
            "phone": (self.profile.get("phone") or "").strip(),
            "linkedin_url": (self.profile.get("linkedin_url") or "").strip(),
            "work_authorization": (self.profile.get("work_authorization") or "").strip(),
            "current_title": (self.profile.get("current_title") or "").strip(),
            "years_experience": str(self.profile.get("years_experience") or "").strip(),
            "notice_period_days": str(self.profile.get("notice_period_days") or "").strip(),
            "school": (self.profile.get("school") or "").strip(),
            "portal_email": (self.profile.get("portal_email") or "").strip(),
            "portal_password": (self.profile.get("portal_password") or "").strip(),
        }

    @staticmethod
    def _pick_value_for_field(attrs: str, values: Dict[str, str]) -> str:
        alias_map = {
            "first_name": ["first", "given"],
            "last_name": ["last", "family", "surname"],
            "full_name": ["full name", "name"],
            "email": ["email", "e-mail"],
            "phone": ["phone", "mobile", "contact"],
            "linkedin_url": ["linkedin", "profile url", "portfolio"],
            "work_authorization": ["work authorization", "authorized", "sponsorship", "visa"],
            "current_title": ["current title", "job title", "headline", "position"],
            "years_experience": ["years", "experience"],
            "notice_period_days": ["notice period", "notice", "joining in", "availability"],
            "school": ["school", "college", "university", "education"],
            "portal_email": ["sign in email", "account email", "login email", "create account email"],
            "portal_password": ["password", "re-enter password", "confirm password"],
        }
        # Keep password mapping strict to avoid filling unrelated text fields.
        if "password" in attrs:
            return values.get("portal_password", "")
        for key, aliases in alias_map.items():
            if key == "portal_password":
                continue
            value = values.get(key, "")
            if not value:
                continue
            if any(alias in attrs for alias in aliases):
                return value
        return ""

    @staticmethod
    def _normalize_url(url: str) -> str:
        return (url or "").split("?")[0].strip()

    @staticmethod
    def _load_historical_results(path: str = "data/results.json") -> List[Dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return []
        try:
            payload = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(payload, list):
                return [x for x in payload if isinstance(x, dict)]
            return []
        except Exception:
            return []
