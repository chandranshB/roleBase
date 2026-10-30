"""Build the kaggle/ dataset folder (one CSV + metadata) from data/jobs/*.jsonl."""
import csv
import datetime as dt
import json
import os
import shutil
from collections import Counter
from pathlib import Path

from scraper import company_stats, fresher_ok, ghost_flags, load

# These boards' terms are about attribution and display, so they stay out of a republished dataset.
SKIP = {"remotive", "remoteok", "himalayas", "jobicy", "weworkremotely", "getonbrd", "workingnomads", "cryptojobs", "themuse"}
COLUMNS = {  # name -> (type, description); also the CSV column order
    "source": ("string", "Job board or applicant tracking system (ATS) feed where the posting was detected: greenhouse, workday, lever, ashby, smartrecruiters, personio, teamtailor, recruitee, workable, phenom, pinpoint, breezy, bamboohr, rippling, join, adp, jazzhr, jobvite, trakstar_hire, amazon, microsoft, eightfold, arbeitnow, usajobs, or jobtech."),
    "company": ("string", "Employer or hiring organization name advertising the job vacancy."),
    "title": ("string", "Official job title as published on the employer's career portal."),
    "url": ("string", "Direct apply link on the employer's official job board (unique primary key per row)."),
    "openings": ("integer", "Number of identical postings merged into this row sharing the same company, title, team, and location (typically 1; higher for bulk retail, hospitality, or warehouse hiring)."),
    "team": ("string", "Internal department, division, or business unit as specified in the posting (empty if not specified)."),
    "category": ("string", "Primary professional field / industry domain inferred from title and team: engineering, data, design, product, sales, marketing, support, finance, healthcare, retail, manufacturing, logistics, legal, people, operations, education, science, security, or administrative."),
    "level": ("string", "Seniority tier inferred from title keywords and experience requirements: intern, junior, mid, senior, lead, staff+, or manager+."),
    "years_experience": ("integer", "Minimum years of professional experience explicitly required in the posting text (integer, empty if not stated)."),
    "employment": ("string", "Employment type arrangement: full_time, part_time, contract, or internship (empty if not specified)."),
    "remote": ("boolean", "Boolean flag indicating whether the position offers remote work (True) or requires in-person attendance (False)."),
    "location": ("string", "Geographic location text as published by the employer (e.g. 'New York, NY', 'London, UK; Remote', or 'Hybrid')."),
    "skills": ("string", "Up to 8 normalized technical and domain skills detected in the posting, separated by semicolons (e.g. 'python;aws;sql')."),
    "pay_min": ("number", "Lower bound of the stated annual salary or compensation in the specified currency (empty if no pay is stated)."),
    "pay_max": ("number", "Upper bound of the stated annual salary or compensation in the specified currency (empty if no pay is stated)."),
    "pay_currency": ("string", "ISO 4217 three-letter currency code for stated compensation (e.g. USD, EUR, GBP, CAD, AUD, INR)."),
    "posted": ("date", "Original publication date of the listing in ISO 8601 format (YYYY-MM-DD), when provided by the source ATS."),
    "first_seen": ("date", "Date when this listing was first indexed and verified by the automated crawler (YYYY-MM-DD)."),
    "closed_at": ("date", "Date when the job was confirmed closed or removed from the employer's career site (YYYY-MM-DD; empty indicates an actively open position)."),
    "ghost_score": ("integer", "Calculated heuristic ghost-job risk score for open listings: 0 = normal, 2 = possibly stale/ghost posting, 3+ = likely ghost posting or perpetual talent pool."),
    "ghost_reasons": ("string", "Specific heuristic audit triggers explaining why ghost_score is above zero (e.g. open over a year, talent pool phrasing, multiple reposts)."),
    "fresher_friendly": ("boolean", "Boolean flag indicating whether the role is open to early-career candidates with 0-2 years of experience (True/False)."),
}
COMPANY_COLUMNS = {
    "company": ("string", "Employer or organization name (included for all companies with 5 or more open roles)."),
    "open_roles": ("integer", "Total number of currently active, open job listings tracked for this employer."),
    "fresher_roles": ("integer", "Number of currently active open roles at this company accessible to early-career candidates (0-2 years of experience)."),
    "fresher_share": ("number", "Proportion of the employer's total open roles that are fresher-friendly (fresher_roles / open_roles, float between 0.0 and 1.0)."),
    "junior_asking_3plus": ("integer", "Number of roles titled 'junior' or 'associate' at this company that ask for 3+ years of experience."),
    "years_stated_share": ("number", "Share of job postings at this company stating an explicit experience requirement (float between 0.0 and 1.0; low values mean the score leans on title keywords)."),
}

DAILY_COLUMNS = {
    "date": ("date", "Observation date in UTC (YYYY-MM-DD)."),
    "open_jobs": ("integer", "Total active open jobs tracked globally on this date across all indexed employers (time series benchmark)."),
    "fresher_friendly_jobs": ("integer", "Total active open jobs verified as fresher-friendly (0-2 years of experience) on this date."),
}
SKILL_COLUMNS = {
    "date": ("date", "Observation date in UTC (YYYY-MM-DD)."),
    "skill": ("string", "Normalized skill keyword tracked across postings (e.g. python, excel, sql, customer_service, cpr)."),
    "jobs": ("integer", "Number of active open job postings on this date explicitly mentioning or requiring this skill."),
    "tagged": ("integer", "Total count of active open job postings on this date that mention at least one tracked skill (denominator for share_pct)."),
    "share_pct": ("number", "Percentage share of skill-tagged open jobs that demand this specific skill (100 * jobs / tagged)."),
}
FILES = {  # file -> (description, its columns): feeds the Kaggle file/column metadata, the column table in the description and the header check below
    "jobs.csv": ("One row per job posting (open, or closed within the last 90 days), with the apply link, level, field, skills, pay where stated and dates. Identical postings are merged (see openings).", COLUMNS),
    "jobs_by_company.csv": ("One row per company with 5+ open jobs: how many of its roles are open to 0-2 years of experience.", COMPANY_COLUMNS),
    "open_jobs_daily.csv": ("Open and fresher-friendly job counts per day since tracking began (a growing time series).", DAILY_COLUMNS),
    "skill_demand_daily.csv": ("Per day and skill: postings that mention the skill, postings that mention any skill, and the share between them.", SKILL_COLUMNS),
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

for name, (_, cols) in FILES.items():  # Kaggle's docs: the field list must cover every CSV column, in order, or descriptions "will not be matched up correctly": fail here, before anything is published
    with open(out / name, encoding="utf-8", newline="") as f:
        assert next(csv.reader(f)) == list(cols), f"{name}: CSV header and column descriptions differ"
    assert all(t and d.strip() for t, d in cols.values()), f"{name}: a column has no type or description"

fields = Counter(r["category"] for r in live if r["category"] != "other")
field_list = ", ".join(f"{k.replace('_', ' ')} ({v:,})" for k, v in fields.most_common(14))
n_companies = len({r["company"] for r in live})
TITLE = "Job Listings: Open Jobs in Every Field (Daily)"
SUBTITLE = "Daily-updated open jobs with apply links, level, field, skills and pay"
assert 6 <= len(TITLE) <= 50 and len(SUBTITLE) <= 80, (len(TITLE), len(SUBTITLE))
SOURCES = ("Public job-board APIs and feeds of Greenhouse, Lever, Ashby, SmartRecruiters, Workday, BambooHR, Rippling, Join.com, ADP Workforce Now, JazzHR, Jobvite, Trakstar Hire, Teamtailor, Personio, Recruitee, Workable, Pinpoint, Breezy HR, Oracle Recruiting and Phenom, plus the careers search APIs of Microsoft, Amazon and "
           "Eightfold-powered sites, the Arbeitnow job API, Sweden's open JobTech job-ad API and USAJOBS (company list: https://github.com/chandranshB/roleBase/blob/main/companies.json). "
           "Collected by https://github.com/chandranshB/roleBase with GitHub Actions; no logins, no private data.")
# Kaggle's API accepts the per-column descriptions below but its server drops them (https://github.com/Kaggle/kaggle-cli/issues/1207), so they are also
# written into the description itself, where they always show. The metadata is still sent: it starts to count as soon as Kaggle fixes that.
COLUMN_TABLE = "\n\n".join(f"**`{name}`**\n\n| Column | Type | Meaning |\n|---|---|---|\n" + "\n".join(f"| `{c}` | {t} | {d.replace('|', '/')} |" for c, (t, d) in cols.items())
                           for name, (_, cols) in FILES.items())
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

## Columns

{COLUMN_TABLE}

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
- Very large employers contribute their newest postings only: up to 2,000 each on Workday, 3,000 on Eightfold, 10,000 on Amazon and Phenom. Workday and Oracle postings carry no years, skills or pay because their list endpoint has no description.
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
    "resources": [{"path": name, "description": desc, "schema": {"fields": [{"name": n, "description": d, "type": t} for n, (t, d) in cols.items()]}}
                  for name, (desc, cols) in FILES.items()],
}, indent=2), encoding="utf-8")
print(f"{len(rows)} rows -> kaggle/jobs.csv (+ jobs_by_company.csv, open_jobs_daily.csv, skill_demand_daily.csv); description {len(DESCRIPTION):,} chars")
