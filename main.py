import os
import sys
import time
import argparse
import multiprocessing
from dotenv import load_dotenv

# Import the run functions from each portal-specific script
from app.runner import run as run_linkedin
from naukri_main import run_naukri
from foundit_main import run_foundit
from monster_main import run_monster

def print_banner():
    banner = """
    ============================================================
    🚀  UNIFIED JOB APPLIER (SUPER APP) - V1.0
    ============================================================
    One command to rule them all: LinkedIn, Naukri, Foundit, Monster
    ============================================================
    """
    print(banner)

def run_all_sequential():
    print("\n[!] Starting Sequential Execution of ALL Agents...")
    agents = [
        ("LinkedIn", run_linkedin),
        ("Naukri", run_naukri),
        ("Foundit", run_foundit),
        ("Monster", run_monster)
    ]
    
    for name, func in agents:
        print(f"\n>>> 🔄 STARTING: {name}")
        try:
            func()
            print(f">>> ✅ FINISHED: {name}")
        except Exception as e:
            print(f">>> ❌ ERROR in {name}: {e}")
        
    print("\n" + "="*60)
    print("🎯  ALL PLANNED APPLICATIONS COMPLETED!")
    print("="*60)

def _parallel_runner_wrapper(func, env_overrides):
    """Top-level wrapper to apply environment overrides in child processes."""
    os.environ.update(env_overrides)
    func()

def run_all_parallel():
    print("\n[!] Launching ALL Agents PARALLELY (All-at-once)...")
    print("[!] Multiple browser windows will open shortly.\n")
    
    agents = [
        ("LinkedIn", run_linkedin),
        ("Naukri", run_naukri),
        ("Foundit", run_foundit),
        ("Monster", run_monster)
    ]
    
    processes = []
    for name, func in agents:
        # Pass PARALLEL_MODE=true to child processes
        env = os.environ.copy()
        env["PARALLEL_MODE"] = "true"
        
        p = multiprocessing.Process(
            target=_parallel_runner_wrapper, 
            args=(func, env),
            name=name
        )
        p.start()
        processes.append(p)
        print(f"  -> Process started for {name} (PID: {p.pid})")
        time.sleep(2) # Slight delay to avoid CPU spike on launch

    print(f"\n✅ All {len(processes)} agents are now running in the background.")
    print("Keep this terminal open to see logs. Press Ctrl+C to stop all.\n")

    try:
        for p in processes:
            p.join()
    except KeyboardInterrupt:
        print("\n\n⚠️ Terminating all processes...")
        for p in processes:
            p.terminate()
            p.join()
        print("🛑 All agents stopped.")

def main():
    # Environment variables are managed by the parent process or system
    
    parser = argparse.ArgumentParser(description="Unified Job Application Runner")
    parser.add_argument("portal", nargs="?", choices=["linkedin", "naukri", "foundit", "monster", "all", "parallel"], 
                        help="Portal to run. 'parallel' runs all at once. If omitted, shows interactive menu.")
    
    args = parser.parse_args()

    if args.portal:
        portal = args.portal.lower()
        if portal == "linkedin":
            run_linkedin()
        elif portal == "naukri":
            run_naukri()
        elif portal == "foundit":
            run_foundit()
        elif portal == "monster":
            run_monster()
        elif portal == "all":
            run_all_sequential()
        elif portal == "parallel":
            run_all_parallel()
        return

    # Interactive Menu
    while True:
        print_banner()
        print(" [1] Run LinkedIn Agent")
        print(" [2] Run Naukri Agent")
        print(" [3] Run Foundit Agent")
        print(" [4] Run Monster Agent")
        print(" [5] Run ALL Agents (One-by-one - Sequential)")
        print(" [6] Run ALL Agents (All-at-once - PARALLEL) 🔥")
        print(" [0] Exit")
        
        try:
            choice = input("\n👉 Select an option: ").strip()
            
            if choice == "1":
                run_linkedin()
            elif choice == "2":
                run_naukri()
            elif choice == "3":
                run_foundit()
            elif choice == "4":
                run_monster()
            elif choice == "5":
                run_all_sequential()
            elif choice == "6":
                run_all_parallel()
            elif choice == "0":
                print("\nExiting. Good luck with your job search!")
                break
            else:
                print("\n⚠️  Invalid choice. Please select 0-6.")
                time.sleep(1)
        except KeyboardInterrupt:
            print("\n\nOperation cancelled. Goodbye!")
            break

if __name__ == "__main__":
    # Required for multiprocessing on macOS/Windows
    multiprocessing.freeze_support()
    main()

