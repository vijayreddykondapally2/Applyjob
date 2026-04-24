import os
import time
import json
from dotenv import load_dotenv
from app.foundit_agent import FounditApplyAgent

def retest_foundit_final():
    load_dotenv()
    # We need a profile for the agent
    profile = {
        "full_name": "Test User",
        "email": os.getenv("LINKEDIN_EMAIL"),
    }
    
    print("--- 🕵️‍♂️ FINAL FOUNDIT PRODUCTION TEST ---")
    agent = FounditApplyAgent(profile)
    
    try:
        agent.start()
        print("Starting Login Flow (using verified LinkedIn method)...")
        agent.login()
        
        # Give it a few seconds to settle on the dashboard
        time.sleep(10)
        
        if "/dashboard" in agent.page.url or agent.page.locator(".profile-icon, .userName").count() > 0:
            print("\n✅ FINAL PASS: Foundit is working perfectly with the new LinkedIn strategy!")
        else:
            print(f"\n⌛ Final URL check: {agent.page.url}")
            print("If you see a LinkedIn 'Allow' screen, the bot should be clicking it now...")
            time.sleep(10)
            if "/dashboard" in agent.page.url or agent.page.locator(".profile-icon, .userName").count() > 0:
                print("\n✅ FINAL PASS (after delay): Foundit Success!")
            else:
                print("❌ Still not at dashboard. Please check the browser window.")

    except Exception as e:
        print(f"💥 Error during final test: {e}")
    finally:
        print("\nKeeping window open for 45 seconds for you to confirm...")
        time.sleep(45)
        agent.close()

if __name__ == "__main__":
    retest_foundit_final()
