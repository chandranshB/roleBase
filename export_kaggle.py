"""Build the kaggle/ dataset folder (one CSV + metadata) from data/jobs/*.jsonl."""
import csv
import json
import os
from pathlib import Path

from scraper import load

# Remotive / RemoteOK terms are about attribution and display, so they stay out of a republished dataset.
SKIP = {"remotive", "remoteok"}
COLUMNS = {  # name -> (type, description); also the CSV column order
    "id": ("string", "Unique job id: <source>:<board>:<id on that board>."),
    "source": ("string", "Where the job was read from: greenhouse, lever, ashby, smartrecruiters or arbeitnow."),
    "company": ("string", "Employer name."),
    "title": ("string", "Job title as posted."),
    "team": ("string", "Department or team if the posting gives one."),
    "category": ("string", "Field inferred from the title and team: engineering, data, design, product, sales, marketing, support, finance, legal, people, operations, security or other."),
    "level": ("string", "Seniority inferred from the title (and years of experience): intern, junior, mid, senior, lead, staff+ or manager+."),
    "years_experience": ("integer", "Minimum years of experience the posting asks for. Empty if not stated."),
    "employment": ("string", "full_time, part_time, contract or internship. Empty if not stated."),
    "remote": ("boolean", "True if the posting is remote or its location says remote."),
    "location": ("string", "Location text as posted."),
    "skills": ("string", "Up to 8 technologies found in the posting, separated by semicolons."),
    "pay_min": ("integer", "Lower end of the stated yearly pay range. Empty if no pay is stated."),
    "pay_max": ("integer", "Upper end of the stated yearly pay range."),
    "pay_currency": ("string", "Currency of the pay range (a $ sign is reported as USD)."),
    "posted": ("date", "Date the job was published (YYYY-MM-DD) when the source gives one."),
    "first_seen": ("date", "Date this scraper first saw the job."),
    "closed_at": ("date", "Date the job disappeared from its board. Empty means still open."),
    "url": ("string", "Apply link."),
}


def flat(j):
    lo, hi, cur = j["pay"] or ("", "", "")
    return {**j, "years_experience": "" if j["years"] is None else j["years"], "skills": ";".join(j["skills"]),
            "pay_min": lo, "pay_max": hi, "pay_currency": cur}


rows = sorted((flat(j) for j in load().values() if j["source"] not in SKIP), key=lambda j: j["id"])
out = Path("kaggle")
out.mkdir(exist_ok=True)
with open(out / "jobs.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)

(out / "dataset-metadata.json").write_text(json.dumps({
    "title": "Job Listings (auto-updated)",
    "subtitle": "Open and recently closed jobs from company career boards, refreshed daily",
    "id": f"{os.environ.get('KAGGLE_USERNAME', 'user')}/job-listings",
    "licenses": [{"name": "other"}],
    "keywords": ["jobs and career", "employment", "business", "internet"],
    "expectedUpdateFrequency": "daily",
    "description": (
        "Job postings scraped every few hours from public company career boards, with how long each posting stays open.\n\n"
        "**Source / provenance.** Public job-board APIs of Greenhouse, Lever, Ashby and SmartRecruiters (a few dozen "
        "companies, listed in `companies.json`) plus the Arbeitnow job API. No logins and no private data. Postings belong "
        "to their employers. Code and raw history: https://github.com/chandranshB/roleBase\n\n"
        "**Updates.** This dataset is refreshed automatically once a day by GitHub Actions. A job that disappears from its "
        "board gets a `closed_at` date, and closed jobs are dropped after 90 days.\n\n"
        "**Caveats.** `level`, `category`, `employment`, `years_experience`, `skills` and pay are read from the posting text with "
        "simple rules, so expect some errors. Pay is only filled when the posting states a yearly range. Company coverage is a "
        "sample, not the whole market."),
    "resources": [{
        "path": "jobs.csv",
        "description": "One row per job posting (open, or closed within the last 90 days).",
        "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in COLUMNS.items()]},
    }],
}, indent=2), encoding="utf-8")
print(f"{len(rows)} rows -> kaggle/jobs.csv")
