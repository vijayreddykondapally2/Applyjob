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

DEBUG_LOG_PATH = "/Users/apple/Projects/linkedin-apply-agent/.cursor/debug-786398.log"
DEBUG_SESSION_ID = "786398"


def _dbg(run_id: str, hypothesis_id: str, location: str, message: str, data: Dict[str, Any]) -> None:
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
        _dbg(
            run_id,
            "H1",
            "linkedin_agent.py:find_jobs:search_url",
            "Prepared LinkedIn jobs search URL.",
            {"easy_apply_only": easy_apply_only, "has_easy_filter_param": "f_AL=true" in search_url},
        )
        # endregion
        try:
            self.page.goto(search_url, wait_until="domcontentloaded", timeout=60000)
        except Exception:
            # region agent log
            _dbg(
                run_id,
                "H4",
                "linkedin_agent.py:find_jobs:goto_exception",
                "LinkedIn search navigation failed.",
                {"easy_apply_only": easy_apply_only},
            )
            # endregion
            # Keep loop alive if LinkedIn intermittently stalls.
            return []
        self.page.wait_for_timeout(3500)
        if easy_apply_only:
            self._apply_top_jobs_and_easy_apply_filters()
            self.page.wait_for_timeout(2000)

        cards = self.page.locator("ul.jobs-search__results-list li")
        count = min(cards.count(), max_jobs)
        jobs: List[JobCard] = []

        for i in range(count):
            card = cards.nth(i)
            title = (card.locator("h3").inner_text(timeout=1500) or "").strip()
            company = (card.locator("h4").inner_text(timeout=1500) or "").strip()
            loc = (card.locator(".job-search-card__location").inner_text(timeout=1500) or "").strip()
            url = card.locator("a").first.get_attribute("href") or ""
            easy_badge = card.locator("span:has-text('Easy Apply')").count() > 0
            card_text = (card.inner_text(timeout=1500) or "").lower()
            already_applied = "applied" in card_text
            if url and title:
                jobs.append(
                    JobCard(
                        title=title,
                        company=company,
                        location=loc,
                        url=url.split("?")[0],
                        is_easy_apply=easy_badge,
                        is_already_applied=already_applied,
                    )
                )
        # region agent log
        _dbg(
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
        """
        assert self.page is not None
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
                btn.click(timeout=4000)
                self.page.wait_for_timeout(600)
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
                try:
                    btn.click(timeout=5000)
                except Exception:
                    btn.click(timeout=5000, force=True)
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
        if easy_btn.count() == 0:
            return ApplyResult(job.title, job.company, job.url, "easy_apply", "skipped", "Button not found")

        easy_btn.first.scroll_into_view_if_needed()
        easy_btn.first.click(timeout=5000)
        self.page.wait_for_timeout(1200)
        filled = self._autofill_external_form()
        progressed = self._process_easy_apply_dialog()
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

    def _detect_apply_action(self) -> str:
        assert self.page is not None
        body = (self.page.inner_text("body") or "").lower()
        if "already applied" in body:
            return "already"
        easy_btn = self.page.locator("button:has-text('Easy Apply')")
        if easy_btn.count() > 0:
            return "easy"
        external_link = self.page.locator("a:has-text('Apply'), a:has-text('Apply on company website')")
        if external_link.count() > 0:
            return "external"
        return "none"

    def _apply_from_job_page(self, job: JobCard) -> ApplyResult:
        action = self._detect_apply_action()
        # region agent log
        _dbg(
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

    def process_jobs(self, jobs: List[JobCard]) -> List[ApplyResult]:
        results: List[ApplyResult] = []
        seen_companies: Set[str] = set()
        historical = self._load_historical_results()
        historical_urls = {self._normalize_url(r.get("url", "")) for r in historical}
        historical_companies = {
            (r.get("company") or "").strip().lower()
            for r in historical
            if (r.get("company") or "").strip()
        }

        for job in jobs:
            if job.is_already_applied:
                results.append(
                    ApplyResult(
                        job.title,
                        job.company,
                        job.url,
                        "n/a",
                        "skipped",
                        "Already applied (job card).",
                    )
                )
                continue

            normalized_url = self._normalize_url(job.url)
            company_key = job.company.strip().lower()

            if normalized_url in historical_urls:
                results.append(
                    ApplyResult(
                        job.title,
                        job.company,
                        job.url,
                        "n/a",
                        "skipped",
                        "Already attempted earlier (URL match).",
                    )
                )
                continue

            if company_key in historical_companies or company_key in seen_companies:
                results.append(
                    ApplyResult(
                        job.title,
                        job.company,
                        job.url,
                        "n/a",
                        "skipped",
                        "Company already attempted; skipping duplicate.",
                    )
                )
                continue

            if not self._confirm(job):
                results.append(ApplyResult(job.title, job.company, job.url, "n/a", "skipped", "User skipped"))
                continue

            self.page.goto(job.url, wait_until="domcontentloaded")
            self.page.wait_for_timeout(2000)
            result = self._apply_from_job_page(job)
            results.append(result)

            # Mark this company as attempted only if we actually proceeded.
            if result.status in {"needs_manual_review", "applied", "submitted"}:
                seen_companies.add(company_key)
        return results

    @staticmethod
    def save_results(results: List[ApplyResult], path: str = "data/results.json") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        previous = LinkedInApplyAgent._load_historical_results(path)
        merged = previous + [asdict(r) for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)

    def _autofill_external_form(self) -> int:
        """
        Generic assisted autofill for external ATS pages.
        We only fill common text-like fields and skip password/file/hidden controls.
        """
        assert self.page is not None
        if not self.profile:
            return 0

        candidate_values = self._candidate_values()
        if not candidate_values:
            return 0

        filled = 0
        fields = self.page.locator("input, textarea, select")
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

    def _process_easy_apply_dialog(self) -> str:
        assert self.page is not None
        for _ in range(8):
            self._autofill_external_form()
            if self._required_fields_missing_count() > 0:
                self._prompt_manual_required_fields()
                if self._required_fields_missing_count() > 0:
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
            break
        return "needs_manual_review"

    def _required_fields_missing_count(self) -> int:
        assert self.page is not None
        try:
            return int(
                self.page.evaluate(
                    """() => {
                        const required = Array.from(document.querySelectorAll('input[required], textarea[required], select[required]'));
                        let missing = 0;
                        for (const el of required) {
                          if (!(el instanceof HTMLElement)) continue;
                          const style = window.getComputedStyle(el);
                          if (style.visibility === 'hidden' || style.display === 'none') continue;
                          const val = (el.value || '').toString().trim();
                          if (!val) missing += 1;
                        }
                        return missing;
                    }"""
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
