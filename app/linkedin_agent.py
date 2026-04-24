from __future__ import annotations

import json
import os
import time
import random
import re
from dataclasses import asdict
from pathlib import Path
from typing import Any, Dict, List, Set
from urllib.parse import quote_plus

from playwright.sync_api import BrowserContext, Page, Playwright, sync_playwright

from app.ai_answerer import AIAnswerer
from app.job_types import ApplyResult, JobCard
from app.question_memory import QuestionMemory
from app.utils import debug_log, get_compressed_dom, should_run_headless


class LinkedInApplyAgent:
    """
    LinkedIn Easy Apply automation agent.

    Navigation model (matches how a human uses LinkedIn):
    ┌─────────────────────────────────────────────────────────┐
    │  1. Login                                               │
    │  2. Search → land on results page (LEFT sidebar + RIGHT │
    │             detail pane shown side-by-side)             │
    │  3. For each job card in the sidebar:                   │
    │     a. Click card  → RIGHT pane refreshes              │
    │     b. Detect Easy Apply button in RIGHT pane           │
    │     c. Click Easy Apply → modal opens                  │
    │     d. Fill every page of the modal (Groq does it)     │
    │     e. Submit                                           │
    │     f. Close modal / dismiss confirmation              │
    │     g. Back to sidebar → next card                     │
    │  4. After all cards on this page → click Next Page      │
    │  5. Repeat from step 3 on the new page                  │
    └─────────────────────────────────────────────────────────┘

    KEY FIX vs previous version:
    - find_jobs() ONLY navigates to a URL on the very first call.  On subsequent
      pages process_jobs() uses the sidebar that is already loaded after
      click_next_page() – we never call page.goto() mid-loop.
    - process_jobs() iterates sidebar cards IN ORDER, never re-loading the page.
    - All form-filling (text, textarea, radio, select) routes through Groq with
      the FULL profile JSON so no field is left blank.
    """

    def __init__(
        self,
        email: str,
        password: str,
        headless: bool | None = None,
        profile: Dict[str, Any] | None = None,
        auto_apply: bool = True,
        user_data_dir: str = "data/browser-profile",
        ai_answerer: AIAnswerer | None = None,
    ) -> None:
        self.email = email
        self.password = password
        self.headless = headless if headless is not None else should_run_headless()
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
        # Track applied URLs in memory for the current session
        self._session_applied: Set[str] = set()

    def __enter__(self) -> "LinkedInApplyAgent":
        self.playwright = sync_playwright().start()
        os.makedirs(self.user_data_dir, exist_ok=True)
        
        # Clean up dangling locks from aborted previous processes
        for lock_file in ["SingletonLock", "SingletonCookie"]:
            lock_path = os.path.join(self.user_data_dir, "Default", lock_file) if "Default" in self.user_data_dir else os.path.join(self.user_data_dir, lock_file)
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except Exception:
                pass

        print(f"Launching browser for LinkedIn... (headless={self.headless})")
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
                pass
        if self.playwright:
            self.playwright.stop()

    # ═══════════════════════════════════════════════════════════════
    # LOGIN
    # ═══════════════════════════════════════════════════════════════

    def login(
        self,
        allow_manual_checkpoint: bool = False,
        manual_timeout_seconds: int = 180,
        manual_login_submit: bool = False,
    ) -> None:
        # Failsafe: if we are headless, we MUST auto-submit. No exceptions.
        if should_run_headless():
            manual_login_submit = False
            allow_manual_checkpoint = False
            
        assert self.page is not None
        self.page.goto("https://www.linkedin.com/feed/", wait_until="domcontentloaded")
        self.page.wait_for_timeout(1500)
        if "/feed" in self.page.url.lower():
            print("✓ Reused existing LinkedIn session.")
            return

        self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        self.page.fill("#username", self.email)
        self.page.fill("#password", self.password)

        if manual_login_submit:
            print(
                "\nCredentials filled.\n"
                "Complete any CAPTCHA/2FA manually, then click Sign in.\n"
                "Waiting for LinkedIn feed...\n"
            )
            self._wait_for_login(timeout_seconds=manual_timeout_seconds)
            return

        self.page.click('button[type="submit"]')
        self.page.wait_for_timeout(4000)
        if "feed" in self.page.url:
            return
        if "checkpoint" in self.page.url or "challenge" in self.page.url:
            if not allow_manual_checkpoint:
                raise RuntimeError("Checkpoint detected. Enable manual checkpoint handling.")
            print("\nCheckpoint detected. Complete manually in browser.\n")
            self._wait_for_login(timeout_seconds=manual_timeout_seconds)
            return
        if allow_manual_checkpoint:
            print("\nLogin did not reach feed – please complete manually.\n")
            self._wait_for_login(timeout_seconds=manual_timeout_seconds)
            return
        raise RuntimeError("LinkedIn login failed.")

    def _wait_for_login(self, timeout_seconds: int = 180) -> None:
        assert self.page is not None
        unlimited = timeout_seconds <= 0
        waited_ms = 0
        step = 2000
        while unlimited or waited_ms < timeout_seconds * 1000:
            url = self.page.url.lower()
            if "/feed" in url or "/jobs" in url or "/mynetwork" in url:
                print("✓ Logged in.")
                return
            self.page.wait_for_timeout(step)
            waited_ms += step
            if waited_ms % 30000 == 0:
                print(f"Still waiting for login… ({waited_ms // 1000}s) | URL: {self.page.url}")
        raise RuntimeError(f"Login timeout after {timeout_seconds}s.")

    # ═══════════════════════════════════════════════════════════════
    # JOB SEARCH  –  called ONCE per search/page to navigate
    # ═══════════════════════════════════════════════════════════════

    def navigate_to_search(
        self,
        keywords: str,
        location: str,
        easy_apply_only: bool = False,
        direct_search_url: str = "",
    ) -> bool:
        """
        Navigate to the job search results page.
        Call this ONCE before starting to process job cards.
        Returns True when job cards are visible.
        """
        assert self.page is not None

        if direct_search_url.strip():
            url = direct_search_url.strip()
            if easy_apply_only and "f_AL=true" not in url:
                url += ("&" if "?" in url else "?") + "f_AL=true"
        else:
            url = (
                "https://www.linkedin.com/jobs/search/"
                f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}"
            )
            if easy_apply_only:
                url += "&f_AL=true"

        print(f"  → Navigating to: {url}")
        try:
            self.page.goto(url, wait_until="domcontentloaded", timeout=60000)
        except Exception as e:
            print(f"  Navigation failed: {e}")
            return False

        curr = self.page.url.lower()
        if "login" in curr or "authwall" in curr or "checkpoint" in curr:
            print("  ⚠ Session expired / authwall. Please log in again.")
            self.page.screenshot(path="data/authwall.png")
            return False

        # Apply Easy Apply filter via UI if not already in URL
        if easy_apply_only and "f_al=true" not in curr:
            self._apply_easy_apply_filter()

        # Wait for cards
        return self._wait_for_cards(timeout_s=20)

    def find_jobs(
        self,
        keywords: str,
        location: str,
        max_jobs: int = 25,
        easy_apply_only: bool = False,
        direct_search_url: str = "",
    ) -> List[JobCard]:
        """
        SCRAPE the currently-loaded search results page for job cards.

        IMPORTANT: This method no longer calls page.goto() – navigation is
        handled by navigate_to_search() (first page) and click_next_page()
        (subsequent pages).  That means calling find_jobs() on page 2 will
        correctly read the cards that click_next_page() loaded, not restart
        from page 1.

        The navigate_to_search() call is kept here only for the VERY FIRST
        page when the caller passes a fresh search URL; after that the runner
        should call click_next_page() then find_jobs() without a URL.
        """
        assert self.page is not None

        # Only navigate if a URL context was provided (first page of a new search)
        if direct_search_url.strip() or (keywords and location):
            if direct_search_url.strip():
                ok = self.navigate_to_search(keywords, location, easy_apply_only, direct_search_url)
            else:
                ok = self.navigate_to_search(keywords, location, easy_apply_only)
            if not ok:
                return []

        # Scroll sidebar to trigger lazy loading of cards
        self._scroll_sidebar()

        return self._scrape_cards(max_jobs)

    def _wait_for_cards(self, timeout_s: int = 20) -> bool:
        """Poll until at least one job card is visible."""
        assert self.page is not None
        deadline = time.time() + timeout_s
        selectors = "li[data-job-id], li[data-occludable-job-id], div[data-job-id], .job-card-container"
        while time.time() < deadline:
            if self.page.locator(selectors).count() > 0:
                return True
            self.page.wait_for_timeout(800)
        print("  ⚠ No job cards appeared within timeout.")
        return False

    def _scroll_sidebar(self) -> None:
        """Scroll the results sidebar to trigger lazy loading."""
        assert self.page is not None
        sidebar = self.page.locator(".jobs-search-results-list, .scaffold-layout__list").first
        if sidebar.count() > 0:
            for _ in range(6):
                sidebar.evaluate("el => el.scrollTop += 1200")
                self.page.wait_for_timeout(400)
        else:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight / 2)")
            self.page.wait_for_timeout(800)

    def _scrape_cards(self, max_jobs: int) -> List[JobCard]:
        """Extract JobCard objects from visible sidebar cards."""
        assert self.page is not None
        selectors = [
            "li[data-job-id]", "div[data-job-id]",
            "li[data-occludable-job-id]", "div[data-occludable-job-id]",
            ".jobs-search-results-list__list-item",
            "ul.jobs-search__results-list li",
            "li.jobs-search-results__list-item",
            "div.job-card-container", "div.base-card",
        ]
        cards = None
        for sel in selectors:
            c = self.page.locator(sel)
            if c.count() > 0:
                cards = c
                print(f"  Found {c.count()} cards via '{sel}'")
                break

        if not cards or cards.count() == 0:
            print("  ⚠ No job cards found on page.")
            try:
                self.page.screenshot(path="data/no_cards.png")
            except Exception:
                pass
            return []

        jobs: List[JobCard] = []
        count = min(cards.count(), max_jobs)

        for i in range(count):
            card = cards.nth(i)
            try:
                title = self._card_text(card, "h3, .job-card-list__title, .base-search-card__title, "
                                              ".artdeco-entity-lockup__title, span.strong")
                company = self._card_text(card, "h4, .job-card-container__company-name, "
                                                ".base-search-card__subtitle, .artdeco-entity-lockup__subtitle")
                loc = self._card_text(card, ".job-search-card__location, .job-card-container__metadata-item, "
                                            ".base-search-card__metadata, .artdeco-entity-lockup__caption")
                url = ""
                for link_sel in [
                    "a[href*='/jobs/view/']",
                    "a.job-card-list__title",
                    "a.base-card__full-link",
                    "a.job-card-container__link",
                    "a",
                ]:
                    loc_el = card.locator(link_sel)
                    if loc_el.count() > 0:
                        url = loc_el.first.get_attribute("href") or ""
                        if url:
                            break

                card_text = (card.inner_text(timeout=1500) or "").lower()
                already_applied = (
                    "applied" in card_text
                    or card.locator(
                        ".job-card-container__footer-item:has-text('Applied'), "
                        ".job-card-list__footer-item:has-text('Applied')"
                    ).count() > 0
                )
                easy_badge = card.locator(
                    "li:has-text('Easy Apply'), span:has-text('Easy Apply'), "
                    ".job-card-container__apply-method:has-text('Easy Apply'), "
                    ".job-card-list__footer-item:has-text('Easy Apply')"
                ).count() > 0

                if url and title:
                    jobs.append(JobCard(
                        title=title,
                        company=company,
                        location=loc,
                        url=url.split("?")[0],
                        is_easy_apply=easy_badge,
                        is_already_applied=already_applied,
                        unique_key=f"{title}_{company}_{loc}".lower().replace(" ", "_"),
                    ))
            except Exception as e:
                print(f"  Card #{i} parse error: {e}")
                continue

        print(f"  Scraped {len(jobs)} jobs ({sum(1 for j in jobs if j.is_easy_apply)} Easy Apply).")
        return jobs

    @staticmethod
    def _card_text(card, selector: str) -> str:
        try:
            loc = card.locator(selector).first
            return (loc.inner_text(timeout=1500) if loc.count() > 0 else "").strip()
        except Exception:
            return ""

    def _apply_easy_apply_filter(self) -> None:
        assert self.page is not None
        for sel in [
            "div.search-reusables__filters-bar button:has-text('Easy Apply')",
            "button:has-text('Easy Apply')",
            "label:has-text('Easy Apply')",
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0:
                    btn.click(timeout=2000)
                    self.page.wait_for_timeout(1500)
                    return
            except Exception:
                continue
        # Fallback: append to URL
        if "f_AL=true" not in self.page.url:
            sep = "&" if "?" in self.page.url else "?"
            self.page.goto(self.page.url + sep + "f_AL=true", wait_until="domcontentloaded")

    # ═══════════════════════════════════════════════════════════════
    # PROCESS JOBS  –  the main card-by-card loop
    # ═══════════════════════════════════════════════════════════════

    def process_jobs(self, discovered_jobs: List[JobCard]) -> List[ApplyResult]:
        """
        Process each job card one by one WITHOUT reloading the page.

        Flow per job:
          click card in sidebar  →  wait for detail pane  →  detect action
          →  Easy Apply (fill modal with Groq)  →  submit  →  next card

        Already-applied / external jobs are skipped and logged.
        """
        assert self.page is not None
        results: List[ApplyResult] = []

        # We keep track of URLs applied to in THIS session to avoid infinite loops
        # if LinkedIn duplicates a card on the same page, but we NO LONGER
        # check historical runs so we never miss new postings from the same company.
        done_urls: Set[str] = set()
        done_urls |= self._session_applied

        print(f"\n  Starting job loop: {len(discovered_jobs)} jobs to process.")

        for idx, job in enumerate(discovered_jobs):
            norm_url = self._normalize_url(job.url)
            label = f"[{idx+1}/{len(discovered_jobs)}] {job.title} @ {job.company}"

            # ── Skip checks ───────────────────────────────────────
            if norm_url in done_urls:
                print(f"{label} → SKIP (already processed this session)")
                continue

            if job.is_already_applied:
                print(f"{label} → SKIP (card badge: Applied)")
                r = ApplyResult(job.title, job.company, job.url, "n/a", "skipped",
                                "Already applied (card badge).")
                results.append(r)
                self._session_applied.add(norm_url)
                continue

            print(f"\n{label}")

            # ── Scroll sidebar to bring this card into view, then click ──
            # We NEVER call page.goto() here — that would reload the search
            # page and reset the sidebar back to card #1.
            clicked = self._click_job_card(job)
            if not clicked:
                print(f"  ⚠ Card click failed — skipping to next job.")
                results.append(ApplyResult(
                    job.title, job.company, job.url, "n/a",
                    "skipped", "Could not click sidebar card."
                ))
                continue

            # ── Wait for detail pane to show this job ─────────────
            self._wait_for_detail_pane(job.title, job.company, timeout_s=15)
            self.page.wait_for_timeout(1500)  # Let buttons fully render

            # Scroll detail pane to top so the Apply button is in view
            self._scroll_detail_pane()
            self.page.wait_for_timeout(500)

            # ── Detect & apply ────────────────────────────────────
            action = self._detect_apply_action()
            print(f"  Action: {action}")

            if action == "already":
                r = ApplyResult(job.title, job.company, job.url, "n/a",
                                "skipped", "Already applied (detail pane).")
                results.append(r)
                self._session_applied.add(norm_url)

            elif action == "easy":
                result = self._try_easy_apply(job)
                results.append(result)
                print(f"  Result: {result.status} — {result.note}")
                if result.status == "submitted":
                    self._session_applied.add(norm_url)

            elif action == "external":
                r = ApplyResult(job.title, job.company, job.url, "external",
                                "skipped", "External apply link — skipped.")
                results.append(r)

            else:
                r = ApplyResult(job.title, job.company, job.url, "n/a",
                                "skipped", "No apply button found.")
                results.append(r)

            # ── Cleanup any dangling modals/toasts, then pace ─────
            self._cleanup_dialogs()
            self.page.wait_for_timeout(1000)

            # --- PACE APPLICATIONS ---
            # User requested 3-5 min delay between each application to avoid account flags
            if idx < len(discovered_jobs) - 1:
                delay_sec = random.randint(180, 300)
                print(f"\n⏳ Pacing: Waiting {delay_sec // 60}m {delay_sec % 60}s before next job...")
                for s in range(delay_sec, 0, -10):
                    if s % 60 == 0:
                        print(f"  ({s // 60}m remaining...)")
                    time.sleep(min(s, 10))

        return results

    # ═══════════════════════════════════════════════════════════════
    # EASY APPLY
    # ═══════════════════════════════════════════════════════════════

    def _try_easy_apply(self, job: JobCard) -> ApplyResult:
        assert self.page is not None

        if self._is_job_already_applied_page():
            return ApplyResult(job.title, job.company, job.url,
                               "easy_apply", "skipped", "Already applied.")

        # Scroll the detail pane so the apply button is in view
        self._scroll_detail_pane()
        self.page.wait_for_timeout(400)

        # Find the Easy Apply button — try progressively broader selectors
        easy_btn = None
        for sel in [
            ".jobs-search__job-details--container button.jobs-apply-button",
            ".jobs-search-results-list__detail-container button.jobs-apply-button",
            ".jobs-details__main-content button.jobs-apply-button",
            ".job-view-layout button.jobs-apply-button",
            ".jobs-unified-top-card button.jobs-apply-button",
            ".job-details-jobs-unified-top-card__container--two-pane button.jobs-apply-button",
            "button.jobs-apply-button:has-text('Easy Apply')",
            "button.jobs-apply-button[aria-label*='Easy Apply']"
        ]:
            try:
                candidate = self.page.locator(sel)
                cnt = candidate.count()
                if cnt > 0:
                    # Prefer visible, enabled buttons
                    for idx in range(cnt):
                        c = candidate.nth(idx)
                        try:
                            if c.is_visible() and c.is_enabled():
                                easy_btn = c
                                print(f"  Easy Apply button → {sel}")
                                break
                        except Exception:
                            continue
                if easy_btn:
                    break
            except Exception:
                continue

        if easy_btn is None and self.ai_answerer and self.ai_answerer.enabled:
            dom = get_compressed_dom(self.page, ".jobs-details, body")
            for sel in self.ai_answerer.analyze_dom_for_elements(dom, "Find the Easy Apply button."):
                try:
                    c = self.page.locator(sel)
                    if c.count() > 0:
                        easy_btn = c.first
                        print(f"  Easy Apply button (AI) → {sel}")
                        break
                except Exception:
                    continue

        if easy_btn is None:
            return ApplyResult(job.title, job.company, job.url,
                               "easy_apply", "skipped", "Easy Apply button not found.")

        # Click it
        try:
            easy_btn.scroll_into_view_if_needed()
            easy_btn.click(timeout=5000)
        except Exception:
            try:
                easy_btn.evaluate("el => el.click()")
            except Exception as e:
                return ApplyResult(job.title, job.company, job.url,
                                   "easy_apply", "failed", f"Click failed: {e}")

        # Wait for modal
        dialog_sel = "div[role='dialog'], .jobs-easy-apply-modal, .artdeco-modal"
        try:
            self.page.wait_for_selector(dialog_sel, timeout=8000)
            print("  Modal opened.")
        except Exception:
            print("  ⚠ Modal did not appear within 8s. Aborting this job.")
            return ApplyResult(job.title, job.company, job.url, "easy_apply", "failed", "Modal did not open.")
        self.page.wait_for_timeout(800)

        # Fill and submit
        filled = self._autofill_dialog(scope_selector=dialog_sel)
        status = self._process_easy_apply_dialog(scope_selector=dialog_sel)

        if status == "submitted":
            return ApplyResult(job.title, job.company, job.url, "easy_apply",
                               "submitted", f"Submitted. Filled {filled} field(s).")
        if status == "blocked":
            return ApplyResult(job.title, job.company, job.url, "easy_apply",
                               "needs_user_input", "Blocked: required field needs manual input.")
        return ApplyResult(job.title, job.company, job.url, "easy_apply",
                           "needs_manual_review", f"Modal opened. Filled {filled} field(s).")

    # ═══════════════════════════════════════════════════════════════
    # FORM FILLING  –  ALL fields routed through Groq
    # ═══════════════════════════════════════════════════════════════

    def _autofill_dialog(self, scope_selector: str = "") -> int:
        """
        Fill every visible input, textarea, and select in the modal.
        Uses Groq for every field that isn't directly mapped from profile keys.
        """
        assert self.page is not None
        profile_vals = self._candidate_values()
        filled = 0

        container = self._get_container(scope_selector)
        fields = container.locator("input, textarea, select")

        for i in range(fields.count()):
            field = fields.nth(i)
            try:
                if not field.is_visible():
                    continue
                tag = (field.evaluate("el => el.tagName") or "").lower()
                ftype = (field.get_attribute("type") or "").lower()
                if ftype in {"hidden", "file", "checkbox", "radio", "submit", "button"}:
                    continue

                # Skip already-filled fields
                current_val = (field.input_value() or "").strip()
                if current_val:
                    continue

                # Build field context
                attrs = " ".join(filter(None, [
                    field.get_attribute("name") or "",
                    field.get_attribute("id") or "",
                    field.get_attribute("placeholder") or "",
                    field.get_attribute("aria-label") or "",
                    field.get_attribute("autocomplete") or "",
                ])).lower()
                question = self._field_label(field, attrs)

                # 1. Try direct profile mapping first (fast, no API call)
                value = self._direct_map(f"{question} {attrs}".lower(), profile_vals)

                # 2. Check question memory (also no API call)
                if not value:
                    value = self.question_memory.lookup(question)

                # 3. Route to Groq
                if not value and self.ai_answerer and self.ai_answerer.enabled:
                    if tag == "select":
                        options = self._select_options(field)
                        value = self.ai_answerer.choose_option(question, options, profile_vals)
                    elif tag == "textarea":
                        # Open-ended questions get a proper professional answer
                        value = self.ai_answerer.answer_free_text(question, profile_vals)
                    else:
                        value = self.ai_answerer.answer_text(question, profile_vals)

                if not value:
                    q_key = question or attrs
                    if q_key and q_key not in self.profile:
                        self.profile[q_key] = ""
                        try:
                            from app.profile_store import save_profile
                            save_profile(self.profile)
                        except Exception:
                            pass
                    continue

                # Fill
                if tag == "select":
                    try:
                        field.select_option(label=value)
                        filled += 1
                        self.question_memory.remember(question, value)
                    except Exception:
                        try:
                            field.select_option(value=value)
                            filled += 1
                            self.question_memory.remember(question, value)
                        except Exception:
                            pass
                else:
                    field.fill(value)
                    filled += 1
                    self.question_memory.remember(question, value)

            except Exception as e:
                print(f"  Field #{i} fill error: {e}")
                continue

        print(f"  Filled {filled} field(s).")
        return filled

    def _process_easy_apply_dialog(self, scope_selector: str = "") -> str:
        """
        Navigate through every page of the Easy Apply modal until submitted.
        Handles: text/select/radio/checkbox → Next → Review → Submit.
        """
        assert self.page is not None
        MAX_STEPS = 20

        for step in range(MAX_STEPS):
            print(f"  Dialog step {step + 1}…")

            # Fill all field types on this page
            self._autofill_dialog(scope_selector)
            self._handle_radios_with_groq(scope_selector)
            self._handle_selects_with_groq(scope_selector)

            # ── Submit ────────────────────────────────────────────
            sub = self.page.locator(
                "button:has-text('Submit application'), "
                "button[aria-label*='Submit application'], "
                "button:has-text('Submit')"
            )
            if sub.count() > 0 and sub.first.is_enabled():
                print("  → Submit application")
                try:
                    sub.first.click(timeout=5000, force=True)
                except Exception as e:
                    print(f"  → Form submit warning: {e}")
                self.page.wait_for_timeout(3000)
                # Dismiss confirmation ("Done" / "Not now")
                for dismiss in ["button:has-text('Done')", "button:has-text('Dismiss')",
                                 "button[aria-label='Dismiss']"]:
                    try:
                        b = self.page.locator(dismiss)
                        if b.count() > 0 and b.first.is_visible():
                            b.first.click()
                            self.page.wait_for_timeout(500)
                    except Exception:
                        pass
                return "submitted"

            # ── Check required fields still missing ───────────────
            missing = self._required_missing_count(scope_selector)
            if missing > 0:
                print(f"  ⚠ {missing} required field(s) still empty.")
                # ── AI targeted retry (no manual input needed at first) ────
                filled_now = self._ai_fill_required_fields(scope_selector)
                missing_after = self._required_missing_count(scope_selector)
                if missing_after > 0:
                    print(f"  ⚠ {missing_after} required field(s) still empty after AI retry.")
                    # Do a second broad autofill pass before giving up
                    self._autofill_dialog(scope_selector)
                    self._handle_radios_with_groq(scope_selector)
                    self._handle_selects_with_groq(scope_selector)
                    if self._required_missing_count(scope_selector) > 0:
                        self._prompt_manual_required_fields()
                        if self._required_missing_count(scope_selector) > 0:
                            print("  ✗ Could not fill all required fields — skipping this job.")
                            return "blocked"

            # ── Review ────────────────────────────────────────────
            for btn_text in ["Review", "Next", "Continue"]:
                btn = self.page.locator(f"button:has-text('{btn_text}')")
                if btn.count() > 0 and btn.first.is_enabled():
                    print(f"  → {btn_text}")
                    try:
                        btn.first.click(timeout=3000, force=True)
                    except Exception as e:
                        print(f"  → Next step warning: {e}")
                    self.page.wait_for_timeout(1500)
                    break
            else:
                # AI fallback for unexpected button labels
                if self.ai_answerer and self.ai_answerer.enabled:
                    dom = get_compressed_dom(self.page, scope_selector or "body")
                    for sel in self.ai_answerer.analyze_dom_for_elements(
                        dom, "Find the Next, Review, Continue or Submit button."
                    ):
                        try:
                            b = self.page.locator(sel).first
                            if b.count() > 0 and b.is_enabled():
                                b.click()
                                self.page.wait_for_timeout(1500)
                                break
                        except Exception:
                            continue
                    else:
                        print("  No progress button found.")
                        break
                else:
                    print("  No progress button found.")
                    break

        return "needs_manual_review"

    def _handle_radios_with_groq(self, scope_selector: str = "") -> None:
        """
        Find every unanswered radio fieldset and answer it using Groq (or
        fall back to 'Yes' / first option).
        """
        assert self.page is not None
        profile_vals = self._candidate_values()
        try:
            container = self._get_container(scope_selector)
            for i in range(container.locator("fieldset").count()):
                group = container.locator("fieldset").nth(i)
                if group.locator("input[type='radio']:checked").count() > 0:
                    continue

                # Get group question text
                legend = group.locator("legend, span.fb-dash-form-element__label").first
                question = (legend.inner_text(timeout=1000) if legend.count() > 0 else "").strip()

                # Get all option labels
                options: List[str] = []
                labels = group.locator("label")
                for j in range(labels.count()):
                    txt = (labels.nth(j).inner_text(timeout=500) or "").strip()
                    if txt:
                        options.append(txt)

                # Ask Groq
                chosen = ""
                if question and options and self.ai_answerer and self.ai_answerer.enabled:
                    chosen = self.ai_answerer.answer_radio(question, options, profile_vals)
                    if chosen:
                        self.question_memory.remember(question, chosen)

                # Click the chosen option (or Yes, or first)
                if chosen:
                    # Find radio whose label matches
                    for j in range(group.locator("label").count()):
                        lbl = group.locator("label").nth(j)
                        if (lbl.inner_text(timeout=500) or "").strip().lower() == chosen.lower():
                            try:
                                inp = lbl.locator("input[type='radio']")
                                if inp.count() > 0:
                                    inp.first.check(force=True, timeout=1500)
                                else:
                                    lbl.click(force=True, timeout=1500)
                            except Exception:
                                pass
                            break
                else:
                    # Fallback: Yes or first radio
                    yes_r = group.locator(
                        "input[type='radio'][value='Yes'], "
                        "input[type='radio'][value='yes'], "
                        "label:has-text('Yes') input[type='radio']"
                    )
                    if yes_r.count() > 0:
                        yes_r.first.check(force=True, timeout=1500)
                    else:
                        first_r = group.locator("input[type='radio']").first
                        if first_r.count() > 0:
                            first_r.check(force=True, timeout=1500)
        except Exception as e:
            print(f"  Radio handler error: {e}")

    def _handle_selects_with_groq(self, scope_selector: str = "") -> None:
        """
        Fill any un-answered <select> elements using Groq.
        Skips dropdowns that already have a real value selected.
        """
        assert self.page is not None
        profile_vals = self._candidate_values()
        try:
            container = self._get_container(scope_selector)
            selects = container.locator("select")
            for i in range(selects.count()):
                sel_el = selects.nth(i)
                if not sel_el.is_visible():
                    continue
                current = (sel_el.input_value() or "").strip().lower()
                if current and current not in {"", "select an option", "please select",
                                               "select", "- select -"}:
                    continue

                options = self._select_options(sel_el)
                if not options:
                    continue

                # Get label
                attrs = " ".join(filter(None, [
                    sel_el.get_attribute("name") or "",
                    sel_el.get_attribute("id") or "",
                    sel_el.get_attribute("aria-label") or "",
                ])).lower()
                question = self._field_label(sel_el, attrs)

                # Ask Groq
                chosen = ""
                if self.ai_answerer and self.ai_answerer.enabled:
                    chosen = self.ai_answerer.choose_option(question, options, profile_vals)
                    if chosen:
                        self.question_memory.remember(question, chosen)

                if chosen:
                    try:
                        sel_el.select_option(label=chosen)
                    except Exception:
                        pass
                elif options:
                    try:
                        sel_el.select_option(label=options[0])
                    except Exception:
                        pass
        except Exception as e:
            print(f"  Select handler error: {e}")

    def _get_container(self, scope_selector: str):
        """Return the scoped container locator, or self.page as fallback."""
        assert self.page is not None
        if scope_selector:
            c = self.page.locator(scope_selector).first
            if c.count() > 0:
                return c
        return self.page

    # ═══════════════════════════════════════════════════════════════
    # DETECT ACTION IN DETAIL PANE
    # ═══════════════════════════════════════════════════════════════

    def _detect_apply_action(self) -> str:
        """Return 'easy' | 'external' | 'already' | 'none'."""
        assert self.page is not None

        # Try to read the detail pane text for "already applied" signals
        pane_text = ""
        for sel in [
            ".jobs-search__job-details--container",
            ".jobs-search-results-list__detail-container",
            ".jobs-details", ".job-view-layout",
            ".scaffold-layout__detail", "#main",
        ]:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0:
                    pane_text = (loc.first.inner_text(timeout=3000) or "").lower()
                    break
            except Exception:
                continue

        if not pane_text:
            try:
                pane_text = (self.page.inner_text("body", timeout=3000) or "").lower()
            except Exception:
                pass

        if "already applied" in pane_text or "applied on" in pane_text:
            return "already"

        # Check for Easy Apply button — always search the full page to avoid
        # missing buttons rendered outside the detected container
        for sel in [
            ".jobs-search__job-details--container button.jobs-apply-button",
            ".jobs-search-results-list__detail-container button.jobs-apply-button",
            ".jobs-details__main-content button.jobs-apply-button",
            ".job-view-layout button.jobs-apply-button",
            ".jobs-unified-top-card button.jobs-apply-button",
            ".job-details-jobs-unified-top-card__container--two-pane button.jobs-apply-button",
            "button.jobs-apply-button:has-text('Easy Apply')",
            "button.jobs-apply-button[aria-label*='Easy Apply']"
        ]:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0:
                    # Make sure at least one is visible
                    for i in range(loc.count()):
                        try:
                            if loc.nth(i).is_visible():
                                return "easy"
                        except Exception:
                            continue
            except Exception:
                continue

        for sel in [
            "a:has-text('Apply on company website')",
            "button:has-text('Apply on company website')",
            "a[href*='apply']:has-text('Apply')",
        ]:
            try:
                loc = self.page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    return "external"
            except Exception:
                continue

        return "none"

    def _wait_for_detail_pane(self, title: str, company: str, timeout_s: int = 10) -> bool:
        """Poll until the right-hand pane shows content for this job."""
        assert self.page is not None
        pane_selectors = [
            ".jobs-search__job-details--container",
            ".jobs-search-results-list__detail-container",
            ".jobs-details", ".scaffold-layout__detail",
        ]
        deadline = time.time() + timeout_s
        while time.time() < deadline:
            for sel in pane_selectors:
                try:
                    pane = self.page.locator(sel).first
                    if pane.count() > 0:
                        text = (pane.inner_text(timeout=2000) or "").lower()
                        if title.lower()[:25] in text or company.lower()[:25] in text:
                            return True
                except Exception:
                    pass
            self.page.wait_for_timeout(500)
        return False

    # ═══════════════════════════════════════════════════════════════
    # CLICK JOB CARD IN SIDEBAR
    # ═══════════════════════════════════════════════════════════════

    def _click_job_card(self, job: JobCard) -> bool:
        """
        Click the job card in the LEFT sidebar without navigating away from
        the search page.

        Strategy:
          1. Scroll the sidebar list so the card is visible (avoids occluded/
             virtualized cards that LinkedIn removes from the DOM when off-screen).
          2. Then click via Playwright / JS fallback.
          We NEVER call page.goto() here — that resets the sidebar to card #1.
        """
        assert self.page is not None
        job_id = ""
        if "/view/" in job.url:
            job_id = job.url.split("/view/")[-1].split("/")[0].split("?")[0]

        # Build ordered selector list: specific → generic
        selectors: List[str] = []
        if job_id:
            selectors += [
                # Title link (most reliable — clicking it loads detail pane)
                f"li[data-job-id='{job_id}'] a.job-card-list__title",
                f"li[data-occludable-job-id='{job_id}'] a.job-card-list__title",
                f"div[data-job-id='{job_id}'] a.job-card-list__title",
                # Any job link inside the card
                f"[data-job-id='{job_id}'] a[href*='/jobs/view/']",
                f"[data-occludable-job-id='{job_id}'] a[href*='/jobs/view/']",
                # Whole card containers  (broader fallback)
                f"li[data-job-id='{job_id}']",
                f"li[data-occludable-job-id='{job_id}']",
                f"div[data-job-id='{job_id}']",
            ]

        safe_title = job.title.replace("'", "\\'")[:40]
        selectors += [
            f"a.job-card-list__title:has-text('{safe_title}')",
            f"div.job-card-container:has-text('{safe_title}')",
        ]
        if job_id:
            selectors.append(f"a[href*='{job_id}']")

        for sel in selectors:
            if not sel:
                continue
            try:
                loc = self.page.locator(sel)
                if loc.count() == 0:
                    continue
                card = loc.first

                # ── Step 1: scroll the SIDEBAR LIST so this card is visible ──
                # LinkedIn virtualises cards — they may not be in the DOM if
                # scrolled out of view.  scrollIntoView on the element handles
                # the viewport; we also nudge the sidebar container scroll.
                self._scroll_sidebar_to_card(card)
                self.page.wait_for_timeout(400)

                # ── Step 2: click ─────────────────────────────────────────
                try:
                    card.click(timeout=4000)
                except Exception:
                    try:
                        card.evaluate("el => el.click()")
                    except Exception:
                        continue

                self.page.wait_for_timeout(800)
                return True
            except Exception:
                continue

        return False

    def _scroll_sidebar_to_card(self, card_locator) -> None:
        """
        Scroll the LEFT sidebar container so *card_locator* is centred in it.
        This is the key to advancing through job cards without reloading the page:
        LinkedIn removes off-screen cards from the DOM (virtual scrolling), so we
        must scroll the sidebar—not the window—to materialise each card before
        clicking it.
        """
        assert self.page is not None
        try:
            card_locator.evaluate("""
                el => {
                    // Find the scrollable sidebar container
                    const SIDEBAR_SELS = [
                        '.jobs-search-results-list',
                        '.scaffold-layout__list',
                        '.jobs-search__results-list',
                        '.jobs-search-results__list',
                    ];
                    let sidebar = null;
                    for (const s of SIDEBAR_SELS) {
                        sidebar = document.querySelector(s);
                        if (sidebar) break;
                    }
                    if (!sidebar) {
                        // Fallback: scroll the element into the viewport
                        el.scrollIntoView({ block: 'center', behavior: 'smooth' });
                        return;
                    }
                    // Centre the card within the sidebar
                    const sbRect  = sidebar.getBoundingClientRect();
                    const elRect  = el.getBoundingClientRect();
                    const offset  = elRect.top - sbRect.top
                                  - (sbRect.height / 2)
                                  + (elRect.height / 2);
                    sidebar.scrollTop += offset;
                }
            """)
        except Exception:
            try:
                card_locator.scroll_into_view_if_needed()
            except Exception:
                pass

    # ═══════════════════════════════════════════════════════════════
    # PAGINATION
    # ═══════════════════════════════════════════════════════════════

    def click_next_page(self) -> bool:
        """
        Click the Next Page button in the pagination bar.
        Does NOT call find_jobs() – caller should call find_jobs() after this
        to scrape the new page's cards.
        """
        assert self.page is not None
        # Scroll to bottom of sidebar to expose pagination
        try:
            sidebar = self.page.locator(".jobs-search-results-list, .scaffold-layout__list").first
            if sidebar.count() > 0:
                sidebar.evaluate("el => el.scrollTop = el.scrollHeight")
            else:
                self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self.page.wait_for_timeout(800)
        except Exception:
            pass

        for sel in [
            "button[aria-label='Next']",
            "button[aria-label='Next page']",
            "li.artdeco-pagination__item--next button",
            "button:has-text('Next')",
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0 and btn.is_enabled():
                    btn.click(timeout=5000)
                    # Wait for new cards to load
                    self.page.wait_for_timeout(2500)
                    self._wait_for_cards(timeout_s=10)
                    return True
            except Exception:
                continue
        return False

    # ═══════════════════════════════════════════════════════════════
    # UTILITY HELPERS
    # ═══════════════════════════════════════════════════════════════

    def _scroll_detail_pane(self) -> None:
        """Scroll the RIGHT-hand job detail pane back to top so the
        Easy Apply button (rendered near the header) is visible."""
        assert self.page is not None
        detail_selectors = [
            ".jobs-search__job-details--container",
            ".jobs-search-results-list__detail-container",
            ".scaffold-layout__detail",
            ".jobs-details",
            ".job-view-layout",
        ]
        for sel in detail_selectors:
            try:
                pane = self.page.locator(sel).first
                if pane.count() > 0:
                    pane.evaluate("el => el.scrollTop = 0")
                    return
            except Exception:
                continue
        # Fallback: scroll window to top
        try:
            self.page.evaluate("window.scrollTo(0, 0)")
        except Exception:
            pass

    def _cleanup_dialogs(self) -> None:
        assert self.page is not None
        for _ in range(3):
            closed = False
            for sel in [
                "button[aria-label='Dismiss']", "button[aria-label*='Dismiss']",
                "button[aria-label='Close']", "button:has-text('Got it')",
                "button:has-text('Dismiss')", ".artdeco-modal__dismiss",
                ".artdeco-toast-item__dismiss",
            ]:
                try:
                    b = self.page.locator(sel).first
                    if b.count() > 0 and b.is_visible():
                        b.click(timeout=1500)
                        self.page.wait_for_timeout(800)
                        # Handle the 'Discard application?' confirmation popup
                        save_btn = self.page.locator("button:has-text('Save'), button[data-control-name='save_application_btn']").first
                        if save_btn.count() > 0 and save_btn.is_visible():
                            save_btn.click(timeout=1500)
                            self.page.wait_for_timeout(800)
                        else:
                            discard_btn = self.page.locator("button:has-text('Discard'), button[data-control-name='discard_application_confirm_btn']").first
                            if discard_btn.count() > 0 and discard_btn.is_visible():
                                discard_btn.click(timeout=1500)
                                self.page.wait_for_timeout(800)
                        closed = True
                        break
                except Exception:
                    continue
            if not closed:
                break

    def _is_job_already_applied_page(self) -> bool:
        assert self.page is not None
        try:
            body = (self.page.inner_text("body") or "").lower()
            return "already applied" in body or "applied on" in body
        except Exception:
            return False

    def _prompt_manual_required_fields(self) -> None:
        print("  ↳ Required fields are still missing. Manual intervention is disabled. Aborting.")

    def _required_missing_count(self, scope_selector: str = "") -> int:
        assert self.page is not None
        try:
            return int(self.page.evaluate(
                """([sel]) => {
                    const root = sel ? document.querySelector(sel) : document;
                    if (!root) return 0;
                    return Array.from(root.querySelectorAll(
                        'input[required], textarea[required], select[required], input[aria-required="true"], textarea[aria-required="true"], select[aria-required="true"]'
                    )).filter(el => {
                        const s = window.getComputedStyle(el);
                        return s.display !== 'none' && s.visibility !== 'hidden'
                               && !(el.value||'').trim();
                    }).length;
                }""",
                [scope_selector]
            ))
        except Exception:
            return 0

    def _ai_fill_required_fields(self, scope_selector: str = "") -> int:
        """
        Targeted AI retry: find every visible required field that is still empty,
        extract its label, ask Groq for the answer, and fill it.
        Returns the number of fields successfully filled.
        """
        assert self.page is not None
        profile_vals = self._candidate_values()
        filled = 0

        try:
            container = self._get_container(scope_selector)
            # Gather all required fields (both attribute styles)
            fields = container.locator(
                'input[required], textarea[required], select[required], '
                'input[aria-required="true"], textarea[aria-required="true"], '
                'select[aria-required="true"]'
            )

            for i in range(fields.count()):
                field = fields.nth(i)
                try:
                    if not field.is_visible():
                        continue

                    # Skip if already filled
                    current_val = (field.input_value() or "").strip()
                    if current_val:
                        continue

                    ftype = (field.get_attribute("type") or "").lower()
                    if ftype in {"hidden", "file", "checkbox", "radio", "submit", "button"}:
                        continue

                    tag = (field.evaluate("el => el.tagName") or "").lower()

                    # Build label context
                    attrs = " ".join(filter(None, [
                        field.get_attribute("name") or "",
                        field.get_attribute("id") or "",
                        field.get_attribute("placeholder") or "",
                        field.get_attribute("aria-label") or "",
                        field.get_attribute("autocomplete") or "",
                    ])).lower()
                    question = self._field_label(field, attrs)
                    print(f"  [Required] Asking AI for: '{question or attrs}'")

                    # Ask Groq
                    value = ""
                    if self.ai_answerer and self.ai_answerer.enabled:
                        if tag == "select":
                            options = self._select_options(field)
                            value = self.ai_answerer.choose_option(question, options, profile_vals)
                        elif tag == "textarea":
                            value = self.ai_answerer.answer_free_text(question, profile_vals)
                        else:
                            # Also try direct map as fast path
                            value = self._direct_map(f"{question} {attrs}".lower(), profile_vals)
                            if not value:
                                value = self.ai_answerer.answer_text(question, profile_vals)

                    if not value:
                        print(f"  [Required] AI returned empty for: '{question or attrs}'. Using raw fallback.")
                        q_key = question or attrs
                        if q_key and q_key not in self.profile:
                            self.profile[q_key] = ""
                            try:
                                from app.profile_store import save_profile
                                save_profile(self.profile)
                            except Exception:
                                pass
                        
                        # Strict raw fallback requested by user to NEVER wait on manual input
                        if tag == "select":
                            options = self._select_options(field)
                            if options:
                                value = options[0]
                        elif ftype == "number":
                            value = "1"
                        else:
                            value = "Yes"

                    # Fill
                    if tag == "select":
                        try:
                            field.select_option(label=value)
                            filled += 1
                            self.question_memory.remember(question, value)
                        except Exception:
                            try:
                                field.select_option(value=value)
                                filled += 1
                            except Exception:
                                pass
                    else:
                        try:
                            field.fill(value)
                            filled += 1
                            self.question_memory.remember(question, value)
                        except Exception:
                            pass

                except Exception as e:
                    print(f"  [Required] Field #{i} error: {e}")
                    continue

        except Exception as e:
            print(f"  [Required] Scan error: {e}")

        print(f"  [Required] AI filled {filled} previously-empty required field(s).")
        return filled

    # ── Field label extraction ──────────────────────────────────────────────────

    @staticmethod
    def _field_label(field, attrs_fallback: str) -> str:
        """Extract the human-readable label for a form field."""
        try:
            label = field.evaluate("""el => {
                const id = el.id || '';
                if (id) {
                    const lbl = document.querySelector(`label[for="${id}"]`);
                    if (lbl) return lbl.textContent.trim();
                }
                const wrap = el.closest('label');
                if (wrap) return wrap.textContent.trim();
                // LinkedIn often wraps in a div with a span label above
                const parent = el.closest('.fb-dash-form-element, .jobs-easy-apply-form-element');
                if (parent) {
                    const span = parent.querySelector('label, legend, span[class*="label"]');
                    if (span) return span.textContent.trim();
                }
                return '';
            }""")
            if label:
                return str(label)
        except Exception:
            pass
        return attrs_fallback

    @staticmethod
    def _select_options(field) -> List[str]:
        try:
            return field.evaluate(
                """el => Array.from(el.options||[])
                   .map(o=>(o.textContent||'').trim())
                   .filter(t=>t&&!/^(select|please|--)/i.test(t))"""
            )
        except Exception:
            return []

    # ── Profile mapping ─────────────────────────────────────────────────────────

    def _candidate_values(self) -> Dict[str, str]:
        """
        Return ALL profile fields as strings for passing to Groq.
        Includes rich fields like etl_experience, current_ctc, etc.
        """
        p = self.profile
        full_name = (p.get("full_name") or "").strip()
        parts = full_name.split()
        return {
            # Basic contact
            "full_name": full_name,
            "first_name": parts[0] if parts else "",
            "last_name": parts[-1] if len(parts) > 1 else "",
            "email": str(p.get("email") or "").strip(),
            "phone": str(p.get("phone") or "").strip(),
            "linkedin_url": str(p.get("linkedin_url") or "").strip(),
            # Work details
            "current_title": str(p.get("current_title") or "").strip(),
            "current_company": str(p.get("current_company") or "").strip(),
            "years_experience": str(p.get("years_experience") or "10").strip(),
            "etl_experience": str(p.get("etl_experience") or "10 years").strip(),
            "informatica_experience": str(p.get("informatica_experience") or "3 years").strip(),
            "plsql_experience": str(p.get("plsql_experience") or "6 years").strip(),
            "unix_shell_scripting_experience": str(p.get("unix_shell_scripting_experience") or "6 years").strip(),
            # Authorization & notice
            "work_authorization": str(p.get("work_authorization") or "Yes").strip(),
            "notice_period": str(p.get("notice_period") or "90 days (3 months)").strip(),
            "notice_period_days": str(p.get("notice_period_days") or "90").strip(),
            # Compensation
            "current_ctc": str(p.get("current_ctc") or "").strip(),
            "variable_ctc": str(p.get("variable_ctc") or "").strip(),
            "expected_ctc": str(p.get("expected_ctc") or "").strip(),
            "expected_ctc_usd": str(p.get("expected_ctc_usd") or "").strip(),
            # Location
            "current_city": str(p.get("current_city") or "Hyderabad").strip(),
            "preferred_city": str(p.get("preferred_city") or "Hyderabad").strip(),
            "location_preference": str(p.get("location_preference") or "Hyderabad").strip(),
            # Education
            "school": str(p.get("school") or "").strip(),
            # Portal creds
            "portal_email": str(p.get("portal_email") or p.get("email") or "").strip(),
            "portal_password": str(p.get("portal_password") or "").strip(),
        }

    @staticmethod
    def _direct_map(attrs: str, values: Dict[str, str]) -> str:
        """
        Fast keyword-based mapping that avoids an API call for the most common
        fields (name, email, phone, etc.).
        """
        alias_map = {
            "first_name":   ["first name", "given name", "firstname"],
            "last_name":    ["last name", "surname", "family name", "lastname"],
            "full_name":    ["full name", "your name"],
            "email":        ["email", "e-mail"],
            "phone":        ["phone", "mobile", "contact number"],
            "linkedin_url": ["linkedin", "profile url", "linkedin url"],
            "work_authorization": ["work authorization", "authorized to work",
                                   "sponsorship", "visa"],
            "current_title": ["current title", "job title", "position", "designation"],
            "years_experience": ["years of experience", "total experience",
                                 "years experience"],
            "notice_period_days": ["notice period", "notice days", "joining in",
                                   "available in", "availability"],
            "current_ctc":  ["current ctc", "current salary", "current package"],
            "expected_ctc": ["expected ctc", "expected salary", "expected package",
                             "desired salary"],
            "current_city": ["current city", "current location", "city"],
            "school":       ["school", "college", "university", "education"],
            "portal_email": ["login email", "account email", "sign in email"],
            "portal_password": ["password"],
        }
        if "password" in attrs:
            return values.get("portal_password", "")
        for key, aliases in alias_map.items():
            if key == "portal_password":
                continue
            val = values.get(key, "")
            if val and any(alias in attrs for alias in aliases):
                return val
        return ""

    @staticmethod
    def _normalize_url(url: str) -> str:
        return (url or "").split("?")[0].strip()

    # ═══════════════════════════════════════════════════════════════
    # PERSISTENCE
    # ═══════════════════════════════════════════════════════════════

    @staticmethod
    def save_results(results: List[ApplyResult], path: str = "data/results.json") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        previous = LinkedInApplyAgent._load_historical_results(path)
        merged = previous + [asdict(r) for r in results]
        with open(path, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
            
        # Also log to unified history for the UI
        try:
            from app.utils import log_application
            for r in results:
                if r.status == "submitted":
                    log_application("LinkedIn", r.job_title, r.company, r.url, "submitted")
        except Exception:
            pass

    @staticmethod
    def _load_historical_results(path: str = "data/results.json") -> List[Dict[str, Any]]:
        p = Path(path)
        if not p.exists():
            return []
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            return [x for x in data if isinstance(x, dict)] if isinstance(data, list) else []
        except Exception:
            return []
