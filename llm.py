"""Thin Gemini client over the public REST API (free tier). No SDK needed.

Retries on overload, rate limits and timeouts, then falls back to other models.
Every retry and fallback is appended to llm_calls.log so failures stay visible.
"""
import json
import logging
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

load_dotenv(Path(__file__).parent / ".env")

API = "https://generativelanguage.googleapis.com/v1beta/models/{m}:generateContent"
BACKOFF = (5, 15, 30)  # seconds to wait between attempts on one model

_log = logging.getLogger("llm")
if not _log.handlers:
    _log.setLevel(logging.INFO)
    _h = logging.FileHandler(Path(__file__).parent / "llm_calls.log", encoding="utf-8")
    _h.setFormatter(logging.Formatter("%(asctime)s %(message)s"))
    _log.addHandler(_h)


class LLMError(Exception):
    pass


def _setting(name, default=""):
    v = os.getenv(name)
    if not v:
        try:  # Streamlit Cloud keeps secrets here
            import streamlit as st
            v = st.secrets.get(name)
        except Exception:
            v = None
    return v or default


def _key():
    k = _setting("GEMINI_API_KEY")
    if not k:
        raise LLMError("GEMINI_API_KEY is not set. See the README (.env or Streamlit secrets).")
    return k


def _models():
    primary = _setting("GEMINI_MODEL", "gemini-3.5-flash-lite")
    fallbacks = _setting("GEMINI_FALLBACK_MODELS", "gemini-3.1-flash-lite,gemini-flash-lite-latest")
    out = []
    for m in [primary] + fallbacks.split(","):
        m = m.strip().replace("models/", "")
        if m and m not in out:
            out.append(m)
    return out


MODEL = _models()[0]


def generate(prompt, system=None, as_json=False, temperature=0.4, retries=3):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system:
        body["systemInstruction"] = {"parts": [{"text": system}]}
    if as_json:
        body["generationConfig"]["responseMimeType"] = "application/json"
    headers = {"x-goog-api-key": _key(), "Content-Type": "application/json"}

    last = "no attempt made"
    for mi, model in enumerate(_models()):
        attempts = retries if mi == 0 else max(1, retries - 1)
        for attempt in range(attempts):
            wait = BACKOFF[min(attempt, len(BACKOFF) - 1)] if attempt < attempts - 1 else 0
            try:
                r = requests.post(API.format(m=model), headers=headers, json=body, timeout=60)
            except (requests.Timeout, requests.ConnectionError) as e:
                last = f"{model}: {type(e).__name__}"
                _log.info("retry %d/%d %s", attempt + 1, attempts, last)
                time.sleep(wait)
                continue
            if r.status_code in (429, 500, 502, 503, 504):  # free-tier limit / overload
                last = f"{model}: HTTP {r.status_code}"
                _log.info("retry %d/%d %s", attempt + 1, attempts, last)
                time.sleep(wait)
                continue
            if r.status_code == 404:  # model not available to this key: try the next one
                last = f"{model}: HTTP 404 (model not available)"
                _log.info("skip %s", last)
                break
            if r.status_code != 200:
                raise LLMError(f"Gemini error {r.status_code}: {r.text[:300]}")
            try:
                parts = r.json()["candidates"][0]["content"]["parts"]
                text = "".join(p.get("text", "") for p in parts)
            except (KeyError, IndexError):
                last = f"{model}: empty or blocked response"
                _log.info("skip %s", last)
                break
            if mi > 0:
                _log.info("fallback model used: %s", model)
            return text
    raise LLMError(f"All Gemini models unavailable (last: {last}). "
                   "Details are in llm_calls.log. Wait a few minutes and retry.")


def parse_json(text):
    t = re.sub(r"^```(?:json)?|```$", "", text.strip(), flags=re.M).strip()
    try:
        return json.loads(t)
    except json.JSONDecodeError:
        m = re.search(r"(\{.*\}|\[.*\])", t, re.S)
        if m:
            return json.loads(m.group(1))
        raise


def generate_json(prompt, **kw):
    """Ask for JSON; if the reply is malformed, ask once more."""
    text = generate(prompt, as_json=True, **kw)
    try:
        return parse_json(text)
    except json.JSONDecodeError:
        text = generate(prompt + "\n\nReturn ONLY valid JSON, nothing else.", as_json=True, **kw)
        return parse_json(text)
