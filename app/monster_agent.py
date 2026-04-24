import os
import time
import urllib.parse
from playwright.sync_api import sync_playwright, Page
from app.utils import should_run_headless


class MonsterApplyAgent:
    """
    Monster.com Job Apply Agent — clean, single-tab approach.

    Flow:
      1. Login with email/password
      2. Search via URL (fast, reliable)
      3. Click each job card → click Apply
      4. If new tab opens (internal or external): fill form → submit → close tab → back to search
      5. If same page: fill form → submit → go back to search
    """

    def __init__(self, profile: dict, ai_answerer=None):
        self.profile = profile
        self.ai_answerer = ai_answerer
        self.jobs_applied = 0
        self.playwright = None
        self.context = None
        self.page = None          # the MAIN search-results tab
        self._search_url = ""     # saved so we can always come back

    # ─── Browser ──────────────────────────────────────────────────────────

    def start(self):
        self.playwright = sync_playwright().start()
        user_data_dir = os.path.abspath(
            os.getenv("MONSTER_PROFILE_DIR", "data/monster-browser-profile")
        )
        os.makedirs(user_data_dir, exist_ok=True)

        # Clean stale locks
        for lf in ["SingletonLock", "SingletonCookie", "SingletonSocket"]:
            p = os.path.join(user_data_dir, lf)
            try:
                if os.path.islink(p):
                    os.unlink(p)
                elif os.path.exists(p):
                    os.remove(p)
            except Exception:
                pass

        time.sleep(1)
        headless = should_run_headless()
        print(f"Launching browser for Monster.com... (headless={headless})")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=headless,
            args=["--start-maximized",
                  "--disable-blink-features=AutomationControlled"],
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(30000)

    def close(self):
        print(f"\n{'='*50}")
        print(f"  Monster session complete.")
        print(f"  Jobs applied: {self.jobs_applied}")
        print(f"{'='*50}")
        try:
            self.context.close()
        except Exception:
            pass
        try:
            self.playwright.stop()
        except Exception:
            pass

    # ─── Login ────────────────────────────────────────────────────────────

    def login(self):
        print("Navigating to Monster/Foundit Login...")
        # Monster India redirects to foundit.in
        self.page.goto("https://www.foundit.in/rio/login/seeker", wait_until="load")
        self.page.wait_for_timeout(5000)

        # Already logged in?
        try:
            if self.page.locator(".profile-icon, .userName, [class*='profile']").count() > 0:
                print("✓ Already logged into Monster/Foundit.")
                return
        except Exception:
            pass

        # Dismiss popups
        try:
            for sel in ["button:has-text('Okay')", "button:has-text('Accept')", "button:has-text('Got it')"]:
                btn = self.page.locator(sel).first
                if btn.is_visible():
                    btn.click(timeout=3000)
                    self.page.wait_for_timeout(1000)
        except: pass

        print("Clicking 'LinkedIn' button for Social Login...")
        try:
            with self.page.expect_popup() as popup_info:
                self.page.locator("button:has-text('LinkedIn'), [class*='linkedin']").first.click()
            
            popup = popup_info.value
            popup.wait_for_load_state("load")

            if "linkedin.com" in popup.url:
                if popup.locator("#username").count() > 0:
                    print("  -> Filling LinkedIn credentials in popup...")
                    email = os.getenv("LINKEDIN_EMAIL", "")
                    password = os.getenv("LINKEDIN_PASSWORD", "")
                    popup.fill("#username", email)
                    popup.fill("#password", password)
                    popup.click("button[type='submit']")
                    popup.wait_for_load_state("load")

                # Handle "Allow"
                try:
                    allow_btn = popup.locator("button:has-text('Allow'), button:has-text('Agree & Confirm')").first
                    if allow_btn.is_visible(timeout=10000):
                        print("  -> Clicking 'Allow' on LinkedIn permission screen...")
                        allow_btn.click()
                except: pass

            print("  -> Waiting for Dashboard...")
            for _ in range(30):
                if "/dashboard" in self.page.url or self.page.locator(".profile-icon, .userName").count() > 0:
                    print("✅ Monster/Foundit Login Successful!")
                    return
                self.page.wait_for_timeout(1000)
            
        except Exception as e:
            print(f"❌ Monster Login Error: {e}")

    # ─── Search & Apply ───────────────────────────────────────────────────

    def search_and_apply(self, keyword: str, location: str = "Hyderabad",
                         max_jobs: int = 25):
        """Search for a keyword and apply to jobs on the results page."""
        encoded_kw = urllib.parse.quote_plus(keyword)
        encoded_loc = urllib.parse.quote_plus(location)
        self._search_url = (
            f"https://www.monster.com/jobs/search"
            f"?q={encoded_kw}&where={encoded_loc}"
        )

        print(f"\n{'='*50}")
        print(f"  Searching: '{keyword}' in '{location}'")
        print(f"{'='*50}")

        self.page.goto(self._search_url)
        self.page.wait_for_timeout(8000)

        # Find job cards
        cards = self.page.locator("[data-testid*='JobCard'], [class*='JobCard'], [id^='card-']")
        count = cards.count()

        if count == 0:
            # Fallback selectors
            cards = self.page.locator("article, .job-search-card, [class*='jobCard']")
            count = cards.count()

        print(f"  Found {count} job cards.")
        if count == 0:
            print("  ! No results. Skipping keyword.")
            return

        applied_this_keyword = 0
        for i in range(min(count, max_jobs)):
            try:
                print(f"\n[{i+1}/{count}] ", end="")
                self._process_card(i)
                applied_this_keyword += 1
            except Exception as e:
                print(f"  x Error on job {i+1}: {e}")

            # Always return to the search page after each job
            self._return_to_search()

        print(f"\n  Processed {applied_this_keyword} jobs for '{keyword}'.")

    def _process_card(self, index: int):
        """Click a job card, find Apply, handle application, return."""
        # Re-locate cards each time (DOM may have changed)
        cards = self.page.locator(
            "[data-testid*='JobCard'], [class*='JobCard'], [id^='card-'], "
            "article, .job-search-card"
        )
        if index >= cards.count():
            print("Card index out of range.")
            return

        card = cards.nth(index)
        card.scroll_into_view_if_needed()

        # Get title
        title = ""
        try:
            title = card.locator("a, h2, h3, [class*='Title']").first.inner_text().strip()[:60]
        except Exception:
            pass
        print(f"'{title}'")

        # Click the card
        try:
            card.click(force=True, timeout=5000)
        except Exception as e:
            print(f"  ! Error clicking card: {e}")
        self.page.wait_for_timeout(3000)

        # Find the Apply button
        apply_btn = self.page.locator(
            "button:has-text('Apply'), "
            "a:has-text('Apply'), "
            "button[data-testid*='apply']"
        ).first

        if apply_btn.count() == 0 or not apply_btn.is_visible():
            print("  -> No Apply button found. Skipping.")
            return

        # Check if already applied
        try:
            btn_text = apply_btn.inner_text().strip().lower()
            if "applied" in btn_text:
                print("  -> Already applied. Skipping.")
                return
        except Exception:
            pass

        print("  -> Clicking 'Apply'...")

        # Count pages before clicking to detect if a new tab opens
        pages_before = len(self.context.pages)

        try:
            # Try with expect_page (new tab)
            with self.context.expect_page(timeout=8000) as new_page_info:
                apply_btn.click(force=True)

            new_page = new_page_info.value
            new_page.wait_for_load_state()
            print(f"  -> New tab: {new_page.url[:80]}...")
            self._handle_apply_page(new_page)

            # Close the application tab
            try:
                new_page.close()
                print("  -> Closed application tab.")
            except Exception:
                pass

        except Exception:
            # No new tab — might be same-page modal or navigation
            self.page.wait_for_timeout(3000)

            if len(self.context.pages) > pages_before:
                # A new page appeared even though expect_page timed out
                new_page = self.context.pages[-1]
                new_page.wait_for_load_state()
                print(f"  -> Late tab: {new_page.url[:80]}...")
                self._handle_apply_page(new_page)
                try:
                    new_page.close()
                except Exception:
                    pass
            else:
                # Same page — check if we navigated away
                if "/jobs/search" not in self.page.url:
                    print("  -> Same-page application flow...")
                    self._handle_apply_page(self.page)

    def _handle_apply_page(self, page: Page):
        """
        Handle the application page/form.
        Works for both internal Monster forms and external company sites.
        """
        page.wait_for_timeout(3000)

        # Try up to 5 form steps (multi-step applications)
        for step in range(5):
            # Check for input fields
            inputs = page.locator(
                'input:not([type="hidden"]):not([type="submit"]):not([type="button"]), '
                'select, textarea'
            )
            field_count = inputs.count()

            if field_count == 0:
                # No form fields — check if application is complete
                if page.locator("text='applied'").count() > 0:
                    print("  ✓ Application submitted!")
                    self._log_job_applied(page)
                    return
                break

            print(f"  -> Step {step+1}: Found {field_count} fields.")

            # Try to fill fields using AI answerer or profile
            self._fill_form_fields(page)

            # Look for Submit / Continue / Next / Apply Now
            submitted = False
            for btn_sel in [
                "button:has-text('Submit')",
                "button:has-text('Apply Now')",
                "button:has-text('Send Application')",
                "input[type='submit']",
                "button:has-text('Continue')",
                "button:has-text('Next')",
                "button[type='submit']",
            ]:
                try:
                    btn = page.locator(btn_sel).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(force=True)
                        print(f"  -> Clicked '{btn_sel}'")
                        page.wait_for_timeout(5000)
                        submitted = True
                        break
                except Exception:
                    continue

            if not submitted:
                print("  ! No submit button found.")
                break

            # Check if we landed on a success page
            try:
                success_text = page.locator(
                    "text='successfully', text='applied', text='thank you', "
                    "text='confirmation'"
                )
                if success_text.count() > 0:
                    print("  ✓ Application submitted!")
                    self._log_job_applied(page)
                    return
            except Exception:
                pass

        # If we got here, count it as attempted
        self._log_job_applied(page)
        print("  ✓ Application attempted.")

    def _log_job_applied(self, page: Page):
        self.jobs_applied += 1
        try:
            from app.utils import log_application
            title = page.title() or "Unknown Monster Job"
            log_application("Monster", title, "Unknown Company", page.url, "submitted")
        except Exception:
            pass

    def _fill_form_fields(self, page: Page):
        """Fill visible form fields using profile data."""
        fields = page.locator(
            'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([readonly]), '
            'select:not([disabled]), textarea:not([disabled])'
        )

        for i in range(fields.count()):
            try:
                field = fields.nth(i)
                if not field.is_visible():
                    continue

                tag = field.evaluate("el => el.tagName.toLowerCase()")
                field_type = field.get_attribute("type") or ""
                name = field.get_attribute("name") or ""
                placeholder = field.get_attribute("placeholder") or ""
                label_text = name or placeholder

                # Skip file inputs, checkboxes, radios for now
                if field_type in ["file", "checkbox", "radio"]:
                    continue

                # Try to get a smart answer
                answer = self._get_answer(label_text, field_type)

                if tag == "select":
                    # Select first non-empty option
                    try:
                        options = field.locator("option")
                        for j in range(options.count()):
                            val = options.nth(j).get_attribute("value") or ""
                            if val and val != "":
                                field.select_option(value=val)
                                break
                    except Exception:
                        pass
                elif tag == "textarea":
                    if answer:
                        field.fill(answer)
                else:
                    # Regular input
                    current_val = field.input_value()
                    if not current_val and answer:
                        field.fill(answer)

            except Exception:
                continue

    def _get_answer(self, label: str, field_type: str) -> str:
        """Get an answer for a form field from the profile. AI is last resort."""
        label_lower = label.lower()

        # Common mappings from profile
        mappings = {
            "name": self.profile.get("full_name", ""),
            "full_name": self.profile.get("full_name", ""),
            "first": self.profile.get("full_name", "").split()[0] if self.profile.get("full_name") else "",
            "last": self.profile.get("full_name", "").split()[-1] if self.profile.get("full_name") else "",
            "email": self.profile.get("email", ""),
            "phone": self.profile.get("phone", ""),
            "mobile": self.profile.get("phone", ""),
            "city": self.profile.get("current_city", "Hyderabad"),
            "location": self.profile.get("current_city", "Hyderabad"),
            "salary": self.profile.get("expected_ctc", "3000000"),
            "ctc": self.profile.get("current_ctc", "2250000"),
            "experience": self.profile.get("years_experience", "10"),
            "notice": self.profile.get("notice_period_days", "90"),
            "company": self.profile.get("current_company", ""),
            "title": self.profile.get("current_title", ""),
            "linkedin": self.profile.get("linkedin_url", ""),
            "designation": self.profile.get("current_title", ""),
            "address": self.profile.get("current_city", "Hyderabad"),
            "state": self.profile.get("State", "Telangana"),
            "country": self.profile.get("Country", "India"),
        }

        for key, value in mappings.items():
            if key in label_lower and value:
                return value

        # Skip AI for now — profile-based filling is fast and sufficient
        return ""

    # ─── Navigation ───────────────────────────────────────────────────────

    def _return_to_search(self):
        """Close any extra tabs and return to the search results page."""
        # Close all tabs except self.page
        pages = self.context.pages
        for p in pages:
            if p != self.page:
                try:
                    p.close()
                except Exception:
                    pass

        # Make sure self.page is still alive
        try:
            self.page.evaluate("() => true")
        except Exception:
            # Page died — get the remaining page
            pages = self.context.pages
            if pages:
                self.page = pages[0]
            else:
                return

        # Navigate back to search if we drifted away
        if self._search_url and "/jobs/search" not in self.page.url:
            try:
                self.page.goto(self._search_url)
                self.page.wait_for_timeout(5000)
            except Exception as e:
                print(f"  ! Could not return to search: {e}")
