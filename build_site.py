"""Build _site/ for GitHub Pages: index.html + one compact jobs.json of open jobs (gzipped by Pages).

jobs.json rows are arrays, newest first:
  0 title, 1 company, 2 location, 3 remote, 4 level (index into "levels"), 5 source (index into "sources"), 6 date,
  7 apply url, 8 category, 9 employment, 10 years of experience (-1 = not stated), 11 pay min, 12 pay max (0 = none),
  13 pay currency, 14 skills (comma separated), 15 team, 16 ghost-job score (2+ possibly, 3+ likely), 17 reasons (score 2+ only),
  18 fresher-friendly (1 = open to 0-2 years of experience)
"""
import datetime as dt
import json
import shutil
import urllib.request
from pathlib import Path

from scraper import company_stats, fresher_ok, ghost_flags, load

levels = ["intern", "junior", "mid", "senior", "lead", "staff+", "manager+"]  # menu order, low to high
jobs = [j for j in load().values() if j["closed_at"] is None]
sources = sorted({j["source"] for j in jobs})
flags = ghost_flags(jobs)


def row(j):
    lo, hi, cur = j["pay"] or (0, 0, "")
    score = sum(p for _, p in flags[j["id"]])
    return [j["title"], j["company"], j["location"], int(j["remote"]), levels.index(j["level"]), sources.index(j["source"]),
            j["posted"] or j["first_seen"], j["url"], j["category"], j["employment"], -1 if j["years"] is None else j["years"],
            lo, hi, cur, ",".join(j["skills"]), j["team"], score, "; ".join(r for r, _ in flags[j["id"]]) if score >= 2 else "",
            int(fresher_ok(j))]


rows = sorted(map(row, jobs), key=lambda r: r[6], reverse=True)
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
(out / "jobs.json").write_text(json.dumps(
    {"updated": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"), "levels": levels, "sources": sources, "rows": rows},
    ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
companies = [[c["company"], c["open_roles"], c["fresher_roles"], c["fresher_share"], c["junior_asking_3plus"], c["years_stated_share"]]
             for c in company_stats(jobs)]  # companies.json rows: name, open roles, fresher roles, share, junior asking 3+ yrs, years-stated share
(out / "companies.json").write_text(json.dumps(companies, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
print(f"{len(rows)} open jobs -> _site/jobs.json, {len(companies)} companies -> _site/companies.json")
rates = fetch_rates()
if rates:
    (out / "rates.json").write_text(json.dumps({"date": rates[1], "rates": rates[0]}, separators=(",", ":")), encoding="utf-8")
    print(f"{len(rates[0])} exchange rates ({rates[1]}) -> _site/rates.json")
