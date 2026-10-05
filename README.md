<p><img src="docs/logo.svg" alt="RoleBase" height="48"></p>

# RoleBase: open job search and job-market data for every field

RoleBase is a free, open job board and job-market dataset that updates itself. It collects around 55,000 open jobs straight from about 490 employers' own career pages, in nursing, retail, finance, education, manufacturing, logistics, engineering and many other fields, and lets you filter for entry-level, remote and salary-listed roles. Postings are kept in git, tagged with level, field, skills and pay, and tracked until they disappear, so you can also see which listings look like ghost jobs. The result is published as a searchable website and a Kaggle dataset, and everything runs on GitHub Actions.

**[Search the jobs](https://chandranshb.github.io/roleBase/)** · **[Kaggle dataset](https://www.kaggle.com/datasets/chandranshbinjola/job-listings)** · **[Roadmap](../../issues)**

## Who it's for

- **People looking for work**, including students and new graduates. Turn on "fresher-friendly" to see roles that ask for two years of experience or less, or browse the companies that hire the most of them.
- **Career changers** who want to see what a field actually asks for: level, years of experience, skills and, where employers state it, pay.
- **Researchers, analysts and journalists** who need a job market dataset with history: when postings appear, how long they stay open and which companies repost the same role.

## Find jobs by field

The site is a plain search box that understands everyday phrases. A few starting points:

| Looking for | Try |
|---|---|
| Healthcare and care work | [nursing jobs](https://chandranshb.github.io/roleBase/#q=nurse&all=1) · [pharmacy and clinical roles](https://chandranshb.github.io/roleBase/#q=pharmacist&all=1) |
| Retail, food and hospitality | [store and restaurant jobs](https://chandranshb.github.io/roleBase/#q=retail&all=1) · [part-time work](https://chandranshb.github.io/roleBase/#q=part-time&all=1) |
| Banking, finance and insurance | [finance jobs](https://chandranshb.github.io/roleBase/#q=finance&all=1) · [accounting roles](https://chandranshb.github.io/roleBase/#q=accountant&all=1) |
| Manufacturing, trades and logistics | [warehouse jobs](https://chandranshb.github.io/roleBase/#q=warehouse&all=1) · [production roles](https://chandranshb.github.io/roleBase/#q=production&all=1) |
| Sales, marketing and support | [sales jobs](https://chandranshb.github.io/roleBase/#q=sales&all=1) · [customer support](https://chandranshb.github.io/roleBase/#q=customer%20support&all=1) |
| Software, data and design | [engineering jobs](https://chandranshb.github.io/roleBase/#q=engineer&all=1) · [data roles](https://chandranshb.github.io/roleBase/#q=data&all=1) |
| Work from anywhere | [remote jobs](https://chandranshb.github.io/roleBase/#q=remote&all=1) · [internships](https://chandranshb.github.io/roleBase/#q=internship&all=1) |

Queries can also include a place, a pay floor or an exclusion, for example `remote jobs for freshers in berlin`, `$100k+` or `-sales`.

## What's in it right now

<table>
  <tr>
    <td><img src="docs/charts/jobs_by_level.svg" alt="Open jobs by seniority level, from internships to management"></td>
    <td><img src="docs/charts/jobs_by_field.svg" alt="Number of open jobs by field, from healthcare and retail to engineering"></td>
  </tr>
  <tr>
    <td><img src="docs/charts/top_skills.svg" alt="Most requested skills in job postings, technical and non-technical"></td>
    <td><img src="docs/charts/top_companies.svg" alt="Employers with the most open job listings"></td>
  </tr>
  <tr>
    <td><img src="docs/charts/fresher_friendly.svg" alt="Companies with the most entry-level, fresher-friendly roles"></td>
    <td><img src="docs/charts/open_jobs_over_time.svg" alt="Open jobs and fresher-friendly jobs over time"></td>
  </tr>
  <tr>
    <td colspan="2"><img src="docs/charts/skill_trends.svg" alt="Skill demand trend over time: share of postings that mention each skill" width="100%"></td>
  </tr>
</table>

The charts are regenerated after every scrape by [`make_charts.py`](make_charts.py), which writes plain SVG with the standard library. No plotting package and no outside service is involved. The "over time" and "skill demand trend" charts start with the first day of tracking and fill in as the scraper keeps running. The trend chart shows, for the three most requested non-IT skills (Excel, customer service, project management and so on) and the three most requested IT skills, the share of skill-tagged postings that mention each one, from the daily snapshot in `data/skills.csv`.

## What it figures out for each job

| Field | How |
|---|---|
| **Company** | Display name from [`companies.json`](companies.json); feed names are cleaned ("Stripe, Inc." becomes "Stripe") |
| **Level** | Intern, junior, mid, senior, lead, staff+ or manager+, from the title, then from years of experience, then from the board's own label |
| **Field** | Engineering, data, design, product, sales, marketing, healthcare, retail, manufacturing, education, science and so on, from the title and team |
| **Years of experience** | First "N years ... experience" after the requirements heading; empty if not stated |
| **Skills** | Up to 8 technologies from a fixed keyword list |
| **Pay** | Yearly range, from the board's data or written in the posting |
| **Fresher-friendly** | Open to 0–2 years: states 2 years or less, or is an intern/junior role that states nothing. A "junior" job asking 3+ years does not count |
| **Ghost-job flag** | Points for age, talent-pool wording, agencies, many identical postings and reposts. A guess, never proof |
| **Open or closed** | A job missing from a board that fetched successfully gets a `closed_at` date |

All of this is read from posting text with simple rules, so expect mistakes. Level and field are the most reliable. Pay and years are only filled when a posting states them.

## How it works

```mermaid
flowchart LR
  A["Public job-board APIs<br/>Greenhouse, Lever, Ashby, SmartRecruiters,<br/>Workday, Microsoft careers + feeds"] --> B[scraper.py]
  B --> C[("data/jobs/*.jsonl<br/>data/pulse.csv<br/>data/archive.csv")]
  C -->|commit| G[(git history)]
  C --> D[build_site.py + build_logos.py] --> E[GitHub Pages site]
  C --> F[export_kaggle.py] --> H[Kaggle dataset]
  C --> I[make_charts.py] --> J[docs/charts/*.svg]
```

- [`scrape.yml`](.github/workflows/scrape.yml) runs every 6 hours: tests, scrape, charts, commit the changes, deploy the site.
- [`kaggle.yml`](.github/workflows/kaggle.yml) runs daily: builds `jobs.csv` and `jobs_by_company.csv` and publishes a new dataset version.
- A failed source never closes its jobs. An empty answer, or one that is suddenly under 40% of the board's previous size, is distrusted too and only believed if it repeats on a third run in a row, so real shutdowns still close their jobs. [`data/health.json`](data/health.json) lists every board with its last good day and bad-run count, and failures show up as warnings on the Actions run.
- Boards are fetched 16 at a time, biggest first; the two big paged APIs (Workday, Microsoft) fetch their pages in parallel. A full run takes about a minute.
- Workday employers can post thousands of jobs, so only the newest 150 per employer are kept (`WD_CAP` in `scraper.py`). Their list endpoint has no description, so years, skills and pay are empty for them.
- Company icons (`build_logos.py`) come from each company's own site first, then the [Simple Icons](https://simpleicons.org) library (CC0 files, exact name matches only), then Google's icon service as a build-time fallback. A company with no usable logo gets a generic sector icon from Tabler Icons (chosen by the field most of its jobs are in) rather than a guessed or wrong logo. Brand names and marks belong to their owners and are shown only to identify who posted a job.
- Jobs that closed more than 90 days ago are dropped from the live files. Their lifetime is kept in `data/archive.csv`, and open and fresher counts per board are logged daily in `data/pulse.csv`. Both are append-only, so git stores only new lines.

### Why the data stays small in git

- One file per board, sorted and compact, so a change touches only the boards that changed.
- Empty values are not stored. The apply link, level and field are rebuilt on load when they can be derived.
- Descriptions are read and thrown away; only the extracted facts are kept.

## On the website

- **Best for you** ranks search results by match, freshness and completeness, plus what you have saved, applied to or hidden.
- **Companies** ranks employers by fresher-friendly roles (smoothed so tiny boards don't win on 5 of 5), how many roles, whether years are actually stated, junior roles that ask for 3+ years, ghost-job signals, and how well their jobs match what you save.
- **Preferences** (fields, experience, job type, remote, pay, optional tech skills) and everything learned from your clicks stay in your browser's storage. Nothing is sent anywhere.

## Data

`data/jobs/<source>_<board>.jsonl` has one JSON object per line. Empty fields are left out.

| Key | Meaning |
|---|---|
| `id` | `<source>:<board>:<id on that board>` |
| `company`, `title`, `team`, `location`, `remote` | As posted |
| `level`, `category`, `employment` | Inferred (see above); `level` and `category` are only stored when they differ from what the title implies |
| `years`, `skills`, `pay` | Years of experience, skill list, `[min, max, currency]` |
| `url` | Apply link (left out when it follows the board's standard pattern) |
| `posted`, `first_seen`, `closed_at` | Dates; empty `closed_at` means still open |
| `reposts` | Earlier closed jobs with the same title on the same board |

The Kaggle dataset has the same data as flat CSV, plus `jobs_by_company.csv` (open roles and fresher share per company). To keep `jobs.csv` lean, `url` (the apply link, unique per row) is the key and there is no `id` column, and identical postings (same company, title, team and location) are merged into one row with an `openings` count.

## Run it yourself

```bash
pip install requests
python test_scraper.py     # self-checks, no network
python scraper.py          # fetch everything and update data/
python make_charts.py      # redraw docs/charts/
python build_site.py       # build _site/ for GitHub Pages
python build_logos.py      # fetch company icons into _site/logos (slow the first time, cached after)
python build_pages.py      # crawlable field and company pages, sitemap.xml (needs the two steps above)
```

**Add a company:** find its board slug (the last part of `boards.greenhouse.io/<slug>`, `jobs.lever.co/<slug>`, `jobs.ashbyhq.com/<slug>` or `jobs.smartrecruiters.com/<slug>`) and add `"slug": "Display Name"` under the right key in [`companies.json`](companies.json). For a Workday employer use `"<tenant>.<pod>.myworkdayjobs.com/<site>"` as the key (the host and first path part of its careers URL), for example `"adobe.wd5.myworkdayjobs.com/external_experienced"`. Add its website to [`domains.json`](domains.json) so it gets an icon. Dead slugs show up as warnings in the log and never close anything.

**Not covered:** companies with no public jobs API, such as Google and Apple, are not scraped. Sites that need a login or forbid scraping are out on purpose.

## Good citizen

- Only public job-board APIs and feeds are used (Microsoft's careers search API is allowed by its robots.txt). Sites that forbid scraping (LinkedIn, Indeed, Naukri and similar) are left out on purpose.
- Every listing belongs to its employer and links back to the original posting. Remotive and RemoteOK entries link to their own pages as their terms ask, and are left out of the Kaggle dataset.
- The scraper identifies itself with a user agent and backs off on rate limits.

## Questions people ask

**How do I find entry-level jobs when I have no experience?**
Switch on "Fresher-friendly" in the search. It keeps roles that state two years of experience or less, and intern or junior roles that state nothing. A "junior" job that asks for three or more years is not counted, which is the most common trap in entry-level listings. The Companies tab ranks employers by how many of their open roles qualify.

**What is a ghost job, and how are they flagged?**
A ghost job is a listing that is not really being hired for, such as an evergreen "talent pool" post or a role open for over a year. RoleBase adds points for age, talent-pool wording, recruiting agencies, many identical postings and reposts, and shows the reason. It is a guess, not proof.

**Where do the listings come from?**
Directly from employers' own application systems (Greenhouse, Lever, Ashby, SmartRecruiters, Workday and a few company career sites) and from a few open feeds. Every job links back to the original posting, so you apply on the employer's page.

**Is there a free job postings dataset I can use for analysis?**
Yes. The [Kaggle dataset](https://www.kaggle.com/datasets/chandranshbinjola/job-listings) has the same data as flat CSV, with the apply link, level, field, skills, pay where stated and open or closed dates.

## Roadmap

Things that need weeks or months of history first, such as how long postings stay open, hiring freezes, skill trends and seasonality, are tracked as [issues](../../issues?q=label%3Aneeds-history).

## Credits

Interface and sector icons are from [Tabler Icons](https://tabler.io/icons) (MIT, © Paweł Kuna). Brand marks, where used, come from [Simple Icons](https://simpleicons.org) (CC0). The Instrument Sans font is bundled under the SIL Open Font License.
