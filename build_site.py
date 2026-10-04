"""Build _site/ for GitHub Pages: index.html + one compact jobs.json of open jobs (gzipped by Pages)."""
import datetime as dt
import json
import shutil
from pathlib import Path

from scraper import LEVELS, load

levels = [n for n, _ in LEVELS] + ["mid"]
jobs = [j for j in load().values() if j["closed_at"] is None]
sources = sorted({j["source"] for j in jobs})
rows = sorted(  # row = [title, company, location, remote, level, source, date, apply url]; newest first
    ([j["title"], j["company"], j["location"], int(j["remote"]), levels.index(j["level"]),
      sources.index(j["source"]), j["posted"] or j["first_seen"], j["url"]] for j in jobs),
    key=lambda r: r[6], reverse=True)

out = Path("_site")
out.mkdir(exist_ok=True)
shutil.copy("web/index.html", out / "index.html")
(out / "jobs.json").write_text(json.dumps(
    {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "levels": levels, "sources": sources, "rows": rows},
    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(rows)} open jobs -> _site/jobs.json")
