import os, requests
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
r = requests.get("https://generativelanguage.googleapis.com/v1beta/models",
                 params={"key": os.getenv("GEMINI_API_KEY"), "pageSize": 100}, timeout=30)
print("HTTP", r.status_code)
for m in r.json().get("models", []):
    if "generateContent" in m.get("supportedGenerationMethods", []):
        print(m["name"].replace("models/", ""))
