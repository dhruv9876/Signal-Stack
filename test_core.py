"""Offline tests: the LLM and the network are mocked, so no API key is needed.
They check the logic that must never be faked: dedup, change detection, ranking,
quote verification, source validation, brief traceability, audit, scheduler logging."""
import json

import pytest

import analysis
import brief
import crawler
import db
import llm
import ranking
import research
import scheduler
import sources


class FakeResp:
    def __init__(self, text="", js=None, status=200):
        self.text, self.content, self._js, self.status_code = text, text.encode(), js, status

    def json(self):
        return self._js

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


@pytest.fixture
def cid(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "t.db"))
    db.init()
    return db.ex("INSERT INTO companies(name,url,profile,created_at) VALUES(?,?,?,?)",
                 ("Acme", "https://acme.test", json.dumps({"summary": "Acme sells widgets"}), db.now()))


def add_source(cid, kind="news", status="approved", url="https://feed.test/a", cat="Market"):
    return db.ex("INSERT INTO sources(company_id,category,name,kind,url,query,reason,status,added_at) "
                 "VALUES(?,?,?,?,?,?,?,?,?)", (cid, cat, "S-" + url, kind, url, "q", "because", status, db.now()))


RSS = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>Rival launches cheaper widget plan</title><link>https://news.test/1</link>
<pubDate>Mon, 16 Nov 2026 08:00:00 GMT</pubDate><description>Rival Inc cut prices by 20 percent</description></item>
<item><title>Regulator updates widget rules</title><link>https://news.test/2</link>
<pubDate>Sun, 15 Nov 2026 08:00:00 GMT</pubDate><description>New rules apply from January</description></item>
</channel></rss>"""


def test_crawl_dedups_and_records_health(cid, monkeypatch):
    sid = add_source(cid)
    monkeypatch.setattr(research, "polite_get", lambda url, check_robots=True: FakeResp(RSS))
    first = crawler.run_crawl(cid)
    second = crawler.run_crawl(cid)
    assert first["new_items"] == 2 and second["new_items"] == 0  # duplicates are not stored twice
    assert db.q1("SELECT last_status, fail_count FROM sources WHERE id=?", (sid,)) == {"last_status": "ok", "fail_count": 0}
    item = db.q1("SELECT * FROM items WHERE title LIKE 'Rival%'")
    assert item["url"] == "https://news.test/1" and item["published"].startswith("2026-11-16")


def test_failed_source_is_counted_not_fatal(cid, monkeypatch):
    sid = add_source(cid)

    def boom(url, check_robots=True):
        raise PermissionError("blocked by robots.txt")
    monkeypatch.setattr(research, "polite_get", boom)
    res = crawler.run_crawl(cid)
    assert res["failed"] == 1
    row = db.q1("SELECT * FROM sources WHERE id=?", (sid,))
    assert row["fail_count"] == 1 and "robots" in row["last_status"]


def test_page_change_detection(cid, monkeypatch):
    sid = add_source(cid, kind="page", url="https://rival.test/pricing", cat="Competitors")
    html1 = "<p>The starter plan costs ten dollars per month for everyone.</p>"
    html2 = html1 + "<p>The new enterprise plan costs one hundred dollars per month.</p>"
    pages = iter([html1, html1, html2])
    monkeypatch.setattr(research, "polite_get", lambda url, check_robots=True: FakeResp(next(pages)))
    assert crawler.run_crawl(cid)["new_items"] == 0   # baseline is not a "development"
    assert crawler.run_crawl(cid)["new_items"] == 0   # unchanged
    assert crawler.run_crawl(cid)["new_items"] == 1   # changed
    it = db.q1("SELECT * FROM items")
    assert "enterprise plan" in it["snippet"]


def test_upload_csv_and_rejects_unsupported(cid):
    n = crawler.add_upload(cid, "lost_deals.csv", b"deal,reason\nA,too expensive\nB,no visa support\n")
    assert n == 1
    assert "too expensive" in db.q1("SELECT snippet FROM items")["snippet"]
    assert crawler.add_upload(cid, "lost_deals.csv", b"deal,reason\nA,too expensive\nB,no visa support\n") == 0
    with pytest.raises(ValueError):
        crawler.add_upload(cid, "x.pdf", b"%PDF")


def test_ranking_orders_sensibly():
    item = {"title": "Rival cuts price", "published": None, "discovered": db.now(), "snippet": "", "url": "u", "source_id": 1}
    hi, p_hi = ranking.score_item(item, "page", {"relevance": 5, "business_impact": 5, "urgency": 5}, [])
    lo, p_lo = ranking.score_item(item, "hn", {"relevance": 1, "business_impact": 1, "urgency": 1, "relevant": False}, [])
    assert p_hi > p_lo
    assert {"strategic_relevance", "source_reliability", "evidence_strength"} <= set(hi)
    assert hi["verification"].startswith("primary source") and lo["verification"].startswith("community")
    assert ranking.novelty("Rival cuts price", ["Rival cuts price today"]) < ranking.novelty("Rival cuts price", ["Weather"])
    assert ranking.recency("2020-01-01T00:00:00+00:00", None) < 0.01


def test_profile_demotes_unverifiable_facts(monkeypatch):
    monkeypatch.setattr(research, "crawl_site", lambda url: ([{"url": url, "text": "We help students apply to universities abroad since 2015."}], []))
    monkeypatch.setattr(llm, "generate_json", lambda p, **k: {
        "summary": "s", "verified_facts": [
            {"fact": "helps students", "evidence_url": "u", "quote": "We help students apply to universities abroad"},
            {"fact": "has 5 million users", "evidence_url": "u", "quote": "We have five million happy users worldwide"}],
        "assumptions": ["probably B2C"]})
    p = research.build_profile("X", "https://x.test")
    assert [f["fact"] for f in p["verified_facts"]] == ["helps students"]
    assert any("5 million" in a for a in p["assumptions"])


def test_discover_builds_urls_in_code_and_rejects_bad_sources(cid, monkeypatch):
    monkeypatch.setattr(llm, "generate_json", lambda p, **k: {
        "competitors": [{"name": "Rival", "website": "https://rival.test", "why_relevant": "same market"}],
        "sources": [
            {"category": "Market", "kind": "news", "name": "Rules", "query": "widget regulation India", "reason": "r"},
            {"category": "Competitors", "kind": "page", "name": "Dead page", "url": "https://gone.test/x", "reason": "r"},
            {"category": "Customers", "kind": "stackexchange", "name": "Bad site", "query": "q", "site": "nonsense", "reason": "r"},
            {"category": "Nope", "kind": "news", "name": "Bad cat", "query": "q", "reason": "r"}]})

    def get(url, check_robots=True):
        raise RuntimeError("404")
    monkeypatch.setattr(research, "polite_get", get)
    res = sources.discover(cid)
    assert res["added"] == 1 and len(res["rejected"]) == 3
    s = db.q1("SELECT * FROM sources")
    assert s["status"] == "pending" and s["url"].startswith("https://news.google.com/rss/search?q=widget")
    assert db.q1("SELECT name FROM competitors")["name"] == "Rival"


def _seed_items(cid):
    sid = add_source(cid)
    for i, t in enumerate(["Rival launches cheaper widget plan", "Regulator updates widget rules"]):
        db.ex("INSERT INTO items(company_id,source_id,category,title,url,published,discovered,snippet,dedup_key)"
              " VALUES(?,?,?,?,?,?,?,?,?)", (cid, sid, "Competitors" if i == 0 else "Market", t, f"https://n.test/{i}",
                                              db.now(), db.now(), "Rival Inc cut prices by 20 percent in a move aimed at us", f"k{i}"))


def test_analysis_then_brief_is_traceable(cid, monkeypatch):
    _seed_items(cid)

    def fake(prompt, **k):
        ids = [int(x) for x in __import__("re").findall(r'"id": (\d+)', prompt)]
        return [{"id": i, "relevant": True, "what_happened": "Rival cut prices.", "why_it_matters": "We may lose deals.",
                 "business_area": "pricing", "signal_type": "threat", "urgency": 4, "business_impact": 4,
                 "relevance": 4, "suggested_action": "Review pricing"} for i in ids]
    monkeypatch.setattr(llm, "generate_json", fake)
    assert analysis.analyze_pending(cid)["analysed"] == 2
    assert db.q1("SELECT COUNT(*) n FROM items WHERE analysis IS NULL")["n"] == 0

    monkeypatch.setattr(llm, "generate", lambda p, **k: "## Executive Summary\nRival cut prices [#1]. Also [#999].")
    r = brief.generate(cid)
    assert "https://n.test/0" in r["body"] and "Source Appendix" in r["body"]
    assert "WARNING" in r["body"] and "999" in r["body"]       # invented ids are flagged
    assert db.q1("SELECT COUNT(*) n FROM reports")["n"] == 1


def test_brief_with_no_evidence_makes_nothing_up(cid, monkeypatch):
    def nope(*a, **k):
        raise AssertionError("LLM must not be called when there is no evidence")
    monkeypatch.setattr(llm, "generate", nope)
    r = brief.generate(cid)
    assert "No analysed, relevant developments" in r["body"]


def test_scheduler_logs_failures_and_audit_runs(cid, monkeypatch):
    add_source(cid)
    monkeypatch.setattr(research, "polite_get", lambda url, check_robots=True: FakeResp(RSS))

    def boom(*a, **k):
        raise llm.LLMError("quota exceeded")
    monkeypatch.setattr(llm, "generate_json", boom)
    assert scheduler.weekly_brief(cid, trigger="manual") is False
    run = db.q1("SELECT * FROM runs ORDER BY id DESC")
    assert run["status"] == "failed" and "quota" in run["message"]

    db.ex("UPDATE sources SET fail_count=4, last_status='error: x'")
    assert scheduler.audit_job(cid, trigger="manual") is True
    rep = db.q1("SELECT body FROM reports WHERE kind='audit'")["body"]
    assert "Dead or failing" in rep and "S-https://feed.test/a" in rep


def test_scheduler_jobs_register(cid):
    s = scheduler.start()
    assert {"weekly", "monthly", "crawl"} <= {j.id for j in s.get_jobs()}
    s.shutdown(wait=False)
