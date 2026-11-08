<p align="center">
  <img src="docs/logo.svg" alt="RoleBase" height="56">
</p>

<h1 align="center">RoleBase</h1>

<p align="center">
  <strong>Automated, distributed open job index and labor-market dataset across 50+ enterprise ATS platforms.</strong>
</p>

<p align="center">
  <a href="https://github.com/chandranshB/roleBase/actions/workflows/ci.yml"><img src="https://github.com/chandranshB/roleBase/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/chandranshB/roleBase/actions/workflows/scrape.yml"><img src="https://github.com/chandranshB/roleBase/actions/workflows/scrape.yml/badge.svg" alt="Scrape & Deploy"></a>
  <a href="https://github.com/chandranshB/roleBase/actions/workflows/kaggle.yml"><img src="https://github.com/chandranshB/roleBase/actions/workflows/kaggle.yml/badge.svg" alt="Kaggle Export"></a>
  <a href="https://www.kaggle.com/datasets/chandranshbinjola/job-listings"><img src="https://img.shields.io/badge/Kaggle-Dataset-20BEFF?logo=kaggle&logoColor=white" alt="Kaggle"></a>
  <a href="https://chandranshb.github.io/roleBase/"><img src="https://img.shields.io/badge/Web-Live%20Search-success" alt="Live Site"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/License-AGPL--3.0-blue.svg" alt="License: AGPL-3.0"></a>
</p>

---

## Overview

RoleBase continuously aggregates, enriches, and archives open job vacancies directly from over **110,000 employer career portals** across **51 applicant tracking systems** (Workday, Greenhouse, Lever, Ashby, SmartRecruiters, ADP, UKG Pro, Dayforce, Paylocity, Oracle HCM, Eightfold, and more).

Every posting is parsed and normalized with seniority level, professional field, required years of experience, yearly salary ranges, and technical/domain skills. Historical job closures are tracked to detect stale listings and ghost jobs.

* 🌐 **Live Web Search**: [chandranshb.github.io/roleBase](https://chandranshb.github.io/roleBase/)
* 📊 **Kaggle Relational Dataset**: [kaggle.com/datasets/chandranshbinjola/job-listings](https://www.kaggle.com/datasets/chandranshbinjola/job-listings)
* 📓 **Starter Analysis Notebook**: [Job Listings Explorer](https://www.kaggle.com/code/chandranshbinjola/job-listings-explorer)
* 🤖 **AI Agent & Developer Guide**: [AGENTS.md](AGENTS.md) · [ARCHITECTURE.md](docs/ARCHITECTURE.md)

---

## Key Features

* **10M+ Active Listings**: Deep index refreshed every 6 hours across 16 parallel runner shards; not restricted to tech (covers healthcare, manufacturing, finance, retail, logistics, education, and legal).
* **Early-Career ("Fresher-Friendly") Filter**: Isolates roles genuinely accessible to candidates with 0–2 years of experience; automatically flags and filters out "junior" titles quietly asking for 3+ years.
* **Ghost Job Risk Scoring**: Transparent heuristic audit flagging perpetual talent pools, undisclosed staffing agencies, serial reposts, and listings open >6–12 months.
* **Longitudinal Labor Market Metrics**: Daily snapshots tracking global open job volume, early-career hiring share, and daily demand shares across 80+ technical and non-technical skills.
* **100% Serverless & Open**: Fully automated on GitHub Actions; persists state in Git, publishes static indices to GitHub Pages, and updates Kaggle daily.

---

## Market Snapshot

<table>
  <tr>
    <td><img src="docs/charts/jobs_by_level.svg?v=20261104_10m" alt="Open jobs by seniority level"></td>
    <td><img src="docs/charts/jobs_by_field.svg?v=20261104_10m" alt="Number of open jobs by field"></td>
  </tr>
  <tr>
    <td><img src="docs/charts/top_skills.svg?v=20261104_10m" alt="Most requested skills in job postings"></td>
    <td><img src="docs/charts/top_companies.svg?v=20261104_10m" alt="Employers with the most open listings"></td>
  </tr>
  <tr>
    <td><img src="docs/charts/fresher_friendly.svg?v=20261104_10m" alt="Companies with the most entry-level roles"></td>
    <td><img src="docs/charts/open_jobs_over_time.svg?v=20261104_10m" alt="Open jobs and fresher-friendly jobs over time"></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/charts/skill_trends.svg?v=20261104_10m" alt="Skill demand trend over time" width="100%"></td>
  </tr>
</table>

---

## Data Schemas

### 1. Kaggle Relational CSVs (`kaggle/`)

| File | Description | Key Fields |
| :--- | :--- | :--- |
| **`jobs.csv`** | Primary job database (open & closed <90 days) | `url`, `company`, `title`, `level`, `category`, `years_experience`, `skills`, `pay_min`, `pay_max`, `pay_currency`, `remote`, `ghost_score`, `first_seen`, `closed_at` |
| **`jobs_by_company.csv`** | Early-career hiring index by employer (5+ roles) | `company`, `open_roles`, `fresher_roles`, `fresher_share`, `junior_asking_3plus`, `years_stated_share` |
| **`open_jobs_daily.csv`** | Daily active and fresher job counts | `date`, `open_jobs`, `fresher_friendly_jobs` |
| **`skill_demand_daily.csv`** | Skill frequency time series | `date`, `skill`, `jobs`, `tagged`, `share_pct` |

### 2. Internal JSONL Store (`data/jobs/*.jsonl`)

One compact, sorted JSONL file per employer board. Empty and derivable values are omitted to minimize storage and diff size:

```json
{
  "id": "greenhouse:stripe:12345",
  "company": "Stripe",
  "title": "Software Engineer, Infrastructure",
  "location": "Seattle, WA",
  "country": "United States",
  "remote": true,
  "level": "senior",
  "category": "engineering",
  "employment": "full_time",
  "years": 5,
  "skills": ["python", "aws", "kubernetes"],
  "pay": [180000, 240000, "USD"],
  "posted": "2026-11-01",
  "first_seen": "2026-11-01",
  "closed_at": null,
  "reposts": 0
}
```

---

## Architecture

```mermaid
flowchart LR
    A["50+ ATS Platforms<br/>(Workday, Greenhouse, Lever, etc.)"] --> B["16 Parallel Shards<br/>(scrape.yml matrix 0..15)"]
    B --> C["Consolidation Engine<br/>(scraper.py --consolidate)"]
    C --> D[("data/jobs/*.jsonl<br/>data/pulse.csv<br/>data/skills.csv")]
    D --> E["Site & Pages Generator<br/>(build_site.py, build_pages.py)"]
    D --> F["Dataset Exporter<br/>(export_kaggle.py)"]
    E --> G["GitHub Pages<br/>chandranshb.github.io/roleBase"]
    F --> H["Kaggle Datasets & Notebooks"]
```

* **Crawl Pipeline**: 16 parallel runner shards partition the 110,000 employer boards with per-provider rate limiting and lane concurrency (`LANES`).
* **Consolidation**: Downloads shard artifacts, prunes jobs closed >90 days into `data/archive.csv`, and precomputes hiring analytics into `data/charts_cache.json`.
* **Zero-RAM Bottleneck**: Processing loops stream files using chunked iterators to operate strictly within standard GitHub Actions runner memory limits (<7 GB).

---

## Local Development

### Prerequisites
* Python 3.10+
* Git

### Setup
```bash
git clone https://github.com/chandranshB/roleBase.git
cd roleBase
pip install -r requirements-dev.txt
```

### Running Tests & Linting
```bash
# Run unit test suite
python -m unittest discover tests -v

# Run legacy integration checks
python test_scraper.py

# Run linter
ruff check rolebase tests
```

### Crawl & Site Build Commands
```bash
# Run single-shard crawl test
python scraper.py --shard 0 --total-shards 16

# Consolidate artifacts & precalculate analytics
python scraper.py --consolidate

# Generate static search indices (_site/)
python build_site.py

# Fetch company icons (cached in logo-cache/)
python build_logos.py

# Generate static SEO HTML pages
python build_pages.py

# Export Kaggle relational CSVs (kaggle/)
python export_kaggle.py
```

---

## Adding an Employer Board

1. Find the employer's ATS identifier:
   * **Greenhouse / Lever / Ashby / SmartRecruiters**: board slug from career page URL (e.g. `boards.greenhouse.io/<slug>`).
   * **Workday**: `<tenant>.<pod>.myworkdayjobs.com/<site>` (e.g. `adobe.wd5.myworkdayjobs.com/external_experienced`).
2. Add `"slug": "Company Name"` under the respective ATS key in [`companies.json`](companies.json).
3. *(Optional)* Add the company's domain to [`domains.json`](domains.json) for high-resolution logo resolution.

---

## Principles & Ethics

* **Official First-Party Sources Only**: Postings are fetched exclusively from public company ATS feeds and open APIs. Aggregators that restrict scraping (LinkedIn, Indeed, Naukri) are omitted on purpose.
* **No Wall Gardens**: Every search result links directly back to the employer's official job portal.
* **Polite Crawling**: Per-provider concurrency limits (`LANES`), exponential backoff on HTTP 429/5xx, and descriptive User-Agent headers.

---

## License

This project is licensed under the [GNU Affero General Public License v3.0](LICENSE).
Company names, logos, and trademarks belong to their respective employers and are used strictly for identification purposes.
