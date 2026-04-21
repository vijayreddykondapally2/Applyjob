# Automating Naukri.com Job Applications

This outlines the exact automation workflow encoded into `app/naukri_agent.py` to recursively crawl Naukri and answer dynamic job questionnaires.

## The Strategy

Naukri job forms aren't structured smoothly sequentially like LinkedIn. When a user clicks "Apply," Naukri redirects them occasionally to external corporate domains ("Apply on Company Site") or opens random embedded internal Modals and Chatbots to interrogate the user on specific job properties.

To automate this perfectly, our robotic pipeline executes the following sequence:

### 1. Direct High-Yield URL Navigation
Instead of querying generic string keywords, the bot routes instantly to a colossal, pre-filtered query URL (like specifying exactly 10 years experience). 
It actively waits up to 15 full seconds (`page.wait_for_selector(".srp-jobtuple-wrapper")`) to ensure the massive React payload fully mounts to the screen.

### 2. Punching Into Contextual Job Tabs
For every job wrapper found:
* It forces a hard click on the literal `<a class="title">` anchor tag string. This ensures the target link is physically fired up into a new tab and isn't blocked by invisible bounding-box styling overlays.

### 3. Evading External Sites 
The agent specifically probes for the `button#apply-button` and avoids elements with the class `.company-site-button`. Using this exclusion logic ensures the system never wanders off-site into untracked company ecosystems where it cannot natively apply.

### 4. Answering Dynamic Questionnaires using AI
If an internal Naukri application prompts a pop-up layer (the bot scopes for `.chatbot`, `.chat-window`, `.qs-window`, or `.modal` tags):
* It cycles through all visible `select`, `textarea`, and `input` fields. 
* It intercepts the AI Answerer Engine (`AIAnswerer.answer_text`), feeding it the candidate's JSON profile coupled with the specific field question.
* It fills the dynamic form and injects strict fallbacks (`"1"` for numerals, `"Yes"` for text) in case the API rate-limiter denies the request.
* It forcibly clicks 'Submit' or 'Save'.

### 5. Recursive Target Suggestion Harvesting
Naukri immediately serves up "Jobs you might be interested in" on the exact same page once your application is executed. 
Before returning, the bot grabs up to three parallel suggestions off the sidebar or footer (via `.similar-jobs a.title`), forces them open in tertiary tabs, executes the entire `_process_naukri_card` sequence internally inside that job scope, closes out the recursive tab, and returns safely back to the core original search context loop!
