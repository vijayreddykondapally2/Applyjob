import os
import json
from dotenv import load_dotenv
from app.naukri_agent import NaukriApplyAgent


def run_naukri():
    load_dotenv()

    profile_path = "data/profile.json"
    if not os.path.exists(profile_path):
        print("Could not find data/profile.json")
        return

    with open(profile_path, "r") as f:
        profile = json.load(f)

    # ── Build AIAnswerer correctly ────────────────────────────────────────────
    # AIAnswerer(enabled, api_key, model, full_profile)
    # The old code passed the profile dict as the first arg (enabled), which
    # evaluated to True but then api_key was empty → Groq calls silently failed.
    ai_answerer = None
    try:
        from app.ai_answerer import AIAnswerer

        groq_api_key = os.getenv("GROQ_API_KEY", "")
        if groq_api_key:
            ai_answerer = AIAnswerer(
                enabled=True,
                api_key=groq_api_key,
                model="llama-3.1-8b-instant",
                full_profile=profile,
            )
            print("AI Answerer enabled (Groq).")
        else:
            print("GROQ_API_KEY not set – running with rule-based fallbacks only.")
    except Exception as e:
        print(f"Could not initialise AIAnswerer: {e}")

    agent = NaukriApplyAgent(profile, ai_answerer)
    try:
        agent.start()
        agent.login()

        # Direct URL search (pre-filtered for 10 years ETL/DWH experience)
        search_target = (
            "https://www.naukri.com/etl-testing-etl-tester-dwh-testing-data-warehouse-testing-"
            "database-testing-bi-testing-informatica-testing-data-warehouse-etl-testing-report-"
            "testing-big-data-testing-data-testing-bi-report-testing-jobs"
            "?k=etl%20testing%2C%20etl%20tester%2C%20dwh%20testing%2C%20data%20warehouse%20testing"
            "%2C%20database%20testing%2C%20bi%20testing%2C%20informatica%20testing%2C%20data%20"
            "warehouse%20etl%20testing%2C%20report%20testing%2C%20big%20data%20testing%2C%20data%20"
            "testing%2C%20bi%20report%20testing&experience=10&nignbevent_src=jobsearchDeskGNB"
        )
        agent.search_jobs_direct(search_target)

    except Exception as e:
        print(f"Critical error: {e}")
    finally:
        agent.close()


if __name__ == "__main__":
    run_naukri()
