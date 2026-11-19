import json

import streamlit as st

import analysis
import brief
import crawler
import db
import interview
import llm
import research
import scheduler
import sources

st.set_page_config(page_title="Signal Stack", layout="wide")
db.init()


@st.cache_resource
def _start_scheduler():
    return scheduler.start()


_start_scheduler()


def show_table(rows):
    rows = [dict(r) for r in (rows or [])]
    if not rows:
        st.caption("Nothing yet.")
        return
    cols = list(rows[0].keys())
    md = "| " + " | ".join(cols) + " |\n"
    md += "| " + " | ".join("---" for _ in cols) + " |\n"
    for r in rows:
        md += "| " + " | ".join(
            str(r.get(c, "")).replace("|", "/").replace("\n", " ") for c in cols) + " |\n"
    st.markdown(md)


def run(label, fn):
    """Run a step with a spinner; show errors instead of crashing the page."""
    try:
        with st.spinner(label):
            return fn()
    except llm.LLMError as e:
        st.error(f"AI step failed: {e}")
    except Exception as e:
        st.error(f"{type(e).__name__}: {e}")
    return None


# ---------- sidebar ----------
if "new_cid" in st.session_state:
    st.session_state["cid"] = st.session_state.pop("new_cid")
companies = db.q("SELECT id, name FROM companies ORDER BY id")
st.sidebar.title("Signal Stack")
cid = None
if companies:
    ids = [c["id"] for c in companies]
    names = {c["id"]: c["name"] for c in companies}
    cid = st.sidebar.selectbox("Company", ids, format_func=lambda i: names[i], key="cid")
PAGES = ["Company Setup", "Profile", "AI Interview", "Signal Stack", "Source Manager",
         "Intelligence Feed", "Monday Brief", "Settings"]
page = st.sidebar.radio("Go to", PAGES)
if page != "Company Setup" and cid is None:
    st.info("Add a company first (Company Setup).")
    st.stop()


# ---------- pages ----------
def page_setup():
    st.header("Company setup")
    st.write("Enter a company name and website. The app reads the public pages "
             "(respecting robots.txt) and builds an initial profile.")
    name = st.text_input("Company name")
    url = st.text_input("Website URL", placeholder="https://www.example.com")
    if st.button("Research company", disabled=not (name.strip() and url.strip())):
        prof = run("Reading the website and building the profile...", lambda: research.build_profile(name.strip(), url.strip()))
        if prof:
            new = db.ex("INSERT INTO companies(name,url,profile,created_at) VALUES(?,?,?,?)",
                        (name.strip(), url.strip(), json.dumps(prof), db.now()))
            st.session_state["new_cid"] = new
            st.rerun()


def page_profile():
    c = db.get_company(cid)
    p = c["profile"]
    st.header(f"Company intelligence profile: {c['name']}")
    st.write(p.get("summary", ""))
    col1, col2 = st.columns(2)
    col1.markdown("**Industry:** " + str(p.get("industry")) + "  \n**Geography:** " + str(p.get("geography")))
    col1.markdown("**Products/services:** " + "; ".join(p.get("products_services", [])))
    col1.markdown("**Customer segments:** " + "; ".join(p.get("customer_segments", [])))
    col2.markdown("**Positioning:** " + str(p.get("positioning")))
    st.subheader("Verified facts (quote found on the page)")
    for f in p.get("verified_facts", []):
        st.markdown(f"- {f.get('fact')}  \n  <small>\"{f.get('quote')}\" ({f.get('evidence_url')})</small>",
                    unsafe_allow_html=True)
    if not p.get("verified_facts"):
        st.warning("No facts could be verified from page text. Everything below is an assumption.")
    st.subheader("Assumptions (not proven by the pages)")
    for a in p.get("assumptions", []):
        st.markdown(f"- {a}")
    if p.get("fetch_errors"):
        st.caption("Fetch limitations: " + " | ".join(p["fetch_errors"]))
    if p.get("interview_profile"):
        st.subheader("Interview profile")
        st.json(p["interview_profile"])
    with st.expander("Edit profile (JSON)"):
        raw = st.text_area("Profile JSON", value=json.dumps(p, indent=2, ensure_ascii=False), height=300)
        if st.button("Save profile"):
            try:
                db.save_profile(cid, json.loads(raw))
                st.success("Saved. Use 'Reassess' in Signal Stack to update sources after a change.")
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")


def page_interview():
    c = db.get_company(cid)
    st.header("AI interview")
    rows = db.q("SELECT * FROM qa WHERE company_id=? ORDER BY idx", (cid,))
    done = [r for r in rows if r["answer"] is not None or r["skipped"]]
    pending = [r for r in rows if r["answer"] is None and not r["skipped"]]
    st.progress(min(len(done) / interview.TOTAL, 1.0), text=f"{len(done)} of {interview.TOTAL} answered")
    if len(done) < interview.TOTAL:
        if not pending:
            res = run("Thinking of the next question...", lambda: interview.next_question(c["profile"], done))
            if not res:
                return
            db.ex("INSERT INTO qa(company_id,idx,question) VALUES(?,?,?)", (cid, len(done) + 1, res["question"]))
            st.rerun()
        cur = pending[0]
        st.subheader(f"Question {cur['idx']} of {interview.TOTAL}")
        st.write(cur["question"])
        ans = st.text_area("Your answer", key=f"ans{cid}_{cur['idx']}")
        b1, b2 = st.columns(2)
        if b1.button("Submit answer", disabled=not ans.strip()):
            db.ex("UPDATE qa SET answer=?, skipped=0 WHERE id=?", (ans.strip(), cur["id"]))
            st.rerun()
        if b2.button("Skip (confidential)"):
            db.ex("UPDATE qa SET answer='', skipped=1 WHERE id=?", (cur["id"],))
            st.rerun()
    else:
        st.success("Interview complete.")
    if len(done) >= interview.TOTAL:
        if st.button("Build / rebuild intelligence profile from answers"):
            ip = run("Structuring the profile...", lambda: interview.build_interview_profile(c["profile"], done))
            if ip:
                prof = c["profile"]
                prof["interview_profile"] = ip
                db.save_profile(cid, prof)
                st.success("Profile updated.")
    if done:
        with st.expander("Review and edit earlier answers"):
            for r in done:
                new = st.text_area(f"Q{r['idx']}: {r['question']}", value=r["answer"] or "", key=f"edit{r['id']}")
                if st.button("Save", key=f"save{r['id']}"):
                    db.ex("UPDATE qa SET answer=?, skipped=? WHERE id=?", (new.strip(), 0 if new.strip() else 1, r["id"]))
                    st.success("Saved. Rebuild the profile, then reassess sources.")


def page_stack():
    st.header("Signal Stack")
    c1, c2 = st.columns(2)
    if c1.button("Discover competitors and sources"):
        res = run("Discovering and validating sources (this takes a minute)...", lambda: sources.discover(cid))
        if res:
            st.success(f"Added {res['added']} candidate sources and {res['competitors']} competitors (pending approval).")
            if res["rejected"]:
                with st.expander(f"{len(res['rejected'])} proposals rejected"):
                    st.write("\n".join(f"- {r}" for r in res["rejected"]))
    if c2.button("Reassess after a business-context change"):
        ch = run("Reassessing sources...", lambda: sources.reassess(cid))
        if ch:
            st.markdown("**What changed**\n" + "\n".join(f"- {x}" for x in ch))
    comps = db.q("SELECT name, website, why FROM competitors WHERE company_id=?", (cid,))
    if comps:
        st.subheader("Competitors and substitutes")
        show_table(comps)
    for cat in ["Customers", "Competitors", "Market", "Internal"]:
        rows = db.q("SELECT * FROM sources WHERE company_id=? AND category=? ORDER BY id", (cid, cat))
        st.subheader(f"{cat} ({len(rows)})")
        if not rows:
            st.caption("No sources yet." if cat != "Internal" else "Upload internal files in Source Manager.")
        for s in rows:
            st.markdown(f"- **{s['name']}** `{s['kind']}` · *{s['status']}*  \n  {s['reason']}")


def page_sources():
    st.header("Source manager")
    top1, top2 = st.columns(2)
    if top1.button("Approve all pending sources"):
        db.ex("UPDATE sources SET status='approved' WHERE company_id=? AND status='pending'", (cid,))
        st.rerun()
    with top2.expander("Upload internal data (.csv, .txt, .md)"):
        f = st.file_uploader("File", type=["csv", "txt", "md"])
        if f and st.button("Add file"):
            try:
                n = crawler.add_upload(cid, f.name, f.getvalue())
                st.success("Added." if n else "Already added (duplicate content).")
            except Exception as e:
                st.error(str(e))
    with st.expander("Add a source manually"):
        cat = st.selectbox("Category", list(sources.CATEGORIES))
        kind = st.selectbox("Kind", ["news", "hn", "stackexchange", "page"])
        nm = st.text_input("Name")
        qy = st.text_input("Search query (or full URL for kind 'page')")
        site = st.selectbox("Stack Exchange site", sorted(sources.SE_SITES))
        why = st.text_input("Reason for monitoring")
        if st.button("Add source") and nm and qy and why:
            try:
                url = qy if kind == "page" else sources.build_url(kind, qy, site)
                if kind == "page":
                    research.polite_get(url)
                db.ex("INSERT INTO sources(company_id,category,name,kind,url,query,reason,status,added_at) "
                      "VALUES(?,?,?,?,?,?,?,'approved',?)", (cid, cat, nm, kind, url, qy, why, db.now()))
                st.success("Added and approved.")
            except Exception as e:
                st.error(f"Could not add: {e}")
    rows = db.q("SELECT * FROM sources WHERE company_id=? AND status!='removed' ORDER BY category, id", (cid,))
    seen, dup_ids = {}, set()
    for s in rows:
        if s["url"] in seen:
            dup_ids.add(s["id"])
        seen[s["url"]] = s["id"]
    for s in rows:
        a, b, c3, d = st.columns([5, 2, 1, 1])
        health = s["last_status"] or "never checked"
        a.markdown(f"**{s['name']}** · {s['category']} · `{s['kind']}`" + (" · ⚠ duplicate" if s["id"] in dup_ids else "")
                   + f"  \n<small>{s['reason']}</small>", unsafe_allow_html=True)
        b.caption(f"{s['status']} · {health}  \nlast check: {s['last_checked'] or '-'} · items: {s['items_found']}")
        if s["status"] != "approved" and c3.button("Approve", key=f"ap{s['id']}"):
            db.ex("UPDATE sources SET status='approved' WHERE id=?", (s["id"],))
            st.rerun()
        if d.button("Remove", key=f"rm{s['id']}"):
            db.ex("UPDATE sources SET status='removed' WHERE id=?", (s["id"],))
            st.rerun()


def page_feed():
    st.header("Intelligence feed")
    if st.button("Crawl approved sources and analyse now"):
        res = run("Crawling...", lambda: crawler.run_crawl(cid))
        if res:
            st.success(f"Checked {res['checked']} sources, {res['failed']} failed, {res['new_items']} new items.")
            an = run("Analysing new items...", lambda: analysis.analyze_pending(cid))
            if an:
                st.success(f"Analysed {an['analysed']} items.")
    rows = db.q("""SELECT i.*, s.name AS source_name, s.kind FROM items i JOIN sources s ON s.id=i.source_id
                   WHERE i.company_id=? ORDER BY COALESCE(i.published, i.discovered) DESC LIMIT 300""", (cid,))
    f1, f2, f3, f4 = st.columns(4)
    cat = f1.multiselect("Category", ["Customers", "Competitors", "Market", "Internal"])
    comp = f2.selectbox("Competitor", ["(any)"] + [x["name"] for x in db.q("SELECT name FROM competitors WHERE company_id=?", (cid,))])
    minrel = f3.slider("Min relevance (1-5)", 1, 5, 1)
    typ = f4.selectbox("Signal type", ["(any)", "opportunity", "threat", "neutral"])
    ver = st.text_input("Verification contains (e.g. primary, press, community)")
    shown = 0
    for r in rows:
        a = json.loads(r["analysis"]) if r["analysis"] else {}
        s = json.loads(r["scores"]) if r["scores"] else {}
        if cat and r["category"] not in cat:
            continue
        if comp != "(any)" and comp.lower() not in (r["title"] + r["source_name"]).lower():
            continue
        if a.get("relevance", 1) < minrel or (typ != "(any)" and a.get("signal_type") != typ):
            continue
        if ver and ver.lower() not in s.get("verification", "unanalysed").lower():
            continue
        shown += 1
        if shown > 60:
            break
        with st.expander(f"[{r['priority'] if r['priority'] is not None else '-'}] {r['category']} · {r['title']}"):
            st.markdown(f"[Open source]({r['url']}) · {r['source_name']} · published {r['published'] or 'unknown'} · discovered {r['discovered']}")
            st.markdown(f"**Evidence:** {a.get('what_happened') or r['snippet']}")
            if a:
                st.markdown(f"**Interpretation:** {a.get('why_it_matters')}")
                st.markdown(f"Type: {a.get('signal_type')} · urgency {a.get('urgency')} · impact {a.get('business_impact')} · area {a.get('business_area')}")
                if a.get("suggested_action"):
                    st.markdown(f"Suggested action: {a['suggested_action']}")
                st.caption(f"{s.get('verification')} · evidence strength {s.get('evidence_strength')} · {s.get('why_ranked')}")
            else:
                st.caption("Not analysed yet.")
    if not shown:
        st.caption("Nothing to show. Approve sources, then crawl.")


def page_brief():
    st.header("Monday brief")
    days = st.number_input("Look-back window (days)", 7, 90, 30)
    b1, b2 = st.columns(2)
    if b1.button("Generate brief now (crawl, analyse, write)"):
        ok = run("Running the full weekly job...", lambda: scheduler.weekly_brief(cid, trigger="manual"))
        last = db.q1("SELECT status, message FROM runs WHERE company_id=? ORDER BY id DESC LIMIT 1", (cid,))
        (st.success if last and last["status"] == "ok" else st.error)(last["message"] if last else "No run logged")
    if b2.button("Re-write brief from stored records (no crawl)"):
        run("Writing...", lambda: brief.generate(cid, int(days)))
    reports = db.q("SELECT id, created_at FROM reports WHERE company_id=? AND kind='brief' ORDER BY id DESC", (cid,))
    if reports:
        pick = st.selectbox("Brief history", [r["id"] for r in reports],
                            format_func=lambda i: next(r["created_at"] for r in reports if r["id"] == i))
        body = db.q1("SELECT body FROM reports WHERE id=?", (pick,))["body"]
        st.download_button("Download as Markdown", body, file_name=f"brief_{pick}.md")
        st.markdown(body)
    else:
        st.caption("No briefs yet.")


def page_settings():
    st.header("Settings")
    tz = st.text_input("Time zone", db.get_setting("timezone", "Asia/Kolkata"))
    bt = st.text_input("Monday brief time (HH:MM)", db.get_setting("brief_time", "08:00"))
    hrs = st.number_input("Crawl every N hours", 1, 168, int(db.get_setting("crawl_every_hours", "24")))
    reg = st.text_input("News region code (e.g. IN, US, GB)", db.get_setting("news_region", "IN"))
    if st.button("Save settings"):
        try:
            db.set_setting("timezone", tz)
            db.set_setting("brief_time", bt)
            db.set_setting("crawl_every_hours", hrs)
            db.set_setting("news_region", reg.upper())
            scheduler.configure()
            st.success("Saved and scheduler updated.")
        except Exception as e:
            st.error(f"Could not apply: {e}")
    st.subheader("Scheduler")
    st.json({k: str(v) for k, v in scheduler.next_runs().items()})
    st.caption("Note: on free hosting the app sleeps when idle and scheduled jobs pause. "
               "Use run_job.py with cron or Task Scheduler for always-on runs.")
    if st.button("Run monthly audit now"):
        scheduler.audit_job(cid, trigger="manual")
        st.rerun()
    audits = db.q("SELECT id, created_at FROM reports WHERE company_id=? AND kind='audit' ORDER BY id DESC", (cid,))
    if audits:
        with st.expander("Latest source-health report"):
            st.markdown(db.q1("SELECT body FROM reports WHERE id=?", (audits[0]["id"],))["body"])
    st.subheader("Run history (failures are shown in red)")
    for r in db.q("SELECT * FROM runs ORDER BY id DESC LIMIT 25"):
        line = f"#{r['id']} · {r['kind']} · company {r['company_id']} · {r['started']} · {r['status']} · {r['message'] or ''}"
        (st.error if r["status"] == "failed" else st.write)(line)


{"Company Setup": page_setup, "Profile": page_profile, "AI Interview": page_interview,
 "Signal Stack": page_stack, "Source Manager": page_sources, "Intelligence Feed": page_feed,
 "Monday Brief": page_brief, "Settings": page_settings}[page]()
