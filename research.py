"""Polite fetching (robots.txt, rate limit) + company research with quote-verified facts."""
import re
import time
import urllib.robotparser as robotparser
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

import llm

UA = "SignalStackBot/0.1 (student project; respects robots.txt)"
_robots = {}
_last_hit = {}
KEYWORDS = ("about", "product", "service", "solution", "pricing", "partner", "student",
            "universit", "program", "course", "blog", "news", "press", "customer", "how-it-works")


def allowed(url):
    p = urlparse(url)
    base = f"{p.scheme}://{p.netloc}"
    if base not in _robots:
        rp = robotparser.RobotFileParser()
        try:
            resp = requests.get(base + "/robots.txt", headers={"User-Agent": UA}, timeout=10)
            rp.parse(resp.text.splitlines() if resp.status_code == 200 else [])
        except Exception:
            rp.parse([])
        _robots[base] = rp
    return _robots[base].can_fetch(UA, url)


def polite_get(url, check_robots=True):
    """GET with a clear user agent, robots.txt check and 1 request/second per host.
    check_robots=False is only used for official RSS/JSON API endpoints (see README)."""
    if check_robots and not allowed(url):
        raise PermissionError("blocked by robots.txt")
    host = urlparse(url).netloc
    wait = 1.0 - (time.time() - _last_hit.get(host, 0))
    if wait > 0:
        time.sleep(wait)
    _last_hit[host] = time.time()
    r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    r.raise_for_status()
    return r


def page_text(html):
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "nav", "footer", "svg"]):
        t.decompose()
    return re.sub(r"\s+", " ", soup.get_text(" ")).strip()


def crawl_site(url, max_pages=5):
    if not url.startswith("http"):
        url = "https://" + url
    pages, errors = [], []
    try:
        r = polite_get(url)
    except Exception as e:
        return [], [f"{url}: {e}"]
    pages.append({"url": url, "text": page_text(r.text)[:7000]})
    soup = BeautifulSoup(r.text, "html.parser")
    host = urlparse(url).netloc.replace("www.", "")
    seen = {url.rstrip("/")}
    for a in soup.find_all("a", href=True):
        if len(pages) >= max_pages:
            break
        link = urljoin(r.url, a["href"]).split("#")[0]
        if urlparse(link).netloc.replace("www.", "") != host or link.rstrip("/") in seen:
            continue
        if not any(k in link.lower() for k in KEYWORDS):
            continue
        seen.add(link.rstrip("/"))
        try:
            rr = polite_get(link)
            pages.append({"url": link, "text": page_text(rr.text)[:5000]})
        except Exception as e:
            errors.append(f"{link}: {e}")
    return pages, errors


def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def build_profile(name, url):
    pages, errors = crawl_site(url)
    corpus = "\n\n".join(f"[PAGE {p['url']}]\n{p['text']}" for p in pages) or \
        "(No page text could be fetched. Put everything you say in 'assumptions'.)"
    prompt = f"""Research this company ONLY from the page text below.
Company: {name}   Website: {url}

{corpus}

Return JSON with keys:
summary (2-3 sentences), industry, geography,
products_services (list), customer_segments (list), positioning (string),
verified_facts: list of {{"fact": str, "evidence_url": str, "quote": str}} where "quote" is an EXACT
  substring copied from the page text that supports the fact (min 20 characters),
assumptions: list of strings (things you infer but the pages do not state),
unknowns: list of important things the pages do not tell us,
keywords: 6-10 search keywords describing the company's problem space."""
    data = llm.generate_json(prompt)
    if not isinstance(data, dict):
        raise llm.LLMError("Unexpected profile format from the model.")
    haystack = _norm(corpus)
    verified, demoted = [], []
    for f in data.get("verified_facts", []):
        quote = _norm(f.get("quote", ""))
        if len(quote) >= 20 and quote in haystack:
            verified.append(f)
        else:  # model could not prove it from the text, so it is only an assumption
            demoted.append(f"(unverified) {f.get('fact', '')}")
    data["verified_facts"] = verified
    data["assumptions"] = list(data.get("assumptions", [])) + demoted
    data["pages_read"] = [p["url"] for p in pages]
    data["fetch_errors"] = errors
    return data
