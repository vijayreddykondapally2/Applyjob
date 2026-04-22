import os
from dotenv import load_dotenv
import time

# Import the run functions from each script
from app.runner import run as run_linkedin
from naukri_main import run_naukri
from foundit_main import run_foundit
from monster_main import run_monster

def main():
    load_dotenv()
    
    print("\n" + "="*60)
    print("🚀 STARTING UNIFIED JOB APPLIER (SUPER APP)")
    print("="*60 + "\n")
    
    # 1. LINKEDIN
    print("\n>>> RUNNING LINKEDIN AGENT...")
    try:
        run_linkedin()
    except Exception as e:
        print(f"❌ LinkedIn Error: {e}")
    
    # 2. NAUKRI
    print("\n>>> RUNNING NAUKRI AGENT...")
    try:
        run_naukri()
    except Exception as e:
        print(f"❌ Naukri Error: {e}")
        
    # 3. FOUNDIT
    print("\n>>> RUNNING FOUNDIT AGENT...")
    try:
        run_foundit()
    except Exception as e:
        print(f"❌ Foundit Error: {e}")
        
    # 4. MONSTER
    print("\n>>> RUNNING MONSTER AGENT...")
    try:
        run_monster()
    except Exception as e:
        print(f"❌ Monster Error: {e}")

    print("\n" + "="*60)
    print("✅ ALL AGENTS FINISHED!")
    print("="*60 + "\n")

if __name__ == "__main__":
    main()
