import os
from dotenv import load_dotenv
from app.ai_answerer import AIAnswerer
import json

load_dotenv(override=True)
with open('data/profile.json') as f:
    profile = json.load(f)

ai = AIAnswerer(True, os.environ["GROQ_API_KEY"], full_profile=profile)
print("Experience:", repr(ai.answer_text("Your total years of Experience in Quality?", profile)))
print("CTC:", repr(ai.answer_text("Current CTC (In Lac)?", profile)))
