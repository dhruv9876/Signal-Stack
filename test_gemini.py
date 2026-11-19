import os, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
key = os.getenv("GEMINI_API_KEY")
model = os.getenv("GEMINI_MODEL")
url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
r = requests.post(url, params={"key": key}, timeout=60,
                  json={"contents": [{"parts": [{"text": "Say OK"}]}]})
print("HTTP", r.status_code)
print(r.text[:300])
