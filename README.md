# Signal Stack: company intelligence and Monday Morning Brief

A small, working version of the EDXSO Assignment 1 workflow. Enter a company name and website; the app researches it, interviews the founder, builds a Signal Stack of sources, crawls them, ranks what it finds and writes a Monday Morning Brief where every claim links back to stored evidence.

Agreed scope (confirmed by EDXSO HR by email): a smaller working version that shows the core flow, with the rest documented below as future work.

## Run it locally

```bash
pip install -r requirements.txt
cp .env.example .env          # then put your own Gemini key in .env (never commit it)
streamlit run app.py
```

Get a free key at aistudio.google.com. On Streamlit Cloud, put `GEMINI_API_KEY = "..."` in the app's Secrets instead of a `.env` file.

Tests (no API key needed, the LLM and network are mocked): `python -m pytest -q`

Command-line jobs (for cron or Task Scheduler): `python run_job.py brief|crawl|audit [company_id]`

## Demo steps

1. Company Setup: enter the name and URL (MeetUniversity first, then a company in another industry).
2. Profile: check verified facts vs assumptions.
3. AI Interview: answer or skip 10 questions, then "Build intelligence profile".
4. Signal Stack: "Discover competitors and sources", then Source Manager: "Approve all pending".
5. Intelligence Feed: "Crawl approved sources and analyse now".
6. Monday Brief: "Generate brief now". Download the Markdown.
7. Stress test: edit an interview answer or the profile, then Signal Stack: "Reassess" to see what changed.

## How it works

| Step | Module | What it does |
|---|---|---|
| Research | `research.py` | Fetches the homepage and up to 4 relevant pages (robots.txt enforced, 1 request/second per host). The LLM must give an exact quote for every "verified fact"; code checks the quote exists in the page text, otherwise the fact is demoted to an assumption. |
| Interview | `interview.py` | One question at a time, generated from the profile and previous answers, skippable, editable later. |
| Discovery | `sources.py` | The LLM proposes competitors and sources with a reason each. Code builds the URLs (the LLM never supplies feed URLs), and page sources are checked for reachability and robots.txt. Nothing is hard-coded per company. |
| Monitoring | `crawler.py` | Fetches approved sources, dedupes by normalised title, detects changed pages by diffing text, records health and history. |
| Analysis | `analysis.py` | Per item: evidence (what the source says) vs interpretation (why it matters), signal type, urgency, impact, relevance. |
| Ranking | `ranking.py` | Priority = 0.30 relevance + 0.20 impact + 0.15 urgency + 0.15 source credibility + 0.10 recency + 0.10 novelty. Source reliability, evidence strength and strategic relevance are stored as separate measures, and the LLM's opinion is never treated as proof. |
| Brief | `brief.py` | The model writes the analysis using only stored items and cites them as [#id]. The Source Appendix is built by code from the database (link, dates, snippet, verification). Unknown cited ids are flagged. With no evidence, no brief text is generated. |
| Automation | `scheduler.py` | APScheduler: weekly brief (Monday, configurable time and time zone), monthly source audit, periodic crawl. Every run is logged; failures show in red under Settings. |

## What works, and what is future work

Working: company setup, quote-verified profile, adaptive 10-question interview with skip and edit, LLM-driven competitor and source discovery, source approval/removal/manual add, crawler with dedup and change detection, CSV/text internal uploads, analysis, ranking with explanations, Monday brief with appendix and Markdown download, weekly and monthly schedulers, run log, source-health audit.

Future work (not built):
- Excel and PDF uploads for internal data
- Email delivery of the brief (currently Markdown download)
- Action tracking (owners, done/not done) inside the brief
- Feed filtering by date range
- Customer-voice sources are limited: Reddit is deliberately excluded (its robots.txt disallows crawling and its API needs authentication), so customer language comes from Hacker News, Stack Exchange and news queries
- Google News RSS gives titles, publishers and dates, not full article text, so evidence snippets are thin
- Page-change monitoring on dynamic sites can be noisy
- Hosted free tiers sleep when idle, so scheduled jobs pause; use `run_job.py` with cron for always-on runs
- No authentication on the dashboard

## Cost statement

Everything is free or open source: Python, Streamlit, SQLite, APScheduler, requests, BeautifulSoup, feedparser, the Google Gemini API free tier, Google News RSS, the Hacker News Algolia API and the Stack Exchange API. No paid APIs or scraping services. Limits: the Gemini free tier has per-minute and per-day request caps that change over time (check AI Studio); the app retries with backoff on 429 errors. Stack Exchange allows limited anonymous requests per day.

## Robots.txt and access

Page crawling checks robots.txt and uses a clear user agent with a 1 request/second limit per host. Official RSS and JSON API endpoints (Google News RSS, Hacker News API, Stack Exchange API) are fetched without a robots check because they are provided for programmatic use. No logins or protections are bypassed.

## Test evidence

`test_core.py` (11 offline tests) covers dedup, health tracking, page-change detection, uploads, ranking, quote verification, source validation, brief traceability, the no-evidence case, scheduler failure logging and the audit. Add your own screenshots, the three sample briefs and the screen recording after running the demo with your key.
