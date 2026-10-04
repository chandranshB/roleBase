"""Build the kaggle/ dataset folder (one CSV + metadata) from data/jobs/*.jsonl."""
import csv
import json
import os
import shutil
from pathlib import Path

from scraper import company_stats, fresher_ok, ghost_flags, load

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
    "years_experience": ("numeric", "Minimum years of experience the posting asks for. Empty if not stated."),
    "employment": ("string", "full_time, part_time, contract or internship. Empty if not stated."),
    "remote": ("boolean", "True if the posting is remote or its location says remote."),
    "location": ("string", "Location text as posted."),
    "skills": ("string", "Up to 8 technologies found in the posting, separated by semicolons."),
    "pay_min": ("numeric", "Lower end of the stated yearly pay range. Empty if no pay is stated."),
    "pay_max": ("numeric", "Upper end of the stated yearly pay range."),
    "pay_currency": ("string", "Currency of the pay range (a $ sign is reported as USD)."),
    "posted": ("datetime", "Date the job was published (YYYY-MM-DD) when the source gives one."),
    "first_seen": ("datetime", "Date this scraper first saw the job."),
    "closed_at": ("datetime", "Date the job disappeared from its board. Empty means still open."),
    "ghost_score": ("numeric", "Ghost-job heuristic for open jobs: 0 = nothing suspicious, 2+ = possibly a ghost job, 3+ = likely. A guess, not proof."),
    "ghost_reasons": ("string", "Why ghost_score is above zero, e.g. open for over a year, talent-pool wording, agency, reposted."),
    "fresher_friendly": ("boolean", "True if open to 0-2 years of experience: states <= 2 years, or an intern/junior role that states nothing. A 'junior' role asking 3+ years is False."),
    "url": ("string", "Apply link."),
}
COMPANY_COLUMNS = {
    "company": ("string", "Employer name (companies with 5+ open jobs only)."),
    "open_roles": ("numeric", "Open jobs at this company."),
    "fresher_roles": ("numeric", "Open jobs that are fresher-friendly (see fresher_friendly in jobs.csv)."),
    "fresher_share": ("numeric", "fresher_roles / open_roles, 0 to 1."),
    "junior_asking_3plus": ("numeric", "Intern/junior-titled jobs that ask for 3+ years of experience."),
    "years_stated_share": ("numeric", "Share of this company's jobs that state a years-of-experience requirement; low values mean the score leans on job titles."),
}


jobs = [j for j in load().values() if j["source"] not in SKIP]
flags = ghost_flags([j for j in jobs if j["closed_at"] is None])  # closed jobs get no score


def flat(j):
    lo, hi, cur = j["pay"] or ("", "", "")
    f = flags.get(j["id"])
    return {**j, "years_experience": "" if j["years"] is None else j["years"], "skills": ";".join(j["skills"]),
            "pay_min": lo, "pay_max": hi, "pay_currency": cur,
            "ghost_score": "" if f is None else sum(p for _, p in f), "ghost_reasons": "; ".join(r for r, _ in f or []),
            "fresher_friendly": fresher_ok(j)}


rows = sorted(map(flat, jobs), key=lambda j: j["id"])
out = Path("kaggle")
out.mkdir(exist_ok=True)
shutil.copy("kaggle_assets/dataset-cover-image.png", out)  # picked up by `kaggle datasets metadata --update`
with open(out / "jobs.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
with open(out / "companies.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, list(COMPANY_COLUMNS))
    w.writeheader()
    w.writerows(company_stats([j for j in jobs if j["closed_at"] is None]))

(out / "dataset-metadata.json").write_text(json.dumps({
    "title": "Job Listings (auto-updated)",
    "subtitle": "Open and recently closed jobs from company career boards, refreshed daily",
    "id": f"{os.environ.get('KAGGLE_USERNAME', 'user')}/job-listings",
    "licenses": [{"name": "other"}],
    "keywords": ["jobs and career", "employment", "business", "internet"],
    "expectedUpdateFrequency": "daily",
    "userSpecifiedSources": "Public job-board APIs of Greenhouse, Lever, Ashby and SmartRecruiters (company list: "
                            "https://github.com/chandranshB/roleBase/blob/main/companies.json) and the Arbeitnow job API. "
                            "Collected by https://github.com/chandranshB/roleBase with GitHub Actions; no logins, no private data.",
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
    }, {
        "path": "companies.csv",
        "description": "One row per company with 5+ open jobs: how many of its roles are open to 0-2 years of experience.",
        "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in COMPANY_COLUMNS.items()]},
    }],
}, indent=2), encoding="utf-8")
print(f"{len(rows)} rows -> kaggle/jobs.csv (+ companies.csv)")
