#!/usr/bin/env python3
"""Job scraper: public ATS APIs + open feeds -> data/jobs/*.jsonl (one file per board). Git is the history.

Every job is enriched from its title/description: level, category, employment type, years of experience,
skills and pay. Only those facts are stored (never the description), and empty values are left out of the files.
"""
import datetime as dt
import html
import json
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DIR = Path("data/jobs")  # one jsonl per board, e.g. greenhouse_stripe.jsonl
COMPANIES = Path("companies.json")  # {"greenhouse": {"stripe": "Stripe"}, ...}  slug -> display name
KEEP_CLOSED_DAYS = 90  # closed jobs older than this are pruned (git history still has them)

S = requests.Session()
S.headers["User-Agent"] = "roleBase-job-scraper"
S.mount("https://", HTTPAdapter(max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504])))

# ---------- understanding a posting ----------
LEVELS = [  # first match wins
    ("intern", r"\bintern(ship)?s?\b|\bco-?op\b|\bworking student\b|\bwerkstudent|\bpraktik|\bazubi\b|\bausbildung"),
    ("junior", r"\bjunior\b|\bjr\b|\bentry[- ]level\b|\bgraduate\b|\bnew grad\b|\bapprentice|\bassociate\b(?! (director|vp|vice))"),
    ("staff+", r"\bstaff\b|\bprincipal\b|\bdistinguished\b|\bfellow\b"),
    # plain "Product/Account/Program Manager" are individual-contributor roles, so only real management counts
    ("manager+", r"\bdirector\b|\bhead of\b|\bvp\b|\bvice president\b|\bchief\b|\bgeneral manager\b|\b(engineering|software|data|design|research|sales) manager\b|\bmanager of\b"),
    ("lead", r"\blead\b"),
    ("senior", r"\bsenior\b|\bsr\b"),
]
CATEGORIES = [  # first match wins; "sales engineer" must hit sales before engineering, etc.
    ("sales", r"\bsales\b|account (executive|manager)|business development|\bbdr\b|\bsdr\b|solutions? (engineer|architect|consultant)|pre-?sales|partnership|revenue|go-to-market"),
    ("design", r"design(?! engineer)|\bux\b|\bui\b|creative"),
    ("data", r"data (scien|analy|engineer)|machine learning|\bml\b|\bai\b|research scientist|applied scientist|analytics|business intelligence|\bbi\b"),
    ("product", r"product (manager|owner|lead)|program manager|\bpm\b|head of product"),
    ("security", r"security|infosec|appsec|penetration|threat"),
    ("engineering", r"engineer|developer|software|devops|\bsre\b|site reliability|back-?end|front-?end|full.?stack|mobile|\bios\b|android|platform|infrastructure|architect|programmer|\bqa\b|quality assurance|sdet|firmware|embedded|technical"),
    ("marketing", r"marketing|growth|\bseo\b|content|communications|brand|social media|community|copywrit"),
    ("support", r"support|customer (success|service|experience)|technical account|help ?desk|implementation|onboarding"),
    ("finance", r"financ|account(ing|ant)|payroll|\btax\b|treasury|audit|controller|fp&a|procurement"),
    ("legal", r"legal|counsel|attorney|compliance|paralegal|privacy"),
    ("people", r"recruit|talent|\bpeople\b|\bhr\b|human resources|workplace"),
    ("operations", r"operations|\bops\b|supply chain|logistics|business (analyst|operations)|chief of staff|strategy|facilit|admin|office|warehouse|driver|technician|mechanic"),
]
SKILLS = ("python java javascript typescript rust c++ c# ruby php swift kotlin scala sql nosql react vue angular node.js django "
          "flask spring-boot aws azure gcp kubernetes docker terraform postgres mysql mongodb redis kafka spark snowflake "
          "databricks dbt airflow pytorch tensorflow llm ml graphql linux git ci/cd figma tableau salesforce hubspot jira sap").split()
ALIAS = dict(golang="go", postgresql="postgres", nodejs="node.js", node="node.js", k8s="kubernetes", reactjs="react",
             **{"react.js": "react", "machine learning": "ml", "spring boot": "spring-boot"}, js="javascript", ts="typescript")
SKILL_RX = re.compile(r"(?<![\w+#.])(" + "|".join(sorted(map(re.escape, SKILLS + list(ALIAS)), key=len, reverse=True)) + r")(?![\w+#])", re.I)
EMP = dict(fulltime="full_time", permanent="full_time", vollzeit="full_time", parttime="part_time", teilzeit="part_time",
           contract="contract", contractor="contract", temporary="contract", freelance="contract",
           intern="internship", internship="internship", praktikum="internship")
YEARS_RX = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
PAY_RX = re.compile(r"([$£€])\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])\s*(?:-|–|—|to)\s*[$£€]?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])")
LEGAL_RX = re.compile(r"[,\s]+(?:inc|llc|ltd|limited|gmbh|ag|corp|corporation|plc|bv|pty|pvt|llp|lp|co)\.?$", re.I)


def text(h):  # HTML (possibly entity-escaped) -> plain text
    return html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(h or "")))


def level(title, years=None, hint=None):
    t = title.lower()
    for name, rx in LEVELS:
        if re.search(rx, t):
            return name
    if hint:
        return hint
    return "mid" if years is None else "junior" if years <= 1 else "mid" if years <= 4 else "senior"


def category(*texts):  # title first, then team/department
    for t in texts:
        for name, rx in CATEGORIES:
            if re.search(rx, (t or "").lower()):
                return name
    return "other"


def years_req(desc):  # first "N years ... experience" after the requirements heading; None if not stated
    m = re.search(r"requirements|qualifications|about you|what you.ll bring|must have|minimum", desc, re.I)
    seg = desc[m.start():] if m else desc
    for y in YEARS_RX.finditer(seg):
        if int(y[1]) <= 20 and "experience" in seg[max(0, y.start() - 100):y.end() + 100].lower():
            return int(y[1])


def skills(t):
    out = []
    for m in SKILL_RX.finditer(t):
        s = m[1].lower()
        s = ALIAS.get(s, s)
        if s not in out:
            out.append(s)
    return out[:8]


def employment(title, *vals):
    for v in vals:
        for x in v if isinstance(v, list) else [v]:
            e = EMP.get(re.sub(r"[\s_-]", "", str(x or "").lower()))
            if e:
                return e
    t = title.lower()
    if re.search(LEVELS[0][1], t):
        return "internship"
    return "contract" if re.search(r"\bcontract(or)?\b|\bfreelance\b|\btemporary\b|\bfixed[- ]term\b", t) \
        else "part_time" if re.search(r"\bpart[- ]time\b", t) else ""


def money(s):
    s = s.replace(",", "").replace(" ", "").lower()
    return int(s[:-1]) * 1000 if s.endswith("k") else int(s)


def find_pay(t):  # yearly pay range written in the text -> [min, max, currency]; "$" is reported as USD
    for m in PAY_RX.finditer(t):
        lo, hi = money(m[2]), money(m[3])
        if 20000 <= lo <= hi <= 2_000_000:
            return [lo, hi, {"$": "USD", "£": "GBP", "€": "EUR"}[m[1]]]


def clean_company(n):  # "Stripe, Inc." -> "Stripe"
    n = " ".join((n or "").split())
    while (m := LEGAL_RX.sub("", n)) != n and m:
        n = m
    return n


def get(url, **kw):
    r = S.get(url, timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def day(v):  # epoch ms / epoch s / ISO string -> YYYY-MM-DD
    if not v:
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        v = float(v)
        v = v / 1000 if v > 1e11 else v
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    return str(v)[:10]


def job(source, scope, rid, company, title, url, location="", remote=False, posted=None,
        desc="", team="", emp=(), pay=None, tags=(), hint=None):
    location, team, title = (location or "").strip(), (team or "").strip(), title.strip()
    yrs = years_req(desc)
    return dict(id=f"{scope}:{rid}", source=source, company=company, title=title, team=team, location=location,
                remote=bool(remote) or "remote" in location.lower(), level=level(title, yrs, hint),
                category=category(title, team), employment=employment(title, *emp), years=yrs,
                skills=skills(f"{title} {' '.join(tags)} {desc}"), pay=pay or find_pay(desc), url=url, posted=day(posted))


# ---------- per-company ATS fetchers (slug, display name -> jobs). `url` is the apply link. ----------
def greenhouse(s, name):
    return [job("greenhouse", f"greenhouse:{s}", j["id"], name or j.get("company_name") or s, j["title"], j["absolute_url"],
                (j.get("location") or {}).get("name"), posted=j.get("first_published") or j.get("updated_at"),
                desc=text(j.get("content")), team=next((d["name"] for d in j.get("departments") or []), ""))
            for j in get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs", params={"content": "true"})["jobs"]]


def lever(s, name):
    out = []
    for j in get(f"https://api.lever.co/v0/postings/{s}", params={"mode": "json"}):
        c, r = j["categories"], j.get("salaryRange") or {}
        pay = [int(r["min"]), int(r["max"]), r.get("currency", "USD")] if r.get("min") and r.get("max") and "year" in r.get("interval", "") else None
        desc = " ".join([j.get("descriptionPlain") or "", j.get("additionalPlain") or ""] + [text(l.get("content")) for l in j.get("lists") or []])
        out.append(job("lever", f"lever:{s}", j["id"], name, j["text"], j["applyUrl"], c.get("location"),
                       j.get("workplaceType") == "remote", j.get("createdAt"), desc=desc,
                       team=c.get("team") or c.get("department"), emp=[c.get("commitment")], pay=pay))
    return out


def ashby(s, name):
    out = []
    for j in get(f"https://api.ashbyhq.com/posting-api/job-board/{s}", params={"includeCompensation": "true"})["jobs"]:
        if not j.get("isListed", True):
            continue
        comp = next((c for t in (j.get("compensation") or {}).get("compensationTiers") or [] for c in t.get("components") or []
                     if c.get("compensationType") == "Salary" and "YEAR" in (c.get("interval") or "")), None)
        pay = [int(comp["minValue"]), int(comp["maxValue"]), comp["currencyCode"]] if comp and comp.get("minValue") and comp.get("maxValue") else None
        out.append(job("ashby", f"ashby:{s}", j["id"], name, j["title"], j.get("applyUrl") or j["jobUrl"], j.get("location"),
                       j.get("isRemote") or j.get("workplaceType") == "Remote", j.get("publishedAt"),
                       desc=j.get("descriptionPlain") or "", team=j.get("department") or j.get("team"),
                       emp=[j.get("employmentType")], pay=pay))
    return out


SR_LEVEL = dict(internship="intern", entry_level="junior", director="manager+", executive="manager+")


def smartrecruiters(s, name):  # list endpoint has no description, so years/skills/pay stay empty here
    out, off = [], 0
    while True:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings", params={"limit": 100, "offset": off})
        for j in d["content"]:
            l = j.get("location") or {}
            loc = l.get("fullLocation") or ", ".join(filter(None, [l.get("city"), (l.get("country") or "").upper()]))
            out.append(job("smartrecruiters", f"smartrecruiters:{s}", j["id"], name or (j.get("company") or {}).get("name") or s,
                           j["name"], f"https://jobs.smartrecruiters.com/{s}/{j['id']}", loc, l.get("remote"), j.get("releasedDate"),
                           team=(j.get("function") or {}).get("label") or (j.get("department") or {}).get("label"),
                           emp=[(j.get("typeOfEmployment") or {}).get("label")],
                           hint=SR_LEVEL.get((j.get("experienceLevel") or {}).get("id"))))
        off += 100
        if not d["content"] or off >= d["totalFound"]:
            return out


# ---------- feeds (whole-board snapshots) ----------
def remotive():
    return [job("remotive", "remotive", j["id"], clean_company(j["company_name"]), j["title"], j["url"],
                j.get("candidate_required_location"), True, j.get("publication_date"),
                desc=text(j.get("description")) + " " + (j.get("salary") or ""), team=j.get("category"),
                emp=[j.get("job_type")], tags=j.get("tags") or [])
            for j in get("https://remotive.com/api/remote-jobs")["jobs"]]


def remoteok():  # row 0 is a legal notice; their terms require linking back, so url is their page
    return [job("remoteok", "remoteok", j["id"], clean_company(j["company"]), j["position"], j["url"], j.get("location"), True,
                j.get("date"), desc=text(j.get("description")), tags=j.get("tags") or [],
                pay=[int(j["salary_min"]), int(j["salary_max"]), "USD"] if int(j.get("salary_min") or 0) and int(j.get("salary_max") or 0) else None)
            for j in get("https://remoteok.com/api")[1:]]


def arbeitnow():  # must read every page, else jobs pushed past the cap would look closed
    out = []
    for p in range(1, 101):
        time.sleep(1)  # their rate limit 429s a fast pager
        d = get("https://www.arbeitnow.com/api/job-board-api", params={"page": p})
        out += [job("arbeitnow", "arbeitnow", j["slug"], clean_company(j["company_name"]), j["title"], j["url"], j.get("location"),
                    j.get("remote"), j.get("created_at"), desc=text(j.get("description")), team=" ".join(j.get("tags") or []),
                    emp=j.get("job_types") or [], tags=j.get("tags") or []) for j in d["data"]]
        if not d["links"].get("next"):
            return out
    raise RuntimeError("arbeitnow: >100 pages")


ATS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, smartrecruiters=smartrecruiters)
FEEDS = dict(remotive=remotive, remoteok=remoteok, arbeitnow=arbeitnow)


# ---------- storage ----------
URLS = dict(greenhouse="https://job-boards.greenhouse.io/{s}/jobs/{r}", lever="https://jobs.lever.co/{s}/{r}/apply",
            ashby="https://jobs.ashbyhq.com/{s}/{r}/application", smartrecruiters="https://jobs.smartrecruiters.com/{s}/{r}")
DEFAULTS = dict(closed_at=None, location="", team="", remote=False, years=None, skills=[], pay=None, employment="", posted=None)


def url_of(id):  # apply URL is derivable for ATS jobs, so it is only stored when it differs
    src, *rest = id.split(":")
    return URLS[src].format(s=rest[0], r=":".join(rest[1:])) if src in URLS else None


def load():
    # split("\n"), not splitlines(): titles can contain U+2028 etc., which splitlines() would cut on
    rows = (l for f in DIR.glob("*.jsonl") for l in f.read_text(encoding="utf-8").split("\n") if l)
    db = {}
    for j in map(json.loads, rows):
        j.setdefault("source", j["id"].split(":")[0])
        j.setdefault("url", url_of(j["id"]))
        j.setdefault("level", level(j["title"], j.get("years")))  # derived, so a better classifier fixes old rows too
        j.setdefault("category", category(j["title"], j.get("team")))
        for k, v in DEFAULTS.items():
            j.setdefault(k, list(v) if isinstance(v, list) else v)
        db[j["id"]] = j
    return db


def slim(j):  # drop empty values and anything derivable from other fields (load() restores it)
    r = {k: v for k, v in j.items() if v is not None and v is not False and v != "" and v != []}
    r.pop("source", None)
    if r.get("url") == url_of(j["id"]):
        del r["url"]
    if r.get("level") == level(j["title"], j.get("years")):
        del r["level"]
    if r.get("category") == category(j["title"], j.get("team")):
        del r["category"]
    return r


def save(db):  # one file per board; sorted + compact + LF => small files, diffs show only real changes
    DIR.mkdir(parents=True, exist_ok=True)
    shards = {}
    for i in sorted(db):
        name = i.rsplit(":", 1)[0].replace(":", "_")
        shards.setdefault(name, []).append(json.dumps(slim(db[i]), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    for f in DIR.glob("*.jsonl"):
        if f.stem not in shards:
            f.unlink()
    for name, lines in shards.items():
        with open(DIR / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")


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


def main():
    cfg = json.loads(COMPANIES.read_text(encoding="utf-8"))
    tasks = [(f"{k}:{s}", lambda k=k, s=s, n=n: ATS[k](s, n)) for k, boards in cfg.items() for s, n in boards.items()] + list(FEEDS.items())

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
    norm = lambda j: re.sub(r"\W", "", clean_company(j["company"]) + j["title"]).lower()
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

    known = {t[0] for t in tasks}  # boards removed from companies.json: close their jobs instead of leaving them open forever
    for scope in open_n:
        if scope not in known:
            fetched[scope] = []

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
