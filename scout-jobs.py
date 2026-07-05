#!/usr/bin/env python3
"""
DevOps Job Radar — scout-jobs
=============================
Pulls remote DevOps/cloud roles from several boards, runs each through the
eligibility GATE (geo / seniority / work-authorization) *before* scoring,
then writes a clean jobs.json for the static frontend.

Design: gate-then-score, config-driven (radar_config.json). The gate lives
here in the scorer (upstream), so the feed itself stops containing roles the
candidate is structurally ineligible for.

Run:
    python scout-jobs.py                 # live: fetch all sources
    python scout-jobs.py --selftest      # offline: run the gate on sample jobs
    python scout-jobs.py --only remotive # fetch a single source (debugging)

Deps: requests, feedparser  (pip install requests feedparser)
"""

import argparse
import datetime as dt
import json
import os
import re
import sys
import time

try:
    import requests
except ImportError:
    requests = None

try:
    import feedparser
except ImportError:
    feedparser = None

HERE = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(HERE, "radar_config.json")
OUT_PATH = os.path.join(HERE, "jobs.json")

UA = {"User-Agent": "devops-job-radar/2.0 (+https://github.com/peter4you58-lab/devops-job-radar)"}
TIMEOUT = 25


# --------------------------------------------------------------------------- #
# Config
# --------------------------------------------------------------------------- #
DEFAULTS = {
    "profile": {"years": 4, "base": "Nigeria"},
    "allowlist": [], "global_signals": [], "africa_signals": [],
    "block_seniority": [], "soft_seniority": [], "geo_country": [],
    "geo_region": [], "block_text": [], "stack_role": [], "stack_tech": [],
    "emea_mode": "block", "cap_fit": 99, "keep_blocked_limit": 40,
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as fh:
            cfg.update(json.load(fh))
        print(f"[cfg] loaded {CONFIG_PATH}")
    except FileNotFoundError:
        print("[cfg] radar_config.json not found — using empty defaults")
    return cfg


# --------------------------------------------------------------------------- #
# HTTP helper
# --------------------------------------------------------------------------- #
def get_json(url, params=None):
    if requests is None:
        raise RuntimeError("requests not installed")
    r = requests.get(url, params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    return r.json()


def clean(s):
    if not s:
        return ""
    s = re.sub(r"<[^>]+>", " ", str(s))          # strip HTML
    s = re.sub(r"\s+", " ", s).strip()
    return s


def norm(title, company, location, url, source, description="", tags=None):
    return {
        "title": clean(title),
        "company": clean(company) or "—",
        "location": clean(location) or "Remote",
        "url": (url or "").strip(),
        "source": source,
        "description": clean(description)[:600],
        "tags": [clean(t).lower() for t in (tags or []) if t],
    }


# --------------------------------------------------------------------------- #
# Sources — each returns a list of normalized dicts. Each is wrapped in
# try/except by run_sources() so one dead board never kills the run.
# --------------------------------------------------------------------------- #
def fetch_remotive():
    out = []
    data = get_json("https://remotive.com/api/remote-jobs",
                    {"category": "devops", "limit": 60})
    for j in data.get("jobs", []):
        out.append(norm(j.get("title"), j.get("company_name"),
                        j.get("candidate_required_location"), j.get("url"),
                        "Remotive", j.get("description"), j.get("tags")))
    return out


def fetch_remoteok():
    out = []
    data = get_json("https://remoteok.com/api")
    for j in data:
        if not isinstance(j, dict) or not j.get("position"):
            continue  # first element is the legal/notice blob
        tags = j.get("tags") or []
        blob = " ".join(str(t) for t in tags) + " " + str(j.get("position", ""))
        if not re.search(r"devops|sre|cloud|infra|kubernetes|platform|sysadmin", blob, re.I):
            continue
        out.append(norm(j.get("position"), j.get("company"),
                        j.get("location") or "Worldwide", j.get("url"),
                        "RemoteOK", j.get("description"), tags))
    return out


def fetch_wwr():
    if feedparser is None:
        print("[wwr] feedparser not installed — skipping")
        return []
    out = []
    feed = feedparser.parse(
        "https://weworkremotely.com/categories/remote-devops-sysadmin-jobs.rss")
    for e in feed.entries:
        # WWR titles look like "Company Name: Job Title"
        raw = e.get("title", "")
        company, _, title = raw.partition(":")
        region = clean(e.get("region", "")) or "Remote"
        out.append(norm(title or raw, company or "—", region,
                        e.get("link"), "WeWorkRemotely",
                        e.get("summary", ""), []))
    return out


def fetch_jobicy():
    out = []
    data = get_json("https://jobicy.com/api/v2/remote-jobs",
                    {"count": 50, "tag": "devops"})
    for j in data.get("jobs", []):
        out.append(norm(j.get("jobTitle"), j.get("companyName"),
                        j.get("jobGeo") or "Remote", j.get("url"),
                        "Jobicy", j.get("jobExcerpt"),
                        (j.get("jobIndustry") or [])))
    return out


def fetch_himalayas():
    out = []
    data = get_json("https://himalayas.app/jobs/api", {"limit": 50})
    for j in data.get("jobs", []):
        loc = j.get("locationRestrictions") or []
        location = ", ".join(loc) if loc else "Worldwide"
        url = j.get("applicationLink") or j.get("url") or ""
        title = j.get("title", "")
        if not re.search(r"devops|sre|cloud|infra|kubernetes|platform", title, re.I):
            continue
        out.append(norm(title, j.get("companyName"), location, url,
                        "Himalayas", j.get("description"),
                        j.get("categories")))
    return out


def fetch_greenhouse(slugs):
    """Company career boards on Greenhouse. Find a slug at
       https://boards.greenhouse.io/<slug> then add it to the list below.
       Unknown/renamed slugs just 404 and are skipped."""
    out = []
    for slug in slugs:
        try:
            data = get_json(
                f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs",
                {"content": "true"})
        except Exception as exc:            # noqa: BLE001
            print(f"[greenhouse:{slug}] skip ({exc})")
            continue
        for j in data.get("jobs", []):
            title = j.get("title", "")
            if not re.search(r"devops|sre|cloud|infra|kubernetes|platform|reliability",
                             title, re.I):
                continue
            loc = (j.get("location") or {}).get("name", "Remote")
            out.append(norm(title, slug.title(), loc, j.get("absolute_url"),
                            f"GH:{slug}", j.get("content"), []))
    return out


SOURCES = {
    "remotive":  fetch_remotive,
    "remoteok":  fetch_remoteok,
    "wwr":       fetch_wwr,
    "jobicy":    fetch_jobicy,
    "himalayas": fetch_himalayas,
}

# Add verified Greenhouse slugs here as you confirm them (many African fintechs
# rotate ATS providers, so verify before trusting). Empty = skipped.
GREENHOUSE_SLUGS = []


def run_sources(only=None):
    jobs = []
    names = [only] if only else list(SOURCES.keys())
    for name in names:
        fn = SOURCES.get(name)
        if not fn:
            print(f"[src] unknown source '{name}'")
            continue
        try:
            got = fn()
            print(f"[src] {name:11s} -> {len(got)} jobs")
            jobs.extend(got)
        except Exception as exc:            # noqa: BLE001
            print(f"[src] {name:11s} FAILED: {exc}")
        time.sleep(1)  # be polite
    if not only and GREENHOUSE_SLUGS:
        try:
            gh = fetch_greenhouse(GREENHOUSE_SLUGS)
            print(f"[src] greenhouse  -> {len(gh)} jobs")
            jobs.extend(gh)
        except Exception as exc:            # noqa: BLE001
            print(f"[src] greenhouse FAILED: {exc}")
    return jobs


# --------------------------------------------------------------------------- #
# The gate  (mirror of the frontend engine — keep the two in sync)
# --------------------------------------------------------------------------- #
def text_blob(job):
    parts = [job.get("title", ""), job.get("company", ""),
             job.get("location", ""), job.get("description", ""),
             " ".join(job.get("tags", []))]
    return " ".join(parts).lower()


def match_years(blob):
    m = re.search(r"(\d+)\s*\+?\s*(?:years|yrs|year)", blob)
    return int(m.group(1)) if m else None


def cap(s):
    return s[:1].upper() + s[1:] if s else s


def score_fit(blob, flags, cfg):
    s = 42
    if any(k in blob for k in cfg["stack_role"]):
        s += 16
    hits = sum(1 for k in cfg["stack_tech"] if k in blob)
    s += min(hits * 4, 16)
    if flags["allow"]:
        s += 12
    if flags["global"]:
        s += 8
    if flags["africa"]:
        s += 10
    if flags["soft"]:
        s -= 6
    if flags["tier"] == "review":
        s -= 10
    return max(28, min(cfg["cap_fit"], round(s)))


def evaluate(job, cfg):
    title = job.get("title", "").lower()
    loc = job.get("location", "").lower()
    co = job.get("company", "").lower()
    blob = text_blob(job)
    reasons = []

    allow = next((a for a in cfg["allowlist"] if a in co), None)
    is_global = any(s in blob for s in cfg["global_signals"])
    is_africa = any(s in blob for s in cfg["africa_signals"])

    # 1. legal wall
    legal = next((t for t in cfg["block_text"] if t in blob), None)
    if legal and not is_africa:
        return "blocked", 0, [f"work-authorization wall ({legal})"]

    # 2. seniority
    hard_sen = next((s for s in cfg["block_seniority"] if s in title), None)
    if hard_sen:
        yrs = match_years(blob)
        if not (yrs is not None and yrs <= cfg["profile"]["years"]):
            return "blocked", 0, [
                f"{cap(hard_sen.strip())}-level — usually 5\u201310+ yrs, "
                f"you have ~{cfg['profile']['years']}"]

    # 3. geography
    geo = geo_why = None
    country = next((g for g in cfg["geo_country"] if g in loc), None)
    region = next((g for g in cfg["geo_region"] if g in loc), None)
    if country:
        geo, geo_why = "blocked", f"{cap(country)}-only location"
    elif region:
        if region in ("emea", "apac"):
            geo = "blocked" if cfg["emea_mode"] == "block" else "review"
            geo_why = f"{region.upper()} — usually EU/work-auth, Africa rarely included"
        else:
            geo, geo_why = "blocked", f"{cap(region)}-region only"

    # 4. rescue signals
    if is_global or is_africa:
        reasons.append("explicitly open to Africa/Nigeria" if is_africa
                       else "remote — anywhere / global")
        geo = None
    elif allow:
        reasons.append(f"Africa-friendly employer ({cap(allow)})")
        if geo == "blocked":
            geo = "review"
            geo_why = f"allowlisted, but listing tags {country or region}"

    if geo == "blocked":
        return "blocked", 0, [geo_why]

    tier = "eligible"
    if geo == "review":
        tier = "review"
        reasons.append(geo_why)
    if allow and tier == "eligible" and not (is_global or is_africa):
        tier = "review"

    if not country and not region and not is_global and not is_africa and not allow:
        bare = loc.strip()
        if bare in ("", "remote"):
            tier = "review"
            reasons.append("location unspecified — confirm it's open to Africa")

    soft = next((s for s in cfg["soft_seniority"] if s in title), None)
    if soft:
        reasons.append(
            f"senior title — a stretch at ~{cfg['profile']['years']} yrs, but go for it")

    flags = {"allow": bool(allow), "global": is_global,
             "africa": is_africa, "soft": bool(soft), "tier": tier}
    score = score_fit(blob, flags, cfg)
    return tier, score, reasons or ["clear on location & seniority"]


# --------------------------------------------------------------------------- #
# Assemble output
# --------------------------------------------------------------------------- #
TIER_ORDER = {"eligible": 0, "review": 1, "blocked": 2}


def dedupe(jobs):
    seen, out = set(), []
    for j in jobs:
        key = j["url"] or (j["title"].lower() + "|" + j["company"].lower())
        if key in seen:
            continue
        seen.add(key)
        out.append(j)
    return out


def build(jobs, cfg):
    for j in jobs:
        tier, score, reasons = evaluate(j, cfg)
        j["verdict"], j["fit"], j["reasons"] = tier, score, reasons

    jobs.sort(key=lambda j: (TIER_ORDER[j["verdict"]], -j["fit"]))

    eligible = [j for j in jobs if j["verdict"] == "eligible"]
    review = [j for j in jobs if j["verdict"] == "review"]
    blocked = [j for j in jobs if j["verdict"] == "blocked"]

    kept = eligible + review + blocked[: cfg["keep_blocked_limit"]]

    return {
        "generated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(kept),
        "summary": {"scanned": len(jobs), "eligible": len(eligible),
                    "review": len(review), "blocked": len(blocked)},
        "jobs": kept,
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--selftest", action="store_true",
                    help="run the gate on built-in samples, no network")
    ap.add_argument("--only", help="fetch a single source")
    ap.add_argument("--out", default=OUT_PATH)
    args = ap.parse_args()

    cfg = load_config()

    if args.selftest:
        jobs = SAMPLE_JOBS
        print(f"[selftest] evaluating {len(jobs)} sample jobs\n")
    else:
        jobs = run_sources(args.only)

    jobs = dedupe(jobs)
    payload = build(jobs, cfg)

    with open(args.out, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, ensure_ascii=False)

    s = payload["summary"]
    print(f"\n[done] scanned {s['scanned']} | "
          f"eligible {s['eligible']} | review {s['review']} | blocked {s['blocked']}")
    print(f"[done] wrote {payload['count']} jobs -> {args.out}")

    if args.selftest:
        print("\n--- verdicts ---")
        for j in payload["jobs"]:
            print(f"  {j['verdict']:8s} {j['fit']:>3}  "
                  f"{j['title'][:44]:44s} [{j['location']}]  "
                  f":: {j['reasons'][0]}")


# --------------------------------------------------------------------------- #
# Offline samples: the 7 from the screenshot + a few that SHOULD pass
# --------------------------------------------------------------------------- #
SAMPLE_JOBS = [
    norm("Senior DevOps Engineer", "Zartis", "Europe", "u1", "Jobicy"),
    norm("Principal DevOps Engineer", "NBCUniversal", "USA", "u2", "Jobicy"),
    norm("Senior DevOps Engineer", "Experian", "Costa Rica", "u3", "Jobicy"),
    norm("Principal DevOps Engineer (Poland Remote)", "Turnitin, LLC", "Poland", "u4", "Jobicy"),
    norm("DevOps Engineer", "PSI CRO", "Latvia", "u5", "Jobicy"),
    norm("Senior Site Reliability Engineer", "Remote", "EMEA", "u6", "Jobicy"),
    norm("Senior Site Reliability Engineer", "Akamai Technologies", "Poland", "u7", "Jobicy"),
    # --- should PASS the gate ---
    norm("Site Reliability Engineer", "Moniepoint", "Nigeria (Remote)", "u8", "test",
         "Scale our distributed platform. AWS, Kubernetes, Terraform, CI/CD."),
    norm("DevOps Engineer", "Canonical", "Remote - Global", "u9", "test",
         "GitOps, Kubernetes, OpenStack. Fully remote, hires worldwide."),
    norm("DevOps Engineer", "Reliance Health", "Remote (Africa)", "u10", "test",
         "AWS infrastructure, CI/CD, multi-country healthcare platform. Terraform, EKS."),
    norm("Mid-level Platform Engineer", "SomeStartup", "Remote - Anywhere", "u11", "test",
         "Docker, Kubernetes, GitHub Actions, Terraform, AWS. Async-first team."),
    norm("Cloud Engineer", "GloboCorp", "Remote", "u12", "test",
         "US citizens only. Must be authorized to work in the US without sponsorship."),
]


if __name__ == "__main__":
    main()
