"""Build the kaggle/ dataset folder (one CSV + metadata) from data/jobs/*.jsonl."""
import csv
import datetime as dt
import json
import os
import shutil
from collections import Counter
from pathlib import Path

from scraper import company_stats, fresher_ok, ghost_flags, load

# Remotive / RemoteOK terms are about attribution and display, so they stay out of a republished dataset.
SKIP = {"remotive", "remoteok"}
COLUMNS = {  # name -> (type, description); also the CSV column order
    "source": ("string", "Where the job was read from: greenhouse, lever, ashby, smartrecruiters, workday, microsoft or arbeitnow."),
    "company": ("string", "Employer name."),
    "title": ("string", "Job title as posted."),
    "url": ("string", "Apply link: opens the posting on the employer's own careers page or job board, where you can apply. Unique per row, so it works as the key."),
    "openings": ("numeric", "How many identical postings (same company, title, team and location) this row stands for. 1 for most jobs; more for roles opened in bulk, such as store jobs."),
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
}
COMPANY_COLUMNS = {
    "company": ("string", "Employer name (companies with 5+ open jobs only)."),
    "open_roles": ("numeric", "Open jobs at this company."),
    "fresher_roles": ("numeric", "Open jobs that are fresher-friendly (see fresher_friendly in jobs.csv)."),
    "fresher_share": ("numeric", "fresher_roles / open_roles, 0 to 1."),
    "junior_asking_3plus": ("numeric", "Intern/junior-titled jobs that ask for 3+ years of experience."),
    "years_stated_share": ("numeric", "Share of this company's jobs that state a years-of-experience requirement; low values mean the score leans on job titles."),
}

DAILY_COLUMNS = {
    "date": ("datetime", "Day (YYYY-MM-DD), UTC."),
    "open_jobs": ("numeric", "Open jobs tracked that day (grows as employers are added, so compare shares, not raw counts, across long periods)."),
    "fresher_friendly_jobs": ("numeric", "Of those, how many are fresher-friendly (0-2 years of experience)."),
}
SKILL_COLUMNS = {
    "date": ("datetime", "Day (YYYY-MM-DD), UTC."),
    "skill": ("string", "Skill keyword found in postings, for example python, excel, customer_service, cpr."),
    "jobs": ("numeric", "Open postings that mention the skill."),
    "tagged": ("numeric", "Open postings that mention at least one skill (the denominator for share_pct)."),
    "share_pct": ("numeric", "100 * jobs / tagged."),
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


def collapse(rows):
    """Identical postings (same company, title, team, location, open/closed) become one row with `openings`, so bulk-posted roles don't pad the file."""
    g = {}
    for r in sorted(rows, key=lambda r: (r["first_seen"], r["url"])):
        k = (r["company"], r["title"].lower().strip(), (r["team"] or "").lower(), r["location"].lower().strip(), bool(r["closed_at"]))
        if k in g:
            g[k]["openings"] += 1
        else:
            g[k] = {**r, "openings": 1}
    return sorted(g.values(), key=lambda r: (r["company"], r["title"], r["url"]))


rows = collapse(map(flat, jobs))
live = [r for r in rows if not r["closed_at"]]
today = dt.date.today().isoformat()
out = Path("kaggle")
out.mkdir(exist_ok=True)
shutil.copy("kaggle_assets/dataset-cover-image.png", out)  # picked up by `kaggle datasets metadata --update`
with open(out / "jobs.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, list(COLUMNS), extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
company_rows = company_stats([j for j in jobs if j["closed_at"] is None])
with open(out / "jobs_by_company.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, list(COMPANY_COLUMNS))
    w.writeheader()
    w.writerows(company_rows)

# two small time series, so the dataset answers "how is it changing" and not only "what is open now"
daily = {}
pulse = Path("data/pulse.csv")
if pulse.exists():
    for r in csv.DictReader(open(pulse, encoding="utf-8")):
        d = daily.setdefault(r["date"], [0, 0])
        d[0] += int(r["open"])
        d[1] += int(r["fresher"])
with open(out / "open_jobs_daily.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(list(DAILY_COLUMNS))
    w.writerows([d, *v] for d, v in sorted(daily.items()))
skills = Path("data/skills.csv")
with open(out / "skill_demand_daily.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f)
    w.writerow(list(SKILL_COLUMNS))
    if skills.exists():
        for r in csv.DictReader(open(skills, encoding="utf-8")):
            if int(r["tagged"]):
                w.writerow([r["date"], r["skill"], r["jobs"], r["tagged"], round(100 * int(r["jobs"]) / int(r["tagged"]), 2)])

fields = Counter(r["category"] for r in live if r["category"] != "other")
field_list = ", ".join(f"{k.replace('_', ' ')} ({v:,})" for k, v in fields.most_common(14))
n_companies = len({r["company"] for r in live})
TITLE = "Job Listings: Open Jobs in Every Field (Daily)"
SUBTITLE = "Daily-updated open jobs with apply links, level, field, skills and pay"
assert 6 <= len(TITLE) <= 50 and len(SUBTITLE) <= 80, (len(TITLE), len(SUBTITLE))
SOURCES = ("Public job-board APIs of Greenhouse, Lever, Ashby, SmartRecruiters and Workday, plus the careers search APIs of Microsoft, Amazon and "
           "Eightfold-powered sites, and the Arbeitnow job API (company list: https://github.com/chandranshB/roleBase/blob/main/companies.json). "
           "Collected by https://github.com/chandranshB/roleBase with GitHub Actions; no logins, no private data.")
DESCRIPTION = f"""# Job Listings: open jobs in every field, updated daily

{len(live):,} open job postings from {n_companies:,} employers, collected straight from their own career pages and refreshed every day. Unlike most job datasets, it is **not limited to tech**: healthcare, retail, finance and banking, education, manufacturing, logistics, sales, marketing, engineering and more are all in here, so you can compare the job market across fields, not just inside one.

Every row has the **apply link**, the level (intern to manager+), the field, the minimum years of experience when the posting states it, skills, employment type, remote flag, location, pay where the employer publishes a yearly range, and the dates the job was posted, first seen and (if it disappeared) closed. A transparent ghost-job score flags listings that look like they are not really being hired for.

## What's inside

| File | What it is |
|---|---|
| `jobs.csv` | One row per posting (open, or closed in the last 90 days): {len(rows):,} rows. Identical postings are merged into one row with an `openings` count. |
| `jobs_by_company.csv` | One row per employer with 5+ open jobs ({len(company_rows):,} employers): how many of its roles are open to people with 0-2 years of experience, and how many "junior" roles quietly ask for 3+. |
| `open_jobs_daily.csv` | Open and fresher-friendly job counts per day since tracking began. |
| `skill_demand_daily.csv` | For every skill, the share of skill-tagged postings that mention it, per day. Technical and non-technical skills (Excel, customer service, project management, CPR, forklift and more). |

## Fields covered (open postings)

{field_list}.

## Ideas for analysis

- Which fields hire the most beginners? Compare `fresher_friendly` by `category`.
- Which employers inflate "junior" titles? See `junior_asking_3plus` in `jobs_by_company.csv`.
- How much do employers pay, and in which fields do they state it at all? (`pay_min`, `pay_max`, `pay_currency`)
- What do employers in a field actually ask for? Skills and years of experience by `category` and `level`.
- Where can you work remotely? `remote` by field and location.
- How long do postings stay open, and how many are reposted? `first_seen`, `closed_at`, `ghost_score`.
- Is demand for a skill rising or falling? `skill_demand_daily.csv` builds a time series as the days go by.
- Text and classification practice: predict `category` or `level` from `title`, or build a job recommender.

## How it is collected

The scraper reads each employer's public job-board API (no logins, no private data). Each posting is enriched from its own text with simple rules: level, field, employment type, years of experience, skills and pay. A job that disappears from its board gets a `closed_at` date. Descriptions are read and thrown away; only the extracted facts are published.

**Source / provenance.** {SOURCES}

## Updates

Refreshed automatically once a day by GitHub Actions; the scraper itself runs every six hours. Closed jobs are dropped from the files after 90 days. Latest build: {today}.

## Limitations

- `level`, `category`, `employment`, `years_experience`, `skills` and pay are inferred from posting text, so expect some errors. Pay is only filled when the posting states a yearly range.
- Workday, Amazon and Eightfold-based employers contribute only their newest postings (60 to 300 each), and Workday postings carry no years, skills or pay because their list endpoint has no description.
- Company coverage is a large sample, not the whole market. Companies that forbid automated collection (for example Google, Meta and Apple) are not included.
- Postings belong to their employers. Use the apply link to apply on the employer's own page.

## Links

Search the same data as a website: https://chandranshb.github.io/roleBase/ . Code, history and issues: https://github.com/chandranshB/roleBase . Starter notebook: https://www.kaggle.com/code/chandranshbinjola/job-listings-explorer

## How to cite

Binjola, C. ({today[:4]}). RoleBase Job Listings: Open Jobs in Every Field. Kaggle. https://www.kaggle.com/datasets/chandranshbinjola/job-listings
"""

(out / "dataset-metadata.json").write_text(json.dumps({
    "title": TITLE,
    "subtitle": SUBTITLE,
    "id": f"{os.environ.get('KAGGLE_USERNAME', 'user')}/job-listings",
    "licenses": [{"name": "other"}],
    "keywords": ["jobs and career", "employment", "business", "healthcare", "finance"],  # Kaggle allows only a few tags: more fails the whole version ("max category limit")
    "expectedUpdateFrequency": "daily",
    "userSpecifiedSources": SOURCES,
    "description": DESCRIPTION,
    "resources": [{
        "path": "jobs.csv",
        "description": "One row per job posting (open, or closed within the last 90 days), with the apply link, level, field, skills, pay where stated and dates. Identical postings are merged (see openings).",
        "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in COLUMNS.items()]},
    }, {
        "path": "jobs_by_company.csv",
        "description": "One row per company with 5+ open jobs: how many of its roles are open to 0-2 years of experience.",
        "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in COMPANY_COLUMNS.items()]},
    }, {
        "path": "open_jobs_daily.csv",
        "description": "Open and fresher-friendly job counts per day since tracking began (a growing time series).",
        "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in DAILY_COLUMNS.items()]},
    }, {
        "path": "skill_demand_daily.csv",
        "description": "Per day and skill: postings that mention the skill, postings that mention any skill, and the share between them.",
        "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in SKILL_COLUMNS.items()]},
    }],
}, indent=2), encoding="utf-8")
print(f"{len(rows)} rows -> kaggle/jobs.csv (+ jobs_by_company.csv, open_jobs_daily.csv, skill_demand_daily.csv)")
