# LinkedIn Apply Agent (MVP)

This MVP helps you search LinkedIn jobs (for example: ETL Testing), classify jobs as Easy Apply or External Apply, and drive a confirmation-first apply flow.

## What this MVP does

- Logs in to LinkedIn with credentials from `.env`.
- Searches jobs by keyword and location.
- Identifies `Easy Apply` vs `External Apply`.
- Prompts `yes/no` before each application step.
- Opens Easy Apply or external company portal and records outcomes.
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
- `MAX_JOBS`
- `HEADLESS` (`false` recommended for MVP)

## Run

```bash
source .venv/bin/activate
python main.py
```

First run stores your baseline profile in `data/profile.json` and reuses it later.

## Next upgrades

- Add stronger field-level autofill on Easy Apply multi-step forms.
- Add adapters for Workday/Greenhouse/Lever external portals.
- Add dashboard + CSV export.
- Add safer throttling and retry controls.
