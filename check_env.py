import os
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(Path(__file__).parent / ".env")
k = os.getenv("GEMINI_API_KEY")
print("Key found:", bool(k), "| length:", len(k) if k else 0)
print("Model:", os.getenv("GEMINI_MODEL"))