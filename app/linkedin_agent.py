from __future__ import annotations

import os
from dataclasses import asdict
from typing import List
from urllib.parse import quote_plus

from playwright.sync_api import Browser, Page, Playwright, sync_playwright

from app.job_types import ApplyResult, JobCard


class LinkedInApplyAgent:
    def __init__(self, email: str, password: str, headless: bool = False) -> None:
        self.email = email
        self.password = password
        self.headless = headless
        self.playwright: Playwright | None = None
        self.browser: Browser | None = None
        self.page: Page | None = None

    def __enter__(self) -> "LinkedInApplyAgent":
        self.playwright = sync_playwright().start()
        self.browser = self.playwright.chromium.launch(headless=self.headless)
        self.page = self.browser.new_page()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        if self.browser:
            self.browser.close()
        if self.playwright:
            self.playwright.stop()

    def login(self) -> None:
        assert self.page is not None
        self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded")
        self.page.fill("#username", self.email)
        self.page.fill("#password", self.password)
        self.page.click('button[type="submit"]')
        self.page.wait_for_timeout(4000)
        if "feed" not in self.page.url and "checkpoint" in self.page.url:
            raise RuntimeError("Checkpoint/captcha detected. Please login manually and retry.")

    def find_jobs(self, keywords: str, location: str, max_jobs: int = 25) -> List[JobCard]:
        assert self.page is not None
        search_url = (
            "https://www.linkedin.com/jobs/search/"
            f"?keywords={quote_plus(keywords)}&location={quote_plus(location)}"
        )
        self.page.goto(search_url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(3000)

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
            if url and title:
                jobs.append(
                    JobCard(
                        title=title,
                        company=company,
                        location=loc,
                        url=url.split("?")[0],
                        is_easy_apply=easy_badge,
                    )
                )
        return jobs

    def _confirm(self, job: JobCard) -> bool:
        print(
            f"\nJob: {job.title}\nCompany: {job.company}\nLocation: {job.location}\n"
            f"Type: {'Easy Apply' if job.is_easy_apply else 'External Apply'}\nURL: {job.url}\n"
        )
        return input("Apply now? (yes/no): ").strip().lower() == "yes"

    def _try_easy_apply(self, job: JobCard) -> ApplyResult:
        assert self.page is not None
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        easy_btn = self.page.locator("button:has-text('Easy Apply')")
        if easy_btn.count() == 0:
            return ApplyResult(job.title, job.company, job.url, "easy_apply", "skipped", "Button not found")

        # MVP safety: open workflow but do not silently submit.
        easy_btn.first.click()
        self.page.wait_for_timeout(1000)
        return ApplyResult(
            job.title,
            job.company,
            job.url,
            "easy_apply",
            "needs_manual_review",
            "Easy Apply dialog opened; complete final answers and submit manually.",
        )

    def _try_external_apply(self, job: JobCard) -> ApplyResult:
        assert self.page is not None
        self.page.goto(job.url, wait_until="domcontentloaded")
        self.page.wait_for_timeout(2000)

        apply_link = self.page.locator("a:has-text('Apply'), a:has-text('Apply on company website')")
        if apply_link.count() == 0:
            return ApplyResult(job.title, job.company, job.url, "external", "skipped", "External apply link not found")
        href = apply_link.first.get_attribute("href")
        if not href:
            return ApplyResult(job.title, job.company, job.url, "external", "failed", "Missing external link href")

        self.page.goto(href, wait_until="domcontentloaded")
        return ApplyResult(
            job.title,
            job.company,
            href,
            "external",
            "needs_manual_review",
            "Company portal opened; continue with assisted/manual completion.",
        )

    def process_jobs(self, jobs: List[JobCard]) -> List[ApplyResult]:
        results: List[ApplyResult] = []
        for job in jobs:
            if not self._confirm(job):
                results.append(ApplyResult(job.title, job.company, job.url, "n/a", "skipped", "User skipped"))
                continue
            if job.is_easy_apply:
                results.append(self._try_easy_apply(job))
            else:
                results.append(self._try_external_apply(job))
        return results

    @staticmethod
    def save_results(results: List[ApplyResult], path: str = "data/results.json") -> None:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        import json

        with open(path, "w", encoding="utf-8") as f:
            json.dump([asdict(r) for r in results], f, indent=2)
