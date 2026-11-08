"""Build _site/ for GitHub Pages: index.html + one compact jobs.json of open jobs (gzipped by Pages).

jobs.json rows are arrays, newest first:
  0 title, 1 company, 2 location, 3 remote, 4 level (index into "levels"), 5 source (index into "sources"), 6 date,
  7 apply url, 8 category, 9 employment, 10 years of experience (-1 = not stated), 11 pay min, 12 pay max (0 = none),
  13 pay currency, 14 skills (comma separated), 15 team, 16 ghost-job score (2+ possibly, 3+ likely), 17 reasons (score 2+ only),
  18 fresher-friendly (1 = open to 0-2 years of experience)
"""
import datetime as dt
import json
import os
import shutil
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from scraper import (
    DIR,
    DEFAULTS,
    category,
    fresher_ok,
    ghost_flags,
    level,
    url_of,
)

t_start = time.time()
levels = ["intern", "junior", "mid", "senior", "lead", "staff+", "manager+"]  # menu order, low to high
levels_map = {l: i for i, l in enumerate(levels)}
SITE_PER_COMPANY = 35  # the page downloads every row, so it lists each employer's newest 35; data/ and the Kaggle dataset keep all of them

files = [DIR / f for f in os.listdir(DIR) if f.endswith(".jsonl")] if DIR.exists() else []


def process_board_file(fpath):
    try:
        content = fpath.read_text(encoding="utf-8")
    except Exception:
        return []

    c_id = content.count('"id":')
    c_closed = content.count('"closed_at":')
    if c_id > 0 and c_id == c_closed:
        return []

    board_jobs = []
    for line in content.split("\n"):
        line = line.strip()
        if not line or '"closed_at":' in line:
            continue
        try:
            j = json.loads(line)
        except Exception:
            continue
        if j.get("closed_at") is not None:
            continue
        board_jobs.append(j)

    board_jobs.sort(key=lambda j: str(j.get("posted") or j.get("first_seen") or ""), reverse=True)
    return board_jobs[:SITE_PER_COMPANY]


candidate_jobs = []
print(f"Streaming {len(files)} board files for site build...")
sys.stdout.flush()
with ThreadPoolExecutor(max_workers=8) as ex:
    for idx, b_jobs in enumerate(ex.map(process_board_file, files, chunksize=100)):
        candidate_jobs.extend(b_jobs)
        if (idx + 1) % 25000 == 0:
            print(f"  {idx + 1}/{len(files)} boards streamed ({time.time() - t_start:.1f}s, {len(candidate_jobs)} candidates)...")
            sys.stdout.flush()

# Select each employer's newest 35
candidate_jobs.sort(key=lambda j: str(j.get("posted") or j.get("first_seen") or ""), reverse=True)
shown, per = [], {}
for j in candidate_jobs:
    comp = j.get("company", "")
    per[comp] = per.get(comp, 0) + 1
    if per[comp] <= SITE_PER_COMPANY:
        shown.append(j)

# Hydrate only shown jobs
for j in shown:
    j.setdefault("source", j["id"].split(":")[0])
    j.setdefault("url", url_of(j["id"]))
    j["level"] = j.get("level") or level(j["title"], j.get("years"))
    j.setdefault("category", category(j["title"], j.get("team")))
    for k, v in DEFAULTS.items():
        j.setdefault(k, list(v) if isinstance(v, list) else v)

sources = sorted({j["source"] for j in shown})
sources_map = {s: i for i, s in enumerate(sources)}
flags = ghost_flags(shown)


def row(j):
    lo, hi, cur = j["pay"] or (0, 0, "")
    score = sum(p for _, p in flags.get(j["id"], []))
    lvl = levels_map.get(j.get("level"), 2)
    src = sources_map.get(j.get("source"), 0)
    return [j["title"], j["company"], j["location"], int(bool(j.get("remote"))), lvl, src,
            str(j.get("posted") or j.get("first_seen") or ""), j["url"], j["category"], j["employment"], -1 if j.get("years") is None else j["years"],
            lo, hi, cur, ",".join(j.get("skills") or []), j.get("team") or "", score, "; ".join(r for r, _ in flags.get(j["id"], [])) if score >= 2 else "",
            int(fresher_ok(j))]


rows = sorted(map(row, shown), key=lambda r: str(r[6]), reverse=True)


def fetch_rates():
    """Free, no key: ECB rates from frankfurter.dev, with open.er-api.com filling any code the ECB does not publish (e.g. KHR).
    Fetched at build time, so the page never calls a rates API itself and the site still works if both are down."""
    rates, date = {"USD": 1.0}, ""
    for url, pick in (("https://api.frankfurter.dev/v1/latest?base=USD", lambda d: (d["rates"], d["date"])),
                      ("https://open.er-api.com/v6/latest/USD", lambda d: (d["rates"], ""))):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "roleBase (github.com/chandranshB/roleBase)"})
            got, day = pick(json.load(urllib.request.urlopen(req, timeout=15)))
        except Exception as e:
            print("rates:", url, "failed:", e)
            continue
        for c, v in got.items():
            rates.setdefault(c, v)
        date = date or day
    return (rates, date or dt.date.today().isoformat()) if len(rates) > 1 else None


out = Path("_site")
out.mkdir(exist_ok=True)
shutil.copy("web/index.html", out / "index.html")  # the search and feed front end
shutil.copy("web/favicon.svg", out / "favicon.svg")
shutil.copytree("web/fonts", out / "fonts", dirs_exist_ok=True)  # Instrument Sans (SIL OFL), bundled so no outside font service is used
FEED_SIZE = 1200
updated_str = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
(out / "feed.json").write_text(json.dumps(
    {"updated": updated_str, "levels": levels, "sources": sources, "rows": rows[:FEED_SIZE], "total": len(rows)},
    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
(out / "jobs.json").write_text(json.dumps(
    {"updated": updated_str, "levels": levels, "sources": sources, "rows": rows, "total": len(rows)},
    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

# Companies stats from charts_cache
cache_path = Path("data/charts_cache.json")
cached_comp = None
total_open_count = len(rows)
if cache_path.exists():
    try:
        d = json.loads(cache_path.read_text(encoding="utf-8"))
        total_open_count = d.get("tot_open", total_open_count)
        cached_comp = d.get("company_stats")
        if not cached_comp and "companies" in d:
            comps = d["companies"]
            fresh = d.get("comp_fresh", {})
            cached_comp = [
                [c, n, fresh.get(c, 0), round(fresh.get(c, 0) / n, 3), 0, 0]
                for c, n in comps.items() if n >= 5
            ]
            cached_comp.sort(key=lambda r: (-r[3], -r[1]))
    except Exception:
        pass

companies = cached_comp or []
(out / "companies.json").write_text(json.dumps(companies, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(rows)} of {total_open_count:,} open jobs -> _site/jobs.json ({min(FEED_SIZE, len(rows))} -> _site/feed.json), {len(companies):,} companies -> _site/companies.json | {time.time()-t_start:.1f}s")
rates = fetch_rates()
if rates:
    (out / "rates.json").write_text(json.dumps({"date": rates[1], "rates": rates[0]}, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rates[0])} exchange rates ({rates[1]}) -> _site/rates.json")
