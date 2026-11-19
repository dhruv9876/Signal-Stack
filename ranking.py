"""Signal ranking. Three separate measures are kept apart on purpose:
  source_reliability  - how trustworthy this kind of source is
  evidence_strength   - how well THIS item is documented (link, date, snippet, primary source)
  strategic_relevance - how much it matters to THIS business (from the analysis step)
The LLM's opinion is never treated as proof that something happened."""
import math
import re
from datetime import datetime, timezone

RELIABILITY = {"upload": 0.9, "page": 0.8, "news": 0.6, "stackexchange": 0.5, "hn": 0.45}
WEIGHTS = {"relevance": 0.30, "impact": 0.20, "urgency": 0.15,
           "credibility": 0.15, "recency": 0.10, "novelty": 0.10}


def _clamp(x, lo=1, hi=5):
    try:
        return max(lo, min(hi, int(float(x))))
    except (TypeError, ValueError):
        return lo


def _parse(ts):
    if not ts:
        return None
    try:
        d = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def recency(published, discovered, now=None):
    now = now or datetime.now(timezone.utc)
    d, penalty = _parse(published), 1.0
    if d is None:                      # unknown publication date: use discovery date, discounted
        d, penalty = _parse(discovered) or now, 0.5
    days = max(0.0, (now - d).total_seconds() / 86400)
    return round(math.exp(-days / 14) * penalty, 3)


def _tokens(s):
    return set(re.findall(r"[a-z0-9]{3,}", (s or "").lower()))


def similarity(a, b):
    ta, tb = _tokens(a), _tokens(b)
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


def novelty(title, other_titles):
    return round(1 - max((similarity(title, t) for t in other_titles), default=0.0), 3)


def evidence_strength(item, kind):
    s = 0.0
    s += 0.35 if item.get("url") else 0
    s += 0.2 if _parse(item.get("published")) else 0
    s += 0.2 if len(item.get("snippet") or "") > 80 else 0
    s += 0.25 if kind in ("page", "upload") else 0
    return round(s, 2)


def verification(kind, corroborating):
    base = {"page": "primary source (page content changed)", "upload": "primary source (internal data)",
            "news": "press-reported", "stackexchange": "community post (unverified claim)",
            "hn": "community post (unverified claim)"}.get(kind, "unverified")
    return base + (f"; corroborated by {corroborating} other source(s)" if corroborating else "")


def score_item(item, kind, analysis, other):
    """other = list of (title, source_id) for the company's other items."""
    rel, imp, urg = (_clamp(analysis.get(k)) for k in ("relevance", "business_impact", "urgency"))
    cred = RELIABILITY.get(kind, 0.4)
    rec = recency(item.get("published"), item.get("discovered"))
    nov = novelty(item["title"], [t for t, _ in other])
    corroborating = len({sid for t, sid in other if sid != item["source_id"] and similarity(item["title"], t) >= 0.6})
    priority = 100 * (WEIGHTS["relevance"] * rel / 5 + WEIGHTS["impact"] * imp / 5
                      + WEIGHTS["urgency"] * urg / 5 + WEIGHTS["credibility"] * cred
                      + WEIGHTS["recency"] * rec + WEIGHTS["novelty"] * nov)
    if not analysis.get("relevant", True):
        priority *= 0.3
    scores = {
        "strategic_relevance": round(rel / 5, 2), "source_reliability": cred,
        "evidence_strength": evidence_strength(item, kind), "recency": rec, "novelty": nov,
        "verification": verification(kind, corroborating),
        "why_ranked": (f"relevance {rel}/5, impact {imp}/5, urgency {urg}/5; source reliability {cred:.2f}; "
                       f"recency {rec:.2f}; novelty {nov:.2f}"),
    }
    return scores, round(priority, 1)
