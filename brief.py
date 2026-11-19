"""Monday Morning Brief. The model writes the analysis; the Source Appendix is built by code
from stored records, so every link, date and snippet is real and traceable."""
import json
import re
from datetime import datetime, timedelta, timezone

import db
import llm

CATEGORIES = ["Customers", "Competitors", "Market", "Internal"]
PER_CATEGORY = 8


def _evidence(cid, days):
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat(timespec="seconds")
    rows = db.q("""SELECT i.*, s.name AS source_name, s.kind AS kind FROM items i
                   JOIN sources s ON s.id = i.source_id
                   WHERE i.company_id=? AND i.analysis IS NOT NULL
                     AND COALESCE(i.published, i.discovered) >= ?
                   ORDER BY i.priority DESC""", (cid, cutoff))
    by_cat = {c: [] for c in CATEGORIES}
    for r in rows:
        a = json.loads(r["analysis"])
        if a.get("relevance", 1) >= 2 and len(by_cat[r["category"]]) < PER_CATEGORY:
            r["a"], r["s"] = a, json.loads(r["scores"])
            by_cat[r["category"]].append(r)
    return by_cat


def _appendix(by_cat, cited):
    lines = ["## Source Appendix", ""]
    for cat in CATEGORIES:
        for r in by_cat[cat]:
            snippet = (r["snippet"] or "").replace("\n", " ")[:220]
            lines.append(
                f"- **[#{r['id']}]** {r['title']} ({cat}{', cited' if r['id'] in cited else ''})  \n"
                f"  Source: {r['source_name']} ({r['kind']}) · Link: {r['url']}  \n"
                f"  Published: {r['published'] or 'unknown'} · Discovered: {r['discovered']}  \n"
                f"  Evidence: \"{snippet}\"  \n"
                f"  Verification: {r['s']['verification']} · evidence strength {r['s']['evidence_strength']} · "
                f"source reliability {r['s']['source_reliability']} · strategic relevance "
                f"{r['s']['strategic_relevance']} · priority {r['priority']}")
    return "\n".join(lines)


def generate(cid, days=30):
    c = db.get_company(cid)
    by_cat = _evidence(cid, days)
    total = sum(len(v) for v in by_cat.values())
    n_sources = db.q1("SELECT COUNT(*) n FROM sources WHERE company_id=? AND status='approved'", (cid,))["n"]
    header = (f"# Monday Morning Brief: {c['name']}\n"
              f"*Generated {db.now()} UTC · window: last {days} days · {total} analysed items "
              f"from {n_sources} approved sources*\n")
    if total == 0:
        body = ("## Executive Summary\nNo analysed, relevant developments were found in this window. "
                "Nothing has been invented to fill the report. Check that sources are approved, run a "
                "crawl, then analysis.\n")
        cited, text = set(), body
    else:
        payload = {cat: [{"id": r["id"], "source": r["source_name"], "title": r["title"],
                          "published": r["published"], "what_happened": r["a"].get("what_happened"),
                          "why_it_matters": r["a"].get("why_it_matters"),
                          "type": r["a"].get("signal_type"), "urgency": r["a"].get("urgency"),
                          "impact": r["a"].get("business_impact"), "verification": r["s"]["verification"],
                          "suggested_action": r["a"].get("suggested_action"),
                          "customer_language": r["a"].get("customer_language"), "priority": r["priority"]}
                         for r in rows] for cat, rows in by_cat.items()}
        prof = json.dumps({k: v for k, v in c["profile"].items() if k not in ("pages_read", "fetch_errors")},
                          ensure_ascii=False)[:3500]
        prompt = f"""Write the Monday Morning Intelligence Brief for the founder of {c['name']}.
COMPANY CONTEXT: {prof}
EVIDENCE (the ONLY facts you may use), grouped by category: {json.dumps(payload, ensure_ascii=False)}

Use exactly these markdown sections, in order:
## Executive Summary (the five most important developments; fewer if fewer exist)
## Customer Signals
## Competitor Intelligence
## Market Intelligence
## Internal Business Intelligence
## Opportunities Identified (each: evidence, impact, next step, priority; if fewer than three are supported by the evidence, say how many are and do not pad)
## Risks & Threats (each: possible impact, urgency)
## Recommended Actions for This Week (prioritised; reason; suggested owner as a ROLE, not a person)
## What We Should Watch

Rules:
- Cite every claim with its item id like [#12]. Use only ids that appear in the evidence.
- Keep evidence and interpretation visibly apart: write "Evidence:" then "Interpretation:" for key items.
- If a category has no items, write "No meaningful developments this period." Do not invent news.
- Explain business implications; do not just summarise articles. No generic advice that ignores the evidence.
- Do not write a Source Appendix; it is added automatically."""
        text = llm.generate(prompt, temperature=0.3)
        cited = {int(x) for x in re.findall(r"\[#(\d+)\]", text)}
        known = {r["id"] for rows in by_cat.values() for r in rows}
        if cited - known:
            text += (f"\n\n> WARNING: the model cited item ids that are not in the evidence set: "
                     f"{sorted(cited - known)}. Treat those statements as unverified.")
    body = header + "\n" + text.strip() + "\n\n" + (_appendix(by_cat, cited) if total else "")
    rid = db.ex("INSERT INTO reports(company_id,kind,created_at,body) VALUES(?,?,?,?)",
                (cid, "brief", db.now(), body))
    return {"id": rid, "body": body, "items": total}
