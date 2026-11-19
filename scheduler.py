"""Two independent automated processes (weekly brief, monthly source audit) + a periodic crawl.
Every run is logged in the `runs` table so failures are visible in the dashboard."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
import json

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import analysis
import brief
import crawler
import db
import sources


def _logged(kind, cid, fn):
    rid = db.ex("INSERT INTO runs(company_id,kind,started,status) VALUES(?,?,?,'running')", (cid, kind, db.now()))
    try:
        msg = fn()
        db.ex("UPDATE runs SET finished=?, status='ok', message=? WHERE id=?", (db.now(), str(msg)[:500], rid))
        return True
    except Exception as e:
        db.ex("UPDATE runs SET finished=?, status='failed', message=? WHERE id=?",
              (db.now(), f"{type(e).__name__}: {e}"[:500], rid))
        return False


def weekly_brief(cid, trigger="scheduled"):
    def job():
        c = crawler.run_crawl(cid)
        a = analysis.analyze_pending(cid)
        r = brief.generate(cid)
        return f"crawl {c}; analysis {a}; brief #{r['id']} with {r['items']} items"
    return _logged(f"weekly_brief:{trigger}", cid, job)


def crawl_only(cid, trigger="scheduled"):
    return _logged(f"crawl:{trigger}", cid, lambda: crawler.run_crawl(cid))


def audit(cid):
    """Monthly Signal Stack audit -> markdown report saved in `reports`."""
    srcs = db.q("SELECT * FROM sources WHERE company_id=? AND status IN ('approved','pending','flagged')", (cid,))
    now = datetime.now(timezone.utc)
    dead = [s for s in srcs if s["fail_count"] >= 3]
    never = [s for s in srcs if s["status"] == "approved" and s["items_found"] == 0 and s["kind"] != "page"
             and s["added_at"] and datetime.fromisoformat(s["added_at"]) < now - timedelta(days=14)]
    inactive = []
    for s in srcs:
        last = db.q1("SELECT MAX(discovered) d FROM items WHERE source_id=?", (s["id"],))["d"]
        if s["kind"] != "page" and last and datetime.fromisoformat(last) < now - timedelta(days=60):
            inactive.append(s)
    seen, dupes = {}, []
    for s in srcs:
        key = (s["url"] or "").lower()
        if key in seen:
            dupes.append((s, seen[key]))
        seen[key] = s
    declining = []
    for s in srcs:
        rel = []
        for it in db.q("SELECT analysis FROM items WHERE source_id=? AND analysis IS NOT NULL "
                       "ORDER BY id DESC LIMIT 5", (s["id"],)):
            rel.append(json.loads(it["analysis"]).get("relevance", 1))
        if len(rel) >= 3 and sum(rel) / len(rel) < 2:
            declining.append((s, sum(rel) / len(rel)))
    try:
        found = sources.discover(cid)
        new_line = f"{found['added']} new candidate sources discovered (pending approval in Source Manager)."
    except Exception as e:
        new_line = f"Discovery step failed: {type(e).__name__}: {e}"

    def lst(rows, fmt):
        return "\n".join(f"- {fmt(r)}" for r in rows) or "- None"
    body = f"""# Monthly Signal Stack Audit
*Generated {db.now()} UTC · {len(srcs)} sources checked*

## Dead or failing URLs (3+ consecutive failures)
{lst(dead, lambda s: f"{s['name']} ({s['last_status']})")}

## Sources that never returned data (approved 14+ days ago)
{lst(never, lambda s: s['name'])}

## Inactive sources (nothing new in 60+ days)
{lst(inactive, lambda s: s['name'])}

## Duplicate sources (same URL)
{lst(dupes, lambda p: f"{p[0]['name']} duplicates {p[1]['name']}")}

## Declining relevance (average of last items below 2/5)
{lst(declining, lambda p: f"{p[0]['name']} (avg {p[1]:.1f}/5)")}

## New sources
- {new_line}

## Recommendations
- Remove: {', '.join(s['name'] for s in dead + [d for d, _ in declining]) or 'nothing'}
- Review and approve or reject the newly discovered candidates.
"""
    db.ex("INSERT INTO reports(company_id,kind,created_at,body) VALUES(?,?,?,?)", (cid, "audit", db.now(), body))
    return f"audit saved: {len(dead)} dead, {len(dupes)} duplicate, {len(declining)} declining; {new_line}"


def audit_job(cid, trigger="scheduled"):
    return _logged(f"audit:{trigger}", cid, lambda: audit(cid))


def _all(fn):
    for c in db.q("SELECT id FROM companies"):
        if db.q1("SELECT 1 x FROM sources WHERE company_id=? AND status='approved'", (c["id"],)):
            fn(c["id"])


def run_all_briefs():
    _all(weekly_brief)


def run_all_audits():
    _all(audit_job)


def run_all_crawls():
    _all(crawl_only)


_sched = None


def configure(s=None):
    s = s or _sched
    tz = ZoneInfo(db.get_setting("timezone", "Asia/Kolkata"))
    hh, mm = (int(x) for x in db.get_setting("brief_time", "08:00").split(":"))
    hours = int(db.get_setting("crawl_every_hours", "24"))
    kw = dict(replace_existing=True, misfire_grace_time=3600, coalesce=True)
    s.add_job(run_all_briefs, CronTrigger(day_of_week="mon", hour=hh, minute=mm, timezone=tz), id="weekly", **kw)
    s.add_job(run_all_audits, CronTrigger(day=1, hour=3, minute=0, timezone=tz), id="monthly", **kw)
    s.add_job(run_all_crawls, IntervalTrigger(hours=hours), id="crawl", **kw)


def start():
    global _sched
    if _sched is None or not _sched.running:
        _sched = BackgroundScheduler(timezone=ZoneInfo(db.get_setting("timezone", "Asia/Kolkata")))
        configure(_sched)
        _sched.start()
    return _sched


def next_runs():
    if not _sched:
        return {}
    return {j.id: j.next_run_time for j in _sched.get_jobs()}
