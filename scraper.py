#!/usr/bin/env python3
"""Job scraper: public ATS APIs + open feeds -> data/jobs/*.jsonl (one file per board). Git is the history.

Every job is enriched from its title/description: level, category, employment type, years of experience,
skills and pay. Only those facts are stored (never the description), and empty values are left out of the files.
"""
import csv
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
S.mount("https://", HTTPAdapter(pool_maxsize=64, max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                                                                    allowed_methods=None)))  # None = retry POST too (Workday searches are read-only)
TIMEOUT = (10, 60)  # connect, read

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
    ("healthcare", r"nurs(e|ing)|physician|clinic(al|ian)|pharmac|medical|therapist|patient|dental|dentist|surgeon|radiolog|caregiver|health ?care|\brn\b|paramedic|phlebotom|\bcna\b|veterinar"),
    ("education", r"teacher|professor|instructor|lecturer|tutor|faculty|curriculum|academic|educator|school"),
    ("science", r"scientist|laborator|chemist|biolog|\blab\b|research (associate|assistant|fellow)|geolog"),
    ("retail", r"\bstore\b|retail|cashier|barista|\bcook\b|\bchef\b|kitchen|bartender|hotel|guest (service|experience)|concierge|housekeep|front desk|merchandis|stylist|restaurant|\bcrew\b|\bshift\b"),
    ("manufacturing", r"manufactur|production (operator|associate|supervisor|technician|worker)|assembl|machinist|weld|electrician|plumb|forklift|fabricat|\bplant\b|\bmill\b|maintenance"),
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


EVERGREEN_RX = re.compile(r"talent (community|network|pool|pipeline)|general application|future (opportunit|role)|evergreen|always hiring"
                          r"|expression of interest|join our (talent|network)", re.I)
HIDDEN_RX = re.compile(r"confidential|stealth|undisclosed|anonymous|our client|staffing|recruit(er|ing|ment)|talent (solutions|partners)", re.I)


def ghost_flags(jobs, today=None):
    """id -> [(reason, points)] for open jobs. Heuristics for *possible* ghost jobs (no real vacancy, resume farming), never proof.
    2+ points = possibly ghost, 3+ = likely. Needs no stored data except `reposts`, which builds up as the scraper keeps running."""
    today = today or dt.date.today()
    same = {}
    for j in jobs:
        k = (j["company"], re.sub(r"\W", "", j["title"].lower()))
        same[k] = same.get(k, 0) + 1
    out = {}
    for j in jobs:
        r, age = [], (today - dt.date.fromisoformat(j["posted"] or j["first_seen"])).days
        if age >= 365:
            r.append(("open for over a year", 2))
        elif age >= 180:
            r.append(("open for over 6 months", 1))
        if EVERGREEN_RX.search(j["title"]):
            r.append(("talent-pool / expression-of-interest wording", 3))
        if HIDDEN_RX.search(j["company"]):
            r.append(("agency or hidden employer", 1))
        n = same[(j["company"], re.sub(r"\W", "", j["title"].lower()))]
        if n >= 8:
            r.append((f"{n} identical postings", 1))
        if j.get("reposts"):
            r.append((f"reposted {j['reposts']}x", 1 if j["reposts"] < 3 else 2))
        out[j["id"]] = r
    return out


def fresher_ok(j):
    """Open to 0-2 years of experience: the posting states <= 2 years, or is an intern/junior role that states nothing.
    A "junior" role that asks for 3+ years is NOT fresher-friendly, and a senior/lead title never is."""
    y = j["years"]
    return j["level"] in ("intern", "junior", "mid") and (y <= 2 if y is not None else j["level"] != "mid")


def company_stats(jobs, min_roles=5):
    """Per company: open roles, how many are fresher-friendly, 'junior' roles that ask 3+ years, and how often years are stated at all."""
    by = {}
    for j in jobs:
        by.setdefault(j["company"], []).append(j)
    out = [dict(company=c, open_roles=len(js), fresher_roles=sum(map(fresher_ok, js)),
                fresher_share=round(sum(map(fresher_ok, js)) / len(js), 3),
                junior_asking_3plus=sum(j["level"] in ("intern", "junior") and (j["years"] or 0) >= 3 for j in js),
                years_stated_share=round(sum(j["years"] is not None for j in js) / len(js), 2))
           for c, js in by.items() if len(js) >= min_roles]
    return sorted(out, key=lambda r: (-r["fresher_share"], -r["open_roles"]))


def get(url, **kw):
    r = S.get(url, timeout=TIMEOUT, **kw)
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


def wd_posted(t):  # "Posted 3 Days Ago" / "Posted Today" / "Posted 30+ Days Ago" -> YYYY-MM-DD
    m = re.search(r"\d+", t or "")
    n = int(m.group()) if m else 1 if "yesterday" in (t or "").lower() else 0
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


WD_CAP = 150  # newest N per tenant: keeps jobs.json small, and the API refuses offsets past 2000 anyway. ponytail: raise if the site copes


def pmap(fn, xs, workers=4):  # a few pages at a time: fast, but polite to one host. Own pool, so no deadlock inside main()'s pool
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, xs))


def workday(s, name):  # s = "<tenant>.<pod>.myworkdayjobs.com/<site>"; the list has no description, so years/skills/pay stay empty
    host, site = s.split("/", 1)

    def page(off):
        r = S.post(f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs", json={"limit": 20, "offset": off, "searchText": ""}, timeout=TIMEOUT)
        r.raise_for_status()
        return r.json()

    first = page(0)
    rest = pmap(page, range(20, min(first["total"], WD_CAP), 20))
    return list({j["externalPath"]: job("workday", f"workday:{s}", j["externalPath"], name, j["title"], f"https://{s}{j['externalPath']}",
                                        j.get("locationsText"), posted=wd_posted(j.get("postedOn")))
                 for d in [first, *rest] for j in d["jobPostings"]}.values())


EF_CAP = 300  # newest N per Eightfold/Amazon site (Starbucks alone lists 20k+ store jobs)


def pcsx(host, domain, source, scope, name, cap=None):  # Eightfold "PCSX" search API behind a careers site; their robots.txt allows /api/pcsx
    def page(off):
        return get(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": "", "start": off})["data"]

    first = page(0)
    size = len(first["positions"])
    rest = pmap(page, range(size, min(first["count"], cap or first["count"]), size), 6) if size else []
    return list({j["id"]: job(source, scope, j["id"], name, j["name"], f"https://{host}/careers/job/{j['id']}",
                              "; ".join(j["standardizedLocations"] or j["locations"] or []), j.get("workLocationOption") == "remote",
                              j.get("postedTs"), team=j.get("department"))
                 for d in [first, *rest] for j in d["positions"]}.values())


def microsoft(s, name):  # s = domain
    return pcsx("apply.careers.microsoft.com", s, "microsoft", f"microsoft:{s}", name)


def eightfold(s, name):  # s = "<careers host>/<domain>", e.g. "apply.starbucks.com/starbucks.com"; the apply URL is stored per job
    host, domain = s.split("/", 1)
    return pcsx(host, domain, "eightfold", f"eightfold:{s}", name, EF_CAP)


def amazon(s, name):  # amazon.jobs' own search endpoint (robots.txt only blocks /internal); newest first
    def page(off):
        return get("https://www.amazon.jobs/en/search.json", params={"offset": off, "result_limit": 100, "sort": "recent"})["jobs"]

    def posted(t):
        try:
            return dt.datetime.strptime(" ".join(t.split()), "%B %d, %Y").date().isoformat()
        except (ValueError, AttributeError):
            return None

    return list({j["id_icims"]: job("amazon", f"amazon:{s}", j["id_icims"], name, j["title"], "https://www.amazon.jobs" + j["job_path"],
                                    j.get("normalized_location") or j.get("location"), False, posted(j.get("posted_date")),
                                    desc=text(" ".join(filter(None, [j.get("description"), j.get("basic_qualifications"), j.get("preferred_qualifications")]))),
                                    team=j.get("job_family") or j.get("job_category"), emp=[j.get("job_schedule_type")],
                                    hint="intern" if j.get("is_intern") else None)
                 for p in pmap(page, range(0, EF_CAP, 100), 3) for j in p}.values())


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


ATS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, smartrecruiters=smartrecruiters, workday=workday, microsoft=microsoft, eightfold=eightfold, amazon=amazon)
FEEDS = dict(remotive=remotive, remoteok=remoteok, arbeitnow=arbeitnow)


# ---------- storage ----------
URLS = dict(greenhouse="https://job-boards.greenhouse.io/{s}/jobs/{r}", lever="https://jobs.lever.co/{s}/{r}/apply",
            ashby="https://jobs.ashbyhq.com/{s}/{r}/application", smartrecruiters="https://jobs.smartrecruiters.com/{s}/{r}",
            workday="https://{s}{r}", microsoft="https://apply.careers.microsoft.com/careers/job/{r}")
DEFAULTS = dict(closed_at=None, location="", team="", remote=False, years=None, skills=[], pay=None, employment="", posted=None, reposts=0)


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
    if not r.get("reposts"):
        r.pop("reposts", None)
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
        name = i.rsplit(":", 1)[0].replace(":", "_").replace("/", "_")
        shards.setdefault(name, []).append(json.dumps(slim(db[i]), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
    for f in DIR.glob("*.jsonl"):
        if f.stem not in shards:
            f.unlink()
    for name, lines in shards.items():
        with open(DIR / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")


def merge(db, fetched, today, pruned=None):
    """db: id->job. fetched: scope->jobs, ONLY scopes that fetched OK (so failures never close jobs).
    Rows that age out are appended to `pruned` if given, so the caller can archive them."""
    seen = {j["id"] for jobs in fetched.values() for j in jobs}
    tkey = lambda i, o: (i.rsplit(":", 1)[0], re.sub(r"\W", "", o["title"].lower()))
    closed = {}  # (board, title) -> how many jobs with that title already closed there: a new one is a repost
    for i, o in db.items():
        if o["closed_at"]:
            closed[tkey(i, o)] = closed.get(tkey(i, o), 0) + 1
    for jobs in fetched.values():
        for j in jobs:
            old = db.get(j["id"])
            db[j["id"]] = {**j, "first_seen": old["first_seen"] if old else today, "closed_at": None,
                           "reposts": old.get("reposts", 0) if old else closed.get(tkey(j["id"], j), 0)}
    for i, o in db.items():
        if o["closed_at"] is None and i not in seen and i.rsplit(":", 1)[0] in fetched:
            o["closed_at"] = today
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
    keep = {i: o for i, o in db.items() if not (o["closed_at"] and o["closed_at"] < cutoff)}
    if pruned is not None:
        pruned.extend(o for i, o in db.items() if i not in keep)
    return keep


def append_csv(path, header, rows):
    new = not path.exists()
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(header)
        w.writerows(rows)


def log_history(db, pruned, today):
    """Append-only history for trends the 90-day job window can't answer later (git stores only the new lines):
    pulse.csv = open/fresher job counts per board once a day; archive.csv = lifetime of every job that aged out."""
    pulse = Path("data/pulse.csv")
    if not (pulse.exists() and any(l.startswith(today + ",") for l in pulse.read_text(encoding="utf-8").split("\n"))):
        counts = {}
        for i, o in db.items():
            if o["closed_at"] is None:
                c = counts.setdefault(i.rsplit(":", 1)[0], [0, 0])
                c[0] += 1
                c[1] += fresher_ok(o)
        append_csv(pulse, ["date", "board", "open", "fresher"], [[today, b, n, f] for b, (n, f) in sorted(counts.items())])
    skills = Path("data/skills.csv")  # skill demand per day: postings that name each skill, out of postings that name any skill
    if not (skills.exists() and any(l.startswith(today + ",") for l in skills.read_text(encoding="utf-8").split(chr(10)))):
        live = [o for o in db.values() if o["closed_at"] is None]
        tagged, n = sum(bool(o["skills"]) for o in live), {}
        for o in live:
            for k in o["skills"]:
                n[k] = n.get(k, 0) + 1
        append_csv(skills, ["date", "skill", "jobs", "tagged"], [[today, k, c, tagged] for k, c in sorted(n.items())])
    if pruned:
        append_csv(Path("data/archive.csv"), ["id", "company", "level", "category", "posted", "first_seen", "closed_at"],
                   [[o["id"], o["company"], o["level"], o["category"], o["posted"] or "", o["first_seen"], o["closed_at"]] for o in pruned])


HEALTH = Path("data/health.json")  # per board: consecutive suspicious runs + last good day, so dead boards are visible
PATIENCE = 2  # a board may look broken (empty / mostly gone) this many runs in a row before we believe it and close its jobs


def verdict(jobs, err, prev, strikes):
    """None = accept this answer. Otherwise why we distrust it (and keep the board's jobs as they were).
    Errors are always distrusted; an empty or shrunken answer is believed once it repeats, so real shutdowns still close."""
    if err:
        return f"error: {err}"
    if strikes >= PATIENCE:
        return None
    if not jobs and prev >= 5:
        return "empty answer"
    if prev >= 20 and len(jobs) < .4 * prev:
        return f"dropped from {prev} to {len(jobs)}"
    return None


def main():
    t_start = time.time()
    cfg = json.loads(COMPANIES.read_text(encoding="utf-8"))
    tasks = [(f"{k}:{s}", lambda k=k, s=s, n=n: ATS[k](s, n)) for k, boards in cfg.items() for s, n in boards.items()] + list(FEEDS.items())
    db = load()
    open_n = {}
    for i, o in db.items():
        if o["closed_at"] is None:
            open_n[i.rsplit(":", 1)[0]] = open_n.get(i.rsplit(":", 1)[0], 0) + 1
    tasks.sort(key=lambda t: -open_n.get(t[0], 0))  # biggest boards first, so the pool never ends waiting on one slow giant
    lines = [l for l in HEALTH.read_text(encoding="utf-8").split(chr(10)) if l] if HEALTH.exists() else []
    health = {r.pop("b"): r for r in map(json.loads, lines)}

    def run(t):
        t0 = time.time()
        try:
            return t[0], t[1](), None, time.time() - t0
        except Exception as e:
            return t[0], None, e, time.time() - t0

    with ThreadPoolExecutor(16) as ex:
        results = list(ex.map(run, tasks))

    today = dt.date.today().isoformat()
    fetched, ats_keys, failed = {}, set(), []
    norm = lambda j: re.sub(r"\W", "", clean_company(j["company"]) + j["title"]).lower()
    new_health = {}
    for scope, jobs, err, _ in sorted(results, key=lambda r: r[0] in FEEDS):  # ATS first, so feed dupes of them get dropped
        h = health.get(scope, {})
        why = verdict(jobs, err, open_n.get(scope, 0), h.get("strikes", 0))
        if why:
            failed.append((scope, why))
            new_health[scope] = {"strikes": h.get("strikes", 0) + (not err), "last_ok": h.get("last_ok", ""), "why": why}
            continue
        new_health[scope] = {"strikes": 0, "last_ok": today}
        if scope in FEEDS:
            jobs = [j for j in jobs if norm(j) not in ats_keys]
        else:
            ats_keys.update(norm(j) for j in jobs)
        fetched[scope] = [j for j in jobs if j["title"]]  # a posting with no title is not a posting

    known = {t[0] for t in tasks}  # boards removed from companies.json: close their jobs instead of leaving them open forever
    for scope in open_n:
        if scope not in known:
            fetched[scope] = []

    for scope, why in failed:
        print(f"::warning title=scrape {scope}::{why}")  # shows up as an annotation on the Actions run
    slow = sorted(results, key=lambda r: -r[3])[:3]
    print("slowest: " + ", ".join(f"{r[0]} {r[3]:.0f}s" for r in slow))
    if not fetched:
        sys.exit("all sources failed")

    before, pruned = set(db), []
    db = merge(db, fetched, today, pruned)
    n_open = sum(o["closed_at"] is None for o in db.values())
    print(f"sources ok={len(fetched)} failed={len(failed)} | new={len(set(db) - before)} open={n_open} closed={len(db) - n_open} | {time.time() - t_start:.0f}s")
    save(db)
    log_history(db, pruned, today)
    HEALTH.write_text(chr(10).join(json.dumps({"b": k, **v}, sort_keys=True) for k, v in sorted(new_health.items())) + chr(10), encoding="utf-8", newline=chr(10))  # one board per line


if __name__ == "__main__":
    main()
