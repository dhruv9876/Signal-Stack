"""Monitoring: fetch approved sources, detect new/changed content, dedupe, store evidence + history."""
import calendar
import csv
import hashlib
import html
import io
import re
from datetime import datetime, timezone

import feedparser
from bs4 import BeautifulSoup

import db
import research


def _iso_struct(t):
    return datetime.fromtimestamp(calendar.timegm(t), timezone.utc).isoformat(timespec="seconds") if t else None


def _iso_epoch(n):
    return datetime.fromtimestamp(int(n), timezone.utc).isoformat(timespec="seconds") if n else None


def _clean(markup):
    return re.sub(r"\s+", " ", BeautifulSoup(markup or "", "html.parser").get_text(" ")).strip()


def _sentences(text):
    return {s.strip() for s in re.split(r"(?<=[.!?])\s+", text) if len(s.strip()) > 25}


def fetch(src):
    """Returns (items, new_hash, new_text). Items are dicts: title,url,published,snippet."""
    kind = src["kind"]
    if kind == "news":
        r = research.polite_get(src["url"], check_robots=False)  # official RSS feed
        out = []
        for e in feedparser.parse(r.content).entries[:15]:
            pub = (e.get("source") or {}).get("title")
            snip = _clean(e.get("summary", ""))
            if pub and pub not in snip:
                snip = f"{pub}: {snip}"
            out.append({"title": _clean(e.get("title", "")), "url": e.get("link"),
                        "published": _iso_struct(e.get("published_parsed") or e.get("updated_parsed")),
                        "snippet": snip[:500]})
        return out, None, None
    if kind == "hn":
        r = research.polite_get(src["url"], check_robots=False)  # official JSON API
        out = []
        for h in r.json().get("hits", [])[:15]:
            title = h.get("title") or h.get("story_title") or ""
            url = h.get("url") or f"https://news.ycombinator.com/item?id={h.get('objectID')}"
            out.append({"title": title, "url": url, "published": h.get("created_at"),
                        "snippet": _clean(h.get("story_text") or "")[:500] or title})
        return out, None, None
    if kind == "stackexchange":
        r = research.polite_get(src["url"], check_robots=False)  # official JSON API
        out = []
        for it in r.json().get("items", [])[:15]:
            out.append({"title": html.unescape(it.get("title", "")), "url": it.get("link"),
                        "published": _iso_epoch(it.get("creation_date")),
                        "snippet": _clean(it.get("body", ""))[:500]})
        return out, None, None
    if kind == "page":
        r = research.polite_get(src["url"])  # robots.txt enforced
        text = research.page_text(r.text)
        h = hashlib.sha1(text.encode()).hexdigest()
        if src["last_hash"] is None:      # first visit = baseline, not a "development"
            return [], h, text[:60000]
        if h == src["last_hash"]:
            return [], h, src["last_text"]
        added = sorted(_sentences(text) - _sentences(src["last_text"] or ""))
        if not added:
            return [], h, text[:60000]
        item = {"title": f"Page changed: {src['name']}", "url": src["url"],
                "published": db.now(), "snippet": " | ".join(added[:5])[:500]}
        return [item], h, text[:60000]
    raise ValueError(f"unknown kind {kind}")


def dedup_key(kind, item):
    title = re.sub(r"\W+", " ", item["title"].lower()).strip()
    if kind == "page":
        return hashlib.sha1((item["url"] + title + item["snippet"]).encode()).hexdigest()
    return hashlib.sha1(title.encode()).hexdigest()


def run_crawl(cid):
    srcs = db.q("SELECT * FROM sources WHERE company_id=? AND status='approved' AND kind!='upload'", (cid,))
    summary = {"checked": 0, "failed": 0, "new_items": 0}
    for s in srcs:
        summary["checked"] += 1
        try:
            items, new_hash, new_text = fetch(s)
            new = 0
            for it in items:
                if not it["title"]:
                    continue
                new += db.ex_count(
                    "INSERT OR IGNORE INTO items(company_id,source_id,category,title,url,published,"
                    "discovered,snippet,dedup_key) VALUES(?,?,?,?,?,?,?,?,?)",
                    (cid, s["id"], s["category"], it["title"], it["url"], it["published"], db.now(),
                     it["snippet"], dedup_key(s["kind"], it)))
            db.ex("UPDATE sources SET last_checked=?, last_status='ok', fail_count=0, last_hash=?, "
                  "last_text=?, items_found=items_found+? WHERE id=?",
                  (db.now(), new_hash if new_hash else s["last_hash"],
                   new_text if new_text is not None else s["last_text"], new, s["id"]))
            summary["new_items"] += new
        except Exception as e:
            summary["failed"] += 1
            db.ex("UPDATE sources SET last_checked=?, last_status=?, fail_count=fail_count+1 WHERE id=?",
                  (db.now(), f"error: {type(e).__name__}: {str(e)[:120]}", s["id"]))
    return summary


def add_upload(cid, filename, data: bytes):
    """Internal intelligence: CSV or text upload. (Excel/PDF are documented future work.)"""
    name = filename.lower()
    if not name.endswith((".csv", ".txt", ".md")):
        raise ValueError("Only .csv, .txt and .md uploads are supported in this version.")
    text = data.decode("utf-8", errors="replace")
    if name.endswith(".csv"):
        rows = list(csv.DictReader(io.StringIO(text)))[:200]
        text = "\n".join("; ".join(f"{k}: {v}" for k, v in r.items() if v) for r in rows)
    url = f"upload://{filename}"
    db.ex_count("INSERT OR IGNORE INTO sources(company_id,category,name,kind,url,reason,status,added_at)"
                " VALUES(?,?,?,?,?,?,?,?)",
                (cid, "Internal", filename, "upload", url,
                 "Internal data supplied by the founder (CRM, feedback, transcripts).", "approved", db.now()))
    src = db.q1("SELECT id FROM sources WHERE company_id=? AND url=?", (cid, url))
    snippet = text[:3000]
    key = hashlib.sha1(snippet.encode()).hexdigest()
    n = db.ex_count("INSERT OR IGNORE INTO items(company_id,source_id,category,title,url,published,"
                    "discovered,snippet,dedup_key) VALUES(?,?,?,?,?,?,?,?,?)",
                    (cid, src["id"], "Internal", f"Internal file: {filename}", url, None, db.now(), snippet, key))
    db.ex("UPDATE sources SET last_checked=?, last_status='ok', items_found=items_found+? WHERE id=?",
          (db.now(), n, src["id"]))
    return n
