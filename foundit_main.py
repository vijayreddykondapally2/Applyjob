import os
from dotenv import load_dotenv
import json

from app.foundit_agent import FounditApplyAgent
from app.ai_answerer import AIAnswerer

def run_foundit():
    load_dotenv()
    
    # Load candidate profile
    profile_path = "data/profile.json"
    with open(profile_path, "r") as f:
        profile = json.load(f)
    
    # Initialize AI Answerer
    ai_answerer = AIAnswerer(
        enabled=True,
        api_key=os.getenv("GROQ_API_KEY", ""),
        full_profile=profile
    )
    
    agent = FounditApplyAgent(profile, ai_answerer)
    
    try:
        agent.start()
        agent.login()
        
        # Keywords to search one by one
        # Use all dynamic keywords in a single search for better narrowing
        keywords = [os.getenv("JOB_KEYWORDS", "ETL Testing").strip()]
        
        location = os.getenv("JOB_LOCATION", "India")
        
        for keyword in keywords:
            # Stop early if 3 pages done or 15 min expired
            if agent._should_stop():
                reason = "3-page limit" if agent.pages_applied >= 3 else "15-min timeout"
                print(f"\n🛑 {reason} reached. Done!")
                break
            try:
                agent.bulk_apply(keyword, location=location, max_pages=3)
            except Exception as e:
                print(f"\n  !! Error during bulk apply for '{keyword}': {e}")
                continue
        
    finally:
        agent.close()

if __name__ == "__main__":
    run_foundit()
