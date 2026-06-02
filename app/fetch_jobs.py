#!/usr/bin/env python3
"""
DevOps Job Radar — fetch remote DevOps/cloud roles from free public APIs,
score each against my stack, drop anything that requires US work
authorization, and write the result to jobs.json (served by GitHub Pages).

No paid services. Runs anywhere Python + requests is available.
"""
import json
import sys
import datetime
import requests

UA = {"User-Agent": "devops-job-radar (+https://github.com/peter4you58-lab/devops-job-radar)"}

# --- TUNE THESE TO YOUR PROFILE -------------------------------------------

# Keywords that describe what YOU can do. More hits -> higher fit score.
MY_STACK = [
    "kubernetes", "eks", "terraform", "argo", "argocd", "helm",
    "github actions", "ci/cd", "docker", "aws", "prometheus", "grafana",
    "datadog", "python", "bash", "linux", "gitops", "trivy", "kyverno",
]

# Title must contain one of these, or the role is skipped.
GOOD_TITLE = ["devops", "sre", "site reliability", "platform",
              "cloud", "infrastructure"]

# If any of these appear, the role is dropped (US-authorization walls etc.).
BLOCKERS = [
    "us only", "u.s. only", "united states only", "us-based only",
    "authorized to work in the us", "authorized to work in the united states",
    "us citizen", "u.s. citizen", "security clearance", "must reside in the us",
    "must reside in the united states", "onsite", "on-site only",
]

# Company application boards to watch directly (clean JSON, never rot).
# Find a Greenhouse token in a careers URL: boards.greenhouse.io/<TOKEN>
GREENHOUSE_TOKENS = []          # e.g. ["gitlab"]
# Find a Lever token in a careers URL: jobs.lever.co/<TOKEN>
LEVER_TOKENS = []               # e.g. ["someco"]

# --------------------------------------------------------------------------


def score(text: str) -> int:
    t = text.lower()
    hits = sum(1 for k in MY_STACK if k in t)
    # 6 strong matches == a perfect fit; cap at 100.
    return round(min(hits / 6, 1.0) * 100)


def blocked(text: str) -> bool:
    t = text.lower()
    return any(b in t for b in BLOCKERS)


def add(jobs: dict, *, title, company, url, location, text, source):
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


def from_remotive(jobs):
    r = requests.get("https://remotive.com/api/remote-jobs?category=devops",
                     headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json().get("jobs", []):
        add(jobs, title=j.get("title", ""), company=j.get("company_name", ""),
            url=j.get("url", ""),
            location=j.get("candidate_required_location", ""),
            text=j.get("description", ""), source="Remotive")


def from_remoteok(jobs):
    r = requests.get("https://remoteok.com/api", headers=UA, timeout=30)
    r.raise_for_status()
    for j in r.json():
        if not isinstance(j, dict) or "position" not in j:
            continue  # first element is a legal/metadata object
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


def main():
    jobs: dict = {}

    base = [("Remotive", from_remotive), ("RemoteOK", from_remoteok)]
    for name, fn in base:
        try:
            fn(jobs)
            print(f"[ok]   {name}")
        except Exception as exc:                       # noqa: BLE001
            print(f"[skip] {name}: {exc}", file=sys.stderr)

    for token in GREENHOUSE_TOKENS:
        try:
            from_greenhouse(jobs, token)
            print(f"[ok]   greenhouse:{token}")
        except Exception as exc:                       # noqa: BLE001
            print(f"[skip] greenhouse:{token}: {exc}", file=sys.stderr)

    for token in LEVER_TOKENS:
        try:
            from_lever(jobs, token)
            print(f"[ok]   lever:{token}")
        except Exception as exc:                       # noqa: BLE001
            print(f"[skip] lever:{token}: {exc}", file=sys.stderr)

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
