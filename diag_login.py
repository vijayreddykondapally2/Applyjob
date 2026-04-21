from playwright.sync_api import sync_playwright

def check():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        print("Navigating to Foundit...")
        page.goto("https://www.foundit.in/")
        page.wait_for_timeout(5000)
        
        print("Clicking Login...")
        login_btn = page.locator("#seekerHeader button:has-text('Login')").first
        if login_btn.count() > 0:
            login_btn.click(force=True)
            print("Clicked Login button.")
        
        page.wait_for_timeout(5000)
        
        print("Dumping Page inputs...")
        inputs = page.evaluate("""() => {
            const res = [];
            document.querySelectorAll('input').forEach(el => {
                res.push({
                    type: el.type,
                    name: el.name,
                    id: el.id,
                    placeholder: el.placeholder,
                    class: el.className
                });
            });
            return res;
        }""")
        for i in inputs:
            print(f"Input: {i}")

        browser.close()

if __name__ == "__main__":
    check()
