#!/usr/bin/env python3
"""Job scraper: public ATS APIs + open feeds -> data/jobs/*.jsonl (one file per board). Git is the history.

Every job is enriched from its title/description: level, category, employment type, years of experience,
skills and pay. Only those facts are stored (never the description), and empty values are left out of the files.
"""
import argparse
from collections import Counter
import csv
import datetime as dt
import functools
import html
import json
import os
import re
import shutil
import sys
import time
import urllib.parse
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, wait
from email.utils import parsedate_to_datetime
from pathlib import Path

from bs4 import BeautifulSoup
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

DIR = Path("data/jobs")  # one jsonl per board, e.g. greenhouse_stripe.jsonl
COMPANIES = Path("companies.json")  # {"greenhouse": {"stripe": "Stripe"}, ...}  slug -> display name
KEEP_CLOSED_DAYS = 90  # closed jobs older than this are pruned (git history still has them)

class Client(requests.Session):
    waits = (2, 6)  # seconds before the 2nd and 3rd try

    def request(self, *a, **kw):  # urllib3's Retry stops once the response starts, so connection cuts / timeouts mid-body are retried here
        for w in (*self.waits, None):
            try:
                return super().request(*a, **kw)
            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout):
                if w is None:
                    raise
                time.sleep(w)


S = Client()
S.headers["User-Agent"] = "roleBase-job-scraper"
S.headers["Accept-Encoding"] = "gzip, deflate"
S.mount("https://", HTTPAdapter(pool_maxsize=128, max_retries=Retry(total=5, backoff_factor=2, status_forcelist=[429, 500, 502, 503, 504],
                                                                    allowed_methods=None)))  # None = retry POST too (Workday searches are read-only)
TIMEOUT = (10, 45)  # connect, read

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
    ("sales", r"\bsales\b|account (executive|manager)|business development|\bbdr\b|\bsdr\b|solutions? (engineer|architect|consultant)|pre-?sales|partnership|revenue|go-to-market|account (partner|representative|associate|specialist|director)"),
    ("design", r"design(?! engineer)|\bux\b|\bui\b|creative"),
    ("data", r"data (scien|analy|engineer)|machine learning|\bml\b|\bai\b|research scientist|applied scientist|analytics|business intelligence|\bbi\b"),
    ("product", r"product (manager|owner|lead)|program manager|\bpm\b|head of product"),
    ("protective", r"guard\b|ochron|vekter|sicherheitsdienst|sicherheitsmitarbeiter|beveilig|doorman|patrol|security (officer|agent|guard)"),
    ("security", r"security|infosec|appsec|penetration|threat"),
    ("engineering", r"engineer|developer|software|devops|\bsre\b|site reliability|back-?end|front-?end|full.?stack|mobile|\bios\b|android|platform|infrastructure|architect|programmer|\bqa\b|quality assurance|sdet|firmware|embedded|technical"),
    ("marketing", r"marketing|growth|\bseo\b|content|communications|brand|social media|community|copywrit"),
    ("support", r"support|customer (success|service|experience)|technical account|help ?desk|implementation|onboarding"),
    ("finance", r"financ|account(ing|ant)|payroll|\btax\b|treasury|audit|controller|fp&a|procurement|banker|teller|underwrit|\bloan|insuranc|adjuster|actuar|fraud|kredit|steuer|\bbank|risk|claims|credit|invest|portfolio|wealth"),
    ("legal", r"legal|counsel|attorney|compliance|paralegal|privacy"),
    ("people", r"recruit|talent|\bpeople\b|\bhr\b|human resources|workplace|\btrainer\b|learning (and|&) development|training (specialist|manager|coordinator)"),
    ("healthcare", r"nurs(e|ing)|physician|clinic(al|ian)|pharmac|medical|therapist|patient|dental|dentist|surgeon|radiolog|caregiver|health ?care|\brn\b|paramedic|phlebotom|\bcna\b|veterinar|psychiat|psycholog|mental health|neurolog|hospital"),
    ("education", r"teacher|professor|instructor|lecturer|tutor|faculty|curriculum|academic|educator|school|teaching"),
    ("science", r"scientist|laborator|chemist|biolog|\blab\b|research (associate|assistant|fellow)|geolog"),
    ("retail", r"\bstore\b|retail|personal train|fitness|\battendant\b|detailer|car wash|lifeguard|cashier|barista|\bcook\b|\bchef\b|kitchen|bartender|hotel|guest (service|experience)|concierge|housekeep|front desk|merchandis|stylist|restaurant|\bcrew\b|\bshift\b|food service|catering|cleaner|reinigung|janitor|arrumadeira|verk.ufer|cafeteria|barber|salon|k.chenhilfe|k.che\b|einzelhandel|kaufmann|gar.om|vendedor|ayudante|hostess|waiter|waitress|meat|grocery|stocker"),
    ("manufacturing", r"painter|mechatronik|mechatronic|manufactur|production (operator|associate|supervisor|technician|worker)|assembl|machinist|weld|electrician|plumb|forklift|fabricat|\bplant\b|\bmill\b|maintenance|operator|operat.r|monteur|fertigung|toolmaker|produktion|produktie|construction|quality|inspector|\bnde\b|operario|op.rateur|lead hand"),
    ("logistics", r"logist|warehouse|\bdelivery (driver|associate|partner)|parts (associate|clerk|specialist)|material handler|\bdriver\b|\blkw\b|fahrer|courier|picker|\bpacker\b|shipping|dispatch|freight"),
    ("administrative", r"assistant|receptionist|secretar|\bclerk|recepcion|data entry|coordinator|administrat"),
    ("social", r"social work|case manag|caseworker|counsel+or|youth|outreach|advocate|community (health|support)"),
    ("consulting", r"consultant|consulting|strategist|advisory|engagement manager|business partner"),
    ("operations", r"operations|\bops\b|supply|logistics|business (analyst|operations)|chief of staff|strategy|facilit|office|warehouse|driver|technician|mechanic|buyer|sourcing|category manager|planner|planning|inventory|distribution|materials|supervisor"),
]
SKILLS = ("python java javascript typescript rust c++ c# ruby php swift kotlin scala sql nosql react vue angular node.js django "
          "flask spring-boot aws azure gcp kubernetes docker terraform postgres mysql mongodb redis kafka spark snowflake "
          "databricks dbt airflow pytorch tensorflow llm ml graphql linux git ci/cd figma tableau salesforce hubspot jira sap").split()
ALIAS = dict(golang="go", postgresql="postgres", nodejs="node.js", node="node.js", k8s="kubernetes", reactjs="react",
             **{"react.js": "react", "machine learning": "ml", "spring boot": "spring-boot"}, js="javascript", ts="typescript")
# skills outside IT, so every field has something to count. Multi-word phrases map to a key with "_" (shown as "Customer service").
SKILLS += ("powerpoint quickbooks erp crm pmp agile scrum budgeting forecasting gaap ifrs payroll procurement negotiation seo copywriting "
           "photoshop illustrator autocad solidworks revit cpr bls acls ehr emr hipaa phlebotomy forklift osha cdl welding haccp hris bilingual spanish").split()
ALIAS.update({"microsoft excel": "excel", "ms excel": "excel", "advanced excel": "excel", "excel spreadsheets": "excel", "microsoft office": "ms_office",
              "ms office": "ms_office", "customer service": "customer_service", "project management": "project_management",
              "six sigma": "six_sigma", "financial modeling": "financial_modeling", "financial modelling": "financial_modeling",
              "accounts payable": "accounts_payable", "accounts receivable": "accounts_receivable", "data entry": "data_entry",
              "supply chain": "supply_chain", "social media": "social_media", "google analytics": "google_analytics",
              "adobe creative suite": "adobe_creative_suite", "patient care": "patient_care", "electronic health records": "ehr",
              "medical coding": "medical_coding", "preventive maintenance": "preventive_maintenance", "food safety": "food_safety",
              "cash handling": "cash_handling", "inventory management": "inventory_management", "lesson planning": "lesson_planning",
              "talent acquisition": "talent_acquisition", "employee relations": "employee_relations", "public speaking": "public_speaking",
              "time management": "time_management", "forklift operator": "forklift"})
SKILL_RX = re.compile(r"(?<![\w+#.])(" + "|".join(sorted(map(re.escape, SKILLS + list(ALIAS)), key=len, reverse=True)) + r")(?![\w+#])", re.I)
EMP = dict(fulltime="full_time", permanent="full_time", vollzeit="full_time", parttime="part_time", teilzeit="part_time",
           contract="contract", contractor="contract", temporary="contract", freelance="contract",
           intern="internship", internship="internship", praktikum="internship")
YEARS_RX = re.compile(r"(\d{1,2})\s*\+?\s*(?:(?:-|–|to)\s*\d{1,2}\s*\+?\s*)?(?:years?|yrs?)\b", re.I)
PAY_RX = re.compile(r"([$£€])\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])\s*(?:-|–|—|to)\s*[$£€]?\s?(\d{2,3}(?:,\d{3})+|\d{2,3}\s?[kK])")
LEGAL_RX = re.compile(r"[,\s]+(?:inc|llc|ltd|limited|gmbh|ag|corp|corporation|plc|bv|pty|pvt|llp|lp|co)\.?$", re.I)


def text(h):  # HTML (possibly entity-escaped) -> plain text
    return html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(h or "")))


RE_LEVELS = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in LEVELS]
RE_CATEGORIES = [(name, re.compile(rx, re.IGNORECASE)) for name, rx in CATEGORIES]


@functools.lru_cache(maxsize=32768)
def level(title, years=None, hint=None):
    t = (title or "").lower()
    for name, rx in RE_LEVELS:
        if rx.search(t):
            return name
    if hint:
        return hint
    return "mid" if years is None else "junior" if years <= 1 else "mid" if years <= 4 else "senior"


@functools.lru_cache(maxsize=32768)
def category(*texts):  # title first, then team/department
    for t in texts:
        if not t:
            continue
        tl = t.lower()
        for name, rx in RE_CATEGORIES:
            if rx.search(tl):
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
    if not n or not isinstance(n, str):
        return ""
    n = " ".join(n.split())
    while (m := LEGAL_RX.sub("", n)) != n and m:
        n = m
    return n


EVERGREEN_RX = re.compile(r"talent (community|network|pool|pipeline)|general application|future (opportunit|role)|evergreen|always hiring"
                          r"|expression of interest|join our (talent|network)", re.I)
HIDDEN_RX = re.compile(r"confidential|stealth|undisclosed|anonymous|our client|staffing|recruit(er|ing|ment)|talent (solutions|partners)", re.I)


@functools.lru_cache(maxsize=32768)
def _clean_title(t):
    return re.sub(r"\W", "", (t or "").lower())


def ghost_flags(jobs, today=None):
    """id -> [(reason, points)] for open jobs. Heuristics for *possible* ghost jobs (no real vacancy, resume farming), never proof.
    2+ points = possibly ghost, 3+ = likely. Needs no stored data except `reposts`, which builds up as the scraper keeps running."""
    today = today or dt.date.today()
    same = {}
    for j in jobs:
        c = j.get("company", "")
        t = _clean_title(j.get("title"))
        k = (c, t)
        same[k] = same.get(k, 0) + 1
    out = {}
    for j in jobs:
        c = j.get("company", "")
        t = _clean_title(j.get("title"))
        r = []
        posted = j.get("posted") or j.get("first_seen")
        if posted:
            try:
                age = (today - dt.date.fromisoformat(posted)).days
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


def fresher_ok(j):
    """Open to 0-2 years of experience: the posting states <= 2 years, or is an intern/junior role that states nothing.
    A "junior" role that asks for 3+ years is NOT fresher-friendly, and a senior/lead title never is."""
    y = j.get("years")
    lvl = j.get("level") or level(j.get("title", ""), y)
    return lvl in ("intern", "junior", "mid") and (y <= 2 if y is not None else lvl != "mid")


def company_stats(jobs, min_roles=5):
    """Per company: open roles, how many are fresher-friendly, 'junior' roles that ask 3+ years, and how often years are stated at all."""
    by = {}
    for j in jobs:
        c = j.get("company", "")
        if c:
            by.setdefault(c, []).append(j)
    out = [dict(company=c, open_roles=len(js), fresher_roles=sum(map(fresher_ok, js)),
                fresher_share=round(sum(map(fresher_ok, js)) / len(js), 3),
                junior_asking_3plus=sum(j.get("level") in ("intern", "junior") and (j.get("years") or 0) >= 3 for j in js),
                years_stated_share=round(sum(j.get("years") is not None for j in js) / len(js), 2))
           for c, js in by.items() if len(js) >= min_roles]
    return sorted(out, key=lambda r: (-r["fresher_share"], -r["open_roles"]))


def body(call, tries=3):  # retries JSON decoding errors, rate-limits, and transient network / 5xx hiccups
    for i in range(tries):
        try:
            r = call()
            r.raise_for_status()
            return r.json()
        except (ValueError, requests.exceptions.RequestException) as e:
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                raise
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def get(url, **kw):
    return body(lambda: S.get(url, timeout=TIMEOUT, **kw))


def day(v):  # epoch ms / epoch s / ISO string -> YYYY-MM-DD
    if not v:
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        v = float(v)
        v = v / 1000 if v > 1e11 else v
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    return str(v)[:10]


US_STATES = {
    "AL": "Alabama", "AK": "Alaska", "AZ": "Arizona", "AR": "Arkansas", "CA": "California",
    "CO": "Colorado", "CT": "Connecticut", "DE": "Delaware", "FL": "Florida", "GA": "Georgia",
    "HI": "Hawaii", "ID": "Idaho", "IL": "Illinois", "IN": "Indiana", "IA": "Iowa",
    "KS": "Kansas", "KY": "Kentucky", "LA": "Louisiana", "ME": "Maine", "MD": "Maryland",
    "MA": "Massachusetts", "MI": "Michigan", "MN": "Minnesota", "MS": "Mississippi", "MO": "Missouri",
    "MT": "Montana", "NE": "Nebraska", "NV": "Nevada", "NH": "New Hampshire", "NJ": "New Jersey",
    "NM": "New Mexico", "NY": "New York", "NC": "North Carolina", "ND": "North Dakota", "OH": "Ohio",
    "OK": "Oklahoma", "OR": "Oregon", "PA": "Pennsylvania", "RI": "Rhode Island", "SC": "South Carolina",
    "SD": "South Dakota", "TN": "Tennessee", "TX": "Texas", "UT": "Utah", "VT": "Vermont",
    "VA": "Virginia", "WA": "Washington", "WV": "West Virginia", "WI": "Wisconsin", "WY": "Wyoming",
    "DC": "District of Columbia", "PR": "Puerto Rico", "GU": "Guam", "VI": "Virgin Islands"
}

CA_PROVINCES = {
    "AB": "Alberta", "BC": "British Columbia", "MB": "Manitoba", "NB": "New Brunswick",
    "NL": "Newfoundland and Labrador", "NS": "Nova Scotia", "NT": "Northwest Territories",
    "NU": "Nunavut", "ON": "Ontario", "PE": "Prince Edward Island", "QC": "Quebec",
    "SK": "Saskatchewan", "YT": "Yukon"
}

AU_STATES = {
    "NSW": "New South Wales", "VIC": "Victoria", "QLD": "Queensland", "WA": "Western Australia",
    "SA": "South Australia", "TAS": "Tasmania", "ACT": "Australian Capital Territory", "NT": "Northern Territory"
}

COUNTRY_MAP = {
    "united states": "United States", "usa": "United States", "u.s.a.": "United States", "u.s.": "United States", "us": "United States",
    "united kingdom": "United Kingdom", "uk": "United Kingdom", "u.k.": "United Kingdom", "great britain": "United Kingdom",
    "england": "United Kingdom", "scotland": "United Kingdom", "wales": "United Kingdom", "northern ireland": "United Kingdom",
    "canada": "Canada", "australia": "Australia", "germany": "Germany", "deutschland": "Germany",
    "india": "India", "france": "France", "netherlands": "Netherlands", "holland": "Netherlands",
    "spain": "Spain", "espana": "Spain", "españa": "Spain", "italy": "Italy", "italia": "Italy",
    "brazil": "Brazil", "brasil": "Brazil", "japan": "Japan", "singapore": "Singapore",
    "ireland": "Ireland", "switzerland": "Switzerland", "sweden": "Sweden", "poland": "Poland",
    "mexico": "Mexico", "israel": "Israel", "united arab emirates": "United Arab Emirates", "uae": "United Arab Emirates",
    "south africa": "South Africa", "new zealand": "New Zealand", "belgium": "Belgium", "austria": "Austria",
    "denmark": "Denmark", "norway": "Norway", "finland": "Finland", "portugal": "Portugal",
    "greece": "Greece", "czech republic": "Czech Republic", "czechia": "Czech Republic",
    "romania": "Romania", "hungary": "Hungary", "philippines": "Philippines", "indonesia": "Indonesia",
    "malaysia": "Malaysia", "thailand": "Thailand", "vietnam": "Vietnam", "south korea": "South Korea",
    "korea": "South Korea", "argentina": "Argentina", "chile": "Chile", "colombia": "Colombia",
    "peru": "Peru", "nigeria": "Nigeria", "kenya": "Kenya", "egypt": "Egypt", "saudi arabia": "Saudi Arabia",
    "turkey": "Turkey", "türkiye": "Turkey", "ukraine": "Ukraine", "pakistan": "Pakistan",
    "bangladesh": "Bangladesh", "taiwan": "Taiwan", "hong kong": "Hong Kong", "luxembourg": "Luxembourg"
}

MAJOR_CITIES = {
    "london": "United Kingdom", "manchester": "United Kingdom", "birmingham": "United Kingdom", "edinburgh": "United Kingdom",
    "paris": "France", "lyon": "France", "marseille": "France",
    "berlin": "Germany", "munich": "Germany", "frankfurt": "Germany", "hamburg": "Germany", "cologne": "Germany",
    "tokyo": "Japan", "osaka": "Japan", "kyoto": "Japan",
    "toronto": "Canada", "vancouver": "Canada", "montreal": "Canada", "ottawa": "Canada", "calgary": "Canada",
    "sydney": "Australia", "melbourne": "Australia", "brisbane": "Australia", "perth": "Australia", "adelaide": "Australia",
    "dublin": "Ireland", "cork": "Ireland",
    "amsterdam": "Netherlands", "rotterdam": "Netherlands", "the hague": "Netherlands",
    "bengaluru": "India", "bangalore": "India", "mumbai": "India", "delhi": "India", "new delhi": "India",
    "hyderabad": "India", "pune": "India", "chennai": "India", "gurgaon": "India", "gurugram": "India", "noida": "India",
    "madrid": "Spain", "barcelona": "Spain", "valencia": "Spain",
    "rome": "Italy", "milan": "Italy",
    "zurich": "Switzerland", "geneva": "Switzerland", "basel": "Switzerland",
    "stockholm": "Sweden", "gothenburg": "Sweden",
    "warsaw": "Poland", "krakow": "Poland", "wroclaw": "Poland",
    "tel aviv": "Israel", "jerusalem": "Israel", "herzliya": "Israel",
    "dubai": "United Arab Emirates", "abu dhabi": "United Arab Emirates",
    "são paulo": "Brazil", "sao paulo": "Brazil", "rio de janeiro": "Brazil",
    "mexico city": "Mexico", "guadalajara": "Mexico", "monterrey": "Mexico",
    "buenos aires": "Argentina", "santiago": "Chile", "bogota": "Colombia", "lima": "Peru",
    "seoul": "South Korea", "singapore": "Singapore", "hong kong": "Hong Kong"
}

RE_US_STATE_END = re.compile(r'(?:,\s*|\s+)([A-Z]{2})(?:\s+\d{5}(?:-\d{4})?)?(?:\s*;\s*|\s*$)', re.IGNORECASE)
RE_COUNTRY_SPLIT = re.compile(r'[,;/|\-]+')
RE_COUNTRY_MAP = [(re.compile(r'\b' + re.escape(k) + r'\b', re.IGNORECASE), v) for k, v in COUNTRY_MAP.items()]
RE_US_STATES = [(re.compile(r'\b' + re.escape(state_name.lower()) + r'\b', re.IGNORECASE), "United States") for code, state_name in US_STATES.items()]
RE_CA_PROVINCES = [(re.compile(r'\b' + re.escape(prov_name.lower()) + r'\b', re.IGNORECASE), "Canada") for code, prov_name in CA_PROVINCES.items()]
RE_MAJOR_CITIES = [(re.compile(r'\b' + re.escape(city) + r'\b', re.IGNORECASE), cntry) for city, cntry in MAJOR_CITIES.items()]


@functools.lru_cache(maxsize=32768)
def country(loc: str) -> str:
    if not loc:
        return ""
    loc_clean = loc.strip()
    loc_lower = loc_clean.lower()

    # 1. Check delimiter-separated tokens from the right (e.g. "City, State/Country" or "City, Country")
    parts = [p.strip().lower() for p in RE_COUNTRY_SPLIT.split(loc_clean) if p.strip()]
    if parts:
        last = parts[-1]
        if last in COUNTRY_MAP:
            return COUNTRY_MAP[last]
        last_u = last.upper()
        if last_u in US_STATES:
            return "United States"
        if last_u in CA_PROVINCES:
            return "Canada"
        if last_u in AU_STATES and any(c in loc_lower for c in ["sydney", "melbourne", "brisbane", "perth", "adelaide", "australia"]):
            return "Australia"

    # 2. Check full country names anywhere in string
    for rx, v in RE_COUNTRY_MAP:
        if rx.search(loc_lower):
            return v

    # 3. Check US full state names
    for rx, v in RE_US_STATES:
        if rx.search(loc_lower):
            return v

    # 4. Check Canadian province names
    for rx, v in RE_CA_PROVINCES:
        if rx.search(loc_lower):
            return v

    # 5. Check major world cities
    for rx, cntry in RE_MAJOR_CITIES:
        if rx.search(loc_lower):
            return cntry

    # 6. Check regex ending with US state or Canadian province
    m = RE_US_STATE_END.search(loc_clean)
    if m:
        st = m.group(1).upper()
        if st in US_STATES:
            return "United States"
        if st in CA_PROVINCES:
            return "Canada"

    return ""


infer_country = country


def job(source, scope, rid, company, title, url, location="", remote=False, posted=None,
        desc="", team="", emp=(), pay=None, tags=(), hint=None, country=None):
    location, team, title = (location or "").strip(), (team or "").strip(), (title or "").strip()
    yrs = years_req(desc)
    cntry = (country or "").strip() or infer_country(location)
    return dict(id=f"{scope}:{rid}", source=source, company=company, title=title, team=team, location=location,
                country=cntry, remote=bool(remote) or "remote" in location.lower(), level=level(title, yrs, hint),
                category=category(title, team), employment=employment(title, *emp), years=yrs,
                skills=skills(f"{title} {' '.join(tags)} {desc}"), pay=pay or find_pay(desc), url=url, posted=day(posted))


# ---------- per-company ATS fetchers (slug, display name -> jobs). `url` is the apply link. ----------
def greenhouse(s, name):
    data = get(f"https://boards-api.greenhouse.io/v1/boards/{s}/jobs", params={"content": "true"})
    return [job("greenhouse", f"greenhouse:{s}", j["id"], name or j.get("company_name") or s, j["title"], j["absolute_url"],
                (j.get("location") or {}).get("name"), posted=j.get("first_published") or j.get("updated_at"),
                desc=text(j.get("content")), team=next((d.get("name") for d in j.get("departments") or [] if isinstance(d, dict) and d.get("name")), ""))
            for j in data.get("jobs", []) if j.get("id") and j.get("title")]


def lever(s, name):
    out = []
    items = get(f"https://api.lever.co/v0/postings/{s}", params={"mode": "json"})
    for j in (items if isinstance(items, list) else []):
        if not j.get("id") or not j.get("text"):
            continue
        c, r = j.get("categories") or {}, j.get("salaryRange") or {}
        pay = [int(r["min"]), int(r["max"]), r.get("currency", "USD")] if r.get("min") and r.get("max") and "year" in r.get("interval", "") else None
        desc = " ".join([j.get("descriptionPlain") or "", j.get("additionalPlain") or ""] + [text(l.get("content")) for l in j.get("lists") or []])
        out.append(job("lever", f"lever:{s}", j["id"], name, j["text"], j.get("applyUrl") or f"https://jobs.lever.co/{s}/{j['id']}", c.get("location"),
                       j.get("workplaceType") == "remote", j.get("createdAt"), desc=desc,
                       team=c.get("team") or c.get("department"), emp=[c.get("commitment")], pay=pay))
    return out


def ashby(s, name):
    out = []
    data = get(f"https://api.ashbyhq.com/posting-api/job-board/{s}", params={"includeCompensation": "true"})
    for j in data.get("jobs", []):
        if not j.get("isListed", True) or not j.get("id") or not j.get("title"):
            continue
        comp = next((c for t in (j.get("compensation") or {}).get("compensationTiers") or [] for c in t.get("components") or []
                     if c.get("compensationType") == "Salary" and "YEAR" in (c.get("interval") or "")), None)
        pay = [int(comp["minValue"]), int(comp["maxValue"]), comp["currencyCode"]] if comp and comp.get("minValue") and comp.get("maxValue") else None
        out.append(job("ashby", f"ashby:{s}", j["id"], name, j["title"], j.get("applyUrl") or j.get("jobUrl") or "", j.get("location"),
                       j.get("isRemote") or j.get("workplaceType") == "Remote", j.get("publishedAt"),
                       desc=j.get("descriptionPlain") or "", team=j.get("department") or j.get("team"),
                       emp=[j.get("employmentType")], pay=pay))
    return out


SR_LEVEL = dict(internship="intern", entry_level="junior", director="manager+", executive="manager+")


def smartrecruiters(s, name):  # list endpoint has no description, so years/skills/pay stay empty here
    out, off = [], 0
    while True:
        d = get(f"https://api.smartrecruiters.com/v1/companies/{s}/postings", params={"limit": 100, "offset": off})
        content = d.get("content", [])
        for j in content:
            if not j.get("id") or not j.get("name"):
                continue
            l = j.get("location") or {}
            loc = l.get("fullLocation") or ", ".join(filter(None, [l.get("city"), (l.get("country") or "").upper()]))
            out.append(job("smartrecruiters", f"smartrecruiters:{s}", j["id"], name or (j.get("company") or {}).get("name") or s,
                           j["name"], f"https://jobs.smartrecruiters.com/{s}/{j['id']}", loc, l.get("remote"), j.get("releasedDate"),
                           team=(j.get("function") or {}).get("label") or (j.get("department") or {}).get("label"),
                           emp=[(j.get("typeOfEmployment") or {}).get("label")],
                           hint=SR_LEVEL.get((j.get("experienceLevel") or {}).get("id"))))
        off += 100
        if not content or off >= d.get("totalFound", 0):
            return out


def wd_posted(t):  # "Posted 3 Days Ago" / "Posted Today" / "Posted 30+ Days Ago" -> YYYY-MM-DD
    if "30+" in (t or ""):  # "30+ days ago" has no date; guessing today-30 would shift every day and rewrite those rows daily
        return None
    m = re.search(r"\d+", t or "")
    n = int(m.group()) if m else 1 if "yesterday" in (t or "").lower() else 0
    return (dt.date.today() - dt.timedelta(days=n)).isoformat()


WD_CAP = 10000  # newest N per tenant: Workday refuses offsets past 2000 on some views, up to 10k on others. The site shows each employer's newest SITE_PER_COMPANY only (build_site.py)


def pmap(fn, xs, workers=4):  # a few pages at a time: fast, but polite to one host. Own pool, so no deadlock inside main()'s pool
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, xs))


def workday(s, name):  # s = "<tenant>.<pod>.myworkdayjobs.com/<site>"; the list has no description, so years/skills/pay stay empty
    host, site = s.split("/", 1)

    def page(off):
        return body(lambda: S.post(f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs", json={"limit": 20, "offset": off, "searchText": ""}, timeout=TIMEOUT))

    first = page(0)
    total = first.get("total", 0)
    rest = pmap(page, range(20, min(total, WD_CAP), 20)) if total > 20 else []
    return list({j["externalPath"]: job("workday", f"workday:{s}", j["externalPath"], name, j["title"], f"https://{s}{j['externalPath']}",
                                        j.get("locationsText"), posted=wd_posted(j.get("postedOn")))
                 for d in [first, *rest] for j in d.get("jobPostings", []) if j.get("externalPath") and j.get("title")}.values())  # entries with no link or no title exist (KBR has one): skip them, not the board


CAPPED = {"workday", "eightfold", "himalayas", "jobicy", "jobtech", "usajobs", "oracle", "phenom", "apple", "themuse"}
HIM_CAP = 400  # newest N Himalayas jobs (of 100k+)
MUSE_CAP = 400  # newest N jobs from The Muse (of 400k+)
ORA_CAP = 10000  # newest N per Oracle career site (uncapped for large enterprises)
USAJOBS_CAP = 15000  # newest N federal postings (uncapped to capture full federal directory)
JT_CAP = 500  # newest N Swedish public-employment ads (of ~40k)
EF_CAP = 10000  # newest N per Eightfold site (Starbucks, Microsoft, etc.)
APPLE_CAP = 10000  # newest N Apple jobs (Apple lists ~6,000)


def pcsx(host, domain, source, scope, name, cap=None):  # Eightfold "PCSX" search API behind a careers site; their robots.txt allows /api/pcsx
    def page(off):
        time.sleep(.2)  # Eightfold's bot protection answers 405 to a burst, so a few pages at a time with a pause (and one site at a time, see LANE_OF)
        return get(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": "", "start": off}).get("data", {})

    first = page(0)
    positions = first.get("positions") or []
    size = len(positions)
    count = first.get("count", 0)
    rest = pmap(page, range(size, min(count, cap or count), size), 4) if size and size < count else []
    return list({j["id"]: job(source, scope, j["id"], name, j["name"], f"https://{host}/careers/job/{j['id']}",
                              "; ".join(j["standardizedLocations"] or j["locations"] or []), j.get("workLocationOption") == "remote",
                              j.get("postedTs"), team=j.get("department"))
                 for d in [first, *rest] for j in (d.get("positions") or []) if j.get("id") and j.get("name")}.values())


def microsoft(s, name):  # s = domain
    return pcsx("apply.careers.microsoft.com", s, "microsoft", f"microsoft:{s}", name)


def eightfold(s, name):  # s = "<careers host>/<domain>", e.g. "apply.starbucks.com/starbucks.com"; the apply URL is stored per job
    host, domain = s.split("/", 1)
    return pcsx(host, domain, "eightfold", f"eightfold:{s}", name, EF_CAP)


def amazon(s, name):  # amazon.jobs' search endpoint; partitioned by category to bypass Elasticsearch 10k window
    def posted(t):
        try:
            return dt.datetime.strptime(" ".join(t.split()), "%B %d, %Y").date().isoformat()
        except (ValueError, AttributeError):
            return None

    try:
        facet_res = get("https://www.amazon.jobs/en/search.json", params={"facets[]": "category", "result_limit": 1})
        cat_facets = facet_res.get("facets", {}).get("category_facet") or []
    except Exception:
        cat_facets = []

    pages = []
    if cat_facets:
        for item in cat_facets:
            for cat_name, count in item.items():
                for off in range(0, min(count, 10000), 100):
                    pages.append((cat_name, off))
    else:
        for off in range(0, 10000, 100):
            pages.append((None, off))

    def fetch_page(arg):
        cat_name, off = arg
        params = {"offset": off, "result_limit": 100, "sort": "recent"}
        if cat_name:
            params["category[]"] = cat_name
        res = get("https://www.amazon.jobs/en/search.json", params=params)
        return res.get("jobs") or []

    out = {}
    for p in pmap(fetch_page, pages, workers=8):
        for j in p:
            jid = j.get("id_icims")
            if not jid or jid in out:
                continue
            loc = j.get("normalized_location") or j.get("location") or ""
            desc = text(" ".join(filter(None, [j.get("description"), j.get("basic_qualifications"), j.get("preferred_qualifications")])))
            team = j.get("job_family") or j.get("job_category")
            emp = [j.get("job_schedule_type")] if j.get("job_schedule_type") else []
            hint = "intern" if j.get("is_intern") else None
            path = j.get("job_path") or f"/en/jobs/{jid}"
            url = f"https://www.amazon.jobs{path}" if path.startswith("/") else path
            out[jid] = job("amazon", f"amazon:{s}", str(jid), name or "Amazon", j.get("title") or "",
                           url, loc, False, posted(j.get("posted_date")),
                           desc=desc, team=team, emp=emp, hint=hint)
    return list(out.values())


def apple(s, name):  # Apple careers API (jobs.apple.com)
    csrf_res = S.get("https://jobs.apple.com/api/v1/CSRFToken", timeout=TIMEOUT)
    csrf_res.raise_for_status()
    csrf = csrf_res.headers.get("X-Apple-CSRF-Token")
    if not csrf:
        raise ValueError("Apple CSRF token not found in response headers")

    headers = {
        "X-Apple-CSRF-Token": csrf,
        "Content-Type": "application/json;charset=UTF-8",
        "Origin": "https://jobs.apple.com",
        "Referer": "https://jobs.apple.com/en-us/search",
    }

    def page(p):
        payload = {
            "query": "",
            "filters": {},
            "page": p,
            "locale": "en-us",
            "sort": "newest",
            "format": {"longDate": "MMMM D, YYYY", "mediumDate": "MMM D, YYYY"},
        }
        r = body(lambda: S.post("https://jobs.apple.com/api/v1/search", json=payload, headers=headers, timeout=TIMEOUT))
        return r.get("res") or {}

    first = page(1)
    total = first.get("totalRecords", 0)
    pages_to_fetch = min(total, APPLE_CAP) // 20 + 1
    rest = pmap(page, range(2, pages_to_fetch + 1), workers=4) if pages_to_fetch > 1 else []

    out = []
    seen = set()
    for d in [first, *rest]:
        for j in d.get("searchResults") or []:
            pos_id = j.get("positionId")
            if not pos_id or not j.get("postingTitle") or pos_id in seen:
                continue
            seen.add(pos_id)
            locs = []
            for loc in j.get("locations") or []:
                parts = filter(None, [loc.get("city"), loc.get("stateProvince"), loc.get("countryName")])
                loc_str = ", ".join(parts) or loc.get("name") or ""
                if loc_str and loc_str not in locs:
                    locs.append(loc_str)
            post_date = j.get("postDateInGMT")
            date_str = post_date[:10] if post_date and len(post_date) >= 10 else None
            team = (j.get("team") or {}).get("teamName")
            desc = text(j.get("jobSummary") or "")
            out.append(job("apple", f"apple:{s}", pos_id, name or "Apple", j["postingTitle"],
                           f"https://jobs.apple.com/en-us/details/{pos_id}",
                           "; ".join(locs), j.get("homeOffice") is True, date_str,
                           desc=desc, team=team))
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
        time.sleep(1)  # their rate limit 429s a fast pager; runs in its own lane (LANES), so the pause costs the other sources nothing
        d = get("https://www.arbeitnow.com/api/job-board-api", params={"page": p})
        out += [job("arbeitnow", "arbeitnow", j["slug"], clean_company(j["company_name"]), j["title"], j["url"], j.get("location"),
                    j.get("remote"), j.get("created_at"), desc=text(j.get("description")), team=" ".join(j.get("tags") or []),
                    emp=j.get("job_types") or [], tags=j.get("tags") or []) for j in d["data"]]
        if not d["links"].get("next"):
            return out
    raise RuntimeError("arbeitnow: >100 pages")


def rss_day(s):
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except (TypeError, ValueError):
        return None


def himalayas():  # remote jobs worldwide; 117k listed, so only the newest HIM_CAP (20 per page, cursor paging)
    out, cur = [], None
    while len(out) < HIM_CAP:
        d = get("https://himalayas.app/jobs/api", params={"limit": 20, **({"cursor": cur} if cur else {})})
        out += [job("himalayas", "himalayas", j["guid"].rstrip("/").rsplit("/", 1)[-1], clean_company(j["companyName"]), j["title"],
                    j["applicationLink"], ", ".join(j.get("locationRestrictions") or []) or "Worldwide", True, j.get("pubDate"),
                    desc=text(j.get("description")), team=" ".join(j.get("parentCategories") or []), emp=[j.get("employmentType")],
                    tags=j.get("categories") or [], hint="junior" if "Entry-level" in (j.get("seniority") or []) else None,
                    pay=[int(j["minSalary"]), int(j["maxSalary"]), j["currency"]] if j.get("minSalary") and j.get("maxSalary") and j.get("currency")
                    and j.get("salaryPeriod") == "annual" else None) for j in d["jobs"]]
        cur = d.get("nextCursor")
        if not cur or not d["jobs"]:
            break
    return out


def jobicy():  # newest 100 remote jobs, salary stated for many
    return [job("jobicy", "jobicy", j["id"], clean_company(j["companyName"]), j["jobTitle"], j["url"], j.get("jobGeo"), True, j.get("pubDate"),
                desc=text(j.get("jobDescription")), team=" ".join(j.get("jobIndustry") or []), emp=j.get("jobType") or [],
                pay=[int(j["salaryMin"]), int(j["salaryMax"]), j["salaryCurrency"]] if j.get("salaryMin") and j.get("salaryMax")
                and j.get("salaryCurrency") and j.get("salaryPeriod") == "yearly" else None)
            for j in get("https://jobicy.com/api/v2/remote-jobs", params={"count": 100})["jobs"]]


def weworkremotely():  # RSS of the whole board; titles are "Company: Role"
    r = S.get("https://weworkremotely.com/remote-jobs.rss", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for i in ET.fromstring(r.content).iter("item"):
        co, _, title = (i.findtext("title") or "").partition(": ")
        out.append(job("weworkremotely", "weworkremotely", i.findtext("link").rstrip("/").rsplit("/", 1)[-1], clean_company(co), title, i.findtext("link"),
                       i.findtext("region") or i.findtext("country"), True, rss_day(i.findtext("pubDate")),
                       desc=text(i.findtext("description")), team=i.findtext("category"), emp=[i.findtext("type")]))
    return out


def getonbrd():  # Latin America's tech board (mostly Spanish postings), programming category
    out = []
    for p in range(1, 21):
        d = get("https://www.getonbrd.com/api/v0/categories/programming/jobs", params={"per_page": 100, "page": p, "expand": '["company"]'})
        for j in d["data"]:
            a = j["attributes"]
            out.append(job("getonbrd", "getonbrd", j["id"], clean_company(a["company"]["data"]["attributes"]["name"]), a["title"],
                           f"https://www.getonbrd.com/jobs/{j['id']}", ", ".join(a.get("countries") or []), a.get("remote"), a.get("published_at"),
                           desc=text(" ".join(filter(None, [a.get("description"), a.get("functions"), a.get("desirable")])))))
        if p >= d["meta"]["total_pages"]:
            return out
    raise RuntimeError("getonbrd: >20 pages")


def workingnomads():  # remote jobs, tags and time-zone notes; the list is short (~50) and complete
    return [job("workingnomads", "workingnomads", j["url"].rstrip("/").rsplit("/", 1)[-1], clean_company(j["company_name"]), j["title"], j["url"],
                j.get("location"), True, j.get("pub_date"), desc=text(j.get("description")), team=j.get("category_name"),
                tags=(j.get("tags") or "").split(","))
            for j in get("https://www.workingnomads.com/api/exposed_jobs/")]


def jobtech():  # Sweden's public employment service (open data, Swedish text): newest JT_CAP of ~40k ads
    def page(off):
        return get("https://jobsearch.api.jobtechdev.se/search", params={"limit": 100, "offset": off, "sort": "pubdate-desc"})["hits"]

    out = []
    for j in (h for hits in pmap(page, range(0, JT_CAP, 100), 3) for h in hits):
        a = j.get("workplace_address") or {}
        out.append(job("jobtech", "jobtech", j["id"], clean_company((j.get("employer") or {}).get("name")), j["headline"], j["webpage_url"],
                       ", ".join(filter(None, [a.get("municipality"), a.get("country")])), False, j.get("publication_date"),
                       desc=(j.get("description") or {}).get("text") or "", team=(j.get("occupation_field") or {}).get("label"),
                       emp=[(j.get("working_hours_type") or {}).get("label"), (j.get("employment_type") or {}).get("label")]))
    return out


def rss_board(url, source, remote=True):  # titles read "Role at Company"
    r = S.get(url, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for i in ET.fromstring(r.content).iter("item"):
        link = i.findtext("link").strip()
        title, _, co = (i.findtext("title") or "").rpartition(" at ")
        out.append(job(source, source, link.rstrip("/").rsplit("/", 1)[-1], clean_company(co), title, link, "", remote, rss_day(i.findtext("pubDate")),
                       desc=text(i.findtext("description")), team=i.findtext("category") or ""))
    return out


def cryptojobs():
    return [j for j in rss_board("https://cryptocurrencyjobs.co/index.xml", "cryptojobs", remote=False) if j["company"]]


def themuse():  # public US & global jobs feed across healthcare, retail, finance, engineering: newest MUSE_CAP
    out = []
    for p in range(1, MUSE_CAP // 20 + 1):
        d = get("https://www.themuse.com/api/public/jobs", params={"page": p})
        for j in d.get("results") or []:
            if not j.get("id") or not j.get("name"):
                continue
            co = (j.get("company") or {}).get("name") or "Unknown"
            locs = [l.get("name") for l in j.get("locations") or [] if l.get("name")]
            loc = "; ".join(locs)
            remote = any("remote" in l.lower() or "flexible" in l.lower() for l in locs)
            jid = str(j["id"])
            url = (j.get("refs") or {}).get("landing_page") or f"https://www.themuse.com/jobs/{jid}"
            desc = text(j.get("contents"))
            cat = next((c.get("name") for c in j.get("categories") or [] if c.get("name")), "")
            out.append(job("themuse", "themuse", jid, co, j["name"], url, loc, remote, j.get("publication_date"),
                           desc=desc, team=cat))
    return out


def usajobs_job(i):  # one SearchResultItem of the USAJOBS API -> job
    d = i["MatchedObjectDescriptor"]
    det = (d.get("UserArea") or {}).get("Details") or {}
    pay = next((r for r in d.get("PositionRemuneration") or [] if r.get("RateIntervalCode") == "PA" and r.get("MinimumRange") and r.get("MaximumRange")), None)
    return job("usajobs", "usajobs", i["MatchedObjectId"], d.get("OrganizationName") or d.get("DepartmentName") or "US federal government", d["PositionTitle"],
               (d.get("ApplyURI") or [d["PositionURI"]])[0], d.get("PositionLocationDisplay"), str(det.get("RemoteIndicator")).lower() == "true",
               d.get("PublicationStartDate"), desc=(d.get("QualificationSummary") or "") + " " + (det.get("JobSummary") or ""),
               team=" ".join(c["Name"] for c in d.get("JobCategory") or []), emp=[(d.get("PositionSchedule") or [{}])[0].get("Name")],
               pay=[int(float(pay["MinimumRange"])), int(float(pay["MaximumRange"])), "USD"] if pay else None)


def usajobs():  # US federal jobs, every occupation; needs a free API key (developer.usajobs.gov): USAJOBS_API_KEY + USAJOBS_EMAIL. Newest USAJOBS_CAP.
    hdr = {"Host": "data.usajobs.gov", "User-Agent": os.environ["USAJOBS_EMAIL"], "Authorization-Key": os.environ["USAJOBS_API_KEY"]}
    out = []
    for p in range(1, USAJOBS_CAP // 500 + 1):
        r = S.get("https://data.usajobs.gov/api/search", headers=hdr, timeout=TIMEOUT, params={"ResultsPerPage": 500, "Page": p, "SortField": "opendate", "SortDirection": "desc"})
        r.raise_for_status()
        items = r.json()["SearchResult"]["SearchResultItems"]
        out += map(usajobs_job, items)
        if len(items) < 500:
            break
    return out


def oracle(s, name):  # Oracle Recruiting Cloud career site, s = "<host>/<site number>", e.g. "ecvz.fa.us2.oraclecloud.com/CX_1"; newest ORA_CAP, no descriptions
    host, site = s.split("/", 1)

    def page(off):
        res = get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions", params={
            "onlyData": "true", "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site},limit=50,offset={off},sortBy=POSTING_DATES_DESC"})
        items = res.get("items", []) if isinstance(res, dict) else []
        d = items[0] if items else {}
        return d.get("TotalJobsCount", 0), d.get("requisitionList", [])

    total, first = page(0)
    rest = pmap(lambda o: page(o)[1], range(50, min(total, ORA_CAP), 50)) if total > 50 else []
    return list({j["Id"]: job("oracle", f"oracle:{s}", j["Id"], name, j["Title"], f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}",
                              j.get("PrimaryLocation"), "REMOTE" in (j.get("WorkplaceTypeCode") or ""), j.get("PostedDate"))
                 for js in [first, *rest] for j in js if j.get("Id") and j.get("Title")}.values())


def workable(s, name):  # apply.workable.com/<slug>; many small and mid-size employers (restaurants, clinics, schools, shops)
    d = get(f"https://apply.workable.com/api/v1/widget/accounts/{s}", params={"details": "true"})
    time.sleep(1)  # their widget API answers 429 to a burst: LANES runs two boards at a time, each pausing a second
    return [job("workable", f"workable:{s}", j["shortcode"], name or d.get("name") or s, j["title"], j["url"],
                ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])), j.get("telecommuting"), j.get("published_on") or j.get("created_at"),
                desc=text(j.get("description")), team=j.get("department") or j.get("function") or j.get("industry"), emp=[j.get("employment_type")])
            for j in d.get("jobs") or [] if j.get("shortcode") and j.get("title")]


def teamtailor(s, name):  # RSS per company: <slug>.teamtailor.com
    r = S.get(f"https://{s}.teamtailor.com/jobs.rss", timeout=TIMEOUT)
    r.raise_for_status()
    ns, out = {"tt": "https://teamtailor.com/locations"}, []
    for i in ET.fromstring(r.content).iter("item"):
        loc = "; ".join(", ".join(filter(None, [l.findtext("tt:city", "", ns), l.findtext("tt:country", "", ns)])) for l in i.iterfind("tt:locations/tt:location", ns))
        out.append(job("teamtailor", f"teamtailor:{s}", i.findtext("guid"), name, i.findtext("title"), i.findtext("link"), loc,
                       i.findtext("remoteStatus") == "fully", rss_day(i.findtext("pubDate")), desc=text(i.findtext("description")),
                       team=i.findtext("tt:department", "", ns)))
    return out


PERSONIO_LEVEL = {"entry-level": "junior", "student": "intern", "executive": "manager+"}


def personio(s, name):  # XML feed per company: <slug>.jobs.personio.de/xml
    r = S.get(f"https://{s}.jobs.personio.de/xml", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for p in ET.fromstring(r.content).iter("position"):
        offices = [p.findtext("office")] + [o.text for o in p.iterfind("additionalOffices/office")]
        desc = " ".join(text(d.findtext("value")) for d in p.iterfind("jobDescriptions/jobDescription"))
        out.append(job("personio", f"personio:{s}", p.findtext("id"), name, p.findtext("name"), f"https://{s}.jobs.personio.de/job/{p.findtext('id')}",
                       "; ".join(filter(None, offices)), False, p.findtext("createdAt"), desc=desc, team=p.findtext("department"),
                       emp=[p.findtext("schedule"), p.findtext("employmentType")], hint=PERSONIO_LEVEL.get(p.findtext("seniority"))))
    return out


def recruitee(s, name):  # <slug>.recruitee.com/api/offers
    out = []
    for j in get(f"https://{s}.recruitee.com/api/offers/")["offers"]:
        sal = j.get("salary") or {}
        out.append(job("recruitee", f"recruitee:{s}", j["id"], name, j["title"], j["careers_url"], j.get("location"), j.get("remote"),
                       j.get("published_at"), desc=text((j.get("description") or "") + " " + (j.get("requirements") or "")), team=j.get("department"),
                       emp=[j.get("employment_type_code")],
                       pay=[int(sal["min"]), int(sal["max"]), sal["currency"]] if sal.get("min") and sal.get("max") and sal.get("period") == "year" else None))
    return out


PH_CAP = 10000  # newest N per Phenom site (Phenom stops at 10,000 results; Advance Auto Parts alone lists 16,000+)


def phenom_job(s, name, j):  # one record of a Phenom careers site's search -> job; the record has no page link of its own, only the apply link
    if not (j.get("jobId") and j.get("title") and j.get("applyUrl")):
        return None
    return job("phenom", f"phenom:{s}", j["jobId"], name, j["title"], j["applyUrl"].removesuffix("/apply") if "myworkdayjobs.com" in j["applyUrl"] else j["applyUrl"],
               j.get("cityStateCountry") or j.get("location"), "remote" in str(j.get("remote") or "").lower(), j.get("postedDate") or j.get("dateCreated"),
               desc=j.get("descriptionTeaser") or "", team=j.get("department") or j.get("category"), emp=[j.get("type"), j.get("employmentType")],
               tags=j.get("ml_skills") or [])


def phenom(s, name):  # Phenom People career site, s = host, e.g. "talent.lowes.com": the /widgets search the page itself calls, newest first, 100 per request
    def page(off):
        return body(lambda: S.post(f"https://{s}/widgets", timeout=TIMEOUT, headers={"Content-Type": "application/json"}, json={
            "lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
            "from": off, "jobs": True, "counts": True, "all_fields": ["category", "country", "state", "city", "type"], "size": 100, "clearAll": False,
            "jdsource": "facets", "isSliderEnable": False, "pageId": "page1", "siteType": "external", "keywords": "", "global": True, "selected_fields": {},
            "sort": {"order": "desc", "field": "postedDate"}, "locationData": {}})).get("refineSearch") or {}

    first = page(0)
    total = first.get("totalHits") or 0
    rest = pmap(page, range(100, min(total, PH_CAP), 100), 3) if total > 100 else []
    jobs = (phenom_job(s, name, j) for d in [first, *rest] for j in (d.get("data") or {}).get("jobs") or [])
    return list({j["id"]: j for j in jobs if j}.values())


def pinpoint(s, name):  # <slug>.pinpointhq.com/postings.json
    d = get(f"https://{s}.pinpointhq.com/postings.json")
    items = d.get("data") if isinstance(d, dict) else d
    out = []
    for j in (items if isinstance(items, list) else []):
        if not j.get("id") or not j.get("title"):
            continue
        l = j.get("location") or {}
        loc = l.get("name") or ", ".join(filter(None, [l.get("city"), l.get("province") or l.get("country")]))
        pay = [int(j["compensation_minimum"]), int(j["compensation_maximum"]), j.get("compensation_currency", "USD")] \
            if j.get("compensation_minimum") and j.get("compensation_maximum") and j.get("compensation_frequency") == "year" else None
        desc = " ".join(filter(None, [text(j.get("description")), text(j.get("key_responsibilities")), text(j.get("skills_knowledge_expertise"))]))
        team = (j.get("job") or {}).get("title") or ""
        jid = j["path"].split("/")[-1] if j.get("path") else str(j["id"])
        url = j.get("url") or f"https://{s}.pinpointhq.com/en/postings/{jid}"
        out.append(job("pinpoint", f"pinpoint:{s}", jid, name, j["title"], url, loc,
                       j.get("workplace_type") == "remote", None, desc=desc, team=team,
                       emp=[j.get("employment_type"), j.get("employment_type_text")], pay=pay))
    return out


def breezy(s, name):  # <slug>.breezy.hr/json
    data = get(f"https://{s}.breezy.hr/json")
    out = []
    for j in (data if isinstance(data, list) else []):
        if not j.get("id") or not j.get("name"):
            continue
        l = j.get("location") or {}
        sal = j.get("salary") or {}
        pay = None
        if isinstance(sal, dict) and sal.get("min") and sal.get("max") and "year" in (sal.get("period") or ""):
            pay = [int(sal["min"]), int(sal["max"]), sal.get("currency", "USD")]
        url = j.get("url") or f"https://{s}.breezy.hr/p/{j['id']}"
        emp_type = (j.get("type") or {}).get("name") if isinstance(j.get("type"), dict) else j.get("type")
        out.append(job("breezy", f"breezy:{s}", str(j["id"]), name or (j.get("company") or {}).get("name") or s,
                       j["name"], url, l.get("name") or "", bool(l.get("is_remote")), j.get("published_date"),
                       team=j.get("department") or "", emp=[emp_type], pay=pay))
    return out


def bamboohr(s, name):  # <slug>.bamboohr.com/careers/list
    d = get(f"https://{s}.bamboohr.com/careers/list")
    out = []
    for j in d.get("result", []):
        if not j.get("id") or not j.get("jobOpeningName"):
            continue
        loc_obj = j.get("location") or {}
        loc = ", ".join(filter(None, [loc_obj.get("city"), loc_obj.get("state")]))
        remote = bool(j.get("isRemote") or j.get("locationType") == "1")
        url = f"https://{s}.bamboohr.com/careers/{j['id']}"
        out.append(job("bamboohr", f"bamboohr:{s}", str(j["id"]), name or s, j["jobOpeningName"],
                       url, loc, remote, team=j.get("departmentLabel") or "",
                       emp=[j.get("employmentStatusLabel")]))
    return out


def rippling(s, name):  # ats.rippling.com/<slug>/jobs
    r = S.get(f"https://ats.rippling.com/{s}/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
    if not m:
        return []
    data = json.loads(m.group(1))
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    out = []
    for q in queries:
        if "job-posts" in q.get("queryKey", []):
            items = q.get("state", {}).get("data", {}).get("items", [])
            for j in items:
                jid = j.get("id")
                title = j.get("name")
                if not jid or not title:
                    continue
                locs = j.get("locations") or []
                loc = ", ".join(filter(None, [locs[0].get("city"), locs[0].get("stateCode") or locs[0].get("countryCode")])) if locs else ""
                remote = any(l.get("workplaceType") in ("REMOTE", "HYBRID") for l in locs)
                url = j.get("url") or f"https://ats.rippling.com/{s}/jobs/{jid}"
                team = (j.get("department") or {}).get("name") or ""
                out.append(job("rippling", f"rippling:{s}", str(jid), name or s, title, url, loc, remote, team=team))
    return out

def join(s, name):  # join.com/companies/<slug>
    r = S.get(f"https://join.com/companies/{s}", timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
    if not m:
        return []
    try:
        d = json.loads(m.group(1))
    except Exception:
        return []
    items = d.get("props", {}).get("pageProps", {}).get("pageData", {}).get("jobs", {}).get("items") or []
    out = []
    for j in items:
        jid = j.get("idParam") or j.get("id")
        title = j.get("title")
        if not jid or not title:
            continue
        city = (j.get("city") or {}).get("cityName")
        country = (j.get("city") or {}).get("countryName") or (j.get("country") or {}).get("iso3166")
        loc = ", ".join(filter(None, [city, country]))
        wp = (j.get("workplaceType") or "").upper()
        remote = wp in ("REMOTE", "HYBRID")
        url = f"https://join.com/companies/{s}/jobs/{jid}"
        category_name = (j.get("category") or {}).get("name")
        emp = [(j.get("employmentType") or {}).get("name")] if (j.get("employmentType") or {}).get("name") else []
        posted = (j.get("createdAt") or "")[:10] or None
        out.append(job("join", f"join:{s}", str(jid), name or s, title, url, loc, remote, posted, team=category_name, emp=emp))
    return out


def adp(s, name):  # workforcenow.adp.com career center
    d = get(f"https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions?cid={s}&timeStamp=1")
    out = []
    for r in d.get("jobRequisitions") or []:
        jid = r.get("itemID")
        title = r.get("requisitionTitle")
        if not jid or not title:
            continue
        locs = []
        for l in r.get("requisitionLocations") or []:
            addr = l.get("address") or {}
            parts = filter(None, [addr.get("cityName"), (addr.get("countrySubdivisionLevel1") or {}).get("codeValue"), addr.get("countryCode")])
            loc_str = ", ".join(parts) or (l.get("nameCode") or {}).get("shortName") or ""
            if loc_str and loc_str.strip() not in locs:
                locs.append(loc_str.strip())
        loc = "; ".join(locs)
        post_date = (r.get("postDate") or "")[:10] or None
        url = f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={s}&jobId={jid}"
        pay = None
        pgr = r.get("payGradeRange") or {}
        min_rate = (pgr.get("minimumRate") or {}).get("amountValue")
        max_rate = (pgr.get("maximumRate") or {}).get("amountValue")
        curr = (pgr.get("minimumRate") or {}).get("currencyCode") or (pgr.get("maximumRate") or {}).get("currencyCode") or "USD"
        if min_rate is not None and max_rate is not None and min_rate > 0 and max_rate > 0:
            if min_rate >= 1000 and max_rate >= 1000:
                pay = [int(min_rate), int(max_rate), curr]
            elif min_rate < 500 and max_rate < 500:
                pay = [int(min_rate * 2080), int(max_rate * 2080), curr]
        out.append(job("adp", f"adp:{s}", str(jid), name or s, title, url, loc, False, post_date, pay=pay))
    return out


def jazzhr(s, name):  # <slug>.applytojob.com/apply
    r = S.get(f"https://{s}.applytojob.com/apply", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    seen = set()
    for m in re.finditer(r'<a\s+[^>]*href=["\'](?:https://[^/]+)?/apply/([a-zA-Z0-9]+)(?:/([^"\']*))?["\'][^>]*>(.*?)</a>', r.text, re.DOTALL | re.I):
        jid, slug, raw_title = m.groups()
        if jid in seen or jid in ("apply", "v2", "jobs"):
            continue
        seen.add(jid)
        title = html.unescape(re.sub(r'<[^>]+>', ' ', raw_title)).strip()
        if not title:
            continue
        url = f"https://{s}.applytojob.com/apply/{jid}"
        out.append(job("jazzhr", f"jazzhr:{s}", jid, name or s, title, url, "", False))
    return out


def jobvite(s, name):  # jobs.jobvite.com/<slug>/jobs
    r = S.get(f"https://jobs.jobvite.com/{s}/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    seen = set()
    for m in re.finditer(r'<a\s+[^>]*href=["\'](?:https://jobs\.jobvite\.com)?/[^/]+/job/([a-zA-Z0-9]+)["\'][^>]*>(.*?)</a>', r.text, re.DOTALL | re.I):
        jid, raw_title = m.groups()
        if jid in seen:
            continue
        seen.add(jid)
        title = html.unescape(re.sub(r'<[^>]+>', ' ', raw_title)).strip()
        if not title:
            continue
        url = f"https://jobs.jobvite.com/{s}/job/{jid}"
        out.append(job("jobvite", f"jobvite:{s}", jid, name or s, title, url, "", False))
    return out


def trakstar_hire(s, name):  # <slug>.hire.trakstar.com/jobs
    r = S.get(f"https://{s}.hire.trakstar.com/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    seen = set()
    for m in re.finditer(r'<a\s+[^>]*href=["\'](?:https://[^/]+)?/jobs/([a-zA-Z0-9]+)/?["\'][^>]*>(.*?)</a>', r.text, re.DOTALL | re.I):
        jid, raw_title = m.groups()
        if jid in seen or jid.lower() in ("jobs", "apply"):
            continue
        seen.add(jid)
        raw_text = html.unescape(re.sub(r'<[^>]+>', '\n', raw_title))
        parts = [p.strip() for p in raw_text.split('\n') if p.strip()]
        if not parts:
            continue
        title = parts[0]
        loc = ", ".join(parts[1:]) if len(parts) > 1 else ""
        remote = "remote" in loc.lower() or "hybrid" in loc.lower()
        url = f"https://{s}.hire.trakstar.com/jobs/{jid}"
        out.append(job("trakstar_hire", f"trakstar_hire:{s}", jid, name or s, title, url, loc, remote))
    return out


def jobscore(s, name):  # hire.jobscore.com/jobs/<slug>/feed.atom
    r = S.get(f"https://hire.jobscore.com/jobs/{s}/feed.atom", timeout=TIMEOUT)
    if not r.ok:
        return []
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    out = []
    for entry in root.findall("atom:entry", ns):
        jid = entry.findtext("atom:id", "", ns).rsplit("/", 1)[-1]
        title = entry.findtext("atom:title", "", ns)
        if not jid or not title:
            continue
        link = f"https://careers.jobscore.com/jobs/{s}"
        for l in entry.findall("atom:link", ns):
            if l.get("rel") == "alternate" or not l.get("rel"):
                link = l.get("href") or link
        loc, dept, emp_type = "", "", []
        for cat in entry.findall("atom:category", ns):
            scheme = cat.get("scheme", "")
            term = cat.get("term", "")
            if "location" in scheme:
                loc = term
            elif "department" in scheme:
                dept = term
            elif "job_type" in scheme:
                emp_type.append(term)
        pub = entry.findtext("atom:updated", "", ns)
        pub_date = pub[:10] if pub and len(pub) >= 10 else None
        content = text(entry.findtext("atom:content", "", ns))
        out.append(job("jobscore", f"jobscore:{s}", jid, name or s, title, link, loc,
                       remote=False, posted=pub_date, desc=content, team=dept, emp=emp_type))
    return out


def zoho(s, name):  # <host>/jobs/Careers/rss
    r = S.get(f"https://{s}/jobs/Careers/rss", timeout=TIMEOUT)
    if not r.ok:
        return []
    try:
        root = ET.fromstring(r.content)
    except Exception:
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    out = []
    for item in channel.findall("item"):
        jid = item.findtext("guid")
        title = item.findtext("title")
        if not jid or not title:
            continue
        link = item.findtext("link") or f"https://{s}/jobs/Careers/{jid}"
        desc_raw = item.findtext("description") or ""
        loc = ""
        m_loc = re.search(r"Location:\s*([^<]+)", desc_raw)
        if m_loc:
            loc = m_loc.group(1).strip()
        m_cat = re.search(r"Category:\s*([^<]+)", desc_raw)
        dept = m_cat.group(1).strip() if m_cat else ""
        pub = item.findtext("pubDate")
        pub_date = None
        if pub:
            try:
                pub_date = parsedate_to_datetime(pub).date().isoformat()
            except Exception:
                pub_date = day(pub)
        desc_clean = text(desc_raw)
        out.append(job("zoho", f"zoho:{s}", jid, name or s, title, link, loc,
                       remote=False, posted=pub_date, desc=desc_clean, team=dept))
    return out


def icims(s, name):  # s = "<subdomain>.icims.com"
    out = []
    page = 0
    seen = set()
    while page < 50:
        r = S.get(f"https://{s}/jobs/search", params={"pr": page, "in_iframe": "1"}, timeout=TIMEOUT)
        if not r.ok or "iCIMS_Anchor" not in r.text:
            break
        matches = re.findall(r'<a\s+[^>]*href=["\']([^"\']*/jobs/(\d+)/[^"\']*)["\'][^>]*class=["\']iCIMS_Anchor["\'][^>]*title=["\']([^"\']+)["\']', r.text)
        if not matches:
            break
        new_jobs = 0
        for href, jid, title_attr in matches:
            if jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            title = re.sub(r'^\d+\s*-\s*', '', title_attr).strip()
            clean_url = href.split("?")[0]
            out.append(job("icims", f"icims:{s}", jid, name or s, title, clean_url, ""))
        if new_jobs == 0:
            break
        page += 1
    return out


def ultipro(s, name):  # s = "{tenant}/JobBoard/{guid}" or "{tenant}/{guid}"
    out = []
    parts = s.split("/")
    tenant = parts[0]
    guid = parts[-1]
    scope_slug = f"{tenant}/JobBoard/{guid}"
    url = f"https://recruiting2.ultipro.com/{tenant}/JobBoard/{guid}/JobBoardView/LoadSearchResults"
    page = 0
    page_size = 50
    seen = set()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    while page < 50:
        payload = {"opportunitySearch": {"Top": page_size, "Skip": page * page_size}}
        try:
            r = S.post(url, json=payload, headers=headers, timeout=TIMEOUT)
            if not r.ok:
                break
            data = r.json()
        except Exception:
            break
        opps = data.get("opportunities") or []
        if not opps:
            break
        for o in opps:
            jid = o.get("Id")
            if not jid or jid in seen:
                continue
            seen.add(jid)
            title = (o.get("Title") or "").strip()
            if not title:
                continue
            loc_parts = []
            for loc_obj in o.get("Locations") or []:
                addr = loc_obj.get("Address") or {}
                city = addr.get("City") or ""
                state = (addr.get("State") or {}).get("Code") or ""
                country = (addr.get("Country") or {}).get("Name") or ""
                p = ", ".join(filter(None, [city, state, country]))
                if p:
                    loc_parts.append(p)
            loc = "; ".join(loc_parts)
            dept = o.get("JobCategoryName") or ""
            posted_date = str(o.get("PostedDate") or "")[:10] or None
            emp = ["full_time"] if o.get("FullTime") is True else ["part_time"] if o.get("FullTime") is False else []
            desc = o.get("BriefDescription") or ""
            apply_url = f"https://recruiting2.ultipro.com/{tenant}/JobBoard/{guid}/OpportunityDetail?opportunityId={jid}"
            out.append(job("ultipro", f"ultipro:{scope_slug}", jid, name or tenant, title,
                           apply_url, loc, posted=posted_date, desc=desc, team=dept, emp=emp))
        if len(opps) < page_size:
            break
        page += 1
    return out


def dayforce(s, name):  # s = "{client}/{board_code}"
    parts = s.split("/")
    client = parts[0]
    board_code = parts[1] if len(parts) > 1 else "candidateportal"
    data = get(f"https://www.dayforcehcm.com/api/{client}/V1/JobFeeds",
               params=dict(internalJobBoardCode=board_code, includeActivePostingOnly="true"))
    out = []
    for j in (data if isinstance(data, list) else []):
        rid = str(j.get("ReferenceNumber") or "")
        title = (j.get("Title") or "").strip()
        if not rid or not title:
            continue
        comp = name or j.get("CompanyName") or client
        apply_url = j.get("ApplyUrl") or j.get("JobDetailsUrl") or f"https://jobs.dayforcehcm.com/en-US/{client}/{board_code}/jobs/{rid}/apply"
        desc = text(j.get("Description"))
        remote = bool(j.get("IsVirtualLocation")) or "remote" in title.lower()
        loc = (j.get("ClientSiteName") or "").strip()
        out.append(job("dayforce", f"dayforce:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=str(j.get("DatePosted") or "")[:10] or None,
                       desc=desc, team=j.get("ParentRequisitionCode") or ""))
    return out


def paylocity(s, name):  # s = "{guid}"
    data = get(f"https://recruiting.paylocity.com/recruiting/v2/api/feed/jobs/{s}")
    out = []
    comp = name or (data.get("displayName") if isinstance(data, dict) else "") or s
    for j in (data.get("jobs", []) if isinstance(data, dict) else []):
        rid = str(j.get("jobId") or j.get("publicJobId") or "")
        title = (j.get("title") or "").strip()
        if not rid or not title:
            continue
        apply_url = j.get("applyUrl") or f"https://recruiting.paylocity.com/recruiting/jobs/Details/{rid}"
        desc = text(j.get("description"))
        loc = (j.get("location") or "").strip()
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("paylocity", f"paylocity:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, desc=desc, team=j.get("department") or ""))
    return out


def paycom(s, name):  # s = "{clientkey}"
    url = f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={s}"
    r = S.get(url, timeout=TIMEOUT)
    if not r.ok:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    comp = name or s
    seen = set()
    for a in soup.find_all("a", href=lambda h: h and ("jobId=" in h or "/jobs/" in h)):
        href = a.get("href") or ""
        m = re.search(r"jobId=(\d+)", href) or re.search(r"/jobs/(\d+)", href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen:
            continue
        seen.add(rid)
        title = a.get_text(strip=True)
        if not title:
            continue
        apply_url = f"https://www.paycomonline.net/v4/ats/web.php/portal/{s}/jobs/{rid}"
        out.append(job("paycom", f"paycom:{s}", rid, comp, title, apply_url))
    return out


def applicantpro(s, name):
    r = S.get(f"https://{s}.applicantpro.com/jobs/", timeout=TIMEOUT)
    if not r.ok:
        return []
    m = re.search(r"domainId\s*:\s*\[?'?(\d+)", r.text)
    if not m:
        return []
    domain_id = m.group(1)
    data = get(f"https://{s}.applicantpro.com/core/jobs/{domain_id}", params={"getParams": "{}"})
    jobs_list = data.get("data", {}).get("jobs", []) if isinstance(data, dict) else []
    out = []
    comp = name or s
    for j in jobs_list:
        rid = str(j.get("id") or "")
        title = (j.get("title") or "").strip()
        if not rid or not title:
            continue
        loc = (j.get("jobLocation") or "").strip()
        wpt = (j.get("workplaceType") or "").lower()
        remote = wpt == "remote" or "remote" in loc.lower() or "remote" in title.lower()
        posted = None
        if j.get("startDateRef"):
            try:
                posted = dt.datetime.strptime(j["startDateRef"].strip(), "%b %d, %Y").date().isoformat()
            except Exception:
                posted = None
        pay = None
        min_sal = j.get("minSalary")
        max_sal = j.get("maxSalary") or min_sal
        pay_type = (j.get("payType") or "").lower()
        if min_sal:
            try:
                lo = float(str(min_sal).replace(",", "").strip())
                hi = float(str(max_sal).replace(",", "").strip())
                if pay_type == "hourly":
                    lo, hi = lo * 2080, hi * 2080
                if 20000 <= lo <= hi <= 2_000_000:
                    pay = [int(lo), int(hi), "USD"]
            except Exception:
                pay = None
        emp = (j.get("employmentType") or "",)
        team = j.get("classification") or j.get("orgTitle") or ""
        apply_url = j.get("jobUrl") or f"https://{s}.applicantpro.com/jobs/{rid}"
        out.append(job("applicantpro", f"applicantpro:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=posted, pay=pay, team=team, emp=emp))
    return out


def isolved(s, name):
    r = S.get(f"https://{s}.isolvedhire.com/jobs/", timeout=TIMEOUT)
    if not r.ok:
        return []
    m = re.search(r"domainId\s*:\s*\[?'?(\d+)", r.text)
    if not m:
        return []
    domain_id = m.group(1)
    data = get(f"https://{s}.isolvedhire.com/core/jobs/{domain_id}", params={"getParams": "{}"})
    jobs_list = data.get("data", {}).get("jobs", []) if isinstance(data, dict) else []
    out = []
    comp = name or s
    for j in jobs_list:
        rid = str(j.get("id") or "")
        title = (j.get("title") or "").strip()
        if not rid or not title:
            continue
        loc = (j.get("jobLocation") or "").strip()
        wpt = (j.get("workplaceType") or "").lower()
        remote = wpt == "remote" or "remote" in loc.lower() or "remote" in title.lower()
        posted = None
        if j.get("startDateRef"):
            try:
                posted = dt.datetime.strptime(j["startDateRef"].strip(), "%b %d, %Y").date().isoformat()
            except Exception:
                posted = None
        pay = None
        min_sal = j.get("minSalary")
        max_sal = j.get("maxSalary") or min_sal
        pay_type = (j.get("payType") or "").lower()
        if min_sal:
            try:
                lo = float(str(min_sal).replace(",", "").strip())
                hi = float(str(max_sal).replace(",", "").strip())
                if pay_type == "hourly":
                    lo, hi = lo * 2080, hi * 2080
                if 20000 <= lo <= hi <= 2_000_000:
                    pay = [int(lo), int(hi), "USD"]
            except Exception:
                pay = None
        emp = (j.get("employmentType") or "",)
        team = j.get("classification") or j.get("orgTitle") or ""
        apply_url = j.get("jobUrl") or f"https://{s}.isolvedhire.com/jobs/{rid}"
        out.append(job("isolved", f"isolved:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=posted, pay=pay, team=team, emp=emp))
    return out


def betterteam(s, name):  # s = "{subdomain}" or "{domain}"
    base = f"https://{s}" if "." in s else f"https://{s}.betterteam.com"
    r = S.get(base, timeout=TIMEOUT)
    if not r.ok:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    comp = name or s
    seen = set()
    for a in soup.select("a[href*='/jobs/'], a[href*='betterteam.com/']"):
        href = a.get("href") or ""
        m = re.search(r"/([a-zA-Z0-9_\-]+)/?$", href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen or rid in ("jobs", "apply", "contact"):
            continue
        seen.add(rid)
        title = a.get_text(strip=True)
        if not title:
            continue
        apply_url = href if href.startswith("http") else f"{base}/{rid}"
        out.append(job("betterteam", f"betterteam:{s}", rid, comp, title, apply_url))
    return out


def gohire(s, name):
    r = S.get(f"https://jobs.gohire.io/{s}", timeout=TIMEOUT)
    if not r.ok:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    comp = name or s
    hdr = soup.select_one(".job-header-left")
    if hdr and not name:
        comp_text = hdr.get_text(strip=True)
        comp = re.sub(r"^Jobs at\s+", "", comp_text).strip() or s
    seen = set()
    for a in soup.select("a.gohire-job, a[href*='jobs.gohire.io']"):
        href = a.get("href") or ""
        m = re.search(r"-(\d+)/?$", href) or re.search(r"/(\d+)/?$", href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen:
            continue
        seen.add(rid)
        title_el = a.select_one(".job-title, h3, h2")
        title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
        if not title:
            continue
        loc_el = a.select_one(".careers-location, .location")
        loc = loc_el.get_text(strip=True) if loc_el else ""
        remote = "remote" in loc.lower() or "remote" in title.lower()
        posted_el = a.select_one(".date-posted")
        posted = None
        if posted_el:
            m_date = re.search(r"Posted\s+(.+)$", posted_el.get_text(strip=True), re.IGNORECASE)
            if m_date:
                for fmt in ("%d %B, %Y", "%d %b, %Y", "%B %d, %Y", "%b %d, %Y"):
                    try:
                        posted = dt.datetime.strptime(m_date.group(1).strip(), fmt).date().isoformat()
                        break
                    except Exception:
                        pass
        apply_url = href if href.startswith("http") else f"https://jobs.gohire.io{href}"
        out.append(job("gohire", f"gohire:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=posted))
    return out


def freshteam(s, name):
    r = S.get(f"https://{s}.freshteam.com/jobs", timeout=TIMEOUT)
    if not r.ok:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    comp = name or s
    seen = set()
    for a in soup.select("a[href*='/jobs/']"):
        href = a.get("href") or ""
        m = re.search(r"/jobs/([a-zA-Z0-9_\-]+)", href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen or (rid.isdigit() and int(rid) < 10):
            continue
        seen.add(rid)
        title_el = a.select_one(".job-title") or a
        title = title_el.get_text(strip=True)
        if not title:
            continue
        loc = a.get("data-portal-location") or ""
        if not loc:
            loc_el = a.find_parent("li")
            if loc_el:
                loc_sub = loc_el.select_one(".location, .job-location, .branch")
                if loc_sub:
                    loc = loc_sub.get_text(strip=True)
        remote = a.get("data-portal-remote-location") == "true" or "remote" in loc.lower() or "remote" in title.lower()
        role_group = a.find_parent(class_=re.compile(r"job-role|department|branch"))
        team = ""
        if role_group:
            role_title = role_group.select_one(".job-role, .group-title, h2, h3")
            if role_title:
                team = role_title.get_text(strip=True)
        apply_url = f"https://{s}.freshteam.com/jobs/{rid}"
        out.append(job("freshteam", f"freshteam:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, team=team))
    return out


def gem(s, name):
    data = get(f"https://api.gem.com/job_board/v0/{s}/job_posts/")
    out = []
    comp = name or s
    for j in (data if isinstance(data, list) else []):
        rid = str(j.get("id") or "")
        title = (j.get("title") or "").strip()
        if not rid or not title:
            continue
        apply_url = j.get("absolute_url") or f"https://jobs.gem.com/{s}/{rid}"
        loc_obj = j.get("location")
        loc = loc_obj.get("name", "") if isinstance(loc_obj, dict) else str(loc_obj or "")
        loc_type = str(j.get("location_type") or "").lower()
        remote = loc_type == "remote" or "remote" in loc.lower() or "remote" in title.lower()
        depts = [d.get("name") for d in j.get("departments", []) if isinstance(d, dict) and d.get("name")]
        dept = depts[0] if depts else ""
        emp = j.get("employment_type") or ""
        desc = j.get("content_plain") or text(j.get("content"))
        posted = str(j.get("first_published_at") or j.get("created_at") or "")[:10] or None
        out.append(job("gem", f"gem:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=posted, desc=desc,
                       team=dept, emp=(emp,) if emp else ()))
    return out


def recooty(s, name):
    r = S.get(f"https://careerspage.io/{s}", timeout=TIMEOUT)
    if not r.ok:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    out = []
    comp = name or s
    seen = set()
    for a in soup.find_all("a", href=lambda h: h and f"careerspage.io/{s}/" in h):
        href = a.get("href") or ""
        path = href.split(f"careerspage.io/{s}/")[-1].strip("/")
        if not path or path in ("report", "login", "#"):
            continue
        rid = path
        if rid in seen:
            continue
        seen.add(rid)
        title = a.get_text(strip=True)
        if not title:
            continue
        card = a
        for _ in range(5):
            if card.parent:
                card = card.parent
                if "card" in " ".join(card.get("class", [])):
                    break
        card_text = card.get_text(separator=" | ", strip=True) if card else ""
        remote = "remote" in card_text.lower() or "remote" in title.lower()
        emp = ()
        for et in ("full time", "part time", "internship", "contract"):
            if et in card_text.lower():
                emp = (et,)
                break
        loc = "Remote" if remote else ""
        out.append(job("recooty", f"recooty:{s}", rid, comp, title, href,
                       location=loc, remote=remote, emp=emp))
    return out


def csod(s, name):
    parts = s.split("/")
    tenant = parts[0]
    site_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 1
    r = S.get(f"https://{tenant}.csod.com/ux/ats/careersite/{site_id}/home?c={tenant}", timeout=TIMEOUT)
    if not r.ok or "csod.context" not in r.text:
        if site_id == 1:
            site_id = 2
            r = S.get(f"https://{tenant}.csod.com/ux/ats/careersite/{site_id}/home?c={tenant}", timeout=TIMEOUT)
    if not r.ok or "csod.context" not in r.text:
        return []
    m = re.search(r"csod\.context\s*=\s*(\{.*?\});", r.text, re.DOTALL)
    if not m:
        return []
    try:
        ctx = json.loads(m.group(1))
    except Exception:
        return []
    token = ctx.get("token")
    if not token:
        return []
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    culture_name = ctx.get("cultureName", "en-US")
    culture_id = ctx.get("cultureID", 1)
    page = 1
    page_size = 50
    out = []
    comp = name or tenant
    seen = set()
    while page <= 20:
        payload = {
            "careerSiteId": site_id,
            "pageNumber": page,
            "pageSize": page_size,
            "cultureId": culture_id,
            "cultureName": culture_name,
            "searchText": "",
        }
        try:
            res = S.post(f"https://{tenant}.csod.com/services/x/career-site/v1/search", headers=headers, json=payload, timeout=TIMEOUT)
            if not res.ok:
                break
            d = res.json()
        except Exception:
            break
        reqs = d.get("data", {}).get("requisitions", [])
        if not reqs:
            break
        for req in reqs:
            rid = str(req.get("requisitionId") or "")
            title = (req.get("displayJobTitle") or "").strip()
            if not rid or not title or rid in seen:
                continue
            seen.add(rid)
            locs = req.get("locations") or []
            loc_parts = []
            for l in locs:
                if isinstance(l, dict):
                    loc_parts.append(", ".join(filter(None, [l.get("city"), l.get("state"), l.get("country")])))
            loc = "; ".join(loc_parts)
            remote = "remote" in loc.lower() or "remote" in title.lower()
            posted = None
            if req.get("postingEffectiveDate"):
                try:
                    posted = dt.datetime.strptime(req["postingEffectiveDate"].strip(), "%m/%d/%Y").date().isoformat()
                except Exception:
                    posted = None
            apply_url = f"https://{tenant}.csod.com/ux/ats/careersite/4/home/requisition/{rid}?c={tenant}"
            out.append(job("csod", f"csod:{s}", rid, comp, title, apply_url,
                           location=loc, remote=remote, posted=posted))
        if len(reqs) < page_size:
            break
        page += 1
    return out


def taleo(s, name):
    parts = s.split("/")
    company = parts[0]
    section = parts[1] if len(parts) > 1 else "ex"
    r = S.get(f"https://{company}.taleo.net/careersection/{section}/joblist.ftl?lang=en", timeout=TIMEOUT)
    if not r.ok or "ftlform" not in r.text:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form")
    if not form:
        return []
    inputs = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name")}
    raw = inputs.get("initialHistory", "")
    decoded = urllib.parse.unquote(urllib.parse.unquote(raw))
    jobs = re.findall(r'!(\d+)!\|!([^!|]+)!\|!\1!\|!\2!\|!(?:\1!\|!)+([^!|]+)!\|!([^!|]+)!', decoded)
    if not jobs:
        jobs = [(m[0], m[1], "", "") for m in re.findall(r'!(\d+)!\|!([^!|]+)!\|!\1!\|!\2!', decoded)]
    out = []
    comp = name or company
    seen = set()
    for match in jobs:
        jid, title = match[0].strip(), match[1].strip()
        loc = match[3].strip() if len(match) > 3 and match[3].strip() != match[2].strip() else ""
        if not jid or not title or jid == title or jid in seen:
            continue
        seen.add(jid)
        apply_url = f"https://{company}.taleo.net/careersection/{section}/jobdetail.ftl?job={jid}"
        remote = "remote" in title.lower() or "remote" in loc.lower()
        out.append(job("taleo", f"taleo:{s}", jid, comp, title, apply_url, location=loc, remote=remote))
    return out


def bullhorn(s, name):  # s = "{cls}/{token}"
    parts = s.split("/")
    cls = parts[0]
    token = parts[1] if len(parts) > 1 else s
    out = []
    comp = name or token
    start = 0
    page_size = 100
    seen = set()
    while start < 500:
        url = f"https://public-rest{cls}.bullhornstaffing.com/rest-services/{token}/search/JobOrder"
        params = {
            "query": "(isOpen:1)",
            "fields": "id,title,publishedCategory(id,name),address(city,state),employmentType,dateLastPublished,publicDescription",
            "count": page_size,
            "start": start,
        }
        try:
            r = S.get(url, params=params, timeout=TIMEOUT)
            if not r.ok:
                break
            d = r.json()
        except Exception:
            break
        data_list = d.get("data", [])
        if not data_list:
            break
        for j in data_list:
            rid = str(j.get("id") or "")
            title = (j.get("title") or "").strip()
            if not rid or not title or rid in seen:
                continue
            seen.add(rid)
            addr = j.get("address") or {}
            loc = ", ".join(filter(None, [addr.get("city"), addr.get("state")]))
            remote = "remote" in loc.lower() or "remote" in title.lower()
            cat = j.get("publishedCategory") or {}
            dept = cat.get("name") or ""
            emp = (j.get("employmentType") or "",)
            desc = text(j.get("publicDescription"))
            posted = day(j.get("dateLastPublished"))
            apply_url = f"https://careers.bullhornstaffing.com/{s}/jobs/{rid}"
            out.append(job("bullhorn", f"bullhorn:{s}", rid, comp, title, apply_url,
                           location=loc, remote=remote, posted=posted, desc=desc,
                           team=dept, emp=emp))
        if len(data_list) < page_size:
            break
        start += page_size
    return out


def successfactors(s, name):
    parts = s.split("/")
    cls = parts[0]
    company = parts[1] if len(parts) > 1 else s
    url = f"https://career{cls}.successfactors.com/career?company={company}&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH"
    try:
        r = S.get(url, timeout=TIMEOUT)
        if not r.ok:
            return []
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.find_all("tr", class_=lambda c: c and "data-row" in c.lower())
    out = []
    comp = name or company
    seen = set()
    for tr in rows:
        a = tr.find("a", class_=lambda c: c and "jobtitle" in c.lower())
        if not a:
            continue
        title = a.text.strip()
        href = a.get("href") or ""
        jid = href.strip("/").split("/")[-1] if href else ""
        if not jid or not title or jid in seen:
            continue
        seen.add(jid)
        dept_el = tr.find(class_=lambda c: c and "department" in c.lower())
        team = dept_el.text.strip() if dept_el else ""
        loc_el = tr.find(class_=lambda c: c and "location" in c.lower())
        loc = loc_el.text.strip() if loc_el else ""
        date_el = tr.find(class_=lambda c: c and "date" in c.lower())
        date_str = date_el.text.strip() if date_el else ""
        posted = None
        if date_str:
            try:
                posted = dt.datetime.strptime(date_str, "%b %d, %Y").date().isoformat()
            except Exception:
                posted = None
        apply_url = href if href.startswith("http") else f"https://career{cls}.successfactors.com{href}"
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("successfactors", f"successfactors:{s}", jid, comp, title, apply_url, location=loc, remote=remote, team=team, posted=posted))
    return out


def avature(s, name):
    comp = name or s
    out = []
    seen = set()
    offset = 0
    limit = 50
    while offset < 500:
        url = f"https://{s}.avature.net/careers/SearchJobs/?jobRecordsPerPage={limit}&jobOffset={offset}"
        try:
            r = S.get(url, timeout=TIMEOUT)
            if not r.ok:
                break
        except Exception:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/JobDetail/"))
        new_jobs = 0
        for a in links:
            href = a.get("href") or ""
            title = a.text.strip()
            if not title or title.lower() == "apply":
                continue
            jid = href.strip("/").split("/")[-1]
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            parent = a.find_parent("li") or a.find_parent("div")
            loc = ""
            if parent:
                loc_el = parent.find(class_=lambda c: c and ("location" in c.lower() or "subtitle" in c.lower() or "meta" in c.lower()))
                if loc_el:
                    loc = loc_el.text.strip().replace("\n", " ")
            apply_url = href if href.startswith("http") else f"https://{s}.avature.net{href}"
            remote = "remote" in loc.lower() or "remote" in title.lower()
            out.append(job("avature", f"avature:{s}", jid, comp, title, apply_url, location=loc, remote=remote))
            new_jobs += 1
        if new_jobs == 0:
            break
        offset += limit
    return out


def recruiterflow(s, name):
    try:
        r = S.get(f"https://recruiterflow.com/{s}/jobs", timeout=TIMEOUT)
        if not r.ok or "jobsList" not in r.text:
            return []
    except Exception:
        return []
    m = re.search(r"window\.jobsList\s*=\s*(\{.*?\});", r.text, re.DOTALL)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    out = []
    comp = name or s
    seen = set()
    for dept, jobs in data.get("department", []):
        for j in jobs:
            jid = str(j.get("job_id") or "")
            title = (j.get("job_name") or "").strip()
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            loc = (j.get("details") or "").strip()
            rel_link = j.get("apply_link") or f"{s}/jobs/{jid}"
            url = rel_link if rel_link.startswith("http") else f"https://recruiterflow.com/{rel_link.lstrip('/')}"
            remote_type = str(j.get("remote_type") or "").lower()
            remote = "remote" in remote_type or "remote" in title.lower() or "remote" in loc.lower()
            posted = None
            if j.get("last_opened"):
                try:
                    posted = j["last_opened"].split("T")[0]
                except Exception:
                    posted = None
            out.append(job("recruiterflow", f"recruiterflow:{s}", jid, comp, title, url, location=loc, remote=remote, team=dept or "", posted=posted))
    return out


def manatal(s, name):
    comp = name or s
    out = []
    seen = set()
    url = f"https://api.manatal.com/open/v3/career-page/{s}/jobs/?page_size=100"
    while url and len(out) < 500:
        try:
            r = S.get(url, timeout=TIMEOUT)
            if not r.ok:
                break
            data = r.json()
        except Exception:
            break
        results = data.get("results") or []
        if not results:
            break
        for j in results:
            jid = str(j.get("id") or "")
            title = (j.get("position_name") or "").strip()
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            loc = (j.get("location_display") or "").strip()
            hash_id = j.get("hash") or jid
            apply_url = f"https://www.careers-page.com/{s}/job/{hash_id}"
            remote = bool(j.get("is_remote")) or "remote" in loc.lower() or "remote" in title.lower()
            team = (j.get("organization_name") or "").strip()
            out.append(job("manatal", f"manatal:{s}", jid, comp, title, apply_url, location=loc, remote=remote, team=team))
        url = data.get("next")
    return out


def loxo(s, name):
    try:
        r = S.get(f"https://app.loxo.co/{s}", timeout=TIMEOUT)
        if not r.ok:
            return []
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    cards = soup.find_all(class_="jobs-listing-card")
    out = []
    comp = name or s
    seen = set()
    for card in cards:
        t_el = card.find(class_="job-title")
        if not t_el:
            continue
        title = t_el.text.strip()
        href = t_el.get("href") or ""
        jid = href.strip("/").split("/")[-1] if href else ""
        if not jid or not title or jid in seen:
            continue
        seen.add(jid)
        loc_el = card.find(class_="job-location")
        loc = loc_el.text.replace("location_on", "").strip() if loc_el else ""
        type_el = card.find(class_="job-type")
        emp = (type_el.text.strip(),) if type_el else ()
        remote = "remote" in loc.lower() or "remote" in title.lower()
        apply_url = f"https://app.loxo.co{href}" if href.startswith("/") else href
        out.append(job("loxo", f"loxo:{s}", jid, comp, title, apply_url, location=loc, remote=remote, emp=emp))
    return out


def careerplug(s, name):
    try:
        r = S.get(f"https://{s}.careerplug.com/jobs", timeout=TIMEOUT)
        if not r.ok:
            return []
    except Exception:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    links = soup.find_all("a", href=re.compile(r"/jobs/\d+"))
    out = []
    comp = name or s
    seen = set()
    for a in links:
        href = a.get("href") or ""
        m = re.search(r"/jobs/(\d+)", href)
        if not m:
            continue
        jid = m.group(1)
        if jid in seen:
            continue
        seen.add(jid)
        text_lines = [l.strip() for l in a.text.splitlines() if l.strip()]
        title = text_lines[0] if text_lines else "Open Role"
        if title.lower() in ("español", "apply", "view job"):
            continue
        loc = ""
        for i, line in enumerate(text_lines):
            if "location:" in line.lower() and i + 1 < len(text_lines):
                loc = text_lines[i + 1]
        remote = "remote" in loc.lower() or "remote" in title.lower()
        apply_url = f"https://{s}.careerplug.com/jobs/{jid}"
        out.append(job("careerplug", f"careerplug:{s}", jid, comp, title, apply_url, location=loc, remote=remote))
    return out


def comeet(s, name):
    parts = s.split("/")
    uid = parts[0]
    token = parts[1] if len(parts) > 1 else ""
    url = f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions?token={token}&details=false" if token else f"https://www.comeet.co/careers-api/2.0/company/{uid}/positions"
    try:
        r = S.get(url, timeout=TIMEOUT)
        if not r.ok:
            return []
        data = r.json()
    except Exception:
        return []
    out = []
    comp = name or uid
    seen = set()
    for j in data:
        jid = str(j.get("uid") or "")
        title = (j.get("name") or "").strip()
        if not jid or not title or jid in seen:
            continue
        seen.add(jid)
        loc_data = j.get("location") or {}
        loc = ", ".join(filter(None, [loc_data.get("city"), loc_data.get("state"), loc_data.get("country")])) if isinstance(loc_data, dict) else ""
        remote = bool(loc_data.get("is_remote")) if isinstance(loc_data, dict) else False
        apply_url = j.get("url_active_page") or j.get("url_comeet_hosted_page") or f"https://www.comeet.com/jobs/{uid}/{jid}"
        team = (j.get("department") or "").strip()
        out.append(job("comeet", f"comeet:{s}", jid, comp, title, apply_url, location=loc, remote=remote, team=team))
    return out


def crelate(s, name):  # RSS feed: https://jobs.crelate.com/portal/{s}/rss
    try:
        r = S.get(f"https://jobs.crelate.com/portal/{s}/rss", timeout=TIMEOUT)
        if not r.ok:
            return []
        root = ET.fromstring(r.content)
    except Exception:
        return []
    channel = root.find("channel")
    if channel is None:
        return []
    out = []
    comp = name or s
    ns = f"https://jobs.crelate.com/portal/{s}"
    seen = set()
    for item in channel.findall("item"):
        guid = item.findtext("guid") or item.findtext("link") or ""
        rid = guid.rstrip("/").split("/")[-1] if guid else ""
        title = (item.findtext("title") or "").strip()
        if not rid or not title or rid in seen:
            continue
        seen.add(rid)
        link = item.findtext("link") or guid
        desc = text(item.findtext("description") or "")
        cat = (item.findtext("category") or "").strip()
        pub = item.findtext("pubDate") or ""
        loc = (item.findtext(f"{{{ns}}}location") or "").strip()
        if not loc:
            for child in item:
                if "location" in child.tag.lower() and child.text:
                    loc = child.text.strip()
                    break
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("crelate", f"crelate:{s}", rid, comp, title, link, loc, remote=remote, posted=pub, desc=desc, team=cat))
    return out


def clearcompany(s, name):  # REST API: https://careers-api.clearcompany.com/v1/{s}
    out = []
    try:
        data = get(f"https://careers-api.clearcompany.com/v1/{s}")
    except Exception:
        return []
    results = data.get("results") or []
    comp = name or s
    seen = set()
    for j in results:
        rid = str(j.get("id") or j.get("userDefinedId") or "")
        title = (j.get("positionTitle") or "").strip()
        if not rid or not title or rid in seen:
            continue
        seen.add(rid)
        cname = name or j.get("brandName") or comp
        link = j.get("applyLink") or f"https://apply.clearcompany.com/jobs?jobId={rid}"
        loc = (j.get("location") or "").strip()
        remote = "remote" in loc.lower() or "remote" in title.lower()
        team = (j.get("departmentName") or "").strip()
        desc = text(j.get("description") or "")
        posted = j.get("postedDate") or j.get("openDate") or ""
        pay = None
        s_low = j.get("salaryRangeLow")
        s_high = j.get("salaryRangeHigh")
        s_type = j.get("salaryType")
        if s_low and s_high:
            try:
                pay = [int(float(s_low)), int(float(s_high)), s_type or "USD"]
            except (ValueError, TypeError):
                pay = None
        out.append(job("clearcompany", f"clearcompany:{s}", rid, cname, title, link, loc, remote=remote, posted=posted, desc=desc, team=team, pay=pay))
    return out


def pageup(s, name):  # http://careers.pageuppeople.com/{s}/caw/en/
    out = []
    try:
        r = S.get(f"http://careers.pageuppeople.com/{s}/caw/en/", timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s
    seen = set()
    for a in soup.select("a.job-link"):
        href = a.get("href") or ""
        title = a.get_text(strip=True)
        if not href or not title:
            continue
        parts = [p for p in href.split("/") if p]
        rid = ""
        for i, p in enumerate(parts):
            if p == "job" and i + 1 < len(parts):
                rid = parts[i + 1]
                break
        if not rid:
            rid = href
        if rid in seen:
            continue
        seen.add(rid)
        full_url = f"http://careers.pageuppeople.com{href}" if href.startswith("/") else href
        tr = a.find_parent("tr") or a.find_parent("li")
        team = ""
        posted = ""
        if tr:
            cells = tr.find_all("td")
            if len(cells) >= 3:
                team = cells[1].get_text(strip=True)
                posted = cells[-1].get_text(strip=True)
        remote = "remote" in title.lower()
        out.append(job("pageup", f"pageup:{s}", rid, comp, title, full_url, location="", remote=remote, posted=posted, team=team))
    return out


def catsone(s, name):  # https://{s}.catsone.com/careers
    url = f"https://{s}.catsone.com/careers" if "." not in s else f"https://{s}/careers"
    try:
        r = S.get(url, timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s
    out = []
    seen = set()
    for a in soup.select("a.table-row, a[href*='/jobs/']"):
        href = a.get("href") or ""
        if "/jobs/" not in href or "/careers/" not in href:
            continue
        r_part = href.split("/careers/", 1)[1].split("?")[0]
        rid = r_part.split("-")[0].strip("/")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        t_el = a.find(class_="title-cell") or a.find("div", class_=lambda c: c and "title" in c)
        title = (t_el.get_text(strip=True) if t_el else a.get_text(strip=True)).strip()
        if not title:
            continue
        cat_el = a.find("div", attrs={"data-label": "Category"})
        team = cat_el.get_text(strip=True) if cat_el else ""
        loc_el = a.find("div", attrs={"data-label": "Location"})
        loc = loc_el.get_text(strip=True) if loc_el else ""
        remote = "remote" in loc.lower() or "remote" in title.lower()
        apply_url = f"https://{s}.catsone.com/careers/{rid}" if "." not in s else f"https://{s}/careers/{rid}"
        out.append(job("catsone", f"catsone:{s}", rid, comp, title, apply_url, location=loc, remote=remote, team=team))
    return out


def talentlyft(s, name):  # https://api.talentlyft.com/v2/public/{s}/jobs
    out = []
    comp = name or s
    page = 1
    seen = set()
    while True:
        try:
            r = S.get(f"https://api.talentlyft.com/v2/public/{s}/jobs", params={"page": page, "perPage": 100}, timeout=TIMEOUT)
            if not r.ok:
                break
            data = r.json()
        except Exception:
            break
        results = data.get("Results") or []
        if not results:
            break
        for j in results:
            shortlink = j.get("ShortlinkUrl") or ""
            rid = shortlink.strip("/").split("/")[-1] if shortlink else str(j.get("Id") or "")
            title = (j.get("Title") or "").strip()
            if not rid or not title or rid in seen:
                continue
            seen.add(rid)
            loc = ", ".join(filter(None, [j.get("City"), j.get("Country")]))
            team = (j.get("Department") or "").strip()
            tags = j.get("JobTags") or []
            apply_url = shortlink or f"https://{s}.talentlyft.com/o/{rid}"
            remote = "remote" in loc.lower() or "remote" in title.lower() or any("remote" in str(t).lower() for t in tags)
            out.append(job("talentlyft", f"talentlyft:{s}", rid, comp, title, apply_url, location=loc, remote=remote, team=team, tags=tags, country=j.get("Country")))
        pages = data.get("Pages") or {}
        if not pages.get("Next") or page >= 10:
            break
        page += 1
    return out


def jobadder(s, name):  # https://clientapps.jobadder.com/{s}
    try:
        r = S.get(f"https://clientapps.jobadder.com/{s}", timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s.split("/")[-1]
    s_parts = set(s.split("/"))
    out = []
    seen = set()
    for a in soup.find_all("a", class_="viewjob"):
        href = a.get("href") or ""
        parts = [p for p in href.split("/") if p]
        digits = [p for p in parts if p.isdigit() and p not in s_parts]
        rid = digits[-1] if digits else parts[-1]
        if not rid or rid in seen:
            continue
        seen.add(rid)
        title = a.get_text(strip=True)
        if not title:
            continue
        card = a.find_parent(class_="pricing-item") or a.find_parent("div")
        posted = ""
        team = ""
        loc = ""
        if card:
            date_el = card.find("sub")
            posted = date_el.get_text(strip=True) if date_el else ""
            items = [li.get_text(strip=True) for li in card.find_all("li") if li.get_text(strip=True)]
            team = items[0] if len(items) >= 1 else ""
            loc = items[2] if len(items) >= 3 else (items[1] if len(items) >= 2 else "")
        apply_url = f"https://clientapps.jobadder.com/{s}/{rid}"
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("jobadder", f"jobadder:{s}", rid, comp, title, apply_url, location=loc, remote=remote, posted=posted, team=team))
    return out


def jobsoid(s, name):  # https://{s}.jobsoid.com
    try:
        r = S.get(f"https://{s}.jobsoid.com", timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s
    out = []
    seen = set()
    for a in soup.find_all("a", class_="jobDetailsLink"):
        href = a.get("href") or ""
        parts = [p for p in href.split("/") if p]
        digits = [p for p in parts if p.isdigit()]
        rid = digits[0] if digits else (parts[1] if len(parts) > 1 else "")
        if not rid or rid in seen:
            continue
        seen.add(rid)
        title = a.get_text(strip=True)
        if not title:
            continue
        card = a.find_parent("div", class_="row") or a.find_parent("li")
        loc = ""
        if card:
            sub = card.find(class_="sub-title")
            if sub:
                loc = sub.get_text(strip=True)
        apply_url = f"https://{s}.jobsoid.com/apply/{rid}"
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("jobsoid", f"jobsoid:{s}", rid, comp, title, apply_url, location=loc, remote=remote))
    return out


def tribepad(s, name):  # https://{s}.tribepad.com/v2/jobs
    try:
        r = S.get(f"https://{s}.tribepad.com/v2/jobs", timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s
    out = []
    seen = set()
    for a in soup.find_all("a", href=lambda h: h and "record=" in h):
        href = a.get("href") or ""
        m = re.search(r"record=(\d+)", href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen:
            continue
        seen.add(rid)
        t_el = a.find(class_="sitebuilder-job-results-item-title") or a.find("h3")
        title = t_el.get_text(strip=True) if t_el else ""
        if not title:
            continue
        meta = a.find(class_="sitebuilder-job-results-item-meta")
        loc = ""
        team = ""
        emp = ()
        pay = None
        if meta:
            for div in meta.find_all("div"):
                txt = div.get_text(strip=True)
                if div.find("i", class_=lambda c: c and "map-marker" in c):
                    loc = txt
                elif div.find("i", class_=lambda c: c and "wallet" in c):
                    pay = find_pay(txt)
                elif div.find("i", class_=lambda c: c and "tag" in c):
                    team = txt
                elif div.find("i", class_=lambda c: c and "clock" in c):
                    emp = (txt,)
        apply_url = f"https://{s}.tribepad.com/members/modules/job/detail.php?record={rid}"
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("tribepad", f"tribepad:{s}", rid, comp, title, apply_url, location=loc, remote=remote, team=team, emp=emp, pay=pay))
    return out


def applicantstack(s, name):  # https://{s}.applicantstack.com/x/openings
    try:
        r = S.get(f"https://{s}.applicantstack.com/x/openings", timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s
    out = []
    seen = set()
    for a in soup.find_all("a", href=lambda h: h and "/x/detail/" in h):
        href = a.get("href") or ""
        parts = [p for p in href.split("/") if p]
        rid = parts[-1]
        if not rid or rid in seen:
            continue
        seen.add(rid)
        title = a.get_text(strip=True)
        if not title:
            continue
        tr = a.find_parent("tr")
        loc = ""
        team = ""
        if tr:
            tds = [td.get_text(strip=True) for td in tr.find_all("td")]
            if len(tds) >= 3:
                team = tds[1]
                loc = tds[2]
            elif len(tds) == 2:
                loc = tds[1]
        apply_url = f"https://{s}.applicantstack.com/x/detail/{rid}"
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("applicantstack", f"applicantstack:{s}", rid, comp, title, apply_url, location=loc, remote=remote, team=team))
    return out


ATS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, smartrecruiters=smartrecruiters, workday=workday, microsoft=microsoft, eightfold=eightfold,
           amazon=amazon, apple=apple, teamtailor=teamtailor, personio=personio, recruitee=recruitee, oracle=oracle, workable=workable, phenom=phenom,
           pinpoint=pinpoint, breezy=breezy, bamboohr=bamboohr, rippling=rippling, join=join, adp=adp, jazzhr=jazzhr, jobvite=jobvite, trakstar_hire=trakstar_hire, jobscore=jobscore, zoho=zoho, icims=icims, ultipro=ultipro, dayforce=dayforce, paylocity=paylocity,
           paycom=paycom, applicantpro=applicantpro, isolved=isolved, betterteam=betterteam, gohire=gohire, freshteam=freshteam, gem=gem, recooty=recooty, csod=csod, taleo=taleo, bullhorn=bullhorn,
           successfactors=successfactors, avature=avature, recruiterflow=recruiterflow, manatal=manatal, loxo=loxo, careerplug=careerplug, comeet=comeet,
           crelate=crelate, clearcompany=clearcompany, pageup=pageup,
           catsone=catsone, talentlyft=talentlyft, jobadder=jobadder, jobsoid=jobsoid, tribepad=tribepad, applicantstack=applicantstack)
FEEDS = dict(remotive=remotive, remoteok=remoteok, arbeitnow=arbeitnow, himalayas=himalayas, jobicy=jobicy, weworkremotely=weworkremotely,
             getonbrd=getonbrd, workingnomads=workingnomads, jobtech=jobtech, cryptojobs=cryptojobs, themuse=themuse)
if os.environ.get("USAJOBS_API_KEY") and os.environ.get("USAJOBS_EMAIL"):  # optional: skipped (not failed) without a key
    FEEDS["usajobs"] = usajobs


# ---------- storage ----------
URLS = dict(greenhouse="https://job-boards.greenhouse.io/{s}/jobs/{r}", lever="https://jobs.lever.co/{s}/{r}/apply",
            ashby="https://jobs.ashbyhq.com/{s}/{r}/application", smartrecruiters="https://jobs.smartrecruiters.com/{s}/{r}",
            workday="https://{s}{r}", microsoft="https://apply.careers.microsoft.com/careers/job/{r}",
            apple="https://jobs.apple.com/en-us/details/{r}",
            pinpoint="https://{s}.pinpointhq.com/en/postings/{r}",
            breezy="https://{s}.breezy.hr/p/{r}",
            bamboohr="https://{s}.bamboohr.com/careers/{r}",
            rippling="https://ats.rippling.com/{s}/jobs/{r}",
            join="https://join.com/companies/{s}/jobs/{r}",
            adp="https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={s}&jobId={r}",
            jazzhr="https://{s}.applytojob.com/apply/{r}",
            jobvite="https://jobs.jobvite.com/{s}/job/{r}",
            trakstar_hire="https://{s}.hire.trakstar.com/jobs/{r}",
            jobscore="https://careers.jobscore.com/jobs/{s}/{r}",
            zoho="https://{s}/jobs/Careers/{r}",
            icims="https://{s}/jobs/{r}/job", ultipro="https://recruiting2.ultipro.com/{s}/OpportunityDetail?opportunityId={r}",
            dayforce="https://jobs.dayforcehcm.com/en-US/{s}/jobs/{r}/apply", paylocity="https://recruiting.paylocity.com/recruiting/jobs/Details/{r}",
            paycom="https://www.paycomonline.net/v4/ats/web.php/portal/{s}/jobs/{r}", applicantpro="https://{s}.applicantpro.com/jobs/{r}",
            isolved="https://{s}.isolvedhire.com/jobs/{r}", betterteam="https://{s}/{r}", gohire="https://jobs.gohire.io/{s}/{r}",
            freshteam="https://{s}.freshteam.com/jobs/{r}", gem="https://jobs.gem.com/{s}/{r}", recooty="https://careerspage.io/{s}/{r}",
            csod="https://{s}.csod.com/ux/ats/careersite/4/home/requisition/{r}?c={s}", taleo="https://{s}.taleo.net/careersection/ex/jobdetail.ftl?job={r}",
            bullhorn="https://careers.bullhornstaffing.com/{s}/jobs/{r}",
            successfactors="https://career{s}.successfactors.com/job/{r}",
            avature="https://{s}.avature.net/careers/JobDetail/{r}",
            recruiterflow="https://recruiterflow.com/{s}/jobs/{r}",
            manatal="https://www.careers-page.com/{s}/job/{r}",
            loxo="https://app.loxo.co/job/{r}",
            careerplug="https://{s}.careerplug.com/jobs/{r}",
            comeet="https://www.comeet.com/jobs/{s}/{r}",
            crelate="https://jobs.crelate.com/portal/{s}/job/{r}",
            clearcompany="https://apply.clearcompany.com/jobs?jobId={r}",
            pageup="http://careers.pageuppeople.com/{s}/caw/en/job/{r}",
            catsone="https://{s}.catsone.com/careers/{r}",
            talentlyft="https://{s}.talentlyft.com/o/{r}",
            jobadder="https://clientapps.jobadder.com/{s}/{r}",
            jobsoid="https://{s}.jobsoid.com/apply/{r}",
            tribepad="https://{s}.tribepad.com/members/modules/job/detail.php?record={r}",
            applicantstack="https://{s}.applicantstack.com/x/detail/{r}")
DEFAULTS = dict(company="", closed_at=None, location="", country="", team="", remote=False, years=None, skills=[], pay=None, employment="", posted=None, reposts=0)


def url_of(id):  # apply URL is derivable for ATS jobs, so it is only stored when it differs
    src, *rest = id.split(":")
    return URLS[src].format(s=rest[0], r=":".join(rest[1:])) if src in URLS else None


def load(scopes=None, open_only=False):
    # split("\n"), not splitlines(): titles can contain U+2028 etc., which splitlines() would cut on
    db = {}
    if scopes is not None:
        existing = set(os.listdir(DIR)) if DIR.exists() else set()
        target_names = {s.replace(":", "_").replace("/", "_") + ".jsonl" for s in scopes}
        files = [DIR / name for name in (target_names & existing)]
    else:
        files = [DIR / f for f in os.listdir(DIR) if f.endswith(".jsonl")] if DIR.exists() else []

    def read_one(f):
        items = []
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            return items
        for l in content.split("\n"):
            l = l.strip()
            if not l:
                continue
            if open_only and '"closed_at":' in l:
                continue
            try:
                j = json.loads(l)
            except Exception:
                continue
            if open_only and j.get("closed_at") is not None:
                continue
            j.setdefault("source", j["id"].split(":")[0])
            j.setdefault("url", url_of(j["id"]))
            j.setdefault("level", level(j["title"], j.get("years")))  # derived, so a better classifier fixes old rows too
            j.setdefault("category", category(j["title"], j.get("team")))
            j.setdefault("country", country(j.get("location", "")))
            for k, v in DEFAULTS.items():
                j.setdefault(k, list(v) if isinstance(v, list) else v)
            items.append((j["id"], j))
        return items

    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(read_one, files):
            for jid, j in batch:
                db[jid] = j
    return db


def slim(j):  # drop empty values and anything derivable from other fields (load() restores it)
    r = {k: v for k, v in j.items() if v is not None and v is not False and v != "" and v != []}
    r.pop("source", None)
    if not r.get("reposts"):
        r.pop("reposts", None)
    if "url" in r and r["url"] == url_of(j["id"]):
        del r["url"]
    if r.get("level") == level(j["title"], j.get("years")):
        del r["level"]
    if r.get("category") == category(j["title"], j.get("team")):
        del r["category"]
    if r.get("country") == country(j.get("location", "")):
        del r["country"]
    return r


def save(db, target_scopes=None):  # one file per board; sorted + compact + LF => small files, diffs show only real changes
    DIR.mkdir(parents=True, exist_ok=True)
    if target_scopes is not None:
        target_names = {s.replace(":", "_").replace("/", "_") for s in target_scopes}
        shards = {}
        for i in sorted(db):
            name = i.rsplit(":", 1)[0].replace(":", "_").replace("/", "_")
            if name in target_names:
                shards.setdefault(name, []).append(json.dumps(slim(db[i]), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
        for name in target_names:
            lines = shards.get(name, [])
            target_file = DIR / f"{name}.jsonl"
            if lines:
                with open(target_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(lines) + "\n")
            elif target_file.exists():
                target_file.unlink()
        return

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
    tkey = lambda i, o: (i.rsplit(":", 1)[0], re.sub(r"\W", "", str(o.get("title") or "").lower()))
    closed = {}  # (board, title) -> how many jobs with that title already closed there: a new one is a repost
    for i, o in db.items():
        if o.get("closed_at"):
            closed[tkey(i, o)] = closed.get(tkey(i, o), 0) + 1
    for jobs in fetched.values():
        for j in jobs:
            old = db.get(j["id"])
            db[j["id"]] = {**j, "first_seen": old["first_seen"] if old else today, "closed_at": None,
                           "reposts": old.get("reposts", 0) if old else closed.get(tkey(j["id"], j), 0)}
    for i, o in db.items():
        if o.get("closed_at") is None and i not in seen and i.rsplit(":", 1)[0] in fetched:
            o["closed_at"] = today
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
    keep = {i: o for i, o in db.items() if not (o.get("closed_at") and isinstance(o["closed_at"], str) and o["closed_at"] < cutoff)}
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


# Every provider is its own lane and all lanes start at once, so a slow or rate-limited provider only ever delays its own boards, never someone else's.
# The number is how many boards of that provider are fetched at the same time (conservative defaults, not published limits); a provider not listed
# here (Arbeitnow, every feed, Amazon, Microsoft...) gets one worker, i.e. one board at a time. Workable and Arbeitnow also pause a second between requests.
LANE_OF = {"microsoft": "eightfold"}  # Microsoft's careers site is Eightfold-hosted: one lane, one site at a time, so their bot protection never sees a burst
LANES = {"greenhouse": 24, "lever": 16, "ashby": 16, "smartrecruiters": 12, "workday": 32, "teamtailor": 4, "oracle": 8, "workable": 2, "personio": 12, "recruitee": 2, "phenom": 4, "apple": 1, "pinpoint": 8, "breezy": 8, "bamboohr": 16, "rippling": 8, "join": 16, "adp": 16, "jazzhr": 16, "jobvite": 8, "trakstar_hire": 8, "jobscore": 8, "zoho": 16, "icims": 8, "ultipro": 16, "dayforce": 16, "paylocity": 16, "paycom": 16, "applicantpro": 8, "isolved": 8, "betterteam": 8, "gohire": 8, "freshteam": 4, "gem": 4, "recooty": 4, "csod": 4, "taleo": 2, "bullhorn": 8, "successfactors": 8, "avature": 8, "recruiterflow": 8, "manatal": 8, "loxo": 8, "careerplug": 8, "comeet": 8, "crelate": 8, "clearcompany": 8, "pageup": 8}
RUN_BUDGET = 16 * 60  # seconds: graceful cutoff so in-flight tasks finish, disk saves, and shards exit cleanly within CI limits


def run_all(tasks, budget=RUN_BUDGET):
    """tasks: [(key, fn)] -> ([(key, result, error, seconds)], unfinished keys). A task runs in the lane named by its key's first part
    ("workable:foo" -> workable, "remotive" -> remotive), at that lane's own pace. Past `budget` seconds the unfinished tasks are reported as timed out."""
    def run(t):
        t0 = time.time()
        try:
            return t[0], t[1](), None, time.time() - t0
        except Exception as e:
            return t[0], None, e, time.time() - t0

    pools = {}
    futs = {}
    for t in tasks:
        lane = t[0].split(":")[0]
        lane = LANE_OF.get(lane, lane)
        pool = pools.get(lane) or pools.setdefault(lane, ThreadPoolExecutor(LANES.get(lane, 1)))
        futs[pool.submit(run, t)] = t[0]
    done, pending = wait(futs, timeout=budget)
    for ex in pools.values():
        ex.shutdown(wait=False, cancel_futures=True)
    return [f.result() for f in done] + [(futs[f], None, TimeoutError(f"still running after {budget}s"), budget) for f in pending], [futs[f] for f in pending]


def main():
    parser = argparse.ArgumentParser(description="RoleBase job scraper")
    parser.add_argument("--shard", type=int, default=0, help="Shard index (0-based)")
    parser.add_argument("--total-shards", type=int, default=1, help="Total number of parallel shards")
    parser.add_argument("--consolidate", action="store_true", help="Merge shards, prune, and log history")
    parser.add_argument("--budget", type=int, default=RUN_BUDGET, help="Run budget in seconds")
    args, _ = parser.parse_known_args()

    t_start = time.time()
    today = dt.date.today().isoformat()

    if args.consolidate:
        print("Consolidating shards...")
        shards_dir = Path("data/shards")
        if shards_dir.exists():
            for sdir in sorted(shards_dir.glob("shard-*")):
                for jf in sdir.glob("*.jsonl"):
                    shutil.copy(jf, DIR / jf.name)
        all_health = {}
        if HEALTH.exists():
            for line in HEALTH.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        r = json.loads(line)
                        all_health[r.pop("b")] = r
                    except Exception:
                        pass
        health_sources = list(shards_dir.glob("shard-*/health.json")) + list(Path("data").glob("health-*.json"))
        for hf in health_sources:
            for line in hf.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        r = json.loads(line)
                        all_health[r.pop("b")] = r
                    except Exception:
                        pass
        if all_health:
            HEALTH.write_text("\n".join(json.dumps({"b": k, **v}, sort_keys=True) for k, v in sorted(all_health.items())) + "\n", encoding="utf-8", newline="\n")
        shutil.rmtree(shards_dir, ignore_errors=True)
        for hf in Path("data").glob("health-*.json"):
            hf.unlink(missing_ok=True)
        cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
        pulse = Path("data/pulse.csv")
        pulse_exists_today = pulse.exists() and any(l.startswith(today + ",") for l in pulse.read_text(encoding="utf-8").split("\n") if l)
        skills_path = Path("data/skills.csv")
        skills_exists_today = skills_path.exists() and any(l.startswith(today + ",") for l in skills_path.read_text(encoding="utf-8").split("\n") if l)

        pulse_counts = {}
        all_skills = Counter()
        tagged_total = 0
        pruned_rows = []
        tot_open = 0
        tot_closed = 0
        all_levels = Counter()
        all_categories = Counter()
        all_companies = Counter()
        all_comp_fresh = Counter()
        all_comp_jr3 = Counter()
        all_comp_years = Counter()

        files = [DIR / f for f in os.listdir(DIR) if f.endswith(".jsonl")] if DIR.exists() else []

        def process_board_file(fpath):
            try:
                content = fpath.read_text(encoding="utf-8")
            except Exception:
                return None
            board = fpath.stem.replace("_", ":", 1)
            lines_to_keep = []
            modified = False
            b_pruned = []
            b_open = 0
            b_fresher = 0
            b_closed = 0
            b_levels = Counter()
            b_categories = Counter()
            b_companies = Counter()
            b_comp_fresh = Counter()
            b_comp_jr3 = Counter()
            b_comp_years = Counter()
            b_skills = Counter()
            b_tagged = 0

            for line in content.split("\n"):
                line = line.strip()
                if not line:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue

                closed_at = j.get("closed_at")
                if closed_at and isinstance(closed_at, str) and closed_at < cutoff:
                    modified = True
                    b_pruned.append([
                        j.get("id", ""), j.get("company", ""), j.get("level", ""), j.get("category", ""),
                        j.get("posted") or "", j.get("first_seen", ""), closed_at
                    ])
                    continue

                lines_to_keep.append(line)

                if closed_at is None:
                    b_open += 1
                    y = j.get("years")
                    lvl = j.get("level") or level(j.get("title", ""), y)
                    b_levels[lvl] += 1
                    cat = j.get("category") or category(j.get("title", ""), j.get("team"))
                    b_categories[cat] += 1
                    is_fresh = (lvl in ("intern", "junior", "mid")) and (y <= 2 if y is not None else lvl != "mid")
                    if is_fresh:
                        b_fresher += 1
                    comp = clean_company(j.get("company", ""))
                    if comp and comp.lower() not in ("unknown", "none", "non renseigné", "non renseigne", "not specified", "null", "n/a"):
                        b_companies[comp] += 1
                        if is_fresh:
                            b_comp_fresh[comp] += 1
                        if lvl in ("intern", "junior") and (y or 0) >= 3:
                            b_comp_jr3[comp] += 1
                        if y is not None:
                            b_comp_years[comp] += 1
                    sks = j.get("skills", [])
                    if sks:
                        b_tagged += 1
                        for s in sks:
                            b_skills[s] += 1
                else:
                    b_closed += 1

            if modified:
                if lines_to_keep:
                    fpath.write_text("\n".join(lines_to_keep) + "\n", encoding="utf-8", newline="\n")
                else:
                    fpath.unlink(missing_ok=True)

            return (board, b_open, b_fresher, b_closed, b_pruned, b_levels, b_categories, b_companies, b_comp_fresh, b_comp_jr3, b_comp_years, b_skills, b_tagged)

        print(f"Processing {len(files)} boards for pruning, metrics, and charts...")
        sys.stdout.flush()
        with ThreadPoolExecutor(max_workers=8) as ex:
            for res in ex.map(process_board_file, files):
                if not res:
                    continue
                board, b_open, b_fresher, b_closed, b_pruned, b_levels, b_categories, b_companies, b_comp_fresh, b_comp_jr3, b_comp_years, b_skills, b_tagged = res
                tot_open += b_open
                tot_closed += b_closed
                all_levels.update(b_levels)
                all_categories.update(b_categories)
                all_companies.update(b_companies)
                all_comp_fresh.update(b_comp_fresh)
                all_comp_jr3.update(b_comp_jr3)
                all_comp_years.update(b_comp_years)
                all_skills.update(b_skills)
                tagged_total += b_tagged
                if b_pruned:
                    pruned_rows.extend(b_pruned)
                if not pulse_exists_today and b_open > 0:
                    pulse_counts[board] = [b_open, b_fresher]

        if not pulse_exists_today and pulse_counts:
            append_csv(pulse, ["date", "board", "open", "fresher"], [[today, b, n, f] for b, (n, f) in sorted(pulse_counts.items())])

        if not skills_exists_today and all_skills:
            append_csv(skills_path, ["date", "skill", "jobs", "tagged"], [[today, k, c, tagged_total] for k, c in sorted(all_skills.items())])

        if pruned_rows:
            append_csv(Path("data/archive.csv"), ["id", "company", "level", "category", "posted", "first_seen", "closed_at"], pruned_rows)

        company_stats_list = [
            [c, n, all_comp_fresh.get(c, 0), round(all_comp_fresh.get(c, 0) / n, 3), all_comp_jr3.get(c, 0), round(all_comp_years.get(c, 0) / n, 2)]
            for c, n in all_companies.items() if n >= 5
        ]
        company_stats_list.sort(key=lambda r: (-r[3], -r[1]))
        charts_cache = {
            "tot_open": tot_open,
            "levels": dict(all_levels),
            "categories": dict(all_categories),
            "skills": dict(all_skills),
            "companies": dict(all_companies),
            "comp_fresh": dict(all_comp_fresh),
            "company_stats": company_stats_list,
        }
        Path("data/charts_cache.json").write_text(json.dumps(charts_cache), encoding="utf-8")

        print(f"Consolidation complete: {tot_open:,} active jobs, {tot_closed:,} closed jobs | {time.time() - t_start:.1f}s")
        sys.stdout.flush()
        return

    cfg = json.loads(COMPANIES.read_text(encoding="utf-8"))
    tasks = [(f"{k}:{s}", lambda k=k, s=s, n=n: ATS[k](s, n)) for k, boards in cfg.items() for s, n in (boards.items() if isinstance(boards, dict) else [(x, "") for x in boards])] + list(FEEDS.items())

    target_scopes = None
    if args.total_shards > 1:
        tasks = [t for i, t in enumerate(tasks) if i % args.total_shards == args.shard]
        target_scopes = {t[0] for t in tasks}
        print(f"Shard {args.shard + 1}/{args.total_shards}: assigned {len(tasks)} boards")

    db = load(target_scopes)
    open_n = {}
    for i, o in db.items():
        if o["closed_at"] is None:
            open_n[i.rsplit(":", 1)[0]] = open_n.get(i.rsplit(":", 1)[0], 0) + 1
    tasks.sort(key=lambda t: -open_n.get(t[0], 0))  # biggest boards first, so the pool never ends waiting on one slow giant

    lines = [l for l in HEALTH.read_text(encoding="utf-8").split(chr(10)) if l] if HEALTH.exists() else []
    health = {r.pop("b"): r for r in map(json.loads, lines)}

    results, unfinished = run_all(tasks, budget=args.budget)

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
    for scope in (target_scopes if target_scopes is not None else open_n):
        if scope not in known:
            fetched[scope] = []

    for scope, why in failed:
        print(f"::warning title=scrape {scope}::{why}")  # shows up as an annotation on the Actions run
    slow = sorted(results, key=lambda r: -r[3])[:3]
    print("slowest: " + ", ".join(f"{r[0]} {r[3]:.0f}s" for r in slow) + f" | busy {sum(r[3] for r in results):.0f}s of {time.time() - t_start:.0f}s")
    busy = {}
    for r in results:
        busy[r[0].split(":")[0]] = busy.get(r[0].split(":")[0], 0) + r[3]
    print("busy per lane (seconds of work / workers): " + ", ".join(f"{k} {v:.0f}/{LANES.get(k, 1)}" for k, v in sorted(busy.items(), key=lambda kv: -kv[1])[:6]))
    if not fetched and tasks:
        print("all assigned sources failed")
        sys.stdout.flush()
        os._exit(1)

    before, pruned = set(db), []
    db = merge(db, fetched, today, pruned)
    # these sources only list their newest N jobs, so a job missing from the list may just have slid out of view, not closed: forget it instead of recording a closure
    db = {i: o for i, o in db.items() if not (o["closed_at"] and i.split(":")[0] in CAPPED)}
    n_open = sum(o["closed_at"] is None for o in db.values())
    print(f"sources ok={len(fetched)} failed={len(failed)} | new={len(set(db) - before)} open={n_open} closed={len(db) - n_open} | {time.time() - t_start:.0f}s")
    save(db, target_scopes)
    if target_scopes is None:
        log_history(db, pruned, today)
    combined_health = {**health, **new_health}
    health_file = Path(f"data/health-{args.shard}.json") if args.total_shards > 1 else HEALTH
    health_file.write_text(chr(10).join(json.dumps({"b": k, **v}, sort_keys=True) for k, v in sorted(combined_health.items())) + chr(10), encoding="utf-8", newline=chr(10))  # one board per line
    if target_scopes is not None and args.total_shards > 1:
        shard_out = Path(f"data/shards/shard-{args.shard}")
        shard_out.mkdir(parents=True, exist_ok=True)
        modified_scopes = set(fetched.keys())
        target_names = {s.replace(":", "_").replace("/", "_") for s in modified_scopes}
        for name in target_names:
            src = DIR / f"{name}.jsonl"
            if src.exists():
                shutil.copy(src, shard_out / f"{name}.jsonl")
        (shard_out / "health.json").write_text(
            chr(10).join(json.dumps({"b": k, **v}, sort_keys=True) for k, v in sorted(combined_health.items())) + chr(10),
            encoding="utf-8", newline=chr(10)
        )
    if unfinished:  # everything is saved; threads stuck in a slow request must not keep the process (and the Actions job) alive
        print(f"::warning title=scrape timed out::{len(unfinished)} sources did not finish: {', '.join(unfinished[:10])}")
    sys.stdout.flush()
    os._exit(0)


if __name__ == "__main__":
    main()
