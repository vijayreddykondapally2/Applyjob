# Graph Report - Applyjob-fixed 2  (2026-04-24)

## Corpus Check
- 23 files · ~22,411 words
- Verdict: corpus is large enough that graph structure adds value.

## Summary
- 295 nodes · 642 edges · 14 communities detected
- Extraction: 67% EXTRACTED · 33% INFERRED · 0% AMBIGUOUS · INFERRED: 211 edges (avg confidence: 0.67)
- Token cost: 0 input · 0 output

## Community Hubs (Navigation)
- [[_COMMUNITY_Community 0|Community 0]]
- [[_COMMUNITY_Community 1|Community 1]]
- [[_COMMUNITY_Community 2|Community 2]]
- [[_COMMUNITY_Community 3|Community 3]]
- [[_COMMUNITY_Community 4|Community 4]]
- [[_COMMUNITY_Community 5|Community 5]]
- [[_COMMUNITY_Community 6|Community 6]]
- [[_COMMUNITY_Community 7|Community 7]]
- [[_COMMUNITY_Community 8|Community 8]]
- [[_COMMUNITY_Community 9|Community 9]]
- [[_COMMUNITY_Community 10|Community 10]]
- [[_COMMUNITY_Community 11|Community 11]]
- [[_COMMUNITY_Community 16|Community 16]]
- [[_COMMUNITY_Community 17|Community 17]]

## God Nodes (most connected - your core abstractions)
1. `AIAnswerer` - 38 edges
2. `LinkedInApplyAgent` - 37 edges
3. `NaukriApplyAgent` - 32 edges
4. `QuestionMemory` - 30 edges
5. `ApplyResult` - 26 edges
6. `JobCard` - 25 edges
7. `get_db()` - 23 edges
8. `FounditApplyAgent` - 20 edges
9. `run()` - 15 edges
10. `MonsterApplyAgent` - 14 edges

## Surprising Connections (you probably didn't know these)
- `api_run()` --calls--> `start_run()`  [INFERRED]
  web_app.py → app/task_manager.py
- `api_status()` --calls--> `get_user_status()`  [INFERRED]
  web_app.py → app/task_manager.py
- `run_foundit()` --calls--> `AIAnswerer`  [INFERRED]
  foundit_main.py → app/ai_answerer.py
- `run_naukri()` --calls--> `AIAnswerer`  [INFERRED]
  naukri_main.py → app/ai_answerer.py
- `run_naukri()` --calls--> `NaukriApplyAgent`  [INFERRED]
  naukri_main.py → app/naukri_agent.py

## Communities

### Community 0 - "Community 0"
Cohesion: 0.06
Nodes (57): create_job_run(), create_user(), _ensure_db_dir(), get_active_runs(), get_all_users(), get_application_stats(), get_applications(), get_credentials() (+49 more)

### Community 1 - "Community 1"
Cohesion: 0.11
Nodes (10): _card_text(), _direct_map(), _field_label(), LinkedInApplyAgent, _load_historical_results(), _normalize_url(), save_results(), _select_options() (+2 more)

### Community 2 - "Community 2"
Cohesion: 0.1
Nodes (16): FounditApplyAgent, Foundit.in Bulk-Apply Agent.      Workflow per keyword:       1. Type keyword in, Click 'Confirm & Apply' on the SAME page (self.page).         The modal opens on, Stop if 3 pages done or 15 min expired., run_foundit(), main(), _parallel_runner_wrapper(), print_banner() (+8 more)

### Community 3 - "Community 3"
Cohesion: 0.19
Nodes (27): AIAnswerer, Groq-powered answerer for LinkedIn Easy Apply form fields.      Design goals:, ApplyResult, JobCard, Click the job card in the LEFT sidebar without navigating away from         the, Scroll the LEFT sidebar container so *card_locator* is centred in it.         Th, Click the Next Page button in the pagination bar.         Does NOT call find_job, Scroll the RIGHT-hand job detail pane back to top so the         Easy Apply butt (+19 more)

### Community 4 - "Community 4"
Cohesion: 0.11
Nodes (11): NaukriApplyAgent, Click the Save/Next button in the chatbot UI (which is often a div, not a button, Write the Q&A log to a JSON file., Check if a page is still open and usable., Click the final Submit button if visible.         If only_if_visible_and_no_save, 1. Click the native Apply button (skip external company-site buttons).         2, Robustly find and click the Apply button on a Naukri job page.         Returns T, Wait up to timeout_ms for questionnaire panel. Returns True if found. (+3 more)

### Community 5 - "Community 5"
Cohesion: 0.14
Nodes (8): MonsterApplyAgent, Search for a keyword and apply to jobs on the results page., Click a job card, find Apply, handle application, return., Handle the application page/form.         Works for both internal Monster forms, Fill visible form fields using profile data., Get an answer for a form field from the profile. AI is last resort., Close any extra tabs and return to the search results page., Monster.com Job Apply Agent — clean, single-tab approach.      Flow:       1. Lo

### Community 6 - "Community 6"
Cohesion: 0.18
Nodes (8): _clean(), _clean_json(), Choose the best radio button option using Groq (delegates to choose_option)., Merge full_profile (base) with candidate_values (overrides), keeping ALL keys., Answer a short single-line text input (name, phone, CTC, years…)., Answer an open-ended textarea question (e.g. 'Describe your ETL experience')., Pick the best option from a dropdown or list of choices., Fill a single input/select/textarea element using AI answerer with fallbacks.

### Community 7 - "Community 7"
Cohesion: 0.16
Nodes (8): check(), Record a question-answer pair for later review., Try to find the label text associated with a radio button or checkbox., Detect what kind of input the chatbot is presenting and fill it:         - Radio, Handle radio button groups inside the chatbot., Handle checkboxes inside the chatbot (multi-select)., Handle a dropdown <select> inside the chatbot., Handle non-standard option buttons (divs/spans/buttons that act as radio choices

### Community 8 - "Community 8"
Cohesion: 0.2
Nodes (12): ensure_profile_dir(), load_profile(), prompt_profile_if_missing(), Load profile.json; interactively create one only if it doesn't exist., save_profile(), _job_matches_keywords(), run(), _split_csv() (+4 more)

### Community 9 - "Community 9"
Cohesion: 0.29
Nodes (4): Iterative wizard loop for standard form-style questionnaires.         Each itera, Return list of (frame, locator, index) for inputs found inside iframes., Click Save / Next / Continue inside the questionnaire panel. Returns True if cli, Return the first visible questionnaire panel locator, or None.

### Community 10 - "Community 10"
Cohesion: 0.33
Nodes (3): Get the best answer for a chatbot question using AI or fallback., Rule-based fallback when AI is unavailable., Find the chatbot text input box and type the answer.         Tries multiple sele

### Community 11 - "Community 11"
Cohesion: 0.67
Nodes (2): _normalize(), _tokens()

### Community 16 - "Community 16"
Cohesion: 1.0
Nodes (1): Strip markdown fences, surrounding quotes, and whitespace.

### Community 17 - "Community 17"
Cohesion: 1.0
Nodes (1): Extract JSON from a response that may be wrapped in markdown.

## Knowledge Gaps
- **64 isolated node(s):** `Production multi-user web application for ApplyJob AI.  Features:   - User regis`, `Top-level wrapper to apply environment overrides in child processes.`, `Load profile.json; interactively create one only if it doesn't exist.`, `SQLite database layer for multi-user ApplyJob AI.  Tables:   - users         : a`, `Thread-safe connection context manager.` (+59 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **Thin community `Community 11`** (4 nodes): `question_memory.py`, `_normalize()`, `.lookup()`, `_tokens()`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 16`** (1 nodes): `Strip markdown fences, surrounding quotes, and whitespace.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.
- **Thin community `Community 17`** (1 nodes): `Extract JSON from a response that may be wrapped in markdown.`
  Too small to be a meaningful cluster - may be noise or needs more connections extracted.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `AIAnswerer` connect `Community 3` to `Community 8`, `Community 1`, `Community 2`, `Community 6`?**
  _High betweenness centrality (0.243) - this node is a cross-community bridge._
- **Why does `NaukriApplyAgent` connect `Community 4` to `Community 2`, `Community 6`, `Community 7`, `Community 9`, `Community 10`?**
  _High betweenness centrality (0.204) - this node is a cross-community bridge._
- **Why does `run_naukri()` connect `Community 2` to `Community 3`, `Community 4`?**
  _High betweenness centrality (0.198) - this node is a cross-community bridge._
- **Are the 27 inferred relationships involving `AIAnswerer` (e.g. with `LinkedInApplyAgent` and `LinkedIn Easy Apply automation agent.      Navigation model (matches how a human`) actually correct?**
  _`AIAnswerer` has 27 INFERRED edges - model-reasoned connections that need verification._
- **Are the 5 inferred relationships involving `LinkedInApplyAgent` (e.g. with `AIAnswerer` and `ApplyResult`) actually correct?**
  _`LinkedInApplyAgent` has 5 INFERRED edges - model-reasoned connections that need verification._
- **Are the 24 inferred relationships involving `QuestionMemory` (e.g. with `LinkedInApplyAgent` and `LinkedIn Easy Apply automation agent.      Navigation model (matches how a human`) actually correct?**
  _`QuestionMemory` has 24 INFERRED edges - model-reasoned connections that need verification._
- **Are the 25 inferred relationships involving `ApplyResult` (e.g. with `LinkedInApplyAgent` and `LinkedIn Easy Apply automation agent.      Navigation model (matches how a human`) actually correct?**
  _`ApplyResult` has 25 INFERRED edges - model-reasoned connections that need verification._