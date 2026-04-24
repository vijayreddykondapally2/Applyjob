import os
from dotenv import load_dotenv
import json

from app.monster_agent import MonsterApplyAgent
from app.ai_answerer import AIAnswerer

def run_monster():
    load_dotenv()
    
    from app.profile_store import PROFILE_PATH
    if not PROFILE_PATH.exists():
        print(f"Could not find profile at {PROFILE_PATH}")
        return
    with open(PROFILE_PATH, "r") as f:
        profile = json.load(f)
    
    ai_answerer = AIAnswerer(
        enabled=True,
        api_key=os.getenv("GROQ_API_KEY", ""),
        full_profile=profile
    )
    
    agent = MonsterApplyAgent(profile, ai_answerer)
    
    try:
        agent.start()
        agent.login()
        
        # Use all dynamic keywords in a single search for better narrowing
        keywords = [os.getenv("JOB_KEYWORDS", "ETL Testing").strip()]
        
        env_location = os.getenv("JOB_LOCATION", "Hyderabad")
        locations = [env_location] if env_location else ["Hyderabad", "Remote"]
        
        for keyword in keywords:
            for loc in locations:
                try:
                    agent.search_and_apply(keyword, location=loc, max_jobs=25)
                except Exception as e:
                    print(f"\n  !! Error for '{keyword}' in '{loc}': {e}")
                    continue
        
    finally:
        agent.close()

if __name__ == "__main__":
    run_monster()
