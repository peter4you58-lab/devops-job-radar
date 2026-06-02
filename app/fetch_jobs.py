#!/usr/bin/env python3
"""
DevOps Job Radar — fetch remote DevOps/cloud roles from free public APIs,
score each against my stack, drop anything that requires US work
authorization, and write the result to jobs.json (served by GitHub Pages).

Primary source is Jobicy (a public remote-jobs API that does NOT block CI
runners). Remotive/RemoteOK are kept as best-effort extras — they often
refuse datacenter IPs, in which case they simply skip.
"""
import sys
import json
import datetime
import requests

UA = {
    "User-Agent": "devops-job-radar (+https://github.com/peter4you58-lab/devops-job-radar)",
    "Accept": "application/json",
}

# --- TUNE THESE TO YOUR PROFILE -------------------------------------------

# Keywords that describe what YOU can do. More hits -> higher fit score.
MY_STACK = [
    "kubernetes", "eks", "terraform", "argo", "argocd", "helm",
    "github actions", "ci/cd", "docker", "aws", "prometheus", "grafana",
    "datadog", "python", "bash", "linux", "gitops", "trivy", "kyverno",
]

# A role is kept only if its title contains one of these.
GOOD_TITLE = ["devops", "sre", "site reliability", "platform",
              "cloud", "infrastructure"]

# Precise phrases only — so a genuinely remote-worldwide role is NOT removed
# just for mentioning the US in passing.
BLOCKERS = [
    "us citizen", "u.s. citizen", "must be a us citizen",
    "security clearance", "active clearance", "ts/sci",
    "authorized to work in the united states",
    "authorized to work in the us", "us work authorization",
    "must reside in the united states", "must reside in the us",
    "united states only", "us-based only", "green card required",
]

# Jobicy search tags (searches title + description). CI-reliable source.
JOBICY_TAGS = ["devops", "kubernetes", "site reliability",
               "platform engineer", "cloud engineer"]

# Remotive search terms (best-effort; often blocked from CI).
REMOTIVE_QUERIES = ["devops", "site reliability", "cloud engineer"]

# Company application boards (clean JSON, never blocked — your Tier-1 targets).
# Greenhouse token = slug in boards.greenhouse.io/<TOKEN>
GREENHOUSE_TOKENS = []          # e.g. ["gitlab"]
# Lever token = slug in jobs.lever.co/<TOKEN>
LEVER_TOKENS = []               # e.g. ["someco"]

# --------------------------------------------------------------------------


def score(text: str) -> int:
    t = text.lower()
    hits = sum(1 for k in MY_STACK if k in t)
    return round(min(hits / 6, 1.0) * 100)


def blocked(text: str) -> bool:
    t = text.lower()
    return any(b in t for b in BLOCKERS)


def add(jobs, *, title, company, url, location, text, source):
    title = (title or "").strip()
    url = (url or "").strip()
    if not url or not title:
        return
    if not any(g in title.lower() for g in GOOD_TITLE):
        return
    if blocked(f"{title} {location} {text}"):
        return
    jobs[url] = {
        "title": title,
        "company": (company or "").strip() or "—",
        "url": url,
        "location": (location or "Remote").strip() or "Remote",
        "fit": score(f"{title} {text}"),
        "source": source,
    }


def from_jobicy(jobs):
    for tag in JOBICY_TAGS:
        r = requests.get("https://jobicy.com/api/v2/remote-jobs",
                         params={"count": 50, "tag": tag},
                         headers=UA, timeout=30)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            add(jobs, title=j.get("jobTitle", ""),
                company=j.get("companyName", ""), url=j.get("url", ""),
                location=j.get("jobGeo", ""),
                text=j.get("jobDescription", ""), source="Jobicy")


def from_remotive(jobs):
    for term in REMOTIVE_QUERIES:
        r = requests.get("https://remotive.com/api/remote-jobs",
                         params={"search": term}, headers=UA, timeout=30)
        r.raise_for_status()
        for j in r.json().get("jobs", []):
            add(jobs, title=j.get("title", ""),
                company=j.get("company_name", ""), url=j.get("url", ""),
                location=j.get("candidate_required_location", ""),
                text=j.get("description", ""), source="Remotive")


def from_remoteok(jobs):
    r = requests.get("https://remoteok.com/api", headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json():
        if not isinstance(j, dict) or "position" not in j:
            continue
        add(jobs, title=j.get("position", ""), company=j.get("company", ""),
            url=j.get("url", ""), location=j.get("location", "Remote"),
            text=" ".join(j.get("tags", [])) + " " + j.get("description", ""),
            source="RemoteOK")


def from_greenhouse(jobs, token):
    r = requests.get(
        f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true",
        headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        loc = (j.get("location") or {}).get("name", "")
        add(jobs, title=j.get("title", ""), company=token.title(),
            url=j.get("absolute_url", ""), location=loc,
            text=j.get("content", ""), source=f"GH:{token}")


def from_lever(jobs, token):
    r = requests.get(f"https://api.lever.co/v0/postings/{token}?mode=json",
                     headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json():
        cats = j.get("categories", {}) or {}
        add(jobs, title=j.get("text", ""), company=token.title(),
            url=j.get("hostedUrl", ""), location=cats.get("location", ""),
            text=j.get("descriptionPlain", ""), source=f"Lever:{token}")


def run_source(jobs, name, fn, *args):
    before = len(jobs)
    try:
        fn(jobs, *args)
        print(f"[ok]   {name}: +{len(jobs) - before} kept (running total {len(jobs)})")
    except Exception as exc:                           # noqa: BLE001
        print(f"[skip] {name}: {exc}", file=sys.stderr)


def main():
    jobs = {}

    run_source(jobs, "Jobicy", from_jobicy)      # primary, CI-reliable
    run_source(jobs, "Remotive", from_remotive)  # best-effort
    run_source(jobs, "RemoteOK", from_remoteok)  # best-effort
    for token in GREENHOUSE_TOKENS:
        run_source(jobs, f"greenhouse:{token}", from_greenhouse, token)
    for token in LEVER_TOKENS:
        run_source(jobs, f"lever:{token}", from_lever, token)

    ranked = sorted(jobs.values(), key=lambda j: j["fit"], reverse=True)
    out = {
        "generated": datetime.datetime.now(datetime.timezone.utc)
        .strftime("%Y-%m-%d %H:%M UTC"),
        "count": len(ranked),
        "jobs": ranked,
    }
    with open("jobs.json", "w", encoding="utf-8") as fh:
        json.dump(out, fh, indent=2, ensure_ascii=False)
    print(f"Wrote {len(ranked)} roles to jobs.json")


if __name__ == "__main__":
    main()
