import os
import time
import threading
from playwright.sync_api import sync_playwright
from app.database import user_browser_profile_dir

# Global state for the active remote session
_active_sessions = {} # {user_id: {"page": page, "context": context, "playwright": p, "last_active": timestamp}}

def get_session(user_id):
    return _active_sessions.get(user_id)

def start_remote_session(user_id, portal="linkedin"):
    if user_id in _active_sessions:
        return _active_sessions[user_id]
    
    profile_dir = user_browser_profile_dir(user_id, portal)
    
    # Error tracking
    _launch_errors = {}

    def run_browser():
        try:
            # 1. Start Xvfb if not already running
            display = f":{99 + user_id}"
            os.environ["DISPLAY"] = display
            import subprocess
            # Start Xvfb in background
            subprocess.Popen(["Xvfb", display, "-screen", "0", "1280x1024x24"], 
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            time.sleep(2) # Wait for Xvfb

            p = sync_playwright().start()
            print(f"[REMOTE] Launching browser for user {user_id} on display {display}")
            
            browser = p.chromium.launch_persistent_context(
                user_data_dir=profile_dir,
                headless=False,
                args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"]
            )
            page = browser.pages[0] if browser.pages else browser.new_page()
            page.set_viewport_size({"width": 1280, "height": 800})
            
            urls = {
                "linkedin": "https://www.linkedin.com/login",
                "naukri": "https://www.naukri.com/nlogin/login",
                "foundit": "https://www.foundit.in/rio/login/seeker",
                "monster": "https://www.foundit.in/rio/login/seeker"
            }
            page.goto(urls.get(portal, "https://www.google.com"), wait_until="domcontentloaded")
            
            _active_sessions[user_id] = {
                "page": page,
                "context": browser,
                "playwright": p,
                "last_active": time.time(),
                "display": display
            }
            
            while user_id in _active_sessions:
                if time.time() - _active_sessions[user_id]["last_active"] > 600: # 10 min timeout
                    stop_remote_session(user_id)
                    break
                time.sleep(10)
        except Exception as e:
            print(f"[REMOTE ERROR] {e}")
            _launch_errors[user_id] = str(e)

    thread = threading.Thread(target=run_browser, daemon=True)
    thread.start()
    
    # Wait for session to initialize (increased to 20s)
    for _ in range(20):
        if user_id in _active_sessions:
            return _active_sessions[user_id]
        if user_id in _launch_errors:
            print(f"Abort start: {_launch_errors[user_id]}")
            return None
        time.sleep(1)
    return None

def stop_remote_session(user_id):
    session = _active_sessions.pop(user_id, None)
    if session:
        try:
            session["context"].close()
            session["playwright"].stop()
        except:
            pass

def remote_command(user_id, cmd, params):
    session = _active_sessions.get(user_id)
    if not session:
        return {"success": False, "error": "No active session"}
    
    session["last_active"] = time.time()
    page = session["page"]
    
    try:
        if cmd == "click":
            x, y = params.get("x"), params.get("y")
            page.mouse.click(x, y)
        elif cmd == "type":
            text = params.get("text")
            page.keyboard.type(text)
        elif cmd == "press":
            key = params.get("key")
            page.keyboard.press(key)
        elif cmd == "screenshot":
            path = f"static/remote_{user_id}.jpg"
            os.makedirs("static", exist_ok=True)
            page.screenshot(path=path, type="jpeg", quality=50)
            return {"success": True, "url": f"/{path}?t={time.time()}"}
        
        return {"success": True}
    except Exception as e:
        return {"success": False, "error": str(e)}
