#!/usr/bin/env python3
"""Job scraper: public ATS APIs + open feeds -> data/jobs.jsonl. Git history is the versioning."""
import datetime as dt
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DB = Path("data/jobs.jsonl")
COMPANIES = Path("companies.json")
KEEP_CLOSED_DAYS = 90  # closed jobs older than this are pruned (git history still has them)

S = requests.Session()
S.headers["User-Agent"] = "roleBase-job-scraper"
S.mount("https://", HTTPAdapter(max_retries=Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504])))

LEVELS = [  # first match wins
    ("intern", r"\bintern(ship)?\b|\bco-?op\b|\bworking student\b"),
    ("junior", r"\bjunior\b|\bjr\b|\bentry\b|\bgraduate\b|\bnew grad\b|\bassociate\b"),
    ("staff+", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b"),
    ("manager+", r"\bmanager\b|\bdirector\b|\bhead of\b|\bvp\b|\bchief\b"),
    ("lead", r"\blead\b"),
    ("senior", r"\bsenior\b|\bsr\b"),
]


def level(title):
    t = title.lower()
    return next((n for n, rx in LEVELS if re.search(rx, t)), "mid")


def get(url, **kw):
    r = S.get(url, timeout=30, **kw)
    r.raise_for_status()
    return r.json()


def day(v):  # epoch ms / epoch s / ISO string -> YYYY-MM-DD
    if not v:
        return None
    if isinstance(v, (int, float)):
        v = v / 1000 if v > 1e11 else v
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    return str(v)[:10]


def job(source, scope, rid, company, title, url, location="", remote=False, posted=None):
    location = (location or "").strip()
    return dict(id=f"{scope}:{rid}", source=source, company=company, title=title.strip(), location=location,
                remote=bool(remote) or "remote" in location.lower(), level=level(title), url=url, posted=day(posted))


# ---- per-company ATS fetchers (slug -> jobs). `url` is the apply link. ----
def greenhouse(s):
    return [job("greenhouse", f"greenhouse:{s}", j["id"], j.get("company_name") or s, j["title"], j["absolute_url"],
                (j.get("location") or {}).get("name"), posted=j.get("first_published") or j.get("updated_at"))
            for j in get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs")["jobs"]]


def lever(s):
    return [job("lever", f"lever:{s}", j["id"], s, j["text"], j["applyUrl"], j["categories"].get("location"),
                j.get("workplaceType") == "remote", j.get("createdAt"))
            for j in get(f"https://api.lever.co/v0/postings/{s}", params={"mode": "json"})]


def ashby(s):
    return [job("ashby", f"ashby:{s}", j["id"], s, j["title"], j.get("applyUrl") or j["jobUrl"], j.get("location"),
                j.get("isRemote"), j.get("publishedAt"))
            for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{s}")["jobs"] if j.get("isListed", True)]


def smartrecruiters(s):
    out, off = [], 0
    while True:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings", params={"limit": 100, "offset": off})
        for j in d["content"]:
            l = j.get("location") or {}
            loc = ", ".join(filter(None, [l.get("city"), (l.get("country") or "").upper()]))
            out.append(job("smartrecruiters", f"smartrecruiters:{s}", j["id"], (j.get("company") or {}).get("name") or s,
                           j["name"], f"https://jobs.smartrecruiters.com/{s}/{j['id']}", loc, l.get("remote"), j.get("releasedDate")))
        off += 100
        if not d["content"] or off >= d["totalFound"]:
            return out


# ---- feeds (whole-board snapshots) ----
def remotive():
    return [job("remotive", "remotive", j["id"], j["company_name"], j["title"], j["url"],
                j.get("candidate_required_location"), True, j.get("publication_date"))
            for j in get("https://remotive.com/api/remote-jobs")["jobs"]]


def remoteok():  # row 0 is a legal notice; their terms require linking back, so url is their page
    return [job("remoteok", "remoteok", j["id"], j["company"], j["position"], j["url"], j.get("location"), True, j.get("date"))
            for j in get("https://remoteok.com/api")[1:]]


def arbeitnow():  # must read every page, else jobs pushed past the cap would look closed
    out = []
    for p in range(1, 101):
        time.sleep(1)  # their rate limit 429s a fast pager
        d = get("https://www.arbeitnow.com/api/job-board-api", params={"page": p})
        out += [job("arbeitnow", "arbeitnow", j["slug"], j["company_name"], j["title"], j["url"], j.get("location"),
                    j.get("remote"), j.get("created_at")) for j in d["data"]]
        if not d["links"].get("next"):
            return out
    raise RuntimeError("arbeitnow: >100 pages")


ATS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, smartrecruiters=smartrecruiters)
FEEDS = dict(remotive=remotive, remoteok=remoteok, arbeitnow=arbeitnow)


def merge(db, fetched, today):
    """db: id->job. fetched: scope->jobs, ONLY scopes that fetched OK (so failures never close jobs)."""
    seen = {j["id"] for jobs in fetched.values() for j in jobs}
    for jobs in fetched.values():
        for j in jobs:
            old = db.get(j["id"])
            db[j["id"]] = {**j, "first_seen": old["first_seen"] if old else today, "closed_at": None}
    for i, o in db.items():
        if o["closed_at"] is None and i not in seen and i.rsplit(":", 1)[0] in fetched:
            o["closed_at"] = today
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
    return {i: o for i, o in db.items() if not (o["closed_at"] and o["closed_at"] < cutoff)}


def load():
    if not DB.exists():
        return {}
    # split("\n"), not splitlines(): titles can contain U+2028 etc., which splitlines() would cut on
    return {j["id"]: j for j in map(json.loads, filter(None, DB.read_text(encoding="utf-8").split("\n")))}


def save(db):  # sorted + compact + LF => git diffs only show real changes
    DB.parent.mkdir(exist_ok=True)
    with open(DB, "w", encoding="utf-8", newline="\n") as f:
        for i in sorted(db):
            f.write(json.dumps(db[i], sort_keys=True, ensure_ascii=False, separators=(",", ":")) + "\n")


def main():
    cfg = json.loads(COMPANIES.read_text(encoding="utf-8"))
    tasks = [(f"{k}:{s}", lambda k=k, s=s: ATS[k](s)) for k, slugs in cfg.items() for s in slugs] + list(FEEDS.items())

    def run(t):
        try:
            return t[0], t[1](), None
        except Exception as e:
            return t[0], None, e

    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(run, tasks))

    db = load()
    open_n = {}
    for i, o in db.items():
        if o["closed_at"] is None:
            open_n[i.rsplit(":", 1)[0]] = open_n.get(i.rsplit(":", 1)[0], 0) + 1

    fetched, ats_keys, failed = {}, set(), []
    norm = lambda j: re.sub(r"\W", "", j["company"] + j["title"]).lower()
    for scope, jobs, err in results:  # ATS results come first, so feed dupes of them get dropped
        if err:
            failed.append((scope, err))
        elif not jobs and open_n.get(scope, 0) >= 5:  # empty answer for a board that had jobs: likely a glitch
            failed.append((scope, "empty response, not closing jobs"))
        elif scope in FEEDS:
            fetched[scope] = [j for j in jobs if norm(j) not in ats_keys]
        else:
            ats_keys.update(norm(j) for j in jobs)
            fetched[scope] = jobs

    for scope, err in failed:
        print(f"FAIL {scope}: {err}", file=sys.stderr)
    if not fetched:
        sys.exit("all sources failed")

    before = set(db)
    db = merge(db, fetched, dt.date.today().isoformat())
    n_open = sum(o["closed_at"] is None for o in db.values())
    print(f"sources ok={len(fetched)} failed={len(failed)} | new={len(set(db) - before)} open={n_open} closed={len(db) - n_open}")
    save(db)


if __name__ == "__main__":
    main()
