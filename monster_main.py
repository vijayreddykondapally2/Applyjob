import os
from dotenv import load_dotenv
import json

from app.monster_agent import MonsterApplyAgent
from app.ai_answerer import AIAnswerer

def run_monster():
    load_dotenv()
    
    profile_path = "data/profile.json"
    with open(profile_path, "r") as f:
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
        
        keywords = [
            "ETL Testing",
            "ETL Tester",
            "Data QA",
            "Data Testing",
        ]
        
        for keyword in keywords:
            for loc in ["Hyderabad", "Remote"]:
                try:
                    agent.search_and_apply(keyword, location=loc, max_jobs=25)
                except Exception as e:
                    print(f"\n  !! Error for '{keyword}' in '{loc}': {e}")
                    continue
        
    finally:
        agent.close()

if __name__ == "__main__":
    run_monster()
