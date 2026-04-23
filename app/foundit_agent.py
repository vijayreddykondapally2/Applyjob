import os
import re
import json
import time
import urllib.parse
from datetime import datetime
from playwright.sync_api import sync_playwright, Page


# Maximum session duration in seconds (15 minutes)
MAX_SESSION_SECONDS = 15 * 60
# Maximum pages to bulk-apply across all keywords
MAX_TOTAL_PAGES = 3


class FounditApplyAgent:
    """
    Foundit.in Bulk-Apply Agent.

    Workflow per keyword:
      1. Type keyword into search bar → Enter
      2. Wait for job cards
      3. Click "Select all"
      4. Click "Quick Apply (N)"
      5. Click "Confirm & Apply" (on the SAME page modal)
      6. Wait → Next page → repeat

    Stops after 3 total pages applied OR 15 minutes, whichever first.
    """

    def __init__(self, profile: dict, ai_answerer=None):
        self.profile = profile
        self.ai_answerer = ai_answerer
        self.jobs_applied = 0
        self.pages_applied = 0          # total pages where bulk was attempted
        self.playwright = None
        self.context = None
        self.page = None
        self._session_start = None

    # ─── Timer ────────────────────────────────────────────────────────────

    def _time_remaining(self) -> float:
        if self._session_start is None:
            return MAX_SESSION_SECONDS
        return MAX_SESSION_SECONDS - (time.time() - self._session_start)

    def _session_expired(self) -> bool:
        return self._time_remaining() <= 0

    def _should_stop(self) -> bool:
        """Stop if 3 pages done or 15 min expired."""
        if self.pages_applied >= MAX_TOTAL_PAGES:
            return True
        if self._session_expired():
            return True
        return False

    # ─── Browser lifecycle ────────────────────────────────────────────────

    def start(self):
        self.playwright = sync_playwright().start()
        user_data_dir = os.path.abspath(
            os.getenv("FOUNDIT_PROFILE_DIR", "data/foundit-browser-profile")
        )
        os.makedirs(user_data_dir, exist_ok=True)

        print("Cleaning up dangling browser locks...")
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
        print("Launching browser for Foundit...")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=False,
            args=["--start-maximized",
                  "--disable-blink-features=AutomationControlled"],
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(60000)

    def close(self):
        print(f"\n{'='*50}")
        print(f"  Session complete.")
        print(f"  Pages processed : {self.pages_applied}")
        print(f"  Jobs applied    : ~{self.jobs_applied}")
        print(f"{'='*50}")
        try:
            if self.context:
                self.context.close()
        except Exception:
            pass
        try:
            if self.playwright:
                self.playwright.stop()
        except Exception:
            pass

    # ─── Safe helpers ─────────────────────────────────────────────────────

    def _is_page_alive(self) -> bool:
        try:
            self.page.evaluate("() => true")
            return True
        except Exception:
            return False

    def _safe_wait(self, ms: int):
        try:
            self.page.wait_for_timeout(ms)
        except Exception:
            pass

    def _recovery_wait(self, reason: str):
        print(f"  ⏳ Recovery wait (60 s) — {reason}")
        self._safe_wait(60000)

    # ─── Login ────────────────────────────────────────────────────────────

    def login(self):
        print("Navigating to Foundit...")
        self.page.goto("https://www.foundit.in/")
        self._safe_wait(5000)

        # Already logged in?
        try:
            if self.page.locator(
                ".profile-icon, .user-profile, .userName, [class*='profile']"
            ).count() > 0:
                print("✓ Already logged into Foundit.")
                self._session_start = time.time()
                return
        except Exception:
            pass

        # Dismiss cookie banner
        try:
            cb = self.page.locator(
                "button:has-text('Accept'), button:has-text('Got it')"
            )
            if cb.count() > 0 and cb.first.is_visible():
                cb.first.click(timeout=3000)
                self._safe_wait(1000)
        except Exception:
            pass

        # Click Login on top ribbon
        print("Clicking 'Login' button on top ribbon...")
        for sel in [
            "#seekerHeader button:has-text('Login')",
            "header button:has-text('Login')",
            "button:has-text('Login')",
            "a:has-text('Login')",
        ]:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(force=True)
                    print(f"  -> Clicked: {sel}")
                    break
            except Exception:
                continue

        self._safe_wait(3000)

        # Fill mobile / email
        mobile = os.getenv("FOUNDIT_MOBILE", "")
        email = os.getenv("FOUNDIT_EMAIL", "")
        login_id = mobile if mobile else email

        if login_id:
            print(f"Entering login ID: {login_id}...")
            for sel in [
                "input[placeholder*='Mobile']",
                "input[placeholder*='Email']",
                "input[type='email']",
                "input[type='text']",
                "input[name='signInName']",
            ]:
                try:
                    loc = self.page.locator(sel).first
                    if loc.count() > 0 and loc.is_visible():
                        loc.fill(login_id)
                        print(f"  -> Filled: {sel}")
                        break
                except Exception:
                    continue

            # Submit to trigger OTP
            for sel in [
                "button[type='submit']",
                "button:has-text('Next')",
                "button:has-text('Continue')",
                "button:has-text('Login')",
            ]:
                try:
                    btn = self.page.locator(sel).first
                    if btn.count() > 0 and btn.is_visible():
                        btn.click(force=True)
                        print(f"  -> Submitted: {sel}")
                        break
                except Exception:
                    continue

        # Wait for OTP
        print("\n" + "=" * 50)
        print("  OTP sent. Please enter it in the browser.")
        print("  Waiting 60 seconds...")
        print("=" * 50 + "\n")
        self._safe_wait(60000)

        # Go back to homepage
        print("Returning to Foundit homepage...")
        self.page.goto("https://www.foundit.in/")
        self._safe_wait(5000)

        self._session_start = time.time()
        print(f"⏱  Session timer started — {MAX_SESSION_SECONDS // 60} min cap.\n")

    # ─── Bulk Apply (main entry) ──────────────────────────────────────────

    def bulk_apply(self, keyword: str, location: str = "India",
                   max_pages: int = 3):
        if self._should_stop():
            reason = "page limit" if self.pages_applied >= MAX_TOTAL_PAGES else "time limit"
            print(f"  ⏱ Stopping — {reason} reached. Skipping '{keyword}'.")
            return

        print(f"\n{'='*50}")
        print(f"  BULK APPLY: '{keyword}'  (up to {max_pages} pages)")
        mins_left = max(0, int(self._time_remaining() // 60))
        print(f"  Time remaining : ~{mins_left} min")
        print(f"  Pages applied  : {self.pages_applied}/{MAX_TOTAL_PAGES}")
        print(f"{'='*50}")

        # Search via search bar
        if not self._search_keyword(keyword):
            self._recovery_wait("search failed")
            return

        # Iterate pages
        for page_num in range(1, max_pages + 1):
            if self._should_stop():
                print("  ⏱ Limit reached. Stopping.")
                return
            if not self._is_page_alive():
                print("  ! Page died. Stopping keyword.")
                return

            print(f"\n--- Page {page_num}/{max_pages} for '{keyword}' "
                  f"(total: {self.pages_applied}/{MAX_TOTAL_PAGES}) ---")
            self._safe_wait(5000)

            # Select all
            if not self._click_select_all():
                self._recovery_wait("Select all not found")
                continue

            # Quick Apply
            if not self._click_quick_apply():
                self._recovery_wait("Quick Apply not found")
                continue

            # Confirm & Apply (on the SAME page, not a new tab)
            if self._click_confirm_apply():
                self.pages_applied += 1
                print(f"  📊 Pages applied: {self.pages_applied}/{MAX_TOTAL_PAGES}")
                if self._should_stop():
                    print("  ✅ 3-page limit reached! Closing.")
                    return
            else:
                self._recovery_wait("Confirm & Apply not found")

            self._safe_wait(5000)

            # Next page (unless last)
            if page_num < max_pages:
                if not self._go_next_page():
                    print(f"  -> No more pages for '{keyword}'.")
                    break

    # ─── Search ───────────────────────────────────────────────────────────

    def _search_keyword(self, keyword: str) -> bool:
        print(f"  -> Searching for '{keyword}'...")
        try:
            self.page.goto("https://www.foundit.in/")
            self._safe_wait(3000)
        except Exception:
            return False

        search_selectors = [
            "input[placeholder*='Search']",
            "input[placeholder*='search']",
            "input[name*='query']",
            "input[type='search']",
            "#heroSectionDesktop input",
        ]

        search_input = None
        for sel in search_selectors:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    search_input = loc
                    print(f"  -> Found search bar: {sel}")
                    break
            except Exception:
                continue

        if not search_input:
            print("  ! Search bar not found. Falling back to URL.")
            encoded = urllib.parse.quote_plus(keyword)
            self.page.goto(
                f"https://www.foundit.in/srp/results?query={encoded}&location=India"
            )
            self._safe_wait(8000)
            return True

        try:
            search_input.click()
            search_input.fill("")
            self._safe_wait(500)
            search_input.fill(keyword)
            self._safe_wait(1000)
        except Exception as e:
            print(f"  ! Error filling search bar: {e}")
            return False

        try:
            btn = self.page.locator(
                "button[type='submit'], button:has-text('Search'), "
                "button[aria-label='Search']"
            ).first
            if btn.count() > 0 and btn.is_visible():
                btn.click(force=True)
            else:
                search_input.press("Enter")
        except Exception:
            try:
                search_input.press("Enter")
            except Exception:
                return False

        print(f"  -> Search submitted for '{keyword}'.")
        self._safe_wait(8000)
        return True

    # ─── Select All ───────────────────────────────────────────────────────

    def _click_select_all(self) -> bool:
        print("  -> Looking for 'Select all'...")
        try:
            self.page.evaluate("window.scrollTo(0, 200)")
            self._safe_wait(1000)
        except Exception:
            pass

        for sel in [
            "text='Select all'",
            "text='Select All'",
            "label:has-text('Select all')",
            "label:has-text('Select All')",
        ]:
            try:
                loc = self.page.locator(sel).first
                if loc.count() > 0 and loc.is_visible():
                    loc.click(force=True)
                    print("  ✓ Clicked 'Select all'.")
                    self._safe_wait(2000)
                    return True
            except Exception:
                continue

        # JS fallback
        try:
            clicked = self.page.evaluate("""() => {
                const els = document.querySelectorAll('label, span, div');
                for (const el of els) {
                    if (/select\\s+all/i.test(el.innerText) && el.offsetParent !== null) {
                        el.click();
                        return true;
                    }
                }
                return false;
            }""")
            if clicked:
                print("  ✓ Clicked 'Select all' (JS).")
                self._safe_wait(2000)
                return True
        except Exception:
            pass

        print("  ! 'Select all' not found.")
        return False

    # ─── Quick Apply ──────────────────────────────────────────────────────

    def _click_quick_apply(self) -> bool:
        print("  -> Looking for 'Quick Apply'...")

        for sel in [
            "#bulk_apply_buttons button:has-text('Quick Apply')",
            ".bulkApplyButtons button:has-text('Quick Apply')",
            "button:has-text('Quick Apply')",
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    try:
                        label = btn.inner_text().strip()
                    except Exception:
                        label = "Quick Apply"
                    print(f"  ✓ Clicking '{label}'...")
                    btn.click(force=True)
                    self._safe_wait(3000)
                    return True
            except Exception:
                continue

        # JS fallback
        try:
            clicked = self.page.evaluate("""() => {
                const btns = document.querySelectorAll('button');
                for (const b of btns) {
                    if (/quick\\s*apply/i.test(b.innerText) && b.offsetParent !== null) {
                        b.click();
                        return b.innerText.trim();
                    }
                }
                return null;
            }""")
            if clicked:
                print(f"  ✓ Clicked '{clicked}' (JS).")
                self._safe_wait(3000)
                return True
        except Exception:
            pass

        print("  ! 'Quick Apply' not found.")
        return False

    # ─── Confirm & Apply ──────────────────────────────────────────────────

    def _click_confirm_apply(self) -> bool:
        """
        Click 'Confirm & Apply' on the SAME page (self.page).
        The modal opens on the current page — do NOT look at new tabs.
        """
        print("  -> Looking for 'Confirm & Apply'...")

        # Wait for the modal to fully render
        self._safe_wait(5000)

        # Scroll page down to make modal button visible
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._safe_wait(2000)
        except Exception:
            pass

        # Also scroll inside any modal container
        try:
            self.page.evaluate("""() => {
                const modals = document.querySelectorAll(
                    '[role="dialog"], .modal, .modalContentContainer, ' +
                    '.quic-modal-container, [class*="modal"]'
                );
                for (const m of modals) {
                    m.scrollTop = m.scrollHeight;
                }
            }""")
            self._safe_wait(1000)
        except Exception:
            pass

        # Attempt 1: JS — find any visible button with 'Confirm' on self.page
        try:
            clicked = self.page.evaluate("""() => {
                const buttons = document.querySelectorAll('button, input[type="submit"]');
                for (const btn of buttons) {
                    const text = (btn.innerText || btn.value || '').trim();
                    if (text.toLowerCase().includes('confirm') &&
                        btn.offsetParent !== null) {
                        btn.scrollIntoView({block: 'center'});
                        btn.click();
                        return text;
                    }
                }
                return null;
            }""")
            if clicked:
                print(f"  ✓ Clicked '{clicked}' (JS).")
                print("  -> Waiting for applications to complete...")
                self._safe_wait(10000)
                self._log_bulk_apply()
                print(f"  ✓ Batch applied! Total so far: ~{self.jobs_applied}")
                return True
        except Exception:
            pass

        # Attempt 2: Playwright selectors on self.page
        for sel in [
            "button:has-text('Confirm')",
            "button:has-text('Confirm & Apply')",
            "button:has-text('Confirm and Apply')",
            "button[type='submit']:has-text('Apply')",
            "input[type='submit']",
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0:
                    btn.scroll_into_view_if_needed()
                    self._safe_wait(500)
                    if btn.is_visible():
                        btn.click(force=True)
                        print(f"  ✓ Clicked confirm ({sel}).")
                        print("  -> Waiting for applications to complete...")
                        self._safe_wait(10000)
                        self._log_bulk_apply()
                        print(f"  ✓ Batch applied! Total so far: ~{self.jobs_applied}")
                        return True
            except Exception:
                continue

        # Debug: dump visible buttons on self.page
        try:
            btns = self.page.evaluate("""() => {
                return Array.from(document.querySelectorAll('button'))
                    .filter(b => b.offsetParent !== null)
                    .map(b => b.innerText.trim())
                    .filter(t => t.length > 0)
                    .slice(0, 25);
            }""")
            print(f"  [debug] Visible buttons on main page: {btns}")
        except Exception:
            pass

        # Also check if Quick Apply opened any modal/overlay that is still loading
        try:
            url = self.page.url
            print(f"  [debug] Current URL: {url[:100]}")
        except Exception:
            pass

        print("  ! 'Confirm & Apply' not found.")
        return False

    def _log_bulk_apply(self):
        self.jobs_applied += 15
        try:
            from app.utils import log_application
            url = self.page.url if self.page else "https://foundit.in"
            log_application("Foundit", "Bulk Apply (15 jobs)", "Multiple Companies", url, "submitted")
        except Exception:
            pass

    # ─── Next Page ────────────────────────────────────────────────────────

    def _go_next_page(self) -> bool:
        print("  -> Looking for 'Next' page button...")
        try:
            self.page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            self._safe_wait(2000)
        except Exception:
            pass

        for sel in [
            ".pagination button:has-text('Next')",
            "button:has-text('Next')",
            "button[aria-label='Next']",
            ".pagination button:last-child",
            "a:has-text('Next')",
        ]:
            try:
                btn = self.page.locator(sel).first
                if btn.count() > 0 and btn.is_visible():
                    btn.click(force=True)
                    print("  ✓ Clicked 'Next'. Loading next page...")
                    self._safe_wait(8000)
                    return True
            except Exception:
                continue

        print("  ! 'Next' button not found.")
        return False
