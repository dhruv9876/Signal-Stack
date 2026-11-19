"""Intelligence analysis: separates what the source says (evidence) from what it means (interpretation)."""
import json

import db
import llm
import ranking

BATCH = 8


def _context(c):
    keep = {k: v for k, v in c["profile"].items() if k not in ("pages_read", "fetch_errors")}
    return json.dumps(keep, ensure_ascii=False)[:3500]


def _prompt(c, batch):
    payload = [{"id": r["id"], "category": r["category"], "source": r["source_name"],
                "title": r["title"], "published": r["published"],
                "snippet": (r["snippet"] or "")[:3000 if r["category"] == "Internal" else 500]}
               for r in batch]
    return f"""You are a strategic analyst for {c['name']}.
BUSINESS CONTEXT: {_context(c)}

Analyse each collected item below. Return a JSON array, one object per item:
{{"id": int, "relevant": bool,
 "what_happened": "restate ONLY what the title/snippet says. No speculation, no invented numbers or dates",
 "why_it_matters": "your INTERPRETATION for this specific business",
 "business_area": str, "signal_type": "opportunity|threat|neutral",
 "urgency": 1-5, "business_impact": 1-5, "relevance": 1-5,
 "suggested_action": str or null,
 "customer_language": "a verbatim phrase from the snippet if a customer voice, else null"}}
Rules: relevance 1 means generic news with no effect on this business. If the snippet is thin, say the
evidence is limited. For Internal items, look for recurring objections, lost-deal reasons, product requests.

ITEMS: {json.dumps(payload, ensure_ascii=False)}"""


def analyze_pending(cid, limit=40):
    c = db.get_company(cid)
    rows = db.q("""SELECT i.*, s.name AS source_name, s.kind AS kind FROM items i
                   JOIN sources s ON s.id = i.source_id
                   WHERE i.company_id=? AND i.analysis IS NULL
                   ORDER BY COALESCE(i.published, i.discovered) DESC LIMIT ?""", (cid, limit))
    if not rows:
        return {"analysed": 0, "failed_batches": 0}
    others = [(r["title"], r["source_id"], r["id"]) for r in
              db.q("SELECT id, title, source_id FROM items WHERE company_id=?", (cid,))]
    done, failed, last_err = 0, 0, None
    for i in range(0, len(rows), BATCH):
        batch = rows[i:i + BATCH]
        try:
            data = llm.generate_json(_prompt(c, batch))
        except llm.LLMError as e:
            failed, last_err = failed + 1, e
            continue
        by_id = {}
        for d in data if isinstance(data, list) else []:
            try:
                by_id[int(d["id"])] = d
            except (KeyError, TypeError, ValueError):
                pass
        for r in batch:
            a = by_id.get(r["id"])
            if not a:
                continue  # leave unanalysed; it is retried on the next run
            other = [(t, sid) for t, sid, iid in others if iid != r["id"]]
            scores, priority = ranking.score_item(r, r["kind"], a, other)
            db.ex("UPDATE items SET analysis=?, scores=?, priority=? WHERE id=?",
                  (json.dumps(a), json.dumps(scores), priority, r["id"]))
            done += 1
    if failed and not done:
        raise last_err
    return {"analysed": done, "failed_batches": failed}
