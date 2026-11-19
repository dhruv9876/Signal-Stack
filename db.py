"""SQLite storage for Signal Stack. One small file, no ORM."""
import json
import os
import sqlite3
import datetime as dt
from contextlib import closing

DB_PATH = os.getenv("SIGNAL_DB", "signal_stack.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS companies(
  id INTEGER PRIMARY KEY, name TEXT, url TEXT, profile TEXT, created_at TEXT);
CREATE TABLE IF NOT EXISTS qa(
  id INTEGER PRIMARY KEY, company_id INT, idx INT, question TEXT, answer TEXT,
  skipped INT DEFAULT 0, UNIQUE(company_id, idx));
CREATE TABLE IF NOT EXISTS competitors(
  id INTEGER PRIMARY KEY, company_id INT, name TEXT, website TEXT, why TEXT,
  UNIQUE(company_id, name));
CREATE TABLE IF NOT EXISTS sources(
  id INTEGER PRIMARY KEY, company_id INT, category TEXT, name TEXT, kind TEXT,
  url TEXT, query TEXT, reason TEXT, status TEXT DEFAULT 'pending',
  added_at TEXT, last_checked TEXT, last_status TEXT, fail_count INT DEFAULT 0,
  last_hash TEXT, last_text TEXT, items_found INT DEFAULT 0,
  UNIQUE(company_id, url));
CREATE TABLE IF NOT EXISTS items(
  id INTEGER PRIMARY KEY, company_id INT, source_id INT, category TEXT,
  title TEXT, url TEXT, published TEXT, discovered TEXT, snippet TEXT,
  dedup_key TEXT, analysis TEXT, scores TEXT, priority REAL,
  UNIQUE(company_id, dedup_key));
CREATE TABLE IF NOT EXISTS reports(
  id INTEGER PRIMARY KEY, company_id INT, kind TEXT, created_at TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS runs(
  id INTEGER PRIMARY KEY, company_id INT, kind TEXT, started TEXT,
  finished TEXT, status TEXT, message TEXT);
CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT);
"""


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")


def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init():
    with closing(_conn()) as c:
        c.executescript(SCHEMA)
        c.commit()


def q(sql, args=()):
    with closing(_conn()) as c:
        return [dict(r) for r in c.execute(sql, args).fetchall()]


def q1(sql, args=()):
    rows = q(sql, args)
    return rows[0] if rows else None


def ex(sql, args=()):
    """Run a write; returns lastrowid."""
    with closing(_conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.lastrowid


def ex_count(sql, args=()):
    """Run a write; returns number of rows affected (0 when INSERT OR IGNORE skipped)."""
    with closing(_conn()) as c:
        cur = c.execute(sql, args)
        c.commit()
        return cur.rowcount


def get_setting(key, default=None):
    r = q1("SELECT value FROM settings WHERE key=?", (key,))
    return r["value"] if r else default


def set_setting(key, value):
    ex("INSERT INTO settings(key,value) VALUES(?,?) "
       "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))


def get_company(cid):
    c = q1("SELECT * FROM companies WHERE id=?", (cid,))
    if c:
        c["profile"] = json.loads(c["profile"] or "{}")
    return c


def save_profile(cid, profile):
    ex("UPDATE companies SET profile=? WHERE id=?", (json.dumps(profile), cid))
