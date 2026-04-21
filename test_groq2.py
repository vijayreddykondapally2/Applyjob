import os, urllib.request, json
from dotenv import load_dotenv

load_dotenv(override=True)
api_key = os.environ["GROQ_API_KEY"]

payload = {
    "model": "llama-3.1-8b-instant",
    "messages": [
        {"role": "system", "content": "You are testing"},
        {"role": "user", "content": "How many years of work experience do you have with AMLS?"}
    ],
    "temperature": 0.1,
    "max_tokens": 100,
}
req = urllib.request.Request(
    "https://api.groq.com/openai/v1/chat/completions",
    data=json.dumps(payload).encode("utf-8"),
    method="POST",
    headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
)
try:
    with urllib.request.urlopen(req) as resp:
        print(resp.read().decode("utf-8"))
except Exception as e:
    import traceback
    traceback.print_exc()

