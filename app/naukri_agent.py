import os
import json
import time
from datetime import datetime
from playwright.sync_api import sync_playwright, Page, Frame

# ─────────────────────────────────────────────────────────────────────────────
# Selectors for the questionnaire panel that Naukri opens on the RIGHT side
# after clicking Apply.  We intentionally exclude the top search bar and any
# disabled / hidden fields so we don't accidentally fill unrelated inputs.
# ─────────────────────────────────────────────────────────────────────────────

# Naukri questionnaire containers (right-side panel / chatbot modal)
_QS_PANEL_SELECTORS = [
    ".chatbot_DrawerContentWrapper",
    ".bot-container",
    ".chat-window",
    ".apply-form",
    ".apply-questionnaire",
    ".questionnaire-container",
    "[class*='chatbot']",
    "[class*='questionnaire']",
    "[class*='applyForm']",
    "[class*='apply-form']",
    ".modal-body",
    ".drawer-content",
]

# Input selectors scoped inside the questionnaire panel
_INPUT_SEL    = 'input:not([type="hidden"]):not([type="submit"]):not([type="button"]):not([disabled]):not([readonly])'
_SELECT_SEL   = 'select:not([disabled])'
_TEXTAREA_SEL = 'textarea:not([disabled]), [contenteditable="true"], [contenteditable="plaintext-only"]'

# Buttons that advance the wizard (includes div.sendMsg for chatbot UI)
_SAVE_NEXT_SEL = (
    "button:has-text('Save'), "
    "button:has-text('Next'), "
    "button:has-text('Continue'), "
    "button:has-text('Proceed'), "
    "div.sendMsg:has-text('Save'), "
    "div.sendMsg:has-text('Next')"
)
_SUBMIT_SEL = (
    "button:has-text('Submit'):not([class*='search']), "
    "button:has-text('Submit Application'), "
    "div.sendMsg:has-text('Submit')"
)

# ── Chatbot-specific selectors (from the Naukri screenshot DOM) ──────────
_CHATBOT_DRAWER_SEL  = ".chatbot_DrawerContentWrapper, .chatbot_Drawer, [class*='chatbot_Drawer']"
_CHATBOT_MSG_SEL     = "[class*='chatbot_MessageContainer'], .chatbot_MessageContainer"


class NaukriApplyAgent:
    def __init__(self, profile: dict, ai_answerer=None):
        self.profile = profile
        self.ai_answerer = ai_answerer
        self.jobs_applied = 0
        self.playwright = None
        self.browser = None
        self.context = None
        self.page = None
        self.qa_log = []  # Stores all Q&A for review
        self._current_job_url = ""  # Track current job being processed
        self._qa_log_path = os.path.join("data", "qa_log.json")

    # ─────────────────────────────────────────────────────────────────────────
    # Browser lifecycle
    # ─────────────────────────────────────────────────────────────────────────

    def start(self):
        self.playwright = sync_playwright().start()
        user_data_dir = os.path.abspath(os.getenv("NAUKRI_PROFILE_DIR", "data/naukri-browser-profile"))
        os.makedirs(user_data_dir, exist_ok=True)
        
        # Clean up dangling locks from aborted previous processes
        for lock_file in ["SingletonLock", "SingletonCookie"]:
            lock_path = os.path.join(user_data_dir, lock_file)
            try:
                if os.path.exists(lock_path):
                    os.remove(lock_path)
            except Exception:
                pass

        print("Launching browser for Naukri...")
        self.context = self.playwright.chromium.launch_persistent_context(
            user_data_dir=user_data_dir,
            headless=(os.getenv("HEADLESS", "false").lower() == "true"),
            args=["--start-maximized", "--disable-blink-features=AutomationControlled"],
        )
        self.page = self.context.new_page()
        self.page.set_default_timeout(60000)

    def close(self):
        # Save the Q&A log before closing
        self._save_qa_log()
        print(f"\n==========================================")
        print(f"Finished. Applied to {self.jobs_applied} Naukri jobs.")
        print(f"Q&A log saved to: {self._qa_log_path}")
        print(f"==========================================")
        if self.context:
            self.context.close()
        if self.playwright:
            self.playwright.stop()

    def _log_qa(self, question: str, answer: str, input_type: str = "text",
                options: list = None):
        """Record a question-answer pair for later review."""
        entry = {
            "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "job_url": self._current_job_url,
            "question": question,
            "answer": answer,
            "input_type": input_type,
        }
        if options:
            entry["available_options"] = options
        self.qa_log.append(entry)
        # Also append to file incrementally (in case of crash)
        self._save_qa_log()

    def _save_qa_log(self):
        """Write the Q&A log to a JSON file."""
        try:
            os.makedirs(os.path.dirname(self._qa_log_path), exist_ok=True)
            with open(self._qa_log_path, "w", encoding="utf-8") as f:
                json.dump(self.qa_log, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"  x Could not save Q&A log: {e}")

    def _is_page_alive(self, page: Page) -> bool:
        """Check if a page is still open and usable."""
        try:
            page.evaluate("() => true")
            return True
        except Exception:
            return False

    # ─────────────────────────────────────────────────────────────────────────
    # Login
    # ─────────────────────────────────────────────────────────────────────────

    def login(self):
        print("Navigating to Naukri login...")
        self.page.goto("https://www.naukri.com/nlogin/login")
        self.page.wait_for_timeout(3000)

        if "login" not in self.page.url.lower():
            print("✓ Already logged into Naukri.")
            return

        email    = os.getenv("NAUKARI_EMAIL", "") or os.getenv("LINKEDIN_EMAIL", "")
        password = os.getenv("NAUKARI_PASSWORD", "") or os.getenv("LINKEDIN_PASSWORD", "")

        if email and password:
            print(f"Injecting login credentials for {email}...")
            try:
                email_loc = self.page.locator("#usernameField, input[placeholder*='Email']")
                if email_loc.count() > 0:
                    email_loc.first.fill(email)

                pass_loc = self.page.locator("#passwordField, input[type='password']")
                if pass_loc.count() > 0:
                    pass_loc.first.fill(password)

                login_btn = self.page.locator("button[type='submit'], button:has-text('Login')")
                if login_btn.count() > 0:
                    login_btn.first.click()
            except Exception as e:
                print(f"Autofill warning: {e}")
        else:
            print("No credentials found in .env – please log in manually.")

        print("Waiting 30 seconds for manual login / OTP if needed...")
        self.page.wait_for_timeout(30000)

    # ─────────────────────────────────────────────────────────────────────────
    # Job search & loop
    # ─────────────────────────────────────────────────────────────────────────

    def search_jobs_direct(self, search_url: str):
        print(f"Searching: {search_url}")
        try:
            self.page.goto(search_url, timeout=60000)
            self.page.wait_for_selector(
                ".srp-jobtuple-wrapper, .cust-job-tuple, article.jobTuple, .jobTuple",
                timeout=15000,
            )
        except Exception as e:
            print(f"Page load / selector warning: {e}")

        self.page.wait_for_timeout(4000)
        print("Starting Naukri job processing loop...")

        found_cards = None
        for sel in [".srp-jobtuple-wrapper", ".cust-job-tuple", "article.jobTuple", ".jobTuple"]:
            cards = self.page.locator(sel)
            if cards.count() > 0:
                found_cards = cards
                break

        if found_cards is None:
            print("No job cards found on page.")
            return

        count = found_cards.count()
        print(f"Found {count} job cards!")

        for i in range(min(15, count)):
            # Check if the main search page is still alive
            if not self._is_page_alive(self.page):
                print("  x Main search page was closed. Stopping.")
                break

            try:
                print(f"\n[{i+1}/{count}] Opening job...")

                with self.context.expect_page() as new_page_info:
                    base_card  = found_cards.nth(i)
                    title_link = base_card.locator("a.title, a.titleQA, a").first
                    if title_link.count() > 0:
                        title_link.click(force=True)
                    else:
                        base_card.click(force=True)

                new_page = new_page_info.value
                new_page.wait_for_load_state()
                new_page.wait_for_timeout(2000)

                self._process_naukri_card(new_page)

                # ── Explore sidebar suggestions ──────────────────────────────
                if self._is_page_alive(new_page):
                    try:
                        suggestions = new_page.locator(
                            ".jobs-you-might-be-interested-in a.title, "
                            ".similar-jobs a.title, "
                            ".suggested-jobs a.title, "
                            "ul.job-tuple-list a.title"
                        )
                        if suggestions.count() > 0:
                            suggest_count = suggestions.count()
                            print(f"  -> Found {suggest_count} suggestions. Exploring top 3...")
                            for j in range(min(3, suggest_count)):
                                try:
                                    with self.context.expect_page() as sub_page_info:
                                        suggestions.nth(j).click(force=True)
                                    sub_page = sub_page_info.value
                                    sub_page.wait_for_load_state()
                                    sub_page.wait_for_timeout(2000)
                                    print(f"    -> Suggestion {j+1}:")
                                    self._process_naukri_card(sub_page)
                                    if self._is_page_alive(sub_page):
                                        sub_page.close()
                                    if self._is_page_alive(new_page):
                                        new_page.wait_for_timeout(1000)
                                except Exception as ex:
                                    print(f"    x Suggestion err: {ex}")
                    except Exception as ex:
                        print(f"  x Suggestions err: {ex}")

                if self._is_page_alive(new_page):
                    new_page.close()
                self.page.wait_for_timeout(2000)

            except Exception as e:
                print(f"  x Error on card {i+1}: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # Core card processor
    # ─────────────────────────────────────────────────────────────────────────

    def _process_naukri_card(self, target_page: Page):
        """
        1. Click the native Apply button (skip external company-site buttons).
        2. Wait for the right-side questionnaire panel to appear.
        3. Detect if it's a chatbot or a standard form.
        4. Fill the questionnaire accordingly.
        5. Tab is closed by the caller.
        """
        try:
            self._current_job_url = target_page.url
        except Exception:
            self._current_job_url = "unknown"

        print("  -> Waiting for Apply button (up to 60 s)...")
        target_page.wait_for_timeout(4000)

        # ── STEP 1: Try many different Apply button selectors ─────────────
        clicked = self._click_apply_button(target_page)

        if not clicked:
            print("  -> No native Apply button found (external or already applied).")
            return

        self.jobs_applied += 1
        try:
            from app.utils import log_application
            title = target_page.title() or "Unknown Naukri Job"
            log_application("Naukri", title, "Unknown Company", self._current_job_url, "submitted")
        except Exception:
            pass
        print("  -> Apply clicked. Looking for questionnaire panel...")

        # ── STEP 2: Wait for the questionnaire panel to appear ────────────
        panel_appeared = self._wait_for_questionnaire_panel(target_page)
        if not panel_appeared:
            # Maybe no questionnaire; look for a direct Submit
            self._try_final_submit(target_page)
            return

        # ── STEP 3: Detect which kind of questionnaire it is ──────────────
        is_chatbot = self._detect_chatbot(target_page)

        if is_chatbot:
            print("  -> Detected CHATBOT-style questionnaire.")
            self._answer_chatbot_loop(target_page)
        else:
            print("  -> Detected FORM-style questionnaire.")
            self._answer_questionnaire_loop(target_page)

    def _click_apply_button(self, target_page: Page) -> bool:
        """
        Robustly find and click the Apply button on a Naukri job page.
        Returns True if clicked successfully.
        """
        # Strategy 1: Try the #apply-button ID first (most reliable)
        try:
            apply_by_id = target_page.locator("#apply-button")
            if apply_by_id.count() > 0 and apply_by_id.first.is_visible():
                btn_text = apply_by_id.first.inner_text().strip().lower()
                if "applied" not in btn_text:
                    print(f"  -> Clicking Apply button (by ID): '{btn_text}'")
                    apply_by_id.first.scroll_into_view_if_needed()
                    target_page.wait_for_timeout(500)
                    apply_by_id.first.evaluate("node => node.click()")
                    target_page.wait_for_timeout(3000)
                    return True
        except Exception as e:
            print(f"  x Apply by ID err: {e}")

        # Strategy 2: Look for buttons/elements with "Apply" text using JS
        try:
            clicked_js = target_page.evaluate("""() => {
                // Find all clickable elements with "Apply" text
                const candidates = [];
                const allEls = document.querySelectorAll('button, a, div[role="button"], [class*="apply"]');
                for (const el of allEls) {
                    const text = (el.innerText || '').trim().toLowerCase();
                    const cls = (el.className || '').toLowerCase();
                    // Must contain "apply"
                    if (!text.includes('apply') && !cls.includes('apply')) continue;
                    // Skip if already applied
                    if (text.includes('already applied') || text.includes('applied')) continue;
                    // Skip if it's a company-site / external apply
                    if (cls.includes('company-site') || cls.includes('external')) continue;
                    // Skip if not visible
                    const rect = el.getBoundingClientRect();
                    if (rect.width === 0 || rect.height === 0) continue;
                    // Prefer buttons with specific apply classes
                    const priority = cls.includes('apply') ? 0 : 1;
                    candidates.push({el, text, priority});
                }
                // Sort by priority (apply-class buttons first)
                candidates.sort((a, b) => a.priority - b.priority);
                if (candidates.length > 0) {
                    candidates[0].el.click();
                    return candidates[0].text;
                }
                return null;
            }""")
            if clicked_js:
                print(f"  -> Clicked Apply button (JS): '{clicked_js}'")
                target_page.wait_for_timeout(3000)
                return True
        except Exception as e:
            print(f"  x Apply by JS err: {e}")

        # Strategy 3: Playwright locator-based approach (broader selectors)
        apply_selectors = [
            "button:has-text('Apply'):not(:has-text('Applied'))",
            "button:has-text('Save & Apply')",
            "a:has-text('Apply'):not(:has-text('Applied'))",
            "div[class*='apply']:has-text('Apply'):not(:has-text('Applied'))",
            "[class*='apply-button']:has-text('Apply')",
            "[class*='applyBtn']:has-text('Apply')",
            "button[class*='apply']",
        ]

        for sel in apply_selectors:
            try:
                loc = target_page.locator(sel)
                for k in range(loc.count()):
                    try:
                        btn = loc.nth(k)
                        if btn.is_visible():
                            btn_text = btn.inner_text().strip().lower()
                            if "applied" in btn_text or "save job" in btn_text:
                                continue
                            print(f"  -> Clicking Apply button (locator '{sel}'): '{btn_text}'")
                            btn.scroll_into_view_if_needed()
                            target_page.wait_for_timeout(300)
                            try:
                                btn.evaluate("node => node.click()")
                            except Exception:
                                btn.click(force=True)
                            target_page.wait_for_timeout(3000)
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        return False

    # ─────────────────────────────────────────────────────────────────────────
    # Questionnaire helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _wait_for_questionnaire_panel(self, page: Page, timeout_ms: int = 15000) -> bool:
        """Wait up to timeout_ms for questionnaire panel. Returns True if found."""
        combined = ", ".join(_QS_PANEL_SELECTORS)
        try:
            page.wait_for_selector(combined, state="visible", timeout=timeout_ms)
            print("  -> Questionnaire panel detected.")
            return True
        except Exception:
            print("  -> No questionnaire panel appeared within timeout.")
            return False

    def _detect_chatbot(self, page: Page) -> bool:
        """Check if the questionnaire is a chatbot-style drawer (vs a standard form)."""
        try:
            chatbot = page.locator(_CHATBOT_DRAWER_SEL)
            if chatbot.count() > 0:
                for i in range(chatbot.count()):
                    try:
                        if chatbot.nth(i).is_visible():
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def _find_panel(self, page: Page):
        """Return the first visible questionnaire panel locator, or None."""
        for sel in _QS_PANEL_SELECTORS:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    for i in range(loc.count()):
                        try:
                            if loc.nth(i).is_visible():
                                return loc.nth(i)
                        except Exception:
                            continue
            except Exception:
                pass
        return None

    # ─────────────────────────────────────────────────────────────────────────
    # CHATBOT-style questionnaire loop
    # ─────────────────────────────────────────────────────────────────────────

    def _answer_chatbot_loop(self, page: Page):
        """
        Handle the Naukri chatbot-style questionnaire.
        Each step:
          1. Read the latest bot question.
          2. Detect the input type (text, radio, checkbox, select, etc).
          3. Fill/select the answer accordingly.
          4. Click Save.
          5. Repeat until the chatbot closes.
        """
        MAX_STEPS = 25
        last_question = ""
        stuck_count = 0

        for step in range(MAX_STEPS):
            print(f"  -> Chatbot step {step + 1}...")
            page.wait_for_timeout(2000)

            if not self._is_page_alive(page):
                print("  -> Page closed during chatbot flow.")
                break

            # ── 1. Check if the chatbot drawer is still visible ───────────
            chatbot_visible = False
            try:
                drawer = page.locator(_CHATBOT_DRAWER_SEL)
                for di in range(drawer.count()):
                    try:
                        if drawer.nth(di).is_visible():
                            chatbot_visible = True
                            break
                    except Exception:
                        continue
            except Exception:
                pass

            if not chatbot_visible:
                print("  -> Chatbot drawer closed. Done.")
                break

            # ── 2. Read the latest question from chat messages ────────────
            question = self._read_last_chatbot_question(page)
            if not question:
                print("  -> No question found in chatbot. Trying Save/Submit...")
                if not self._click_chatbot_save(page):
                    self._try_final_submit(page)
                break

            # If same question as last time, the UI might be stuck
            if question == last_question:
                stuck_count += 1
                if stuck_count >= 3:
                    print(f"  -> Stuck on same question 3 times. Ending loop.")
                    self._click_chatbot_save(page)
                    break
                print(f"  -> Same question repeated ({stuck_count}/3). Trying to advance...")
                self._click_chatbot_save(page)
                page.wait_for_timeout(2000)
                continue
            else:
                stuck_count = 0

            last_question = question
            print(f"  -> Q: '{question[:100]}'")

            # ── 3. Detect the input type and fill accordingly ─────────────
            handled = self._handle_chatbot_question(page, question)


            if not handled:
                print("  -> Could not find any input for this question. Trying Save anyway...")

            # ── 4. Click Save to submit this answer ───────────────────────
            page.wait_for_timeout(500)
            self._click_chatbot_save(page)

            # ── 5. Wait for next question to appear ───────────────────────
            page.wait_for_timeout(2000)

        else:
            print("  -> Reached max chatbot steps. Attempting final submit...")
            self._try_final_submit(page)

    def _handle_chatbot_question(self, page: Page, question: str) -> bool:
        """
        Detect what kind of input the chatbot is presenting and fill it:
        - Radio buttons (Yes/No, multiple choice)
        - Checkboxes (multi-select)
        - Select/dropdown
        - Text input / number input
        - Textarea / contenteditable
        Returns True if an input was found and filled.
        """
        # Scope to the chatbot panel
        panel = page.locator(_CHATBOT_DRAWER_SEL)
        if panel.count() == 0:
            panel = page  # fallback to full page
        else:
            panel = panel.first

        # ── Check for RADIO BUTTONS ───────────────────────────────────────
        radios = panel.locator('input[type="radio"]')
        radio_count = radios.count()
        if radio_count > 0:
            visible_radios = []
            for ri in range(radio_count):
                try:
                    if radios.nth(ri).is_visible() or radios.nth(ri).is_enabled():
                        visible_radios.append(ri)
                except Exception:
                    continue

            if visible_radios:
                print(f"  -> Found {len(visible_radios)} radio buttons")
                return self._handle_chatbot_radio(page, panel, radios, visible_radios, question)

        # ── Check for CHECKBOXES ──────────────────────────────────────────
        checkboxes = panel.locator('input[type="checkbox"]')
        checkbox_count = checkboxes.count()
        if checkbox_count > 0:
            visible_cbs = []
            for ci in range(checkbox_count):
                try:
                    if checkboxes.nth(ci).is_visible() or checkboxes.nth(ci).is_enabled():
                        visible_cbs.append(ci)
                except Exception:
                    continue

            if visible_cbs:
                print(f"  -> Found {len(visible_cbs)} checkboxes")
                return self._handle_chatbot_checkbox(page, panel, checkboxes, visible_cbs, question)

        # ── Check for SELECT/DROPDOWN ─────────────────────────────────────
        selects = panel.locator('select:not([disabled])')
        if selects.count() > 0:
            for si in range(selects.count()):
                try:
                    if selects.nth(si).is_visible():
                        print(f"  -> Found dropdown")
                        return self._handle_chatbot_select(page, selects.nth(si), question)
                except Exception:
                    continue

        # ── Check for CLICKABLE OPTION BUTTONS (non-standard radio) ───────
        # Some chatbots present options as clickable divs/spans/buttons
        option_handled = self._handle_chatbot_clickable_options(page, panel, question)
        if option_handled:
            return True

        # ── Check for TEXT INPUT / NUMBER / TEXTAREA ──────────────────────
        typed = self._type_chatbot_answer(page, panel, question)
        return typed

    def _handle_chatbot_radio(self, page, panel, radios, visible_indices, question: str) -> bool:
        """Handle radio button groups inside the chatbot."""
        try:
            # Group by name attribute
            groups = {}
            for ri in visible_indices:
                radio = radios.nth(ri)
                name = radio.get_attribute("name") or f"unnamed_{ri}"
                if name not in groups:
                    groups[name] = []
                groups[name].append(ri)

            for group_name, indices in groups.items():
                # Collect labels for each option
                option_labels = []
                for ri in indices:
                    radio = radios.nth(ri)
                    label = self._get_radio_label(radio, panel)
                    option_labels.append(label or f"option_{ri}")

                print(f"  -> Radio group '{group_name}': options = {option_labels}")

                # Ask AI to pick the best option
                chosen_label = ""
                if self.ai_answerer and self.ai_answerer.enabled:
                    try:
                        chosen_label = self.ai_answerer.choose_option(question, option_labels, self.profile)
                    except Exception as e:
                        print(f"  x AI radio error: {e}")

                if not chosen_label:
                    # Rule-based fallback
                    q_lower = question.lower()

                    # Notice period → prefer "3 Months"
                    if any(kw in q_lower for kw in ["notice period", "notice"]):
                        for oi, lbl in enumerate(option_labels):
                            if "3 month" in lbl.lower() or "90" in lbl.lower():
                                chosen_label = lbl
                                break
                    # Join immediately / available → prefer "Yes"
                    elif any(kw in q_lower for kw in ["join", "available", "willing", "comfortable",
                                                       "relocat", "authoriz", "authoris", "visa",
                                                       "work permit", "worked", "experience"]):
                        for oi, lbl in enumerate(option_labels):
                            if lbl.lower().strip() in ("yes", "true", "1"):
                                chosen_label = lbl
                                break

                    # General fallback: prefer "Yes" if present, else first option
                    if not chosen_label:
                        for oi, lbl in enumerate(option_labels):
                            if lbl.lower().strip() in ("yes", "true", "1"):
                                chosen_label = lbl
                                break
                    if not chosen_label:
                        chosen_label = option_labels[0]  # pick first

                # Log the radio selection
                self._log_qa(question, chosen_label, input_type="radio", options=option_labels)

                # Click the chosen radio
                clicked = False
                for oi, ri in enumerate(indices):
                    lbl = option_labels[oi]
                    if lbl.lower().strip() == chosen_label.lower().strip() or \
                       chosen_label.lower().strip() in lbl.lower().strip() or \
                       lbl.lower().strip() in chosen_label.lower().strip():
                        try:
                            radio = radios.nth(ri)
                            radio.check(force=True)
                            print(f"  -> Selected radio: '{lbl}'")
                            clicked = True
                            break
                        except Exception:
                            # Try clicking the label instead
                            try:
                                rid = radio.get_attribute("id")
                                if rid:
                                    label_el = panel.locator(f'label[for="{rid}"]')
                                    if label_el.count() > 0:
                                        label_el.first.click(force=True)
                                        print(f"  -> Selected radio via label: '{lbl}'")
                                        clicked = True
                                        break
                            except Exception:
                                pass

                if not clicked:
                    # Just click the first visible one
                    try:
                        radios.nth(indices[0]).check(force=True)
                        print(f"  -> Selected first radio as fallback")
                        clicked = True
                    except Exception as e:
                        print(f"  x Radio click failed: {e}")

                return clicked

        except Exception as e:
            print(f"  x Radio handling error: {e}")
        return False

    def _handle_chatbot_checkbox(self, page, panel, checkboxes, visible_indices, question: str) -> bool:
        """Handle checkboxes inside the chatbot (multi-select)."""
        try:
            option_labels = []
            for ci in visible_indices:
                cb = checkboxes.nth(ci)
                label = self._get_radio_label(cb, panel)  # same logic works for checkboxes
                option_labels.append(label or f"option_{ci}")

            print(f"  -> Checkbox options: {option_labels}")

            # Ask AI which ones to select
            selected_labels = []
            if self.ai_answerer and self.ai_answerer.enabled:
                try:
                    # Ask AI for all that apply
                    chosen = self.ai_answerer.choose_option(
                        f"{question} (Select all that apply. Return comma-separated values.)",
                        option_labels, self.profile
                    )
                    if chosen:
                        selected_labels = [c.strip() for c in chosen.split(",")]
                except Exception as e:
                    print(f"  x AI checkbox error: {e}")

            if not selected_labels:
                # Fallback: check all of them (or the first one)
                selected_labels = option_labels[:1]

            checked_any = False
            for ci in visible_indices:
                cb = checkboxes.nth(ci)
                lbl = option_labels[visible_indices.index(ci)] if ci in visible_indices else ""
                should_check = any(
                    sl.lower().strip() in lbl.lower().strip() or lbl.lower().strip() in sl.lower().strip()
                    for sl in selected_labels
                )
                if should_check or not selected_labels:
                    try:
                        cb.check(force=True)
                        print(f"  -> Checked: '{lbl}'")
                        checked_any = True
                    except Exception:
                        try:
                            cid = cb.get_attribute("id")
                            if cid:
                                label_el = panel.locator(f'label[for="{cid}"]')
                                if label_el.count() > 0:
                                    label_el.first.click(force=True)
                                    print(f"  -> Checked via label: '{lbl}'")
                                    checked_any = True
                        except Exception:
                            pass

            self._log_qa(question, ", ".join(selected_labels), input_type="checkbox", options=option_labels)
            return checked_any

        except Exception as e:
            print(f"  x Checkbox handling error: {e}")
        return False

    def _handle_chatbot_select(self, page, select_el, question: str) -> bool:
        """Handle a dropdown <select> inside the chatbot."""
        try:
            opts = select_el.locator("option")
            opt_count = opts.count()
            if opt_count <= 1:
                return False

            option_texts = []
            for oi in range(opt_count):
                txt = opts.nth(oi).inner_text().strip()
                if txt:
                    option_texts.append(txt)

            print(f"  -> Dropdown options: {option_texts[:10]}...")

            chosen = ""
            if self.ai_answerer and self.ai_answerer.enabled:
                try:
                    chosen = self.ai_answerer.choose_option(question, option_texts, self.profile)
                except Exception as e:
                    print(f"  x AI dropdown error: {e}")

            if chosen:
                for oi in range(opt_count):
                    if opts.nth(oi).inner_text().strip() == chosen:
                        val = opts.nth(oi).get_attribute("value")
                        select_el.select_option(val)
                        print(f"  -> Selected dropdown: '{chosen}'")
                        self._log_qa(question, chosen, input_type="select", options=option_texts)
                        return True

            # Fallback: select first non-empty option
            if opt_count > 1:
                val = opts.nth(1).get_attribute("value")
                select_el.select_option(val)
                chosen_fallback = opts.nth(1).inner_text().strip()
                print(f"  -> Selected dropdown fallback: '{chosen_fallback}'")
                self._log_qa(question, chosen_fallback, input_type="select", options=option_texts)
                return True

        except Exception as e:
            print(f"  x Dropdown handling error: {e}")
        return False

    def _handle_chatbot_clickable_options(self, page, panel, question: str) -> bool:
        """
        Handle non-standard option buttons (divs/spans/buttons that act as radio choices).
        Some chatbots render options as clickable chips/buttons instead of real radio inputs.
        """
        # Look for option-like clickable elements inside the chatbot
        option_selectors = [
            "[class*='option']",
            "[class*='chip']",
            "[class*='choice']",
            "[class*='answer-option']",
            "[class*='bot-option']",
            "[class*='quickReply']",
            "[class*='quick-reply']",
            "button[class*='option']",
        ]

        for sel in option_selectors:
            try:
                options = panel.locator(sel)
                cnt = options.count()
                if cnt < 2:
                    continue  # Need at least 2 options to be a choice

                visible_options = []
                for oi in range(cnt):
                    try:
                        opt = options.nth(oi)
                        if opt.is_visible():
                            txt = opt.inner_text().strip()
                            if txt and len(txt) < 100:  # Labels should be short-ish
                                visible_options.append((oi, txt))
                    except Exception:
                        continue

                if len(visible_options) < 2:
                    continue

                option_labels = [txt for _, txt in visible_options]
                print(f"  -> Found clickable options: {option_labels}")

                # Ask AI
                chosen = ""
                if self.ai_answerer and self.ai_answerer.enabled:
                    try:
                        chosen = self.ai_answerer.choose_option(question, option_labels, self.profile)
                    except Exception:
                        pass

                if not chosen:
                    # Fallback: pick "Yes" if available, else first
                    for lbl in option_labels:
                        if lbl.lower().strip() in ("yes", "true"):
                            chosen = lbl
                            break
                    if not chosen:
                        chosen = option_labels[0]

                # Click the chosen option
                for oi, txt in visible_options:
                    if txt.lower().strip() == chosen.lower().strip() or \
                       chosen.lower().strip() in txt.lower().strip():
                        try:
                            options.nth(oi).click(force=True)
                            print(f"  -> Clicked option: '{txt}'")
                            return True
                        except Exception:
                            pass

                # Fallback: click first visible option
                try:
                    idx = visible_options[0][0]
                    options.nth(idx).click(force=True)
                    print(f"  -> Clicked first option as fallback: '{visible_options[0][1]}'")
                    return True
                except Exception:
                    pass

            except Exception:
                continue

        return False

    def _type_chatbot_answer(self, page: Page, panel, question: str) -> bool:
        """
        Find the chatbot text input box and type the answer.
        Tries multiple selectors and input types.
        """
        # Get the answer first
        answer = self._get_answer_for_question(question)
        print(f"  -> A: '{answer[:80]}'")
        self._log_qa(question, answer, input_type="text")

        input_selectors = [
            # Naukri-specific from the screenshot DOM
            "[class*='sendMsgbtn_container'] input",
            "[class*='InputBox'] input",
            "[class*='sendMsg'] input",
            # Standard chatbot input patterns  
            ".chatbot_DrawerContentWrapper input[type='text']",
            ".chatbot_DrawerContentWrapper input[type='number']",
            ".chatbot_DrawerContentWrapper input:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='submit']):not([type='button'])",
            ".chatbot_DrawerContentWrapper textarea",
            ".chatbot_DrawerContentWrapper [contenteditable='true']",
            "[class*='chatbot'] input[type='text']",
            "[class*='chatbot'] input[type='number']",
            "[class*='chatbot'] input:not([type='hidden']):not([type='radio']):not([type='checkbox']):not([type='submit']):not([type='button'])",
            "[class*='chatbot'] textarea",
            "[class*='chatbot'] [contenteditable='true']",
            # Broadest fallback inside the panel
            ".chatbot_Drawer input:not([type='hidden']):not([type='radio']):not([type='checkbox'])",
            ".chatbot_Drawer textarea",
            # Also check inside the message container itself (inline inputs)
            "[class*='chatbot_MessageContainer'] input:not([type='hidden']):not([type='radio']):not([type='checkbox'])",
            "[class*='chatbot_MessageContainer'] textarea",
        ]

        for sel in input_selectors:
            try:
                loc = page.locator(sel)
                for i in range(loc.count()):
                    try:
                        el = loc.nth(i)
                        if el.is_visible():
                            tag = (el.evaluate("el => el.tagName") or "").lower()
                            is_contenteditable = el.get_attribute("contenteditable")

                            # Check if already has a value and clear it first
                            current_val = ""
                            try:
                                current_val = el.input_value() if tag in ("input", "textarea") else ""
                            except Exception:
                                pass

                            if is_contenteditable:
                                el.click()
                                page.wait_for_timeout(300)
                                page.keyboard.press("Meta+a")
                                page.keyboard.type(str(answer))
                            else:
                                el.fill(str(answer))

                            print(f"  -> Typed into '{sel}'")
                            return True
                    except Exception:
                        continue
            except Exception:
                continue

        return False

    def _read_last_chatbot_question(self, page: Page) -> str:
        """
        Read the latest bot question from the chatbot message area.
        The chatbot shows questions as chat bubbles. We want the last one
        that looks like a question or prompt text.
        """
        try:
            # Try multiple selectors for message bubbles
            msg_selectors = [
                # Naukri chatbot specific classes
                "[class*='chatbot_MessageContainer'] [class*='botMsg']",
                "[class*='chatbot_MessageContainer'] [class*='msg']",
                ".chatbot_MessageContainer .msg",
                ".msgWrap .msg",
                # Generic chatbot patterns
                "[class*='chatbot'] [class*='message']",
                "[class*='chat'] [class*='bot-message']",
                "[class*='chat-message']",
            ]

            for sel in msg_selectors:
                try:
                    msgs = page.locator(sel)
                    cnt = msgs.count()
                    if cnt > 0:
                        # Read messages from latest backwards to find the last bot question
                        for idx in range(cnt - 1, max(cnt - 8, -1), -1):
                            try:
                                txt = msgs.nth(idx).inner_text().strip()
                                if txt and len(txt) > 5:
                                    # Skip if it looks like the user's own answer (just a number, "Yes", etc)
                                    if len(txt) <= 4 and (txt.isdigit() or txt.lower() in ("yes", "no")):
                                        continue
                                    return txt
                            except Exception:
                                continue
                except Exception:
                    continue

            # Broader fallback: get all text from the message container
            container_sels = [
                ".chatbot_MessageContainer",
                "[class*='chatbot_MessageContainer']",
                "[class*='chatWindow']",
            ]
            for csel in container_sels:
                try:
                    container = page.locator(csel)
                    if container.count() > 0:
                        all_text = container.last.inner_text()
                        lines = [line.strip() for line in all_text.split('\n') if line.strip() and len(line.strip()) > 5]
                        # Return the last line that looks like a question
                        for line in reversed(lines):
                            if line.endswith('?') or len(line) > 15:
                                return line
                except Exception:
                    continue

        except Exception as e:
            print(f"  x Error reading chatbot question: {e}")

        return ""

    def _click_chatbot_save(self, page: Page) -> bool:
        """Click the Save/Next button in the chatbot UI (which is often a div, not a button)."""
        save_selectors = [
            "div.sendMsg",
            "[class*='sendMsg']",
            ".chatbot_DrawerContentWrapper button:has-text('Save')",
            ".chatbot_DrawerContentWrapper button:has-text('Next')",
            ".chatbot_DrawerContentWrapper button:has-text('Submit')",
            ".chatbot_Drawer button:has-text('Save')",
            ".chatbot_Drawer button:has-text('Next')",
            ".chatbot_Drawer button:has-text('Submit')",
        ]

        for sel in save_selectors:
            try:
                btns = page.locator(sel)
                for bi in range(btns.count()):
                    try:
                        btn = btns.nth(bi)
                        if btn.is_visible():
                            btn_text = btn.inner_text().strip().lower()
                            # Only click if it says Save, Next, Submit, or similar
                            if any(kw in btn_text for kw in ["save", "next", "submit", "continue", "proceed", "send"]):
                                print(f"  -> Clicking chatbot '{btn_text}' button")
                                try:
                                    btn.evaluate("node => node.click()")
                                except Exception:
                                    btn.click(force=True)
                                page.wait_for_timeout(1500)
                                return True
                    except Exception:
                        continue
            except Exception:
                continue

        # Also try the combined selector from _SAVE_NEXT_SEL
        try:
            btn = page.locator(_SAVE_NEXT_SEL)
            for bi in range(btn.count()):
                try:
                    if btn.nth(bi).is_visible():
                        print("  -> Clicking Save/Next (fallback)...")
                        btn.nth(bi).evaluate("node => node.click()")
                        page.wait_for_timeout(1500)
                        return True
                except Exception:
                    continue
        except Exception:
            pass

        return False

    def _get_answer_for_question(self, question: str) -> str:
        """Get the best answer for a chatbot question using AI or fallback."""
        # Try AI first
        if self.ai_answerer and self.ai_answerer.enabled:
            try:
                answer = self.ai_answerer.answer_text(question, self.profile)
                if answer and answer.strip():
                    return answer.strip()
            except Exception as e:
                print(f"  x AI answerer error: {e}")

        # Rule-based fallback
        return self._fallback_text(question)

    # ─────────────────────────────────────────────────────────────────────────
    # FORM-style questionnaire loop (original approach for non-chatbot forms)
    # ─────────────────────────────────────────────────────────────────────────

    def _answer_questionnaire_loop(self, page: Page):
        """
        Iterative wizard loop for standard form-style questionnaires.
        Each iteration:
          1. Find inputs inside the panel.
          2. Answer them via AI (or fallback).
          3. Click Save / Next / Continue.
          4. Repeat until Submit appears or no inputs remain.
        """
        MAX_STEPS = 20

        for step in range(MAX_STEPS):
            print(f"  -> Questionnaire step {step + 1}...")

            # Give the panel time to render new content
            page.wait_for_timeout(2000)

            panel = self._find_panel(page)
            if panel is None:
                print("  -> Panel closed. Checking for submit...")
                self._try_final_submit(page)
                break

            # ── Collect inputs inside panel ───────────────────────────────
            inputs_in_panel = panel.locator(
                f'{_INPUT_SEL}, {_SELECT_SEL}, {_TEXTAREA_SEL}'
            )

            # Also check iframes that may be inside the panel
            frame_inputs = self._find_inputs_in_frames(page)

            total_inputs = inputs_in_panel.count() + len(frame_inputs)

            if total_inputs == 0:
                print("  -> No inputs in panel. Checking for submit...")
                if not self._try_final_submit(page):
                    if not self._click_save_next(page):
                        print("  -> Nothing to do. Ending loop.")
                        break
                else:
                    break
                continue

            # ── Fill page-level inputs inside panel ──────────────────────
            for idx in range(inputs_in_panel.count()):
                self._fill_element(inputs_in_panel.nth(idx), page)

            # ── Fill frame-level inputs ───────────────────────────────────
            for (frame, el_loc, idx) in frame_inputs:
                self._fill_element(el_loc.nth(idx), frame)

            # ── Advance wizard ────────────────────────────────────────────
            # Only submit if no Save/Next is visible (means we are on the last step)
            submitted = self._try_final_submit(page, only_if_visible_and_no_save=True)
            if submitted:
                break

            advanced = self._click_save_next(page)
            if not advanced:
                if self._try_final_submit(page):
                    break
                print("  -> No Save/Next/Submit found. Ending loop.")
                break

        else:
            print("  -> Reached max questionnaire steps. Attempting final submit...")
            self._try_final_submit(page)

    def _find_inputs_in_frames(self, page: Page):
        """Return list of (frame, locator, index) for inputs found inside iframes."""
        results = []
        try:
            for frame in page.frames:
                if frame == page.main_frame:
                    continue
                try:
                    f_loc = frame.locator(f'{_INPUT_SEL}, {_SELECT_SEL}, {_TEXTAREA_SEL}')
                    cnt = f_loc.count()
                    if cnt > 0:
                        for i in range(cnt):
                            results.append((frame, f_loc, i))
                except Exception:
                    pass
        except Exception:
            pass
        return results

    def _fill_element(self, el, context):
        """Fill a single input/select/textarea element using AI answerer with fallbacks."""
        try:
            tag       = (el.evaluate("el => el.tagName") or "").lower()
            type_attr = (el.get_attribute("type") or "").lower()

            try:
                if not el.is_visible():
                    return
            except Exception:
                return

            # Gather question context from multiple attributes
            question = (
                el.get_attribute("placeholder")
                or el.get_attribute("aria-label")
                or el.get_attribute("name")
                or el.get_attribute("id")
                or "question"
            )

            # If it's a chatbot or if the placeholder is generic, try getting the last chatbot message
            if "Type" in question or "question" in question.lower() or "answer" in question.lower() or len(question.strip()) < 5:
                try:
                    chat_msgs = context.locator(".msgWrap .msg, .chatbot_MessageContainer .msg, [class*='chatbot_MessageContainer'] [class*='botMsg'], [class*='chatbot'] [class*='message']")
                    if chat_msgs.count() > 0:
                        last_msg = chat_msgs.last.inner_text().strip()
                        if last_msg:
                            question = last_msg
                    else:
                        container = context.locator(".chatbot_MessageContainer, [class*='chatWindow'], .apply-questionnaire")
                        if container.count() > 0:
                            all_text = container.last.inner_text()
                            lines = [line.strip() for line in all_text.split('\n') if line.strip()]
                            for line in reversed(lines):
                                if line.endswith('?') or len(line) > 10:
                                    question = line
                                    break
                except Exception:
                    pass

            # ── SELECT / DROPDOWN ─────────────────────────────────────────
            if tag == "select":
                opts = el.locator("option")
                opt_count = opts.count()
                if opt_count <= 1:
                    return

                option_texts = []
                for oi in range(opt_count):
                    txt = opts.nth(oi).inner_text().strip()
                    if txt:
                        option_texts.append(txt)

                chosen = ""
                if self.ai_answerer and self.ai_answerer.enabled:
                    chosen = self.ai_answerer.choose_option(question, option_texts, self.profile)

                if chosen:
                    for oi in range(opt_count):
                        if opts.nth(oi).inner_text().strip() == chosen:
                            val = opts.nth(oi).get_attribute("value")
                            el.select_option(val)
                            break
                else:
                    fallback_val = opts.nth(1).get_attribute("value")
                    el.select_option(fallback_val)

            # ── RADIO BUTTON ──────────────────────────────────────────────
            elif type_attr == "radio":
                name_attr = el.get_attribute("name") or ""
                if name_attr:
                    try:
                        group = context.locator(f'input[type="radio"][name="{name_attr}"]')
                        group_count = group.count()
                        option_labels = []
                        for gi in range(group_count):
                            lbl = self._get_radio_label(group.nth(gi), context)
                            option_labels.append(lbl or f"option_{gi}")

                        chosen_label = ""
                        if self.ai_answerer and self.ai_answerer.enabled:
                            chosen_label = self.ai_answerer.answer_radio(
                                question, option_labels, self.profile
                            )

                        if chosen_label:
                            for gi in range(group_count):
                                lbl = option_labels[gi]
                                if lbl.lower() == chosen_label.lower() or chosen_label.lower() in lbl.lower():
                                    group.nth(gi).check(force=True)
                                    break
                            else:
                                group.first.check(force=True)
                        else:
                            # Fallback: select "Yes" radio or first option
                            yes_found = False
                            for gi in range(group_count):
                                lbl = option_labels[gi].lower()
                                if lbl in ("yes", "true", "1"):
                                    group.nth(gi).check(force=True)
                                    yes_found = True
                                    break
                            if not yes_found:
                                group.first.check(force=True)
                    except Exception:
                        el.check(force=True)
                else:
                    el.check(force=True)

            # ── CHECKBOX ──────────────────────────────────────────────────
            elif type_attr == "checkbox":
                el.check(force=True)

            # ── NUMBER ────────────────────────────────────────────────────
            elif type_attr == "number":
                val = "10"
                if self.ai_answerer and self.ai_answerer.enabled:
                    val = self.ai_answerer.answer_text(question, self.profile) or "10"
                el.fill(str(val))

            # ── TEXTAREA (free text) or CONTENTEDITABLE ────────────────────────────────
            elif tag == "textarea" or el.get_attribute("contenteditable"):
                val = ""
                if self.ai_answerer and self.ai_answerer.enabled:
                    val = self.ai_answerer.answer_free_text(question, self.profile)
                if not val:
                    val = "Experienced ETL/Data Warehouse consultant with 10 years of expertise."
                
                # If contenteditable, fill won't work perfectly sometimes, try evaluating
                if tag != "textarea" and el.get_attribute("contenteditable"):
                    el.click()
                    context.wait_for_timeout(500)
                    context.keyboard.type(str(val))
                else:
                    el.fill(str(val))

            # ── TEXT / EMAIL / TEL / DEFAULT ──────────────────────────────
            else:
                val = ""
                if self.ai_answerer and self.ai_answerer.enabled:
                    val = self.ai_answerer.answer_text(question, self.profile)
                if not val:
                    val = self._fallback_text(question)
                el.fill(str(val))

        except Exception as e:
            print(f"    x Fill error: {e}")

    def _get_radio_label(self, radio_el, context) -> str:
        """Try to find the label text associated with a radio button or checkbox."""
        try:
            radio_id = radio_el.get_attribute("id")
            if radio_id:
                lbl = context.locator(f'label[for="{radio_id}"]')
                if lbl.count() > 0:
                    return lbl.first.inner_text().strip()

            # Try parent label
            try:
                parent_label = radio_el.locator("xpath=ancestor::label")
                if parent_label.count() > 0:
                    return parent_label.first.inner_text().strip()
            except Exception:
                pass

            # Try sibling text
            try:
                sibling = radio_el.locator("xpath=following-sibling::*[1]")
                if sibling.count() > 0:
                    txt = sibling.first.inner_text().strip()
                    if txt:
                        return txt
            except Exception:
                pass

            lbl = radio_el.get_attribute("aria-label") or radio_el.get_attribute("value") or ""
            return lbl.strip()
        except Exception:
            return ""

    def _click_save_next(self, page: Page) -> bool:
        """Click Save / Next / Continue inside the questionnaire panel. Returns True if clicked."""
        try:
            panel = self._find_panel(page)
            scope = panel if panel is not None else page

            btn = scope.locator(_SAVE_NEXT_SEL)
            for bi in range(btn.count()):
                try:
                    if btn.nth(bi).is_visible():
                        print("  -> Clicking Save/Next...")
                        try:
                            btn.nth(bi).evaluate("node => node.click()")
                        except Exception:
                            btn.nth(bi).click(force=True)
                        page.wait_for_timeout(2000)
                        return True
                except Exception:
                    continue
        except Exception as e:
            print(f"  x Save/Next error: {e}")
        return False

    def _try_final_submit(self, page: Page, only_if_visible_and_no_save: bool = False) -> bool:
        """
        Click the final Submit button if visible.
        If only_if_visible_and_no_save=True, only submit when no Save/Next button
        is also visible (avoids premature submission mid-wizard).
        Returns True if submitted.
        """
        try:
            submit_btn = page.locator(_SUBMIT_SEL)
            visible_submit = None
            for si in range(submit_btn.count()):
                try:
                    if submit_btn.nth(si).is_visible():
                        visible_submit = submit_btn.nth(si)
                        break
                except Exception:
                    continue

            if visible_submit is None:
                return False

            if only_if_visible_and_no_save:
                save_btn = page.locator(_SAVE_NEXT_SEL)
                for bi in range(save_btn.count()):
                    try:
                        if save_btn.nth(bi).is_visible():
                            return False  # Not the final step yet
                    except Exception:
                        continue

            print("  -> Clicking SUBMIT!")
            try:
                visible_submit.evaluate("node => node.click()")
            except Exception:
                visible_submit.click(force=True)
            page.wait_for_timeout(3000)
            return True

        except Exception as e:
            print(f"  x Submit error: {e}")
            return False

    def _fallback_text(self, question: str) -> str:
        """Rule-based fallback when AI is unavailable."""
        q = question.lower()
        profile = self.profile

        if any(k in q for k in ["notice", "notice period"]):
            return str(profile.get("notice_period_days", "90"))
        if any(k in q for k in ["current ctc", "current salary", "current compensation"]):
            return str(profile.get("current_ctc", "22.75 LPA"))
        if any(k in q for k in ["expected ctc", "expected salary", "expected compensation"]):
            return str(profile.get("expected_ctc", "30 LPA"))
        if any(k in q for k in ["experience", "years"]):
            return str(profile.get("years_experience", "10"))
        if any(k in q for k in ["phone", "mobile", "contact"]):
            return str(profile.get("phone", ""))
        if any(k in q for k in ["email"]):
            return str(profile.get("email", ""))
        if any(k in q for k in ["name"]):
            return str(profile.get("full_name", ""))
        if any(k in q for k in ["city", "location", "place"]):
            return str(profile.get("current_city", "Hyderabad"))
        if any(k in q for k in ["company", "employer", "organisation", "organization"]):
            return "Tata Consultancy Services"
        if any(k in q for k in ["authorize", "authoris", "visa", "work permit"]):
            return "Yes"
        if any(k in q for k in ["relocate", "relocation"]):
            return "Yes"

        return "Yes"
