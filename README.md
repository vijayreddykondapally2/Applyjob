# LinkedIn Apply Agent (MVP)

This MVP helps you search LinkedIn jobs (for example: ETL Testing), classify jobs as Easy Apply or External Apply, and drive a confirmation-first apply flow.

## What this MVP does

- Logs in to LinkedIn with credentials from `.env`.
- Searches jobs by keyword and location.
- Identifies `Easy Apply` vs `External Apply`.
- Auto-processes jobs in sequence (`AUTO_APPLY=true`).
- Opens Easy Apply or external company portal and records outcomes.
- Attempts autofill for common fields on external ATS pages, then pauses for your review.
- Saves run results to `data/results.json`.

## Safety model

This MVP intentionally avoids silent background submissions. It opens the apply flow and keeps a manual review checkpoint before final submit.

## Setup

1. Create and activate venv:

```bash
python3 -m venv .venv
source .venv/bin/activate
```

2. Install dependencies:

```bash
pip install -r requirements.txt
python -m playwright install chromium
```

3. Configure environment:

```bash
cp .env.example .env
```

Edit `.env` and set:

- `LINKEDIN_EMAIL`
- `LINKEDIN_PASSWORD`
- `JOB_KEYWORDS` (default: `etl testing`)
- `JOB_LOCATION`
- `JOB_SEARCH_URL` (optional; if set, agent uses this exact LinkedIn search URL)
- `MAX_JOBS`
- `HEADLESS` (`false` recommended for MVP)
- `AUTO_APPLY` (`true` to skip per-job yes/no prompts)
- `KEEP_BROWSER_OPEN` (`true` keeps browser open until you press Enter)
- `MANUAL_LOGIN_SUBMIT` (`true` fills username/password and waits for you to click Sign in)
- `ALLOW_MANUAL_CHECKPOINT` (`true` recommended)
- `MANUAL_CHECKPOINT_TIMEOUT` (seconds to wait for captcha/OTP completion, set `0` for unlimited wait)
- `BROWSER_PROFILE_DIR` (persistent browser session directory; helps avoid repeated login checkpoints)
- `ENABLE_AI_ANSWERING` (`true` enables AI-assisted answers on external forms)
- `GROQ_API_KEY` and `GROQ_MODEL` (used when AI answering is enabled)
- `AI_JOB_MATCHING` and `AI_JOB_MAX_SELECT` (AI shortlists best-fit jobs per cycle before applying)
- `STRICT_KEYWORD_FILTER` and `STRICT_KEYWORDS` (hard-skip jobs not matching your target terms)
- `DEFAULT_EXPERIENCE_YEARS` and `DEFAULT_NOTICE_PERIOD_DAYS` (defaults for missing fields)
- `CONTINUOUS_LOOP` and `LOOP_WAIT_SECONDS` (re-search/apply in cycles, default every 30s)
- `MAX_CYCLES` (`0` means unlimited; set >0 to stop automatically after N cycles)
- `EASY_APPLY_ONLY` (`true` processes only easy-apply jobs and skips external in that run)
- `PORTAL_EMAIL` and `PORTAL_PASSWORD` (for account/email-password fields on external ATS pages)

## Run

```bash
source .venv/bin/activate
python main.py
```

First run stores your baseline profile in `data/profile.json` and reuses it later.
The agent also accumulates history in `data/results.json` and skips jobs already attempted (same URL) or duplicate companies.
It also learns question-answer pairs in `data/question_memory.json` so similar questions across different company portals get answered consistently.

## Next upgrades

- Add stronger field-level autofill on Easy Apply multi-step forms.
- Add adapters for Workday/Greenhouse/Lever external portals.
- Add dashboard + CSV export.
- Add safer throttling and retry controls.
