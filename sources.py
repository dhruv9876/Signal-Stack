"""Source discovery: the LLM proposes competitors and sources; code builds and validates URLs.
Nothing here is hard-coded per company."""
import json
from urllib.parse import quote_plus

import db
import llm
import research

KINDS = {"news", "hn", "stackexchange", "page"}
CATEGORIES = ("Customers", "Competitors", "Market")
SE_SITES = {"stackoverflow", "academia", "money", "workplace", "superuser", "webmasters",
            "softwareengineering", "travel", "law", "economics", "parenting", "expatriates",
            "pm", "ux", "askubuntu", "serverfault"}


def build_url(kind, query, site=None):
    region = db.get_setting("news_region", "IN")
    qq = quote_plus(query)
    if kind == "news":  # Google News RSS search
        return f"https://news.google.com/rss/search?q={qq}&hl=en-{region}&gl={region}&ceid={region}:en"
    if kind == "hn":  # Hacker News Algolia API
        return f"https://hn.algolia.com/api/v1/search_by_date?tags=story&query={qq}"
    if kind == "stackexchange":  # Stack Exchange API
        return ("https://api.stackexchange.com/2.3/search/advanced?order=desc&sort=creation"
                f"&q={qq}&site={site}&filter=withbody&pagesize=15")
    raise ValueError(kind)


def _context(profile):
    keep = {k: v for k, v in profile.items() if k not in ("pages_read", "fetch_errors")}
    return json.dumps(keep, ensure_ascii=False)[:7000]


def _prompt(c, existing):
    names = [s["name"] for s in existing]
    comps = [x["name"] for x in db.q("SELECT name FROM competitors WHERE company_id=?", (c["id"],))]
    return f"""Build a monitored intelligence "Signal Stack" for {c['name']} ({c['url']}).
COMPANY AND INTERVIEW PROFILE: {_context(c['profile'])}
Already have these sources (do not repeat): {names[:60]}
Already known competitors: {comps}

Return JSON: {{"competitors": [{{"name","website","why_relevant"}}],
"sources": [{{"category","kind","name","query","site","url","reason"}}]}}
Rules:
- 5 to 10 real competitors or substitutes customers use instead. Only companies you are confident exist; website = real homepage.
- Sources: at least 7 Customers, 8 Competitors, 7 Market. category is exactly Customers, Competitors or Market.
- kind is one of:
  "news"  = Google News RSS search; give "query" (concise search terms),
  "hn"    = Hacker News search; give "query",
  "stackexchange" = give "query" and "site" from {sorted(SE_SITES)},
  "page"  = one specific public URL (a competitor's pricing/blog/news page, a regulator's notices page); give "url" ONLY if you are confident it exists.
- Customers: aim at customer problems, complaints, questions and alternatives considered, in customers' own words. Not company mentions.
- Competitors: a "news" query per competitor (name plus launch, pricing, partnership, funding, hiring) and "page" sources where confident.
- Market: regulation, policy, industry, economy, technology queries specific to this company's market and geography.
- Every source needs "name" and a one-sentence "reason" explaining why it is specific, recent and credible for this company.
- Do not use Reddit. Do not invent URLs."""


def discover(cid):
    c = db.get_company(cid)
    existing = db.q("SELECT name FROM sources WHERE company_id=?", (cid,))
    data = llm.generate_json(_prompt(c, existing))
    if not isinstance(data, dict):
        raise llm.LLMError("Unexpected discovery format from the model.")
    comps = data.get("competitors", [])[:10]
    for comp in comps:
        if comp.get("name"):
            db.ex_count("INSERT OR IGNORE INTO competitors(company_id,name,website,why) VALUES(?,?,?,?)",
                        (cid, comp["name"], comp.get("website", ""), comp.get("why_relevant", "")))
    added, rejected = 0, []
    for s in data.get("sources", []):
        cat, kind = s.get("category"), s.get("kind")
        if cat not in CATEGORIES or kind not in KINDS or not s.get("name") or not s.get("reason"):
            rejected.append(f"{s.get('name')}: malformed")
            continue
        query = (s.get("query") or "").strip()
        if kind == "page":
            url = (s.get("url") or "").strip()
            if not url.startswith("http"):
                rejected.append(f"{s['name']}: no valid url")
                continue
            try:
                research.polite_get(url)  # must be reachable and allowed by robots.txt
            except Exception as e:
                rejected.append(f"{s['name']}: {url} not usable ({e})")
                continue
        else:
            if not query:
                rejected.append(f"{s['name']}: no query")
                continue
            if kind == "stackexchange" and s.get("site") not in SE_SITES:
                rejected.append(f"{s['name']}: unsupported stackexchange site")
                continue
            url = build_url(kind, query, s.get("site"))
        added += db.ex_count(
            "INSERT OR IGNORE INTO sources(company_id,category,name,kind,url,query,reason,status,added_at)"
            " VALUES(?,?,?,?,?,?,?,'pending',?)",
            (cid, cat, s["name"], kind, url, query, s["reason"], db.now()))
    return {"added": added, "competitors": len(comps), "rejected": rejected}


def reassess(cid):
    """After the business context changed: flag sources that no longer fit, add new candidates.
    Returns a plain-language list of what changed."""
    c = db.get_company(cid)
    cur = db.q("SELECT id,category,name,reason FROM sources WHERE company_id=? "
               "AND status IN ('approved','pending')", (cid,))
    prompt = f"""A company's business context changed. Re-check which existing monitoring sources still fit.
COMPANY AND INTERVIEW PROFILE (updated): {_context(c['profile'])}
EXISTING SOURCES: {json.dumps(cur)[:8000]}
Return JSON: {{"drop": [{{"id": int, "why": str}}], "summary": str}}
Only list a source in "drop" if it is clearly no longer relevant given the updated context."""
    data = llm.generate_json(prompt)
    changes = []
    for d in (data.get("drop", []) if isinstance(data, dict) else []):
        row = next((s for s in cur if s["id"] == d.get("id")), None)
        if row:
            db.ex("UPDATE sources SET status='flagged', reason=reason||?  WHERE id=?",
                  (f"  [Flagged on reassessment: {d.get('why', '')}]", row["id"]))
            changes.append(f"Flagged as no longer useful: {row['name']} ({d.get('why', '')})")
    res = discover(cid)
    changes.append(f"Added {res['added']} new candidate sources (pending your approval).")
    if isinstance(data, dict) and data.get("summary"):
        changes.insert(0, data["summary"])
    return changes
