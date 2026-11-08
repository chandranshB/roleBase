"""Build the kaggle/ dataset folder (one CSV + metadata) from data/jobs/*.jsonl."""
import csv
import datetime as dt
import json
import os
import shutil
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scraper import (
    DIR,
    DEFAULTS,
    clean_company,
    category,
    country,
    fresher_ok,
    level,
    url_of,
    EVERGREEN_RX,
    HIDDEN_RX,
    _clean_title,
)

# These boards' terms are about attribution and display, so they stay out of a republished dataset.
SKIP = {"remotive", "remoteok", "himalayas", "jobicy", "weworkremotely", "getonbrd", "workingnomads", "cryptojobs", "themuse"}
COLUMNS = {  # name -> (type, description); also the CSV column order
    "source": ("string", "Job board or applicant tracking system (ATS) feed where the posting was detected across 51 enterprise ATS platforms (e.g. workday, greenhouse, lever, ashby, smartrecruiters, adp, ultipro, dayforce, paylocity, paycom, applicantpro, isolved, betterteam, gohire, freshteam, gem, recooty, csod, taleo, bullhorn, successfactors, avature, recruiterflow, manatal, loxo, careerplug, comeet, crelate, clearcompany, pageup, join, personio, bamboohr, rippling, oracle, jazzhr, jobvite, jobscore, zoho, icims, or public feeds)."),
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
    "country": ("string", "Country of the position or employment jurisdiction (e.g. 'United States', 'United Kingdom', 'Germany', 'India', 'Canada')."),
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

today_date = dt.date.today()
today = today_date.isoformat()


def board_ghost_flags(jobs):
    same = Counter((j.get("company", ""), _clean_title(j.get("title"))) for j in jobs)
    out = {}
    for j in jobs:
        c = j.get("company", "")
        t = _clean_title(j.get("title"))
        r = []
        posted = j.get("posted") or j.get("first_seen")
        if posted:
            try:
                age = (today_date - dt.date.fromisoformat(posted)).days
                if age >= 365:
                    r.append(("open for over a year", 2))
                elif age >= 180:
                    r.append(("open for over 6 months", 1))
            except Exception:
                pass
        if EVERGREEN_RX.search(j.get("title", "")):
            r.append(("talent-pool / expression-of-interest wording", 3))
        if HIDDEN_RX.search(c):
            r.append(("agency or hidden employer", 1))
        n = same.get((c, t), 0)
        if n >= 8:
            r.append((f"{n} identical postings", 1))
        if j.get("reposts"):
            r.append((f"reposted {j['reposts']}x", 1 if j["reposts"] < 3 else 2))
        out[j["id"]] = r
    return out


def flat(j, flags):
    lo, hi, cur = j.get("pay") or ("", "", "")
    f = flags.get(j.get("id"))
    c = j.get("country") or country(j.get("location", ""))
    y = j.get("years")
    if y is not None:
        try:
            if isinstance(y, (int, float)):
                import math
                y = "" if math.isnan(y) else int(y)
            elif str(y).isdigit():
                y = int(y)
            else:
                y = ""
        except Exception:
            y = ""
    else:
        y = ""
    return {**j, "country": c, "years_experience": y, "skills": ";".join(j.get("skills") or []),
            "pay_min": lo, "pay_max": hi, "pay_currency": cur,
            "ghost_score": "" if f is None else sum(p for _, p in f), "ghost_reasons": "; ".join(r for r, _ in f or []),
            "fresher_friendly": fresher_ok(j)}


def collapse(rows):
    """Identical postings (same company, title, team, location, open/closed) become one row with `openings`, so bulk-posted roles don't pad the file."""
    g = {}
    for r in sorted(rows, key=lambda r: (str(r.get("first_seen") or ""), str(r.get("url") or ""))):
        k = (str(r.get("company") or ""), str(r.get("title") or "").lower().strip(), str(r.get("team") or "").lower(), str(r.get("location") or "").lower().strip(), bool(r.get("closed_at")))
        if k in g:
            g[k]["openings"] += 1
        else:
            g[k] = {**r, "openings": 1}
    return sorted(g.values(), key=lambda r: (str(r.get("company") or ""), str(r.get("title") or ""), str(r.get("url") or "")))


def process_board(fpath):
    src = fpath.stem.split("_")[0]
    if src in SKIP:
        return None
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception:
        return None

    board_jobs = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        j.setdefault("source", src)
        j.setdefault("url", url_of(j.get("id", "")))
        j.setdefault("level", level(j.get("title", ""), j.get("years")))
        j.setdefault("category", category(j.get("title", ""), j.get("team")))
        for k, v in DEFAULTS.items():
            j.setdefault(k, list(v) if isinstance(v, list) else v)
        board_jobs.append(j)

    if not board_jobs:
        return None

    flags = board_ghost_flags([j for j in board_jobs if j.get("closed_at") is None])
    b_rows = collapse(map(lambda j: flat(j, flags), board_jobs))
    return b_rows


def main():
    out = Path("kaggle")
    out.mkdir(exist_ok=True)
    shutil.copy("kaggle_assets/dataset-cover-image.png", out)  # picked up by `kaggle datasets metadata --update`

    files = [DIR / f for f in os.listdir(DIR) if f.endswith(".jsonl")] if DIR.exists() else []

    total_rows = 0
    live_count = 0
    live_companies = set()
    fields_counter = Counter()

    BATCH_SIZE = 2000
    t_start = time.time()

    with open(out / "jobs.csv", "w", encoding="utf-8", newline="") as f_jobs:
        w = csv.DictWriter(f_jobs, list(COLUMNS), extrasaction="ignore")
        w.writeheader()

        with ThreadPoolExecutor(max_workers=8) as ex:
            for i in range(0, len(files), BATCH_SIZE):
                batch = files[i:i + BATCH_SIZE]
                for b_rows in ex.map(process_board, batch, chunksize=50):
                    if not b_rows:
                        continue
                    w.writerows(b_rows)
                    total_rows += len(b_rows)

                    for r in b_rows:
                        if not r.get("closed_at"):
                            live_count += 1
                            c = r.get("company")
                            if c:
                                live_companies.add(c)
                            cat = r.get("category")
                            if cat and cat != "other":
                                fields_counter[cat] += 1
                f_jobs.flush()
                elapsed = time.time() - t_start
                done_files = min(i + BATCH_SIZE, len(files))
                speed = done_files / elapsed if elapsed > 0 else 0
                eta = (len(files) - done_files) / speed if speed > 0 else 0
                print(f"[{elapsed:6.1f}s] Exported {done_files:,}/{len(files):,} boards ({total_rows:,} rows, {speed:.1f} b/s, ETA {eta/60:.1f}m)...", flush=True)

    # Company stats
    cached_comp = None
    cache_path = Path("data/charts_cache.json")
    if cache_path.exists():
        try:
            cached_comp = json.loads(cache_path.read_text(encoding="utf-8")).get("company_stats")
        except Exception:
            pass

    if cached_comp:
        company_rows = [
            {"company": r[0], "open_roles": r[1], "fresher_roles": r[2], "fresher_share": r[3], "junior_asking_3plus": r[4], "years_stated_share": r[5]}
            for r in cached_comp
        ]
    else:
        company_rows = []

    with open(out / "jobs_by_company.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, list(COMPANY_COLUMNS))
        w.writeheader()
        w.writerows(company_rows)

    # two small time series, so the dataset answers "how is it changing" and not only "what is open now"
    daily = {}
    pulse = Path("data/pulse.csv")
    if pulse.exists():
        with open(pulse, encoding="utf-8") as f_pulse:
            for r in csv.DictReader(f_pulse):
                try:
                    d = daily.setdefault(r["date"], [0, 0])
                    d[0] += int(r.get("open") or 0)
                    d[1] += int(r.get("fresher") or 0)
                except Exception:
                    pass
    with open(out / "open_jobs_daily.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(DAILY_COLUMNS))
        w.writerows([d, *v] for d, v in sorted(daily.items()))

    skills = Path("data/skills.csv")
    with open(out / "skill_demand_daily.csv", "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(list(SKILL_COLUMNS))
        if skills.exists():
            with open(skills, encoding="utf-8") as f_skills:
                for r in csv.DictReader(f_skills):
                    try:
                        tagged = int(r.get("tagged") or 0)
                        jobs_count = int(r.get("jobs") or 0)
                        if tagged > 0:
                            w.writerow([r.get("date", ""), r.get("skill", ""), jobs_count, tagged, round(100 * jobs_count / tagged, 2)])
                    except Exception:
                        pass

    for name, (_, cols) in FILES.items():  # Kaggle's docs: the field list must cover every CSV column, in order, or descriptions "will not be matched up correctly": fail here, before anything is published
        with open(out / name, encoding="utf-8", newline="") as f:
            assert next(csv.reader(f)) == list(cols), f"{name}: CSV header and column descriptions differ"
        assert all(t and d.strip() for t, d in cols.values()), f"{name}: a column has no type or description"

    field_list = ", ".join(f"{k.replace('_', ' ')} ({v:,})" for k, v in fields_counter.most_common(14))
    n_companies = len(live_companies)
    TITLE = "Job Listings: Open Jobs in Every Field (Daily)"
    SUBTITLE = "Daily-updated open jobs with apply links, level, field, skills and pay"
    assert 6 <= len(TITLE) <= 50 and len(SUBTITLE) <= 80, (len(TITLE), len(SUBTITLE))
    SOURCES = ("Public career portals, APIs and feeds across 51 enterprise ATS platforms (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, ADP, UKG Pro, Dayforce, Paylocity, Paycom, ApplicantPro, iSolved, Betterteam, GoHire, Freshteam, Gem, Recooty, CSOD, Taleo, Bullhorn, SAP SuccessFactors, Avature, Recruiterflow, Manatal, Loxo, CareerPlug, Comeet, Crelate, ClearCompany, PageUp People, Join, Personio, BambooHR, Rippling, Oracle HCM, JazzHR, Jobvite, JobScore, Zoho Recruit, iCIMS, Amazon, Apple, Microsoft, Eightfold, and more). "
               "Collected with automated tooling; no logins, no private data.")
    COLUMN_TABLE = "\n\n".join(f"**`{name}`**\n\n| Column | Type | Meaning |\n|---|---|---|\n" + "\n".join(f"| `{c}` | {t} | {d.replace('|', '/')} |" for c, (t, d) in cols.items())
                               for name, (_, cols) in FILES.items())
    DESCRIPTION = f"""# Job Listings: open jobs in every field, updated daily

{live_count:,} open job postings from {n_companies:,} employers, collected straight from their own career pages and refreshed every day. Unlike most job datasets, it is **not limited to tech**: healthcare, retail, finance and banking, education, manufacturing, logistics, sales, marketing, engineering and more are all in here, so you can compare the job market across fields, not just inside one.

Every row has the **apply link**, the level (intern to manager+), the field, the minimum years of experience when the posting states it, skills, employment type, remote flag, location and country, pay where the employer publishes a yearly range, and the dates the job was posted, first seen and (if it disappeared) closed. A transparent ghost-job score flags listings that look like they are not really being hired for.

## What's inside

| File | What it is |
|---|---|
| `jobs.csv` | One row per posting (open, or closed in the last 90 days): {total_rows:,} rows. Identical postings are merged into one row with an `openings` count. |
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
- Very large employers contribute their newest postings only: up to 2,000 each on Workday, 3,000 on Eightfold, 10,000 on Phenom. Workday and Oracle postings carry no years, skills or pay because their list endpoint has no description.
- Company coverage is a large sample, not the whole market. Companies that forbid automated collection (for example Google, Meta and Apple) are not included.
- Postings belong to their employers. Use the apply link to apply on the employer's own page.

## Links

Search the same data as a website: https://chandranshb.github.io/roleBase/ . Starter notebook: https://www.kaggle.com/code/chandranshbinjola/job-listings-explorer

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
    print(f"\nCompleted in {time.time() - t_start:.1f}s: {total_rows:,} rows -> kaggle/jobs.csv (+ jobs_by_company.csv, open_jobs_daily.csv, skill_demand_daily.csv); description {len(DESCRIPTION):,} chars", flush=True)


if __name__ == "__main__":
    main()
