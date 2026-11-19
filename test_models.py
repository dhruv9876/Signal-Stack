import os, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
key = os.getenv("GEMINI_API_KEY")
models = ["gemini-flash-latest", "gemini-3.7-flash", "gemini-3.6-flash",
          "gemini-3.5-flash", "gemini-3.5-flash-lite", "gemini-3.1-flash-lite",
          "gemini-flash-lite-latest", "gemini-3.8-flash"]
for m in models:
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
    try:
        r = requests.post(url, params={"key": key}, timeout=25,
                          json={"contents": [{"parts": [{"text": "Say OK"}]}]})
        print(m, "->", r.status_code)
    except Exception as e:
        print(m, "-> error", type(e).__name__)
