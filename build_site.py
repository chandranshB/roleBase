"""Build _site/ for GitHub Pages: index.html + one compact jobs.json of open jobs (gzipped by Pages).

jobs.json rows are arrays, newest first:
  0 title, 1 company, 2 location, 3 remote, 4 level (index into "levels"), 5 source (index into "sources"), 6 date,
  7 apply url, 8 category, 9 employment, 10 years of experience (-1 = not stated), 11 pay min, 12 pay max (0 = none),
  13 pay currency, 14 skills (comma separated), 15 team, 16 ghost-job score (2+ possibly, 3+ likely), 17 reasons (score 2+ only)
"""
import datetime as dt
import json
import shutil
from pathlib import Path

from scraper import ghost_flags, load

levels = ["intern", "junior", "mid", "senior", "lead", "staff+", "manager+"]  # menu order, low to high
jobs = [j for j in load().values() if j["closed_at"] is None]
sources = sorted({j["source"] for j in jobs})
flags = ghost_flags(jobs)


def row(j):
    lo, hi, cur = j["pay"] or (0, 0, "")
    score = sum(p for _, p in flags[j["id"]])
    return [j["title"], j["company"], j["location"], int(j["remote"]), levels.index(j["level"]), sources.index(j["source"]),
            j["posted"] or j["first_seen"], j["url"], j["category"], j["employment"], -1 if j["years"] is None else j["years"],
            lo, hi, cur, ",".join(j["skills"]), j["team"], score, "; ".join(r for r, _ in flags[j["id"]]) if score >= 2 else ""]


rows = sorted(map(row, jobs), key=lambda r: r[6], reverse=True)
out = Path("_site")
out.mkdir(exist_ok=True)
shutil.copy("web/index.html", out / "index.html")
(out / "jobs.json").write_text(json.dumps(
    {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "levels": levels, "sources": sources, "rows": rows},
    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(rows)} open jobs -> _site/jobs.json")
