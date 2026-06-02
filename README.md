# DevOps Job Radar

A self-updating dashboard that scouts remote DevOps/cloud roles every day, scores
each one against a defined skill profile, drops anything gated by US work
authorization, and publishes a ranked, filterable board — all on free
infrastructure.

**Live dashboard:** `https://peter4you58-lab.github.io/devops-job-radar/`
*(enable GitHub Pages once, see setup below)*

## What it does

- Pulls live postings from free public APIs (Remotive, RemoteOK) and, optionally,
  directly from company application boards (Greenhouse / Lever) — clean JSON
  feeds that don't rot like individual job links.
- Scores every role 0–100 by how many of my stack keywords it matches.
- Filters out roles requiring US citizenship, clearance, or US-only residency.
- Renders a searchable dashboard with a min-fit filter and an "applied" tracker
  (persisted in the browser), so it doubles as an application tracker.

## Architecture

```
GitHub Actions (daily cron)
        │  runs
        ▼
app/fetch_jobs.py  ──fetch──>  free job APIs
        │  writes
        ▼
   jobs.json  ──committed back to repo──>  GitHub Pages  ──serves──>  index.html
```

No servers, no cloud bill, no card required. Actions is free on public repos;
Pages hosts the static page for free.

## Tech

Python · GitHub Actions · GitHub Pages · vanilla HTML/CSS/JS · public REST/JSON APIs

## Setup (one time)

1. Push these files to the repo `main` branch.
2. **Settings → Pages → Build and deployment → Source: Deploy from a branch →
   `main` / `/ (root)`.** Wait ~1 min; the dashboard goes live at the URL above.
3. **Actions tab → `scout-jobs` → Run workflow** to populate `jobs.json` for the
   first time. After that it refreshes automatically every day at 06:00 UTC.

## Customize

Open `app/fetch_jobs.py`:

- `MY_STACK` — the keywords that define your fit score. Edit to match your skills.
- `GOOD_TITLE` — only roles whose title contains one of these are kept.
- `BLOCKERS` — phrases that disqualify a role (authorization walls, on-site, etc.).
- `GREENHOUSE_TOKENS` / `LEVER_TOKENS` — add company tokens to watch their boards
  directly. The token is the slug in a careers URL
  (`boards.greenhouse.io/<token>`, `jobs.lever.co/<token>`).

## Run locally

```bash
pip install -r requirements.txt
python app/fetch_jobs.py     # writes jobs.json
python -m http.server 8000   # open http://localhost:8000
```

## Notes

`jobs.json` is regenerated and committed by the workflow; no need to edit it by
hand. The applied-tracker state lives in your browser (localStorage), so it's
private to you and not committed.
