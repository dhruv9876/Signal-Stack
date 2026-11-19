"""Command-line runner, handy for cron / Task Scheduler when the dashboard is not running.
  python run_job.py brief [company_id]
  python run_job.py crawl [company_id]
  python run_job.py audit [company_id]
"""
import sys

import db
import scheduler

db.init()
job = sys.argv[1] if len(sys.argv) > 1 else "brief"
ids = [int(sys.argv[2])] if len(sys.argv) > 2 else [c["id"] for c in db.q("SELECT id FROM companies")]
fn = {"brief": scheduler.weekly_brief, "crawl": scheduler.crawl_only, "audit": scheduler.audit_job}[job]
for cid in ids:
    ok = fn(cid, trigger="cli")
    print(f"company {cid}: {job} {'ok' if ok else 'FAILED (see runs table)'}")
