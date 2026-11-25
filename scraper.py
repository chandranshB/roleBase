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
    waits = (1, 3)  # seconds before the 2nd and 3rd try

    def request(self, *a, **kw):  # urllib3's Retry stops once the response starts, so connection cuts / timeouts mid-body are retried here
        for w in (*self.waits, None):
            try:
                return super().request(*a, **kw)
            except (requests.exceptions.ChunkedEncodingError, requests.exceptions.ConnectionError, requests.exceptions.ReadTimeout) as exc:
                msg = str(exc)
                if "NameResolutionError" in msg or "Failed to resolve" in msg or w is None:
                    raise
                time.sleep(w)


S = Client()
S.headers["User-Agent"] = "argus-job-scraper"
S.headers["Accept-Encoding"] = "gzip, deflate"
S.mount("https://", HTTPAdapter(pool_maxsize=128, max_retries=Retry(total=2, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504],
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
    location = str(location or "").strip()
    team = str(team or "").strip()
    title = str(title or "").strip()
    yrs = years_req(desc)
    cntry = str(country or "").strip() or infer_country(location)
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


WD_CAP = 50000  # newest N per tenant; partitions via facets when Workday exceeds 2000 offset window


def pmap(fn, xs, workers=4):  # a few pages at a time: fast, but polite to one host. Own pool, so no deadlock inside main()'s pool
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(fn, xs))


def workday(s, name):  # s = "<tenant>.<pod>.myworkdayjobs.com/<site>"; the list has no description, so years/skills/pay stay empty
    host, site = s.split("/", 1)
    url = f"https://{host}/wday/cxs/{host.split('.')[0]}/{site}/jobs"

    def page(off, facets=None):
        payload = {"limit": 20, "offset": off, "searchText": ""}
        if facets:
            payload["appliedFacets"] = facets
        return body(lambda: S.post(url, json=payload, timeout=TIMEOUT))

    def collect_query(first_d, facets=None):
        tot = min(first_d.get("total", 0) or 0, WD_CAP)
        if tot <= 20:
            return [first_d]
        rest = pmap(lambda o: page(o, facets), range(20, min(tot, 2000), 20), 3)
        return [first_d, *rest]

    def fetch_partitioned(d, base_facets, depth=0):
        docs = []
        tot = d.get("total", 0) or 0
        if tot <= 2000 or depth >= 2:
            return collect_query(d, base_facets or None)
        # Pick the facet dimension not yet in base_facets whose largest bucket is smallest (to fall <= 2000)
        best = None
        for f in (d.get("facets") or []):
            fp = f.get("facetParameter")
            vals = [v for v in (f.get("values") or []) if v.get("id") and (v.get("count") or 0) > 0]
            if not fp or fp in base_facets or not vals:
                continue
            mx = max(v["count"] for v in vals)
            key = (mx >= 2000, mx, len(vals))
            if best is None or key < best[0]:
                best = (key, fp, vals)
        if not best:
            return collect_query(d, base_facets or None)
        _, fp, vals = best
        for v in vals:
            nf = {**base_facets, fp: [v["id"]]}
            try:
                fd = page(0, nf)
            except Exception:
                continue
            docs.extend(fetch_partitioned(fd, nf, depth + 1))
        return docs

    first = page(0)
    total = first.get("total", 0) or 0
    docs = collect_query(first) if total <= 2000 else fetch_partitioned(first, {})
    return list({j["externalPath"]: job("workday", f"workday:{s}", j["externalPath"], name, j["title"], f"https://{s}{j['externalPath']}",
                                        j.get("locationsText"), posted=wd_posted(j.get("postedOn")))
                 for d in docs for j in d.get("jobPostings", []) if j.get("externalPath") and j.get("title")}.values())  # entries with no link or no title exist (KBR has one): skip them, not the board


CAPPED = {"workday", "eightfold", "himalayas", "jobicy", "jobtech", "usajobs", "oracle", "phenom", "apple", "themuse", "aidevboard", "nofluffjobs", "arbeitsagentur", "mycareersfuture", "freehire", "workatastartup", "talentbrew"}
AIDEV_CAP = 50000  # AI Dev Jobs (~9k total)
HIM_CAP = 2000  # newest N Himalayas jobs (cursor paging; 20/req)
MUSE_CAP = 1000  # newest N jobs from The Muse
ORA_CAP = 50000  # per Oracle career site (partitions via facets past 10k)
USAJOBS_CAP = 50000  # federal postings
JT_CAP = 100000  # Swedish public-employment ads (~43k, partitioned past 2k offset cap)
EF_CAP = 100000  # per Eightfold site (Starbucks ~22k, Microsoft, etc.)
APPLE_CAP = 50000  # all Apple jobs


def pcsx(host, domain, source, scope, name, cap=None):  # Eightfold "PCSX" search API behind a careers site; their robots.txt allows /api/pcsx
    def page(off):
        for wait in (0.35, 2.0, 6.0, 15.0):
            time.sleep(wait)  # Eightfold's WAF answers 405/429 to bursts; back off cleanly without dropping the board
            r = S.get(f"https://{host}/api/pcsx/search", params={"domain": domain, "query": "", "start": off}, timeout=TIMEOUT)
            if r.status_code in (405, 429):
                continue
            r.raise_for_status()
            return r.json().get("data", {})
        r.raise_for_status()
        return {}

    first = page(0)
    positions = first.get("positions") or []
    size = len(positions)
    count = first.get("count", 0)
    rest = pmap(page, range(size, min(count, cap or count), size), 2) if size and size < count else []
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


def getonbrd():  # Latin America's tech board across all categories
    try:
        cats_res = get("https://www.getonbrd.com/api/v0/categories", params={"per_page": 100})
        cats = [c["id"] for c in (cats_res.get("data") or []) if c.get("id")]
    except Exception:
        cats = []
    if not cats:
        cats = ["programming"]
    out = {}
    for cat in cats:
        for p in range(1, 51):
            d = get(f"https://www.getonbrd.com/api/v0/categories/{cat}/jobs", params={"per_page": 100, "page": p, "expand": '["company"]'})
            for j in d.get("data") or []:
                a = j["attributes"]
                out[j["id"]] = job("getonbrd", "getonbrd", j["id"], clean_company(a["company"]["data"]["attributes"]["name"]), a["title"],
                                   f"https://www.getonbrd.com/jobs/{j['id']}", ", ".join(a.get("countries") or []), a.get("remote"), a.get("published_at"),
                                   desc=text(" ".join(filter(None, [a.get("description"), a.get("functions"), a.get("desirable")]))), team=cat)
            if p >= (d.get("meta") or {}).get("total_pages", 1):
                break
    return list(out.values())


def workingnomads():  # remote jobs, tags and time-zone notes; the list is short (~50) and complete
    return [job("workingnomads", "workingnomads", j["url"].rstrip("/").rsplit("/", 1)[-1], clean_company(j["company_name"]), j["title"], j["url"],
                j.get("location"), True, j.get("pub_date"), desc=text(j.get("description")), team=j.get("category_name"),
                tags=(j.get("tags") or "").split(","))
            for j in get("https://www.workingnomads.com/api/exposed_jobs/")]


def jobtech():  # Sweden's public employment service: partition past 2,000 offset limit by region & occupation-field
    def query_hits(extra_params, count_hint=2000):
        pages = []
        cap = min(count_hint, 2000, JT_CAP)
        for off in range(0, cap, 100):
            try:
                res = get("https://jobsearch.api.jobtechdev.se/search", params={"limit": 100, "offset": off, "sort": "pubdate-desc", **extra_params})
                hits = res.get("hits") or []
                pages.extend(hits)
                if len(hits) < 100:
                    break
            except Exception:
                break
        return pages

    try:
        meta = get("https://jobsearch.api.jobtechdev.se/search", params={"limit": 0, "stats": "region", "stats.limit": 30})
        total = ((meta.get("total") or {}).get("value")) or 0
        reg_stats = ((meta.get("stats") or [{}])[0]).get("values") or []
    except Exception:
        total, reg_stats = 0, []

    all_hits = {}
    if total and total <= 2000:
        for h in query_hits({}, total):
            all_hits[h["id"]] = h
    elif reg_stats:
        field_ids = []
        for rv in reg_stats:
            rcnt, rcode = rv.get("count") or 0, rv.get("concept_id")
            if not rcode or rcnt <= 0:
                continue
            if rcnt <= 2000:
                for h in query_hits({"region": rcode}, rcnt):
                    all_hits[h["id"]] = h
            else:
                if not field_ids:
                    fmeta = get("https://jobsearch.api.jobtechdev.se/search", params={"limit": 0, "stats": "occupation-field", "stats.limit": 30})
                    field_ids = [fv["concept_id"] for fv in (((fmeta.get("stats") or [{}])[0]).get("values") or []) if fv.get("concept_id")]
                for fcode in field_ids:
                    for h in query_hits({"region": rcode, "occupation-field": fcode}, 2000):
                        all_hits[h["id"]] = h
                if len(all_hits) >= JT_CAP:
                    break
    else:
        def page(off):
            return get("https://jobsearch.api.jobtechdev.se/search", params={"limit": 100, "offset": off, "sort": "pubdate-desc"})["hits"]

        for hits in pmap(page, range(0, min(JT_CAP, 2000), 100), 3):
            for h in hits:
                all_hits[h["id"]] = h

    out = []
    for j in list(all_hits.values())[:JT_CAP]:
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


def oracle(s, name):  # Oracle Recruiting Cloud career site, s = "<host>/<site number>", e.g. "ecvz.fa.us2.oraclecloud.com/CX_1"; partitions past 10k via facets
    host, site = s.split("/", 1) if "/" in s else (s, "CX")

    def page(off, facet_expr="", include_facets=False):
        facets_part = "facetsList=LOCATIONS;WORK_LOCATIONS;WORKPLACE_TYPES;TITLES;CATEGORIES;ORGANIZATIONS;POSTING_DATES;FLEX_FIELDS," if include_facets else ""
        res = get(f"https://{host}/hcmRestApi/resources/latest/recruitingCEJobRequisitions", params={
            "onlyData": "true",
            "expand": "requisitionList.secondaryLocations",
            "finder": f"findReqs;siteNumber={site},{facets_part}{facet_expr}limit=50,offset={off},sortBy=POSTING_DATES_DESC"})
        items = res.get("items", []) if isinstance(res, dict) else []
        d = items[0] if items else {}
        return d

    def facet_choices(df, used_params):
        # postingDatesFacet (30=<=30d + 31=>30d) gives a complete 2-bucket split without top-10 truncation; skip Id=7 subset when 30 is present
        specs = [
            ("postingDatesFacet", "selectedPostingDatesFacet"),
            ("organizationsFacet", "selectedOrganizationsFacet"),
            ("categoriesFacet", "selectedCategoriesFacet"),
            ("locationsFacet", "selectedLocationsFacet"),
            ("workLocationsFacet", "selectedWorkLocationsFacet"),
            ("workplaceTypesFacet", "selectedWorkplaceTypesFacet"),
        ]
        for fkey, param_name in specs:
            if param_name in used_params:
                continue
            vals = [v for v in (df.get(fkey) or []) if v.get("Id") is not None and (v.get("TotalCount") or 0) > 0]
            if not vals:
                continue
            if fkey == "postingDatesFacet":
                ids_str = {str(v["Id"]) for v in vals}
                if "30" in ids_str:
                    vals = [v for v in vals if str(v["Id"]) != "7"]
            if vals:
                return param_name, vals
        return None, []

    def collect(first_doc, expr="", used_params=frozenset(), depth=0):
        tot = first_doc.get("TotalJobsCount", 0) or 0
        if tot <= 10000 or depth >= 2:
            out_docs = [first_doc]
            cap = min(tot, 10000, ORA_CAP)
            if cap > 50:
                out_docs.extend(pmap(lambda o, e=expr: page(o, facet_expr=e), range(50, cap, 50)))
            return out_docs
        df = first_doc if any(first_doc.get(k) for k in ("postingDatesFacet", "organizationsFacet", "categoriesFacet", "locationsFacet")) else page(0, facet_expr=expr, include_facets=True)
        param_name, vals = facet_choices(df, used_params)
        if not param_name:
            cap = min(tot, 10000, ORA_CAP)
            return [first_doc, *(pmap(lambda o, e=expr: page(o, facet_expr=e), range(50, cap, 50)) if cap > 50 else [])]
        out_docs = []
        nused = used_params | {param_name}
        for v in vals:
            nexpr = f"{expr}{param_name}={v['Id']},"
            fd = page(0, facet_expr=nexpr, include_facets=(v.get("TotalCount", 0) > 10000 and depth + 1 < 2))
            out_docs.extend(collect(fd, nexpr, nused, depth + 1))
        return out_docs

    d0 = page(0)
    total = d0.get("TotalJobsCount", 0) or 0
    if total <= 10000:
        docs = [d0, *(pmap(lambda o: page(o), range(50, min(total, ORA_CAP), 50)) if total > 50 else [])]
    else:
        docs = collect(d0)
    return list({j["Id"]: job("oracle", f"oracle:{s}", j["Id"], name, j["Title"], f"https://{host}/hcmUI/CandidateExperience/en/sites/{site}/job/{j['Id']}",
                              j.get("PrimaryLocation"), "REMOTE" in (j.get("WorkplaceTypeCode") or ""), j.get("PostedDate"))
                 for j in (d.get("requisitionList") or [] for d in docs) for j in j if j.get("Id") and j.get("Title")}.values())


def workable(s, name):  # apply.workable.com/<slug>; many small and mid-size employers (restaurants, clinics, schools, shops)
    d = get(f"https://apply.workable.com/api/v1/widget/accounts/{s}", params={"details": "true"})
    time.sleep(1)  # their widget API answers 429 to a burst: LANES runs two boards at a time, each pausing a second
    return [job("workable", f"workable:{s}", j["shortcode"], name or d.get("name") or s, j["title"], j["url"],
                ", ".join(filter(None, [j.get("city"), j.get("state"), j.get("country")])), j.get("telecommuting"), j.get("published_on") or j.get("created_at"),
                desc=text(j.get("description")), team=j.get("department") or j.get("function") or j.get("industry"), emp=[j.get("employment_type")])
            for j in d.get("jobs") or [] if j.get("shortcode") and j.get("title")]


def teamtailor(s, name):  # RSS per company (capped at 100); overflows to /jobs/show_more?page=N turbo-stream HTML
    base = f"https://{s}" if "." in s else f"https://{s}.teamtailor.com"
    r = S.get(f"{base}/jobs.rss", timeout=TIMEOUT)
    r.raise_for_status()
    ns, out = {"tt": "https://teamtailor.com/locations"}, []
    seen_ids = set()
    for i in ET.fromstring(r.content).iter("item"):
        link = i.findtext("link") or ""
        m_id = re.search(r"/jobs/(\d+)", link)
        seen_ids.add(m_id.group(1) if m_id else link.split("?")[0])
        loc = "; ".join(", ".join(filter(None, [l.findtext("tt:city", "", ns), l.findtext("tt:country", "", ns)])) for l in i.iterfind("tt:locations/tt:location", ns))
        out.append(job("teamtailor", f"teamtailor:{s}", i.findtext("guid"), name, i.findtext("title"), link, loc,
                       i.findtext("remoteStatus") == "fully", rss_day(i.findtext("pubDate")), desc=text(i.findtext("description")),
                       team=i.findtext("tt:department", "", ns)))
    if len(out) >= 100:
        prev_page_ids = set()
        for pg in range(2, 100):
            try:
                rp = S.get(f"{base}/jobs/show_more", params={"page": pg}, headers={"User-Agent": FEISHU_BROWSER_UA}, timeout=TIMEOUT)
                if not rp.ok or not rp.text.strip():
                    break
            except Exception:
                break
            clean_html = re.sub(r"</?template\b[^>]*>", "", rp.text)
            soup = BeautifulSoup(clean_html, "html.parser")
            links = soup.find_all("a", href=re.compile(r"/jobs/\d+"))
            if not links:
                break
            page_ids = set()
            for a in links:
                href = (a.get("href") or "").split("?")[0]
                if not href:
                    continue
                if href.startswith("/"):
                    href = f"{base}{href}"
                m_id = re.search(r"/jobs/(\d+)", href)
                jid = m_id.group(1) if m_id else href
                page_ids.add(jid)
                if jid in seen_ids:
                    continue
                seen_ids.add(jid)
                title = a.get_text(" ", strip=True)
                if not title:
                    continue
                card = a.find_parent("li") or a.find_parent("div") or a
                spans = [sp.get_text(strip=True) for sp in card.select("div span") if sp.get_text(strip=True) and sp.get_text(strip=True) not in ("·", "•")]
                team = spans[0] if len(spans) >= 2 else ""
                loc = ", ".join(spans[1:]) if len(spans) >= 2 else (spans[0] if spans else "")
                remote = "remote" in loc.lower() or "remote" in title.lower()
                out.append(job("teamtailor", f"teamtailor:{s}", jid, name, title, href, loc, remote, team=team))
            if not page_ids or (prev_page_ids and page_ids <= prev_page_ids):
                break
            prev_page_ids = page_ids
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


PH_CAP = 50000  # per Phenom site; partitions via country/state/category facets when >10,000 results


def phenom_job(s, name, j):  # one record of a Phenom careers site's search -> job; the record has no page link of its own, only the apply link
    if not (j.get("jobId") and j.get("title") and j.get("applyUrl")):
        return None
    return job("phenom", f"phenom:{s}", j["jobId"], name, j["title"], j["applyUrl"].removesuffix("/apply") if "myworkdayjobs.com" in j["applyUrl"] else j["applyUrl"],
               j.get("cityStateCountry") or j.get("location"), "remote" in str(j.get("remote") or "").lower(), j.get("postedDate") or j.get("dateCreated"),
               desc=j.get("descriptionTeaser") or "", team=j.get("department") or j.get("category"), emp=[j.get("type"), j.get("employmentType")],
               tags=j.get("ml_skills") or [])


def phenom(s, name):  # Phenom People career site, s = host, e.g. "talent.lowes.com": the /widgets search the page itself calls, newest first, 100 per request
    def page(off, selected=None):
        return body(lambda: S.post(f"https://{s}/widgets", timeout=TIMEOUT, headers={"Content-Type": "application/json"}, json={
            "lang": "en_us", "deviceType": "desktop", "country": "us", "pageName": "search-results", "ddoKey": "refineSearch", "sortBy": "", "subsearch": "",
            "from": off, "jobs": True, "counts": True, "all_fields": ["category", "country", "state", "city", "type"], "size": 100, "clearAll": False,
            "jdsource": "facets", "isSliderEnable": False, "pageId": "page1", "siteType": "external", "keywords": "", "global": True, "selected_fields": selected or {},
            "sort": {"order": "desc", "field": "postedDate"}, "locationData": {}})).get("refineSearch") or {}

    def collect_partition(d, sel=None, depth=0):
        tot = d.get("totalHits") or 0
        if tot <= 10000 or depth >= 2:
            rest = pmap(lambda o: page(o, sel), range(100, min(tot, 10000, PH_CAP), 100), 3) if tot > 100 else []
            return [d, *rest]
        aggs = (d.get("data") or {}).get("aggregations") or []
        best = None
        for agg in aggs:
            fld = agg.get("field")
            vals = agg.get("value") if isinstance(agg.get("value"), dict) else {}
            if not fld or (sel and fld in sel) or not vals:
                continue
            mx = max((int(c) for c in vals.values() if isinstance(c, (int, float))), default=0)
            key = (mx > 10000, len(vals), mx)
            if best is None or key < best[0]:
                best = (key, fld, list(vals.keys()))
        if not best:
            rest = pmap(lambda o: page(o, sel), range(100, min(tot, 10000), 100), 3) if tot > 100 else []
            return [d, *rest]
        _, fld, keys = best
        out_docs = []
        sub_firsts = pmap(lambda k: (k, page(0, {**(sel or {}), fld: [k]})), keys, 3)
        for k, fd in sub_firsts:
            out_docs.extend(collect_partition(fd, {**(sel or {}), fld: [k]}, depth + 1))
        return out_docs

    first = page(0)
    total = first.get("totalHits") or 0
    if total <= 10000:
        docs = [first, *(pmap(page, range(100, min(total, PH_CAP), 100), 3) if total > 100 else [])]
    else:
        docs = collect_partition(first)
    jobs = (phenom_job(s, name, j) for d in docs for j in (d.get("data") or {}).get("jobs") or [])
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
    try:
        d = get(f"https://{s}.bamboohr.com/careers/list")
    except Exception:
        return []
    if not isinstance(d, dict):
        return []
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


def rippling(s, name):  # ats.rippling.com/<slug>/jobs + public v1 board API (full list without SSR 20-cap)
    out = []
    seen = set()
    try:
        r = S.get(f"https://api.rippling.com/platform/api/ats/v1/board/{s}/jobs", timeout=TIMEOUT)
        if r.ok:
            data = r.json()
            if isinstance(data, list):
                for j in data:
                    if not isinstance(j, dict):
                        continue
                    jid = str(j.get("uuid") or j.get("id") or "")
                    title = (j.get("name") or "").strip()
                    if not jid or not title or jid in seen:
                        continue
                    seen.add(jid)
                    wl = j.get("workLocation") or {}
                    loc = (wl.get("label") or "").strip() if isinstance(wl, dict) else ""
                    if not loc and isinstance(j.get("locations"), list) and j["locations"]:
                        l0 = j["locations"][0] or {}
                        loc = ", ".join(filter(None, [l0.get("city"), l0.get("stateCode") or l0.get("countryCode")]))
                    dept = j.get("department") or {}
                    team = (dept.get("label") or dept.get("name") or "") if isinstance(dept, dict) else ""
                    remote = "remote" in loc.lower() or "remote" in title.lower() or any(
                        isinstance(l, dict) and l.get("workplaceType") in ("REMOTE", "HYBRID") for l in (j.get("locations") or [])
                    )
                    url = j.get("url") or f"https://ats.rippling.com/{s}/jobs/{jid}"
                    out.append(job("rippling", f"rippling:{s}", jid, name or s, title, url, loc, remote,
                                   posted=j.get("createdAt") or j.get("updatedAt"), team=team))
                return out
    except Exception:
        pass
    r = S.get(f"https://ats.rippling.com/{s}/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
    if not m:
        return []
    try:
        data = json.loads(m.group(1))
    except Exception:
        return []
    queries = data.get("props", {}).get("pageProps", {}).get("dehydratedState", {}).get("queries", [])
    for q in queries:
        if "job-posts" in q.get("queryKey", []):
            items = q.get("state", {}).get("data", {}).get("items", [])
            for j in items:
                jid = j.get("id")
                title = j.get("name")
                if not jid or not title or str(jid) in seen:
                    continue
                seen.add(str(jid))
                locs = j.get("locations") or []
                loc = ", ".join(filter(None, [locs[0].get("city"), locs[0].get("stateCode") or locs[0].get("countryCode")])) if locs else ""
                remote = any(l.get("workplaceType") in ("REMOTE", "HYBRID") for l in locs)
                url = j.get("url") or f"https://ats.rippling.com/{s}/jobs/{jid}"
                team = (j.get("department") or {}).get("name") or ""
                out.append(job("rippling", f"rippling:{s}", str(jid), name or s, title, url, loc, remote, team=team))
    return out


_JOIN_GQL = """query PublicJobsList($input: PublicJobsQueryInput!) {
  publicJobs(input: $input) {
    items { idParam id title workplaceType remoteType createdAt category { name } employmentType { name } country { iso3166 } city { cityName countryName } }
    pageInfo { page pageCount pageSize rowCount }
  }
}"""


def join(s, name):  # join.com/companies/<slug> via __NEXT_DATA__ + candidate-api GraphQL pagination past 5-job SSR cap
    r = S.get(f"https://join.com/companies/{s}", timeout=TIMEOUT)
    r.raise_for_status()
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text)
    if not m:
        return []
    try:
        d = json.loads(m.group(1))
    except Exception:
        return []
    page_data = d.get("props", {}).get("pageProps", {}).get("pageData", {})
    jobs_box = page_data.get("jobs") or {}
    items = list(jobs_box.get("items") or [])
    company_id = (page_data.get("company") or {}).get("id")
    pagination = jobs_box.get("pagination") or {}
    row_count = pagination.get("total") or len(items)
    if company_id and (row_count > len(items) or len(items) >= 5):
        gql_items = []
        pg = 1
        while pg <= 100:
            try:
                gr = S.post("https://join.com/candidate-api/graphql", timeout=TIMEOUT,
                            headers={"Content-Type": "application/json"},
                            json={"query": _JOIN_GQL, "variables": {"input": {"companyId": company_id, "paginationPage": {"page": pg, "pageSize": 100}}}})
                if not gr.ok:
                    break
                pj = (gr.json().get("data") or {}).get("publicJobs") or {}
                batch = pj.get("items") or []
                if not batch:
                    break
                gql_items.extend(batch)
                pinfo = pj.get("pageInfo") or {}
                if pg >= (pinfo.get("pageCount") or 1):
                    break
                pg += 1
            except Exception:
                break
        if gql_items:
            items = gql_items
    out = []
    seen = set()
    for j in items:
        if not isinstance(j, dict):
            continue
        jid = j.get("idParam") or j.get("id")
        title = j.get("title")
        if not jid or not title or str(jid) in seen:
            continue
        seen.add(str(jid))
        c_raw = j.get("city")
        city = c_raw.get("cityName") if isinstance(c_raw, dict) else (c_raw if isinstance(c_raw, str) else "")
        co_raw = j.get("country")
        cntry = (c_raw.get("countryName") if isinstance(c_raw, dict) else "") or (co_raw.get("iso3166") if isinstance(co_raw, dict) else (co_raw if isinstance(co_raw, str) else ""))
        loc = ", ".join(filter(None, [city, cntry]))
        wp = (j.get("workplaceType") or "").upper()
        remote = wp in ("REMOTE", "HYBRID")
        url = f"https://join.com/companies/{s}/jobs/{jid}"
        cat_raw = j.get("category")
        category_name = cat_raw.get("name") if isinstance(cat_raw, dict) else (cat_raw if isinstance(cat_raw, str) else "")
        emp_raw = j.get("employmentType")
        emp_name = emp_raw.get("name") if isinstance(emp_raw, dict) else (emp_raw if isinstance(emp_raw, str) else "")
        emp = [emp_name] if emp_name else []
        posted = (str(j.get("createdAt") or ""))[:10] or None
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
        for l in (r.get("requisitionLocations") or []):
            if not isinstance(l, dict):
                continue
            addr = l.get("address") if isinstance(l.get("address"), dict) else {}
            subdiv = addr.get("countrySubdivisionLevel1")
            subdiv_code = subdiv.get("codeValue") if isinstance(subdiv, dict) else (subdiv if isinstance(subdiv, str) else None)
            parts = filter(None, [addr.get("cityName"), subdiv_code, addr.get("countryCode")])
            nc = l.get("nameCode")
            nc_short = nc.get("shortName") if isinstance(nc, dict) else (nc if isinstance(nc, str) else "")
            loc_str = ", ".join(parts) or nc_short or ""
            if loc_str and loc_str.strip() not in locs:
                locs.append(loc_str.strip())
        loc = "; ".join(locs)
        post_date = (r.get("postDate") or "")[:10] or None
        url = f"https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid={s}&jobId={jid}"
        pay = None
        pgr = r.get("payGradeRange") if isinstance(r.get("payGradeRange"), dict) else {}
        min_obj = pgr.get("minimumRate") if isinstance(pgr.get("minimumRate"), dict) else {}
        max_obj = pgr.get("maximumRate") if isinstance(pgr.get("maximumRate"), dict) else {}
        min_rate = min_obj.get("amountValue")
        max_rate = max_obj.get("amountValue")
        curr = min_obj.get("currencyCode") or max_obj.get("currencyCode") or "USD"
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


def jobvite(s, name):  # jobs.jobvite.com/<slug>/jobs + Show More paginated category searches
    r = S.get(f"https://jobs.jobvite.com/{s}/jobs", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    seen = set()

    def collect_html(txt):
        for m in re.finditer(r'<a\s+[^>]*href=["\'](?:https://jobs\.jobvite\.com)?/[^/]+/job/([a-zA-Z0-9_-]+)["\'][^>]*>(.*?)</a>', txt, re.DOTALL | re.I):
            jid, raw_title = m.groups()
            if jid in seen:
                continue
            seen.add(jid)
            title = html.unescape(re.sub(r'<[^>]+>', ' ', raw_title)).strip()
            if not title:
                continue
            url = f"https://jobs.jobvite.com/{s}/job/{jid}"
            out.append(job("jobvite", f"jobvite:{s}", jid, name or s, title, url, "", False))

    collect_html(r.text)
    show_more = sorted(set(re.findall(r'href=["\']([^"\']*/search\?c=[^"\']+)["\']', r.text)))
    for cpath in show_more:
        base_cat = html.unescape(cpath)
        for p in range(50):
            cu = re.sub(r'([?&])p=\d+', rf'\1p={p}', base_cat) if re.search(r'[?&]p=\d+', base_cat) else f"{base_cat}{'&' if '?' in base_cat else '?'}p={p}"
            if cu.startswith("/"):
                cu = f"https://jobs.jobvite.com{cu}"
            try:
                rc = S.get(cu, timeout=TIMEOUT)
                if not rc.ok:
                    break
            except Exception:
                break
            collect_html(rc.text)
            if not re.search(rf'(?:[?&]|&amp;)p={p + 1}\b', rc.text):
                break
    return out


def trakstar_hire(s, name):  # <slug>.hire.trakstar.com/?p=N (RB.init_data.paginator uses ?p=2, ?p=3, ...)
    out = []
    seen = set()
    page = 1
    while page <= 100:
        r = S.get(f"https://{s}.hire.trakstar.com/", params={"p": page} if page > 1 else None, timeout=TIMEOUT)
        r.raise_for_status()
        new_in_page = 0
        for m in re.finditer(r'<a\s+[^>]*href=["\'](?:https://[^/]+)?/jobs/([a-zA-Z0-9]+)/?["\'][^>]*>(.*?)</a>', r.text, re.DOTALL | re.I):
            jid, raw_title = m.groups()
            if jid in seen or jid.lower() in ("jobs", "apply"):
                continue
            seen.add(jid)
            raw_text = html.unescape(re.sub(r'<[^>]+>', '\n', raw_title))
            parts = [p.strip() for p in raw_text.split('\n') if p.strip()]
            if not parts:
                continue
            new_in_page += 1
            title = parts[0]
            loc = ", ".join(parts[1:]) if len(parts) > 1 else ""
            remote = "remote" in loc.lower() or "hybrid" in loc.lower()
            url = f"https://{s}.hire.trakstar.com/jobs/{jid}"
            out.append(job("trakstar_hire", f"trakstar_hire:{s}", jid, name or s, title, url, loc, remote))
        has_next = f"?p={page + 1}" in r.text
        if new_in_page == 0 or (not has_next and new_in_page < 25):
            break
        page += 1
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
    host = s if "." in s else f"{s}.icims.com"
    out = []
    page = 0
    seen = set()
    while page < 500:
        r = S.get(f"https://{host}/jobs/search", params={"pr": page, "in_iframe": "1"}, timeout=TIMEOUT)
        if not r.ok or "iCIMS_Anchor" not in r.text:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        new_jobs = 0
        for a in soup.select("a.iCIMS_Anchor"):
            href = a.get("href") or ""
            m_id = re.search(r"/jobs/(\d+)", href)
            if not m_id:
                continue
            jid = m_id.group(1)
            if jid in seen:
                continue
            seen.add(jid)
            title_attr = a.get("title") or a.get_text(" ", strip=True) or ""
            title = re.sub(r"^\d+\s*-\s*", "", title_attr).strip()
            if not title:
                continue
            new_jobs += 1
            clean_url = href.split("?")[0]
            if clean_url.startswith("/"):
                clean_url = f"https://{host}{clean_url}"
            out.append(job("icims", f"icims:{s}", jid, name or host, title, clean_url, ""))
        if new_jobs == 0:
            break
        page += 1
    return out


def ultipro(s, name):  # s = "{tenant}/jobboard/{guid}" or "{tenant}/{guid}"
    out = []
    parts = s.split("/")
    tenant = parts[0]
    guid = parts[-1]
    url = f"https://recruiting2.ultipro.com/{tenant}/JobBoard/{guid}/JobBoardView/LoadSearchResults"
    page = 0
    page_size = 50
    seen = set()
    headers = {
        "Content-Type": "application/json; charset=utf-8",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
    }
    while page < 500:
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
            out.append(job("ultipro", f"ultipro:{s}", jid, name or tenant, title,
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
        title = str(j.get("Title") or "").strip()
        if not rid or not title:
            continue
        comp = name or str(j.get("CompanyName") or "") or client
        apply_url = j.get("ApplyUrl") or j.get("JobDetailsUrl") or f"https://jobs.dayforcehcm.com/en-US/{client}/{board_code}/jobs/{rid}/apply"
        desc = text(j.get("Description"))
        remote = bool(j.get("IsVirtualLocation")) or "remote" in title.lower()
        loc = str(j.get("ClientSiteName") or "").strip()
        out.append(job("dayforce", f"dayforce:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=str(j.get("DatePosted") or "")[:10] or None,
                       desc=desc, team=str(j.get("ParentRequisitionCode") or "")))
    return out


def paylocity(s, name):  # s = "{guid}": parse window.pageData JSON from /All/{s}, with v2 JSON feed fallback
    out = []
    seen = set()
    try:
        r = S.get(f"https://recruiting.paylocity.com/recruiting/jobs/All/{s}", timeout=TIMEOUT)
        if r.ok:
            m = re.search(r"window\.pageData\s*=\s*(\{.*?\});", r.text, re.DOTALL)
            if m:
                pd = json.loads(m.group(1))
                comp = name or pd.get("CompanyName") or pd.get("ModuleTitle") or s
                for j in (pd.get("Jobs") or []):
                    rid = str(j.get("JobId") or "")
                    title = str(j.get("JobTitle") or "").strip()
                    if not rid or not title or rid in seen:
                        continue
                    seen.add(rid)
                    jl = j.get("JobLocation") or {}
                    loc = str(j.get("LocationName") or "").strip() or ", ".join(filter(None, [jl.get("City"), jl.get("State"), jl.get("Country")]))
                    remote = bool(j.get("IsRemote")) or j.get("IndeedRemoteType") == 1 or "remote" in loc.lower() or "remote" in title.lower()
                    dept = j.get("HiringDepartment") or ""
                    posted = str(j.get("PublishedDate") or "")[:10] or None
                    desc = text(j.get("Description"))
                    apply_url = f"https://recruiting.paylocity.com/recruiting/jobs/Details/{rid}"
                    out.append(job("paylocity", f"paylocity:{s}", rid, comp, title, apply_url,
                                   location=loc, remote=remote, posted=posted, desc=desc, team=dept))
                if out or "Jobs" in pd:
                    return out
    except Exception:
        pass
    data = get(f"https://recruiting.paylocity.com/recruiting/v2/api/feed/jobs/{s}")
    comp = name or (data.get("displayName") if isinstance(data, dict) else "") or s
    for j in (data.get("jobs", []) if isinstance(data, dict) else []):
        rid = str(j.get("jobId") or j.get("publicJobId") or "")
        title = (j.get("title") or "").strip()
        if not rid or not title or rid in seen:
            continue
        seen.add(rid)
        apply_url = j.get("applyUrl") or f"https://recruiting.paylocity.com/recruiting/jobs/Details/{rid}"
        desc = text(j.get("description"))
        loc = (j.get("location") or "").strip()
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("paylocity", f"paylocity:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, desc=desc, team=j.get("department") or ""))
    return out


def paycom(s, name):  # s = "{clientkey}": parse Mantle sessionJWT from React portal HTML, fallback to legacy <a>
    url = f"https://www.paycomonline.net/v4/ats/web.php/jobs?clientkey={s}"
    r = S.get(url, timeout=TIMEOUT)
    if not r.ok:
        return []
    out = []
    comp = name or s
    seen = set()
    m_cfg = re.search(r"var\s+configsFromHost\s*=\s*(\{.*?\});", r.text, re.DOTALL)
    if m_cfg:
        try:
            cfg = json.loads(m_cfg.group(1))
            jwt = cfg.get("sessionJWT")
            lib = json.loads(cfg["libConfig"]) if isinstance(cfg.get("libConfig"), str) else (cfg.get("libConfig") or {})
            base = (lib.get("atsPortalMantleServiceUrl") or "https://portal-applicant-tracking.us-cent.paycomonline.net/").rstrip("/")
            if jwt:
                skip, take = 0, 500
                while True:
                    sr = body(lambda: S.post(f"{base}/api/ats/job-posting-previews/search",
                                             headers={"Authorization": jwt, "Content-Type": "application/json"},
                                             json={"skip": skip, "take": take, "filtersForQuery": {}}, timeout=TIMEOUT))
                    rows = (sr.get("jobPostingPreviews") or []) if isinstance(sr, dict) else []
                    for j in rows:
                        rid = str(j.get("jobId") or "")
                        title = str(j.get("jobTitle") or "").strip()
                        if not rid or not title or rid in seen:
                            continue
                        seen.add(rid)
                        loc = str(j.get("locations") or "").strip()
                        rtype = str(j.get("remoteType") or "").lower()
                        remote = "remote" in rtype or "remote" in loc.lower() or "remote" in title.lower()
                        emp = [j.get("positionType")] if j.get("positionType") else []
                        posted = str(j.get("postedOn") or "")[:10] or None
                        desc = text(j.get("description"))
                        apply_url = f"https://www.paycomonline.net/v4/ats/web.php/portal/{s}/jobs/{rid}"
                        out.append(job("paycom", f"paycom:{s}", rid, comp, title, apply_url,
                                       location=loc, remote=remote, posted=posted, desc=desc, emp=emp))
                    total = (sr.get("jobPostingPreviewsCount") or 0) if isinstance(sr, dict) else 0
                    skip += take
                    if not rows or len(rows) < take or skip >= total:
                        break
                if out:
                    return out
        except Exception:
            pass
    soup = BeautifulSoup(r.text, "html.parser")
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


def _applicantpro_like(source, domain_suffix, s, name):
    base = f"https://{s}.{domain_suffix}"
    r = S.get(f"{base}/jobs/", timeout=TIMEOUT)
    if not r.ok:
        return []
    m = re.search(r"domainId\s*:\s*\[?'?(\d+)", r.text)
    if not m:
        return []
    domain_id = m.group(1)
    comp = name or s

    def fetch_jobs(gp):
        d = get(f"{base}/core/jobs/{domain_id}", params={"getParams": json.dumps(gp)})
        return (d.get("data") or {}) if isinstance(d, dict) else {}

    first = fetch_jobs({})
    total = first.get("jobCount") or 0
    seen = {}
    for j in (first.get("jobs") or []):
        if isinstance(j, dict) and j.get("id"):
            seen[str(j["id"])] = j

    if total > len(seen):
        try:
            sf = get(f"{base}/core/jobs/{domain_id}/search-filters/all", params={
                "domainId": domain_id, "showLocation": 1, "showEmploymentType": 1,
                "showWorkplaceType": 1, "showJobBoardClassifications": 1,
                "showOrgUnit": 1, "showJobCategory": 1, "showCustomCategory": 1,
            })
        except Exception:
            sf = {}
        if isinstance(sf, dict):
            states = [x["id"] for x in (sf.get("stateId") or []) if isinstance(x, dict) and x.get("id")]
            sorts = [x["id"] for x in (sf.get("sort") or []) if isinstance(x, dict) and x.get("id")]
            orgs = [x["id"] for x in (sf.get("department") or []) if isinstance(x, dict) and x.get("id")]
            cats = [x["id"] for x in (sf.get("jobCategoryId") or []) if isinstance(x, dict) and x.get("id")]

            def add_bucket(gp):
                try:
                    data = fetch_jobs(gp)
                    jl = data.get("jobs") or []
                    for j in jl:
                        if isinstance(j, dict) and j.get("id"):
                            seen[str(j["id"])] = j
                    return data.get("jobCount") or 0, len(jl)
                except Exception:
                    return 0, 0

            overflow_states = []
            if states:
                for st, (scnt, slen) in zip(states, pmap(lambda st_id: add_bucket({"stateId": st_id}), states, 6)):
                    if scnt > slen:
                        overflow_states.append(st)
            if len(seen) < total and orgs:
                pmap(lambda o: add_bucket({"orgUnitId": o}), orgs, 6)
            if len(seen) < total and cats:
                pmap(lambda c: add_bucket({"jobCategoryId": c}), cats, 6)
            for st in overflow_states:
                if len(seen) >= total:
                    break
                for so in sorts:
                    add_bucket({"stateId": st, "sort": so})
                if len(seen) < total:
                    pmap(lambda ch, _st=st: add_bucket({"stateId": _st, "keywords": ch}), list("etaoinshrdlcumwfgypbvkjxqz"), 6)

    out = []
    for rid, j in seen.items():
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
        apply_url = j.get("jobUrl") or f"{base}/jobs/{rid}"
        out.append(job(source, f"{source}:{s}", rid, comp, title, apply_url,
                       location=loc, remote=remote, posted=posted, pay=pay, team=team, emp=emp))
    return out


def applicantpro(s, name):
    return _applicantpro_like("applicantpro", "applicantpro.com", s, name)


def isolved(s, name):
    return _applicantpro_like("isolved", "isolvedhire.com", s, name)


def betterteam(s, name):  # s = "{subdomain}" or "{domain}": React Router turbo-stream JSON + <a> fallback
    base = f"https://{s}" if "." in s else f"https://{s}.betterteam.com"
    r = S.get(base, timeout=TIMEOUT)
    if not r.ok:
        return []
    out = []
    comp = name or s
    seen = set()

    m_stream = re.search(r'streamController\.enqueue\("((?:\\.|[^"\\])*)"\)', r.text)
    if m_stream:
        try:
            arr = json.loads(json.loads('"' + m_stream.group(1) + '"'))

            def dec(val, stack=frozenset()):
                if isinstance(val, bool) or val is None or isinstance(val, (str, float)):
                    return val
                if isinstance(val, int):
                    if val < 0:
                        return "" if val == -5 else None
                    if val in stack or val >= len(arr):
                        return None
                    return dec(arr[val], stack | {val})
                if isinstance(val, list):
                    return [dec(x, stack) for x in val]
                if isinstance(val, dict):
                    return {(arr[int(k[1:])] if k.startswith("_") and k[1:].isdigit() and int(k[1:]) < len(arr) else k): dec(v, stack)
                            for k, v in val.items()}
                return val

            raw_jobs = []
            for i, el in enumerate(arr):
                if el == "routes/CareersPage" and i + 1 < len(arr) and isinstance(arr[i + 1], int):
                    cp = dec(arr[arr[i + 1]])
                    if isinstance(cp, dict) and isinstance(cp.get("jobs"), list):
                        raw_jobs = cp["jobs"]
                        break
            if not raw_jobs:
                for i, el in enumerate(arr):
                    if el == "jobs" and i + 1 < len(arr) and isinstance(arr[i + 1], list):
                        cand = dec(arr[i + 1])
                        if cand and isinstance(cand[0], dict) and "slug" in cand[0]:
                            raw_jobs = cand
                            break
            for j in raw_jobs:
                if not isinstance(j, dict):
                    continue
                rid = str(j.get("slug") or "").strip("/")
                title = str(j.get("title") or "").strip()
                if not rid or not title or rid in seen:
                    continue
                seen.add(rid)
                loc_obj = j.get("location") if isinstance(j.get("location"), dict) else {}
                loc = (loc_obj.get("rawLocation") or ", ".join(filter(None, [loc_obj.get("city"), loc_obj.get("state"), loc_obj.get("country")])) or "").strip()
                remote = bool(j.get("isRemote")) or "remote" in loc.lower() or "remote" in title.lower()
                emp = [j.get("type")] if j.get("type") else []
                out.append(job("betterteam", f"betterteam:{s}", rid, comp, title, f"{base}/{rid}",
                               location=loc, remote=remote, emp=emp))
            if out:
                return out
        except Exception:
            pass

    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a[href*='/jobs/'], a[href*='betterteam.com/'], a[href^='/']"):
        href = a.get("href") or ""
        m = re.search(r"/([a-zA-Z0-9_\-]+)/?$", href)
        if not m:
            continue
        rid = m.group(1)
        if rid in seen or rid in ("jobs", "apply", "contact", "privacy", "terms", "cookies"):
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
    base_url = r.url.split("?")[0]
    out = []
    comp = name or s
    seen = set()
    page = 1
    while page <= 100:
        if page > 1:
            r = S.get(base_url, params={"page": page}, timeout=TIMEOUT)
            if not r.ok:
                break
        soup = BeautifulSoup(r.text, "html.parser")
        if page == 1 and not name:
            hdr = soup.select_one(".job-header-left")
            if hdr:
                comp = re.sub(r"^Jobs at\s+", "", hdr.get_text(strip=True)).strip() or s
        new_jobs = 0
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
            new_jobs += 1
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
        if new_jobs == 0 or (f"page={page + 1}" not in r.text and new_jobs < 10):
            break
        page += 1
    return out


def freshteam(s, name):
    out = []
    comp = name or s
    seen = set()
    page = 1
    while page <= 50:
        r = S.get(f"https://{s}.freshteam.com/jobs", params={"page": page} if page > 1 else None, timeout=TIMEOUT)
        if not r.ok:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        new_jobs = 0
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
            new_jobs += 1
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
        if new_jobs == 0 or (f"page={page + 1}" not in r.text and new_jobs < 1000):
            break
        page += 1
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
    explicit_sid = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    r = None
    for probe_sid in ([explicit_sid] if explicit_sid is not None else [1, 2, 3, 4, 5, 6]):
        cand = S.get(f"https://{tenant}.csod.com/ux/ats/careersite/{probe_sid}/home?c={tenant}", timeout=TIMEOUT)
        if cand.ok and "csod.context" in cand.text:
            r = cand
            break
    if not r:
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
    page_size = 50
    out = []
    comp = name or tenant
    seen = set()
    site_ids = [explicit_sid] if explicit_sid is not None else [1, 2, 3, 4, 5, 6]
    for site_id in site_ids:
        page = 1
        while page <= 200:
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
            reqs = (d.get("data") or {}).get("requisitions") or []
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
                apply_url = f"https://{tenant}.csod.com/ux/ats/careersite/{site_id}/home/requisition/{rid}?c={tenant}"
                out.append(job("csod", f"csod:{s}", rid, comp, title, apply_url,
                               location=loc, remote=remote, posted=posted))
            if len(reqs) < page_size:
                break
            page += 1
    return out


def taleo(s, name):
    parts = s.split("/")
    company = parts[0]
    host = company if "." in company else f"{company}.taleo.net"
    section = parts[1] if len(parts) > 1 else "ex"
    r = S.get(f"https://{host}/careersection/{section}/joblist.ftl?lang=en", timeout=TIMEOUT)
    if (not r.ok or ("ftlform" not in r.text and "FacetedSearchSettings" not in r.text) or "Career Section Unavailable" in r.text) and len(parts) == 1:
        r2 = S.get(f"https://{host}/careersection/default.ftl", timeout=TIMEOUT)
        if r2.ok and ("ftlform" in r2.text or "FacetedSearchSettings" in r2.text):
            r = r2
            m_sec = re.search(r"/careersection/([^/]+)/", r.url)
            if m_sec:
                section = m_sec.group(1)
    if not r.ok or "Career Section Unavailable" in r.text:
        return []
    comp = name or company.split(".")[0]
    out = []
    seen = set()

    if "ftlform" not in r.text and "FacetedSearchSettings" in r.text:
        m_qs = re.search(r"queryString\s*:\s*['\"]([^'\"]+)['\"]", r.text)
        portal_qs = m_qs.group(1) if m_qs else ""
        search_url = f"https://{host}/careersection/rest/jobboard/searchjobs?lang=en&{portal_qs}" if portal_qs else f"https://{host}/careersection/rest/jobboard/searchjobs?lang=en"
        pg = 1
        while pg <= 200:
            payload = {
                "multilineEnabled": False,
                "sortingSelection": {"sortBySelectionParam": "3", "ascendingSortingOrder": "false"},
                "fieldData": {"fields": {"KEYWORD": "", "LOCATION": ""}, "valid": True},
                "filterSelectionParam": {"searchFilterSelections": []},
                "advancedSearchFiltersSelectionParam": {"searchFilterSelections": []},
                "pageNo": pg,
            }
            try:
                rp = S.post(search_url, json=payload, headers={"Content-Type": "application/json", "tz": "GMT+00:00"}, timeout=TIMEOUT)
                if not rp.ok:
                    break
                data = rp.json()
            except Exception:
                break
            reqs = data.get("requisitionList") or []
            if not reqs:
                break
            for item in reqs:
                jid = str(item.get("jobId") or "")
                cols = item.get("column") or []
                lidx = int(item.get("linkedColumn") or 0)
                title = cols[lidx] if lidx < len(cols) else ""
                if not jid or not title or jid in seen:
                    continue
                seen.add(jid)
                loc = ""
                for ci in item.get("locationsColumns") or []:
                    if isinstance(ci, int) and ci < len(cols) and cols[ci]:
                        raw_loc = cols[ci]
                        if isinstance(raw_loc, str) and raw_loc.startswith("["):
                            try:
                                raw_loc = ", ".join(str(x) for x in json.loads(raw_loc) if x)
                            except Exception:
                                pass
                        loc = str(raw_loc)
                        break
                apply_url = f"https://{host}/careersection/{section}/jobdetail.ftl?job={jid}"
                remote = "remote" in title.lower() or "remote" in loc.lower()
                out.append(job("taleo", f"taleo:{s}", jid, comp, title, apply_url, location=loc, remote=remote))
            pdata = data.get("pagingData") or {}
            tot = int(pdata.get("totalCount") or 0)
            psize = int(pdata.get("pageSize") or 25)
            if tot and pg * psize >= tot:
                break
            pg += 1
        return out

    if "ftlform" not in r.text:
        return []
    soup = BeautifulSoup(r.text, "html.parser")
    form = soup.find("form", attrs={"name": "ftlform"}) or soup.find("form")
    if not form:
        return []
    inputs = {i.get("name"): i.get("value", "") for i in form.find_all("input") if i.get("name")}

    def parse_history(raw_text):
        dec = urllib.parse.unquote(urllib.parse.unquote(raw_text or ""))
        jobs = re.findall(r'!(\d+)!\|!([^!|]+)!\|!\1!\|!\2!\|!(?:\1!\|!)+([^!|]+)!\|!([^!|]+)!', dec)
        if not jobs:
            jobs = [(m[0], m[1], "", "") for m in re.findall(r'!(\d+)!\|!([^!|]+)!\|!\1!\|!\2!', dec)]
        added = 0
        for match in jobs:
            jid, title = match[0].strip(), match[1].strip()
            loc = match[3].strip() if len(match) > 3 and match[3].strip() != match[2].strip() else ""
            if not jid or not title or jid == title or jid in seen:
                continue
            seen.add(jid)
            added += 1
            apply_url = f"https://{host}/careersection/{section}/jobdetail.ftl?job={jid}"
            remote = "remote" in title.lower() or "remote" in loc.lower()
            out.append(job("taleo", f"taleo:{s}", jid, comp, title, apply_url, location=loc, remote=remote))
        return added

    p1_added = parse_history(inputs.get("initialHistory", ""))
    act = (form.get("action") or "joblist.ftl").split(";")[0].split(".ftl")[0] + ".ajax"
    act_url = urllib.parse.urljoin(f"https://{host}/careersection/{section}/", act)
    if p1_added >= 20 and "rlPager" in r.text and inputs.get("ftlpageid"):
        ajax_q = {
            "ftlpageid": inputs.get("ftlpageid", ""),
            "ftlinterfaceid": "requisitionListInterface",
            "ftlcompid": "rlPager",
            "jsfCmdId": "rlPager",
            "ftlcompclass": "PagerComponent",
            "ftlcallback": "ftlPager_processResponse",
            "ftlajaxid": "ftlx1",
        }
        for pg in range(2, 101):
            try:
                rp = S.post(act_url, params=ajax_q, data={**inputs, "iframemode": "1", "rlPager.currentPage": str(pg)}, timeout=TIMEOUT)
                if not rp.ok or parse_history(rp.text) == 0:
                    break
            except Exception:
                break
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
    while start < 20000:
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
    comp = name or s
    out = []
    seen = set()

    def scrape_search_site(base_host, initial_html=None):
        startrow = 0
        while startrow < 10000:
            if startrow == 0 and initial_html is not None:
                html_text = initial_html
            else:
                url = f"{base_host.rstrip('/')}/search/?q=&startrow={startrow}"
                try:
                    r = S.get(url, timeout=TIMEOUT)
                    if not r.ok:
                        break
                    html_text = r.text
                except Exception:
                    break
            soup = BeautifulSoup(html_text, "html.parser")
            rows = soup.find_all("tr", class_=lambda c: c and "data-row" in c.lower())
            if not rows:
                break
            new_in_page = 0
            for tr in rows:
                a = tr.find("a", class_=lambda c: c and ("jobtitle" in c.lower() or "title" in c.lower()))
                if not a:
                    continue
                title = a.text.strip()
                href = a.get("href") or ""
                jid = href.strip("/").split("/")[-1] if href else ""
                if not jid or not title or jid in seen:
                    continue
                seen.add(jid)
                new_in_page += 1
                loc_el = tr.find(class_=lambda c: c and "location" in c.lower())
                loc = loc_el.text.strip() if loc_el else ""
                date_el = tr.find(class_=lambda c: c and "date" in c.lower())
                date_str = date_el.text.strip() if date_el else ""
                posted = None
                if date_str:
                    for fmt in ("%b %d, %Y", "%d-%b-%Y", "%Y-%m-%d"):
                        try:
                            posted = dt.datetime.strptime(date_str, fmt).date().isoformat()
                            break
                        except Exception:
                            pass
                apply_url = href if href.startswith("http") else f"{base_host.rstrip('/')}{href}"
                remote = "remote" in loc.lower() or "remote" in title.lower()
                out.append(job("successfactors", f"successfactors:{s}", jid, comp, title, apply_url, location=loc, remote=remote, posted=posted))
            if new_in_page == 0:
                break
            startrow += len(rows)

    if "." in s and "/" not in s:
        scrape_search_site(f"https://{s}")
        return out

    parts = s.split("/", 1)
    cls = parts[0]
    company = parts[1] if len(parts) > 1 else s
    sf_host = f"https://{cls}" if "." in cls else f"https://career{cls}.successfactors.com"
    url = f"{sf_host}/career?company={company}&career_ns=job_listing_summary&navBarLevel=JOB_SEARCH"
    try:
        r = S.get(url, timeout=TIMEOUT)
        if not r.ok:
            return []
    except Exception:
        return []
    # If redirected to an RMK /search/ site (e.g., Tenaris), paginate startrow cleanly across all pages
    parsed_url = urllib.parse.urlparse(r.url)
    if "/search" in parsed_url.path and parsed_url.netloc and "successfactors.com" not in parsed_url.netloc:
        scrape_search_site(f"{parsed_url.scheme}://{parsed_url.netloc}", initial_html=r.text)
        return out
    soup = BeautifulSoup(r.text, "html.parser")
    rows = soup.find_all("tr", class_=lambda c: c and "data-row" in c.lower())
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
        apply_url = href if href.startswith("http") else f"{sf_host}{href}"
        remote = "remote" in loc.lower() or "remote" in title.lower()
        out.append(job("successfactors", f"successfactors:{s}", jid, comp, title, apply_url, location=loc, remote=remote, team=team, posted=posted))
    return out


def avature(s, name):
    comp = name or s
    out = []
    seen = set()
    offset = 0
    while offset < 10000:
        url = f"https://{s}.avature.net/careers/SearchJobs/?jobRecordsPerPage=50&jobOffset={offset}"
        try:
            r = S.get(url, timeout=TIMEOUT)
            if not r.ok:
                break
        except Exception:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/JobDetail/"))
        new_jobs = 0
        page_items = 0
        for a in links:
            href = a.get("href") or ""
            title = a.text.strip()
            if not title or title.lower() == "apply":
                continue
            page_items += 1
            jid = href.strip("/").split("/")[-1]
            if not jid or jid in seen:
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
        if new_jobs == 0 or page_items == 0:
            break
        # Step by actual page size returned by the portal (servers often ignore jobRecordsPerPage and return 12/18/20)
        offset += page_items
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
    while url and len(out) < 20000:
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
    out = []
    comp = name or s
    seen = set()
    for page in range(1, 200):
        try:
            r = S.get(f"https://{s}.careerplug.com/jobs", params={"page": page} if page > 1 else None, timeout=TIMEOUT)
            if not r.ok:
                break
        except Exception:
            break
        soup = BeautifulSoup(r.text, "html.parser")
        links = soup.find_all("a", href=re.compile(r"/jobs/\d+"))
        new_jobs = 0
        for a in links:
            href = a.get("href") or ""
            m = re.search(r"/jobs/(\d+)", href)
            if not m:
                continue
            jid = m.group(1)
            if jid in seen:
                continue
            text_lines = [l.strip() for l in a.text.splitlines() if l.strip()]
            title = text_lines[0] if text_lines else "Open Role"
            if title.lower() in ("español", "apply", "view job"):
                continue
            seen.add(jid)
            new_jobs += 1
            loc = ""
            for i, line in enumerate(text_lines):
                if "location:" in line.lower() and i + 1 < len(text_lines):
                    loc = text_lines[i + 1]
            remote = "remote" in loc.lower() or "remote" in title.lower()
            apply_url = f"https://{s}.careerplug.com/jobs/{jid}"
            out.append(job("careerplug", f"careerplug:{s}", jid, comp, title, apply_url, location=loc, remote=remote))
        if new_jobs == 0 or (f"page={page + 1}" not in r.text and new_jobs < 30):
            break
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


def pageup(s, name):  # http://careers.pageuppeople.com/{s}/caw/en/listing/?page={p}&page-items=100
    out = []
    comp = name or s
    seen = set()
    for page in range(1, 50):
        try:
            r = S.get(f"http://careers.pageuppeople.com/{s}/caw/en/listing/", params={"page": page, "page-items": 100}, timeout=TIMEOUT)
            if page == 1 and (not r.ok or "job-link" not in r.text):
                r = S.get(f"http://careers.pageuppeople.com/{s}/caw/en/", timeout=TIMEOUT)
            if not r.ok:
                break
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            break
        new_jobs = 0
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
            new_jobs += 1
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
        if new_jobs == 0 or new_jobs < 100:
            break
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
        if not pages.get("Next") or page >= 200:
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


def tribepad(s, name):  # https://{s}.tribepad.com/v2/jobs?page=N
    out = []
    comp = name or s
    seen = set()
    for page in range(1, 100):
        try:
            r = S.get(f"https://{s}.tribepad.com/v2/jobs", params={"page": page} if page > 1 else None, timeout=TIMEOUT)
            if not r.ok:
                break
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            break
        new_jobs = 0
        for a in soup.find_all("a", href=lambda h: h and "record=" in h):
            href = a.get("href") or ""
            m = re.search(r"record=(\d+)", href)
            if not m:
                continue
            rid = m.group(1)
            if rid in seen:
                continue
            t_el = a.find(class_="sitebuilder-job-results-item-title") or a.find("h3")
            title = t_el.get_text(strip=True) if t_el else ""
            if not title:
                continue
            seen.add(rid)
            new_jobs += 1
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
        if new_jobs == 0 or f"page={page + 1}" not in r.text:
            break
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


def factorial(s, name):  # Factorial HR public career portal
    try:
        r = S.get(f"https://{s}.factorial.com/", timeout=TIMEOUT)
        if not r.ok:
            r = S.get(f"https://{s}.factorialhr.com/", timeout=TIMEOUT)
        if not r.ok:
            return []
        soup = BeautifulSoup(r.text, "html.parser")
    except Exception:
        return []
    comp = name or s
    out = []
    seen = set()
    for a in soup.find_all("a", href=re.compile(r"/job_posting/")):
        href = a.get("href") or ""
        m = re.search(r"/job_posting/(?:.+?-)?([a-zA-Z0-9]+)$", href.rstrip("/"))
        if not m:
            continue
        jid = m.group(1)
        if jid in seen:
            continue
        seen.add(jid)
        parent = a.find_parent(class_=re.compile(r"card|posting|job|item|row|container", re.I)) or a.parent
        t_items = [part.strip() for part in parent.get_text(separator="\n", strip=True).split("\n") if part.strip()]
        title = t_items[0] if t_items else a.get_text(strip=True)
        dept, workplace = None, None
        for item in t_items[1:]:
            low = item.lower()
            if low in ("remote", "hybrid", "on-site", "onsite"):
                workplace = item
            elif low not in ("apply now", "view offer", "apply", "view"):
                if not dept:
                    dept = item
        is_remote = (workplace and "remote" in workplace.lower()) or ("remote" in title.lower())
        out.append(job("factorial", f"factorial:{s}", jid, comp, title, href,
                       location=workplace, remote=is_remote, team=dept))
    return out


def aidevboard():  # 8,400+ AI/ML engineering roles from 489 AI-first companies
    out = []
    page = 1
    while len(out) < AIDEV_CAP:
        d = get("https://aidevboard.com/api/v1/jobs", params={"per_page": 50, "page": page})
        jobs = d.get("jobs", [])
        if not jobs:
            break
        for j in jobs:
            pay = ([int(j["salary_min"]), int(j["salary_max"]), "USD"]
                   if j.get("salary_min") and j.get("salary_max") else None)
            out.append(job("aidevboard", "aidevboard", j["id"], clean_company(j.get("company_name")),
                           j.get("title"), j.get("apply_url") or j.get("url"), j.get("location"),
                           j.get("workplace") == "remote", j.get("published_at"),
                           desc=text(j.get("description")), tags=j.get("tags") or [], pay=pay))
        if page >= (d.get("total_pages") or d.get("pages") or 1) or d.get("has_next") is False:
            break
        page += 1
    return out


def larajobs():  # curated remote/hybrid Laravel and PHP community jobs
    out = []
    d = get("https://larajobs.com/api/jobs")
    for j in d:
        tags = [t.get("name") or t.get("slug") for t in (j.get("tags") or []) if isinstance(t, dict)]
        out.append(job("larajobs", "larajobs", str(j["id"]), clean_company(j.get("organization")),
                       j.get("title"), j.get("url"), j.get("location"),
                       "remote" in (j.get("location") or "").lower(), j.get("published_at"),
                       emp=[j.get("type")] if j.get("type") else None, tags=tags))
    return out


def fourdayweek():  # 4-day work week and flexible remote jobs via RSS feed
    r = S.get("https://4dayweek.io/feed", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    clean_xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)[a-zA-Z0-9#]+;", " ", r.text)
    for i in ET.fromstring(clean_xml).iter("item"):
        author = i.findtext("author") or i.findtext("{http://purl.org/dc/elements/1.1/}creator") or ""
        title = i.findtext("title") or ""
        suffix = f" at {author}"
        if author and title.endswith(suffix):
            title = title[:-len(suffix)].strip()
        link = i.findtext("link") or ""
        job_id = link.rstrip("/").rsplit("-", 1)[-1] or link.rstrip("/").rsplit("/", 1)[-1]
        out.append(job("fourdayweek", "fourdayweek", job_id, clean_company(author), title, link,
                       None, True, rss_day(i.findtext("pubDate")),
                       desc=text(i.findtext("description"))))
    return out


def nodesk():  # curated remote jobs RSS feed; titles formatted as 'Role at Company'
    r = S.get("https://nodesk.co/remote-jobs/index.xml", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    clean_xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)[a-zA-Z0-9#]+;", " ", r.text)
    for i in ET.fromstring(clean_xml).iter("item"):
        raw_title = i.findtext("title") or ""
        parts = raw_title.rsplit(" at ", 1)
        title = parts[0].strip() if len(parts) == 2 else raw_title
        company = parts[1].strip() if len(parts) == 2 else ""
        link = i.findtext("link") or ""
        job_id = link.rstrip("/").rsplit("/", 1)[-1]
        out.append(job("nodesk", "nodesk", job_id, clean_company(company), title, link,
                       None, True, rss_day(i.findtext("pubDate")),
                       desc=text(i.findtext("description"))))
    return out


def remotepython():  # remote Python engineering jobs RSS feed; titles formatted as 'Role at Company'
    r = S.get("https://www.remotepython.com/latest/jobs/feed/", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    clean_xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)[a-zA-Z0-9#]+;", " ", r.text)
    for i in ET.fromstring(clean_xml).iter("item"):
        raw_title = i.findtext("title") or ""
        parts = raw_title.rsplit(" at ", 1)
        title = parts[0].strip() if len(parts) == 2 else raw_title
        company = parts[1].strip() if len(parts) == 2 else ""
        link = i.findtext("link") or ""
        job_id = link.rstrip("/").rsplit("/", 1)[-1]
        out.append(job("remotepython", "remotepython", job_id, clean_company(company), title, link,
                       None, True, rss_day(i.findtext("pubDate")),
                       desc=text(i.findtext("description"))))
    return out



NFJ_CAP = 50000


def nofluffjobs():  # 15k+ tech & software engineering jobs across Europe
    d = get("https://nofluffjobs.com/api/posting")
    postings = d.get("postings", []) if isinstance(d, dict) else d
    out = []
    for p in postings[:NFJ_CAP]:
        jid = str(p.get("id") or "")
        title = p.get("title") or ""
        if not jid or not title:
            continue
        sal = p.get("salary") or {}
        pay = None
        if sal.get("from") and sal.get("to") and sal.get("currency"):
            period = str(sal.get("period") or "").lower()
            low, high = int(sal["from"]), int(sal["to"])
            curr = sal["currency"]
            if period == "month":
                pay = [low * 12, high * 12, curr]
            elif period in ("year", "annually", "annual"):
                pay = [low, high, curr]
        posted = None
        if p.get("posted"):
            try:
                posted = dt.datetime.fromtimestamp(p["posted"] / 1000, tz=dt.timezone.utc).date().isoformat()
            except Exception:
                pass
        url = f"https://nofluffjobs.com/job/{p['url']}" if p.get("url") else None
        remote = bool(p.get("fullyRemote")) or "remote" in jid.lower()
        sen = p.get("seniority") or []
        hint = "junior" if "Junior" in sen or "Trainee" in sen else None
        out.append(job("nofluffjobs", "nofluffjobs", jid, clean_company(p.get("name")), title, url,
                       location="Remote" if remote else None, remote=remote, posted=posted,
                       team=p.get("category"), pay=pay, hint=hint))
    return out


def landingjobs():  # curated tech roles from Landing.jobs
    d = get("https://landing.jobs/api/v1/jobs")
    out = []
    for j in (d if isinstance(d, list) else []):
        jid = str(j.get("id") or "")
        title = j.get("title") or ""
        if not jid or not title:
            continue
        low = j.get("gross_salary_low")
        high = j.get("gross_salary_high")
        curr = j.get("currency_code") or "EUR"
        pay = [int(low), int(high), curr] if low and high else None
        posted = str(j.get("published_at") or "")[:10] or None
        remote = bool(j.get("remote"))
        tags = j.get("tags") or []
        desc = text(j.get("role_description"))
        out.append(job("landingjobs", "landingjobs", jid, "Landing.jobs Partner", title, j.get("url"),
                       location="Remote" if remote else None, remote=remote, posted=posted,
                       desc=desc, tags=tags, pay=pay))
    return out


def cryptocurrencyjobs():  # Web3, DeFi and crypto career postings
    r = S.get("https://cryptocurrencyjobs.co/index.xml", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    clean_xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)[a-zA-Z0-9#]+;", " ", r.text)
    for i in ET.fromstring(clean_xml).iter("item"):
        raw_title = i.findtext("title") or ""
        parts = raw_title.rsplit(" at ", 1)
        title = parts[0].strip() if len(parts) == 2 else raw_title
        company = parts[1].strip() if len(parts) == 2 else ""
        link = i.findtext("link") or ""
        jid = link.rstrip("/").rsplit("/", 1)[-1]
        out.append(job("cryptocurrencyjobs", "cryptocurrencyjobs", jid, clean_company(company), title, link,
                       None, True, rss_day(i.findtext("pubDate")), desc=text(i.findtext("description"))))
    return out


def cryptojobslist():  # crypto and blockchain ecosystem positions
    r = S.get("https://cryptojobslist.com/rss.xml", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    clean_xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)[a-zA-Z0-9#]+;", " ", r.text)
    for i in ET.fromstring(clean_xml).iter("item"):
        raw_title = i.findtext("title") or ""
        title = raw_title.split(" - ")[0].strip() if " - " in raw_title else raw_title
        link = i.findtext("link") or ""
        m = re.search(r"-at-([a-zA-Z0-9\-]+)$", link.rstrip("/"))
        company = m.group(1).replace("-", " ").title() if m else ""
        jid = link.rstrip("/").rsplit("/", 1)[-1]
        out.append(job("cryptojobslist", "cryptojobslist", jid, clean_company(company), title, link,
                       None, True, rss_day(i.findtext("pubDate")), desc=text(i.findtext("description"))))
    return out


def golangprojects():  # Go language remote developer listings
    r = S.get("https://www.golangprojects.com/rss.xml", timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    clean_xml = re.sub(r"&(?!(?:amp|lt|gt|quot|apos);)[a-zA-Z0-9#]+;", " ", r.text)
    for i in ET.fromstring(clean_xml).iter("item"):
        raw_title = i.findtext("title") or ""
        parts = re.split(r"[ \s]*[@|at][ \s]*", raw_title, maxsplit=1)
        title = parts[0].strip() if len(parts) == 2 else raw_title
        company = parts[1].strip() if len(parts) == 2 else ""
        link = i.findtext("link") or ""
        jid = link.rstrip("/").rsplit("/", 1)[-1].replace(".html", "")
        out.append(job("golangprojects", "golangprojects", jid, clean_company(company), title, link,
                       None, True, rss_day(i.findtext("pubDate")), desc=text(i.findtext("description"))))
    return out



# ---------- New ATS Crawlers ----------
def hireology(s, name):
    out = []
    seen = set()
    page = 1
    while page <= 500:
        try:
            d = get(f"https://api.hireology.com/v2/public/careers/{s}", params={"page": page, "page_size": 100})
        except Exception:
            break
        if not isinstance(d, dict):
            break
        rows = d.get("data") or []
        if not rows:
            break
        for j in rows:
            jid = str(j.get("id") or "")
            if not jid or not j.get("name") or jid in seen:
                continue
            seen.add(jid)
            locs = []
            for l in j.get("locations") or []:
                if isinstance(l, dict):
                    parts = filter(None, [l.get("city"), l.get("state")])
                    loc_str = ", ".join(parts)
                    if loc_str and loc_str not in locs:
                        locs.append(loc_str)
            comp_obj = j.get("compensation") or {}
            pay = None
            if isinstance(comp_obj, dict):
                min_c = comp_obj.get("comp_range_min")
                max_c = comp_obj.get("comp_range_max")
                if min_c and max_c:
                    try:
                        pay = [int(float(min_c)), int(float(max_c)), "USD"]
                    except (ValueError, TypeError):
                        pass
            desc = text(j.get("job_description") or "")
            team = (j.get("job_family") or {}).get("name") if isinstance(j.get("job_family"), dict) else ""
            emp_status = j.get("employment_status") or ""
            emp = [emp_status] if emp_status else None
            out.append(job("hireology", f"hireology:{s}", jid, name or (j.get("organization") or {}).get("name") or s,
                           j["name"], j.get("career_site_url") or f"https://careers.hireology.com/{s}/{jid}/description",
                           location="; ".join(locs), remote=j.get("remote") is True, posted=j.get("created_at"),
                           desc=desc, team=team, emp=emp, pay=pay))
        if len(rows) < 100 or len(out) >= int(d.get("count") or 0):
            break
        page += 1
    return out


FEISHU_BROWSER_UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"

def feishu(s, name):
    forced = ""
    host = s
    if "/" in host:
        host, forced = host.split("/", 1)
        forced = forced.strip("/")
    if "." not in host:
        host = f"{host}.jobs.feishu.cn"

    path = forced
    if not path:
        try:
            r = S.get(f"https://{host}/", headers={"User-Agent": FEISHU_BROWSER_UA, "Accept-Language": "zh-CN,zh;q=0.9"}, timeout=TIMEOUT)
            m = re.search(r'id="js-websiteInfo"[^>]*>(.*?)</script>', r.text, re.S)
            if m:
                path = ((json.loads(m.group(1)).get("website_info")) or {}).get("path", "")
        except Exception:
            pass
    if not path:
        path = "index"

    headers = {
        "User-Agent": FEISHU_BROWSER_UA,
        "Origin": f"https://{host}",
        "Referer": f"https://{host}/",
        "Portal-Channel": "office",
        "Portal-Platform": "pc",
        "website-path": path,
        "Content-Type": "application/json"
    }

    out = []
    seen = set()
    limit = 50
    for page in range(500):
        body_payload = {
            "keyword": "", "limit": limit, "offset": page * limit, "portal_type": 2,
            "job_category_id_list": [], "location_code_list": [], "subject_id_list": [],
            "recruitment_id_list": [], "job_function_id_list": []
        }
        try:
            res = body(lambda: S.post(f"https://{host}/api/v1/search/job/posts", json=body_payload, headers=headers, timeout=TIMEOUT))
        except Exception:
            break
        data = (res.get("data") or {}) if isinstance(res, dict) else {}
        posts = data.get("job_post_list") or []
        if not posts:
            break
        new_jobs = 0
        for j in posts:
            jid = str(j.get("id") or "")
            if not jid or not j.get("title") or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            cities = ", ".join(c.get("name", "") for c in (j.get("city_list") or []) if isinstance(c, dict) and c.get("name"))
            jf = j.get("job_function") or {}
            team = jf.get("name", "") if isinstance(jf, dict) else ""
            desc = text("\n\n".join(x for x in [j.get("description", ""), j.get("requirement", "")] if x))
            post_time = j.get("publish_time")
            posted_date = None
            if post_time:
                try:
                    posted_date = time.strftime("%Y-%m-%d", time.gmtime(int(post_time) / 1000))
                except Exception:
                    posted_date = str(post_time)[:10]
            url = f"https://{host}/{path}/position/{jid}/detail"
            out.append(job("feishu", f"feishu:{s}", jid, name or s.split(".")[0], j["title"], url,
                           location=cities, posted=posted_date, desc=desc, team=team))
        if new_jobs == 0 or len(posts) < limit or len(out) >= int(data.get("count") or 0):
            break
    return out


def _decrypt_moka(resp):
    if isinstance(resp, dict) and "data" in resp and "necromancer" in resp:
        try:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            from cryptography.hazmat.primitives import padding
            import base64
            key = resp["necromancer"].encode("utf-8")
            iv = b"de7c21ed8d6f50fe"
            cipher = Cipher(algorithms.AES(key), modes.CBC(iv))
            decryptor = cipher.decryptor()
            pt = decryptor.update(base64.b64decode(resp["data"])) + decryptor.finalize()
            unpadder = padding.PKCS7(128).unpadder()
            dec = unpadder.update(pt) + unpadder.finalize()
            return json.loads(dec.decode("utf-8"))
        except Exception:
            return resp
    return resp


def moka(s, name):
    delim = "/" if "/" in s else ":"
    parts = s.split(delim, 1)
    org_id = parts[0]
    site_id = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else None
    if not site_id:
        return []

    headers = {"Origin": "https://app.mokahr.com", "Content-Type": "application/json"}
    out = []
    seen = set()
    limit = 50
    for page in range(500):
        payload = {"orgId": org_id, "siteId": site_id, "locale": "zh-CN", "limit": limit, "offset": page * limit}
        try:
            raw = body(lambda: S.post("https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2", json=payload, headers=headers, timeout=TIMEOUT))
        except Exception:
            break
        dec = _decrypt_moka(raw)
        data = dec.get("data") if isinstance(dec, dict) else {}
        if isinstance(data, str):
            try:
                data = json.loads(data)
            except Exception:
                data = {}
        if not isinstance(data, dict):
            data = {}
        jobs = data.get("jobs") or data.get("list") or []
        if not isinstance(jobs, list) or not jobs:
            break
        new_jobs = 0
        for j in jobs:
            if not isinstance(j, dict):
                continue
            jid = str(j.get("id") or "")
            if not jid or not j.get("title") or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            locs = ", ".join(x.get("cityName", "") for x in (j.get("locations") or []) if isinstance(x, dict) and x.get("cityName"))
            dep = j.get("department") or {}
            team = dep.get("name", "") if isinstance(dep, dict) else ""
            desc = text(j.get("jobDescription") or "")
            posted_date = j.get("publishedAt") or j.get("createdAt")
            if posted_date and len(str(posted_date)) >= 10:
                posted_date = str(posted_date)[:10]
            pay = None
            mn, mx = j.get("minSalary"), j.get("maxSalary")
            if mn and mx:
                try:
                    pay = [int(mn), int(mx), "CNY"]
                except (ValueError, TypeError):
                    pass
            url = f"https://app.mokahr.com/social-recruitment/{org_id}/{site_id}/job/{jid}"
            out.append(job("moka", f"moka:{s}", jid, name or org_id, j["title"], url,
                           location=locs, posted=posted_date, desc=desc, team=team, pay=pay))
        if new_jobs == 0 or len(jobs) < limit:
            break
    return out


def beisen(s, name):
    slug = s.replace(".zhiye.com", "").strip()
    headers = {
        "Origin": f"https://{slug}.zhiye.com",
        "Referer": f"https://{slug}.zhiye.com/social/jobs",
        "Content-Type": "application/json"
    }
    out = []
    seen = set()
    limit = 50
    for page in range(500):
        body_payload = {
            "PageIndex": page, "PageSize": limit, "LocId": [], "Category": ["1"],
            "KeyWords": "", "SpecialType": 0, "PortalId": "",
            "DisplayFields": ["Category", "Kind", "LocId", "PostDate", "Salary"]
        }
        try:
            res = body(lambda: S.post(f"https://{slug}.zhiye.com/api/Jobad/GetJobAdPageList", json=body_payload, headers=headers, timeout=TIMEOUT))
        except Exception:
            break
        posts = (res.get("Data") or []) if isinstance(res, dict) else []
        if not posts:
            break
        new_jobs = 0
        for j in posts:
            jid = str(j.get("JobAdId") or j.get("Id") or "")
            title = j.get("JobAdName") or ""
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            locs = ", ".join(j.get("LocNames") or [])
            post_date = j.get("PostDate")
            date_str = str(post_date)[:10] if post_date and len(str(post_date)) >= 10 else None
            desc = text(j.get("Duty") or "")
            url = f"https://{slug}.zhiye.com/social/jobs"
            out.append(job("beisen", f"beisen:{s}", jid, name or slug, title, url,
                           location=locs, posted=date_str, desc=desc))
        if new_jobs == 0 or len(posts) < limit or len(out) >= int(res.get("Count") or 0):
            break
    return out


def tencent(s, name):
    out = []
    seen = set()
    size = 100
    for page in range(1, 200):
        d = get("https://careers.tencent.com/tencentcareer/api/post/Query",
                params={"pageSize": size, "pageIndex": page, "language": "zh-cn"})
        data = d.get("Data") or {}
        posts = data.get("Posts") or []
        if not posts:
            break
        for j in posts:
            jid = str(j.get("PostId") or "")
            title = j.get("RecruitPostName") or ""
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            loc = j.get("LocationName") or ""
            team = j.get("CategoryName") or j.get("BGName") or ""
            desc = text(j.get("Responsibility") or "")
            update_time = j.get("LastUpdateTime") or ""
            posted_date = None
            m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", update_time)
            if m:
                posted_date = f"{m.group(1)}-{m.group(2)}-{m.group(3)}"
            url = j.get("PostURL") or f"https://careers.tencent.com/jobdesc.html?postId={jid}"
            out.append(job("tencent", f"tencent:{s}", jid, name or "Tencent", title, url,
                           location=loc, posted=posted_date, desc=desc, team=team))
        if len(posts) < size or len(out) >= int(data.get("Count") or 0):
            break
    return out


def bytedance(s, name):
    headers = {"User-Agent": FEISHU_BROWSER_UA, "portal-channel": "office", "portal-platform": "pc", "Content-Type": "application/json"}
    out = []
    seen = set()
    limit = 50
    for page in range(500):
        body_payload = {
            "keyword": "", "limit": limit, "offset": page * limit,
            "job_category_id_list": [], "location_code_list": [],
            "subject_id_list": [], "recruitment_id_list": []
        }
        try:
            res = body(lambda: S.post("https://jobs.bytedance.com/api/v1/search/job/posts", json=body_payload, headers=headers, timeout=TIMEOUT))
        except Exception:
            break
        data = (res.get("data") or {}) if isinstance(res, dict) else {}
        posts = data.get("job_post_list") or []
        if not posts:
            break
        new_jobs = 0
        for j in posts:
            jid = str(j.get("id") or "")
            title = j.get("title") or ""
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            city = (j.get("city_info") or {}).get("name", "") if isinstance(j.get("city_info"), dict) else ""
            cat = j.get("job_category") or {}
            team = cat.get("name", "") if isinstance(cat, dict) else ""
            desc = text("\n\n".join(x for x in [j.get("description", ""), j.get("requirement", "")] if x))
            post_time = j.get("publish_time")
            posted_date = None
            if post_time:
                try:
                    posted_date = time.strftime("%Y-%m-%d", time.gmtime(int(post_time) / 1000))
                except Exception:
                    posted_date = str(post_time)[:10]
            url = f"https://jobs.bytedance.com/experienced/position/{jid}/detail"
            out.append(job("bytedance", f"bytedance:{s}", jid, name or "ByteDance", title, url,
                           location=city, posted=posted_date, desc=desc, team=team))
        if new_jobs == 0 or len(posts) < limit or len(out) >= int(data.get("count") or 0):
            break
    return out


def netease(s, name):
    headers = {"Content-Type": "application/json"}
    out = []
    seen = set()
    size = 100
    for page in range(1, 200):
        payload = {"currentPage": page, "pageSize": size, "keyword": ""}
        try:
            res = body(lambda: S.post("https://hr.163.com/api/hr163/position/queryPage", json=payload, headers=headers, timeout=TIMEOUT))
        except Exception:
            break
        data = (res.get("data") or {}) if isinstance(res, dict) else {}
        posts = data.get("list") or []
        if not posts:
            break
        new_jobs = 0
        for j in posts:
            jid = str(j.get("id") or "")
            title = j.get("name") or ""
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            locs = ", ".join(j.get("workPlaceNameList") or [])
            team = j.get("firstDepName") or j.get("firstPostTypeName") or ""
            desc = text(j.get("description") or j.get("requirement") or "")
            up_time = j.get("updateTime")
            posted_date = None
            if up_time:
                try:
                    posted_date = time.strftime("%Y-%m-%d", time.gmtime(int(up_time) / 1000))
                except Exception:
                    pass
            url = f"https://hr.163.com/position/detail?id={jid}"
            out.append(job("netease", f"netease:{s}", jid, name or "NetEase", title, url,
                           location=locs, posted=posted_date, desc=desc, team=team))
        if new_jobs == 0 or len(posts) < size or len(out) >= int(data.get("total") or 0):
            break
    return out


def jd(s, name):
    headers = {"Referer": "https://zhaopin.jd.com/web/job/job_info_list/3", "X-Requested-With": "XMLHttpRequest"}
    out = []
    seen = set()
    size = 50
    for page in range(1, 500):
        form = {"pageIndex": page, "pageSize": size, "jobType": 3}
        try:
            res = body(lambda: S.post("https://zhaopin.jd.com/web/job/job_list", data=form, headers=headers, timeout=TIMEOUT))
        except Exception:
            break
        rows = res if isinstance(res, list) else (res.get("data") or res.get("list") or [])
        if not rows:
            break
        new_jobs = 0
        for j in rows:
            jid = str(j.get("positionId") or "")
            title = j.get("positionName") or ""
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            loc = j.get("workCity") or ""
            team = j.get("positionDeptName") or ""
            desc = text(j.get("qualification") or j.get("workContent") or "")
            pub = j.get("formatPublishTime") or j.get("publishTime") or ""
            posted_date = str(pub)[:10] if len(str(pub)) >= 10 else None
            url = f"https://zhaopin.jd.com/web/job/job_detail/{jid}"
            out.append(job("jd", f"jd:{s}", jid, name or "JD.com", title, url,
                           location=loc, posted=posted_date, desc=desc, team=team))
        if new_jobs == 0 or len(rows) < size:
            break
    return out


def unitree(s, name):
    import unicodedata
    headers = {"Accept-Language": "zh-CN,zh;q=0.9", "Origin": "https://www.unitree.com", "Referer": "https://www.unitree.com/"}
    try:
        d = get("https://api.unitree.com/website/job/list", params={"perPage": 300}, headers=headers)
    except Exception:
        return []
    items = ((d.get("data") or {}).get("items")) or []
    out = []
    for j in items:
        jid = str(j.get("id") or "")
        title = unicodedata.normalize("NFKC", j.get("title") or "")
        if not jid or not title:
            continue
        jd_desc = unicodedata.normalize("NFKC", "\n\n".join(x for x in [j.get("duty", ""), j.get("ability", "")] if x))
        sal = (j.get("salary") or "").strip()
        pay = None
        m = re.match(r"(\d+)-(\d+)", sal)
        if m:
            pay = [int(m.group(1)) * 1000 * 12, int(m.group(2)) * 1000 * 12, "CNY"]
        post_time = j.get("postTime") or ""
        posted_date = post_time.replace("/", "-") if post_time else None
        url = f"https://www.unitree.com/cn/position/{jid}"
        out.append(job("unitree", f"unitree:{s}", jid, name or "Unitree Robotics", title, url,
                       location=str(j.get("cityId") or ""), posted=posted_date, desc=text(jd_desc), pay=pay))
    return out


def baidu(s, name):
    headers = {
        "User-Agent": FEISHU_BROWSER_UA,
        "Referer": "https://talent.baidu.com/jobs/social",
        "Accept": "application/json, text/plain, */*",
    }
    out = []
    seen = set()
    size = 20
    for rtype in ("SOCIAL", "GRADUATE", "INTERN"):
        cat_count = 0
        for page in range(1, 500):
            form = {"recruitType": rtype, "pageSize": size, "keyWord": "", "curPage": page}
            try:
                res = body(lambda: S.post("https://talent.baidu.com/httservice/getPostListNew", data=form, headers=headers, timeout=TIMEOUT))
            except Exception:
                break
            data = res.get("data") or {}
            rows = data.get("list") or []
            if not rows:
                break
            cat_count += len(rows)
            for j in rows:
                jid = str(j.get("postId") or "")
                title = j.get("name") or ""
                if not jid or not title or jid in seen:
                    continue
                seen.add(jid)
                dept = j.get("orgName") or j.get("bgShortName") or ""
                loc = j.get("workPlace") or ""
                posted_date = j.get("publishDate") or j.get("updateDate") or None
                jd_desc = "\n\n".join(x for x in [j.get("workContent", ""), j.get("serviceCondition", "")] if x)
                url = f"https://talent.baidu.com/jobs/social-detail/{jid}"
                out.append(job("baidu", f"baidu:{s}", jid, name or "Baidu", title, url,
                               location=loc, team=dept, posted=posted_date, desc=text(jd_desc)))
            total = int(data.get("total") or 0)
            if cat_count >= total or len(rows) < size:
                break
    return out


def yc(s, name):
    out = []
    seen = set()
    # 1. Work at a Startup company portal
    try:
        r = S.get(f"https://www.workatastartup.com/companies/{s}", timeout=TIMEOUT)
        if r.status_code == 200:
            m = re.search(r'data-page="([^"]+)"', r.text)
            if m:
                data = json.loads(html.unescape(m.group(1)))
                c = data.get("props", {}).get("company", {})
                c_name = name or c.get("name") or s
                desc = text(c.get("hiringDescriptionHtml") or c.get("techDescriptionHtml") or c.get("description") or "")
                jobs = c.get("jobs", []) or []
                for j in jobs:
                    if not isinstance(j, dict):
                        continue
                    jid = j.get("id")
                    title = j.get("title")
                    if not jid or not title:
                        continue
                    s_jid = str(jid)
                    if s_jid in seen:
                        continue
                    seen.add(s_jid)
                    loc = j.get("location") or ""
                    remote = "remote" in loc.lower()
                    emp = [j.get("jobType")] if j.get("jobType") else []
                    sal = j.get("salaryRange") or ""
                    pay = find_pay(sal) if sal else None
                    url = f"https://www.workatastartup.com/jobs/{jid}"
                    out.append(job("yc", f"yc:{s}", s_jid, c_name, title, url,
                                   location=loc, remote=remote, emp=emp, pay=pay, desc=desc))
    except Exception:
        pass

    # 2. Y Combinator direct company profile
    try:
        r = S.get(f"https://www.ycombinator.com/companies/{s}", timeout=TIMEOUT)
        if r.status_code == 200:
            m = re.search(r'data-page="([^"]+)"', r.text)
            if m:
                data = json.loads(html.unescape(m.group(1)))
                c = data.get("props", {}).get("company", {})
                c_name = name or c.get("name") or s
                desc = text(c.get("long_description") or c.get("one_liner") or "")
                jobs = c.get("jobPostings", []) or []
                for j in jobs:
                    if not isinstance(j, dict):
                        continue
                    jid = j.get("id")
                    title = j.get("title")
                    if not jid or not title:
                        continue
                    s_jid = str(jid)
                    if s_jid in seen:
                        continue
                    seen.add(s_jid)
                    loc = j.get("location") or ""
                    remote = "remote" in loc.lower()
                    emp = [j.get("type")] if j.get("type") else []
                    sal = j.get("salaryRange") or ""
                    pay = find_pay(sal) if sal else None
                    j_url = j.get("url") or ""
                    if j_url.startswith("/"):
                        url = f"https://www.ycombinator.com{j_url}"
                    elif j_url:
                        url = j_url
                    else:
                        url = f"https://www.workatastartup.com/jobs/{jid}"
                    out.append(job("yc", f"yc:{s}", s_jid, c_name, title, url,
                                   location=loc, remote=remote, emp=emp, pay=pay, desc=desc))
    except Exception:
        pass

    return out


def jibe(s, name):
    comp = name or s
    out = []
    seen = set()
    page = 1
    base = f"https://{s}" if "." in s else f"https://{s}.jibeapply.com"
    while page <= 500:
        url = f"{base}/api/jobs?page={page}&limit=100"
        try:
            r = S.get(url, timeout=TIMEOUT)
            if not r.ok:
                break
            d = r.json()
        except Exception:
            break
        jobs = d.get("jobs") or []
        if not jobs:
            break
        new_jobs = 0
        for item in jobs:
            data = item.get("data") if isinstance(item, dict) else {}
            if not data:
                continue
            jid = str(data.get("slug") or data.get("req_id") or item.get("id") or "")
            title = (data.get("title") or item.get("title") or "").strip()
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            loc = (data.get("full_location") or data.get("short_location") or data.get("location_name") or data.get("city") or "").strip()
            remote = bool(data.get("remote")) or "remote" in loc.lower() or "remote" in title.lower()
            emp = [data.get("employment_type")] if data.get("employment_type") else []
            desc = data.get("description") or data.get("short_description") or ""
            posted = None
            p_date = data.get("posted_date") or ""
            if p_date:
                try:
                    posted = p_date.split("T")[0]
                except Exception:
                    pass
            apply_url = data.get("apply_url") or f"{base}/jobs/{jid}"
            cat = data.get("category") or data.get("categories") or ""
            team = ", ".join(cat) if isinstance(cat, list) else str(cat).strip()
            out.append(job("jibe", f"jibe:{s}", jid, comp, title, apply_url,
                           location=loc, remote=remote, emp=emp, desc=desc, posted=posted, team=team))
        if new_jobs == 0:
            break
        page += 1
    return out


def talentbrew(s, name):
    comp = name or s
    out = []
    seen = set()
    base = f"https://{s}" if "." in s else f"https://{s}.talentbrew.com"

    def parse_html(html_text):
        soup = BeautifulSoup(html_text, "html.parser")
        links = soup.find_all("a", href=lambda h: h and "/job/" in h)
        new_jobs = 0
        for a in links:
            href = a.get("href") or ""
            if not href or "/search-jobs" in href:
                continue
            jid = a.get("data-job-id") or href.rstrip("/").split("/")[-1]
            t_el = a.find(["h2", "h3"]) or a
            title = t_el.get_text(strip=True)
            if not jid or not title or jid in seen:
                continue
            seen.add(jid)
            new_jobs += 1
            parent = a.find_parent(["li", "div", "tr"]) or a
            loc_el = parent.find(class_=lambda c: c and "location" in c.lower())
            loc = loc_el.get_text(strip=True) if loc_el else ""
            remote = "remote" in loc.lower() or "remote" in title.lower()
            apply_url = href if href.startswith("http") else f"{base}{href}"
            out.append(job("talentbrew", f"talentbrew:{s}", str(jid), comp, title, apply_url,
                           location=loc, remote=remote))
        return new_jobs

    def pull_sitemap():
        added = 0
        try:
            sr = S.get(f"{base}/sitemap.xml", timeout=TIMEOUT)
            if sr.ok and "<loc>" in sr.text:
                for loc_url in re.findall(r"<loc>(https?://[^<]+/job/[^<]+)</loc>", sr.text):
                    parts = [urllib.parse.unquote(p) for p in urllib.parse.urlparse(loc_url).path.strip("/").split("/")]
                    if len(parts) < 3 or parts[0] != "job":
                        continue
                    jid = parts[-1]
                    if not jid or jid in seen:
                        continue
                    seen.add(jid)
                    added += 1
                    slug_title = parts[2] if len(parts) >= 4 else parts[1]
                    title = slug_title.replace("-", " ").strip().title()
                    loc_part = parts[1].replace("-", " ").strip().title() if len(parts) >= 4 else ""
                    if loc_part.isdigit():
                        loc_part = ""
                    out.append(job("talentbrew", f"talentbrew:{s}", str(jid), comp, title, loc_url,
                                   location=loc_part, remote="remote" in (loc_part + " " + title).lower()))
        except Exception:
            pass
        return added

    try:
        r1 = S.get(f"{base}/search-jobs?p=1", timeout=TIMEOUT)
        if not r1.ok:
            return out
    except Exception:
        return out
    n1 = parse_html(r1.text)
    if n1 == 0:
        return out
    m_tp = re.search(r'data-total-pages=["\']?(\d+)', r1.text)
    total_pages = int(m_tp.group(1)) if m_tp else None
    if total_pages and total_pages > 25 and pull_sitemap() > 0:
        return out
    pg = 1
    max_pages = min(total_pages, 100) if total_pages else 100
    for pg in range(2, max_pages + 1):
        try:
            r = S.get(f"{base}/search-jobs?p={pg}", timeout=TIMEOUT)
            if not r.ok or parse_html(r.text) == 0:
                break
        except Exception:
            break
    if (total_pages and total_pages > 100) or pg >= 100:
        pull_sitemap()
    return out


# ---------- New Feeds ----------
def arbeitsagentur():
    headers = {"X-API-Key": "jobboerse-jobsuche"}
    out = []
    seen = set()
    size = 100

    def pull(extra_params):
        max_total = 0
        for page in range(1, 101):
            d = get("https://rest.arbeitsagentur.de/jobboerse/jobsuche-service/pc/v6/jobs",
                    params={"page": page, "size": size, **extra_params}, headers=headers)
            max_total = max(max_total, int(d.get("maxErgebnisse") or 0))
            rows = d.get("ergebnisliste") or []
            if not rows:
                break
            new_rows = 0
            for j in rows:
                refnr = j.get("referenznummer")
                title = j.get("stellenangebotsTitel")
                if not refnr or not title or refnr in seen:
                    continue
                seen.add(refnr)
                new_rows += 1
                company = j.get("firma") or "Bundesagentur für Arbeit"
                loc_list = []
                for loc in j.get("stellenlokationen") or []:
                    if isinstance(loc, dict) and loc.get("ort"):
                        loc_list.append(loc["ort"])
                location = ", ".join(loc_list) or "Germany"
                posted_date = j.get("datumErsteVeroeffentlichung")
                date_str = str(posted_date)[:10] if posted_date and len(str(posted_date)) >= 10 else None
                url = f"https://www.arbeitsagentur.de/jobsuche/jobdetail/{refnr}"
                out.append(job("arbeitsagentur", "arbeitsagentur", refnr, clean_company(company), title, url,
                               location=location, posted=date_str, country="Germany"))
            if len(rows) < size or new_rows == 0:
                break
        return max_total

    total = pull({})
    if total > 10000:
        for art in (2, 4, 34):
            pull({"angebotsart": art})
    return out


_MCF_CATEGORIES = (
    "Accounting / Auditing / Taxation", "Admin / Secretarial", "Advertising / Media",
    "Architecture / Interior Design", "Banking and Finance", "Building and Construction",
    "Consulting", "Customer Service", "Design", "Education and Training", "Engineering",
    "Entertainment", "Environment / Health", "Events / Promotions", "F&B",
    "General Management", "General Work", "Healthcare / Pharmaceutical", "Hospitality",
    "Human Resources", "Information Technology", "Insurance", "Legal",
    "Logistics / Supply Chain", "Manufacturing", "Marketing / Public Relations",
    "Medical / Therapy Services", "Personal Care / Beauty", "Professional Services",
    "Public / Civil Service", "Purchasing / Merchandising", "Real Estate / Property Management",
    "Repair and Maintenance", "Risk Management", "Sales / Retail", "Sciences / Laboratory / R&D",
    "Security and Investigation", "Social Services", "Telecommunications", "Travel / Tourism",
    "Others", "Wholesale Trade", "Precision Engineering",
)
_MCF_EMPLOYMENT_TYPES = ("Full Time", "Permanent", "Contract Basis", "Part Time", "Temporary", "Freelance", "Flexi Work", "Internship")


def mycareersfuture():
    headers = {"Content-Type": "application/json"}
    out = []
    seen = set()
    limit = 100

    def parse_rows(rows):
        for j in rows:
            uuid = j.get("uuid")
            title = j.get("title")
            if not uuid or not title or uuid in seen:
                continue
            seen.add(uuid)
            company = (j.get("postedCompany") or {}).get("name") or (j.get("hiringCompany") or {}).get("name") or "Employer"
            sal = j.get("salary") or {}
            pay = None
            if sal.get("minimum") and sal.get("maximum"):
                try:
                    mult = 12 if (sal.get("type") or {}).get("salaryType") == "Monthly" else 1
                    pay = [int(sal["minimum"]) * mult, int(sal["maximum"]) * mult, "SGD"]
                except (ValueError, TypeError):
                    pass
            meta = j.get("metadata") or {}
            posted_date = meta.get("originalPostingDate")
            date_str = str(posted_date)[:10] if posted_date and len(str(posted_date)) >= 10 else None
            cat = ", ".join(c.get("category", "") for c in (j.get("categories") or []) if isinstance(c, dict))
            url = f"https://www.mycareersfuture.gov.sg/job/{uuid}"
            out.append(job("mycareersfuture", "mycareersfuture", uuid, clean_company(company), title, url,
                           location="Singapore", posted=date_str, country="Singapore", team=cat, pay=pay))

    # limit & page must be query params on MyCareersFuture v2 search; page >= 100 returns HTTP 418, so partition >10k via categories
    def fetch_page(pg, payload=None, throttle=0.25):
        for wait in (throttle, 5.0, 15.0):
            if wait:
                time.sleep(wait)
            try:
                return body(lambda: S.post("https://api.mycareersfuture.gov.sg/v2/search",
                                           params={"limit": limit, "page": pg}, json=payload or {}, headers=headers, timeout=TIMEOUT))
            except requests.HTTPError as e:
                if getattr(e.response, "status_code", None) == 429:
                    continue
                raise
            except requests.exceptions.RetryError:
                continue
        return body(lambda: S.post("https://api.mycareersfuture.gov.sg/v2/search",
                                   params={"limit": limit, "page": pg}, json=payload or {}, headers=headers, timeout=TIMEOUT))

    def pull_slice(payload, first_doc=None):
        d0 = first_doc if first_doc is not None else fetch_page(0, payload)
        rows0 = (d0.get("results") or []) if isinstance(d0, dict) else []
        parse_rows(rows0)
        tot = int(d0.get("total") or 0) if isinstance(d0, dict) else 0
        if len(rows0) >= limit and tot > limit:
            max_page = min((tot - 1) // limit, 99)  # API hard-caps at page=99 (10k offset); HTTP 418 at page=100
            for res in pmap(lambda pg, p=payload: fetch_page(pg, p), range(1, max_page + 1), 2):
                rows = (res.get("results") or []) if isinstance(res, dict) else []
                parse_rows(rows)
        elif len(rows0) >= limit and tot == 0:
            for page in range(1, 100):
                res = fetch_page(page, payload)
                rows = (res.get("results") or []) if isinstance(res, dict) else []
                if not rows:
                    break
                parse_rows(rows)
                if len(rows) < limit:
                    break
        return tot

    first = fetch_page(0, {}, throttle=0)
    total = int(first.get("total") or 0) if isinstance(first, dict) else 0
    if total <= 10000:
        pull_slice({}, first)
    else:
        for cat in _MCF_CATEGORIES:
            d0 = fetch_page(0, {"categories": [cat]}, throttle=0.15)
            ctot = int(d0.get("total") or 0) if isinstance(d0, dict) else 0
            if ctot == 0:
                continue
            if ctot <= 10000:
                pull_slice({"categories": [cat]}, d0)
            else:
                for et in _MCF_EMPLOYMENT_TYPES:
                    pull_slice({"categories": [cat], "employmentTypes": [et]})
    return out


def freehire():
    out = []
    limit = 100
    for offset in range(0, 5000, limit):
        d = get("https://freehire.me/api/v1/agent/jobs/search", params={"limit": limit, "offset": offset})
        rows = d.get("data") or []
        if not rows:
            break
        for j in rows:
            slug = j.get("public_slug")
            title = j.get("title")
            if not slug or not title:
                continue
            company = j.get("company") or j.get("company_slug") or "Employer"
            url = j.get("url") or f"https://freehire.me/job/{slug}"
            loc = j.get("location") or ", ".join(j.get("cities") or [])
            enrich = j.get("enrichment") or {}
            pay = None
            if enrich.get("salary_min") and enrich.get("salary_max"):
                try:
                    curr = enrich.get("salary_currency") or "USD"
                    pay = [int(enrich["salary_min"]), int(enrich["salary_max"]), curr]
                except (ValueError, TypeError):
                    pass
            posted = j.get("posted_at")
            date_str = str(posted)[:10] if posted and len(str(posted)) >= 10 else None
            desc = text(j.get("description") or "")
            team = enrich.get("category") or ""
            out.append(job("freehire", "freehire", slug, clean_company(company), title, url,
                           location=loc, remote=bool(j.get("work_mode") == "remote"), posted=date_str,
                           desc=desc, team=team, pay=pay))
        if len(rows) < limit:
            break
    return out


def workatastartup():
    out = []
    seen = set()
    # 1. WaaS category pages
    roles = [
        'software-engineer', 'designer', 'product-manager', 'sales-manager',
        'operations', 'marketing', 'recruiting', 'science', 'finance', 'legal'
    ]
    for role in roles:
        try:
            r = S.get(f"https://www.workatastartup.com/jobs/l/{role}", timeout=TIMEOUT)
            if r.status_code == 200:
                m = re.search(r'data-page="([^"]+)"', r.text)
                if m:
                    data = json.loads(html.unescape(m.group(1)))
                    jobs = data.get("props", {}).get("jobs", []) or []
                    for j in jobs:
                        if not isinstance(j, dict):
                            continue
                        jid = j.get("id")
                        title = j.get("title")
                        company = j.get("companyName")
                        slug = j.get("companySlug") or "yc"
                        if not jid or not title:
                            continue
                        s_jid = str(jid)
                        if s_jid in seen:
                            continue
                        seen.add(s_jid)
                        loc = j.get("location") or ""
                        remote = "remote" in loc.lower()
                        emp = [j.get("jobType")] if j.get("jobType") else []
                        sal = j.get("salary") or ""
                        pay = find_pay(sal) if sal else None
                        url = f"https://www.workatastartup.com/jobs/{jid}"
                        desc = text(j.get("companyOneLiner") or "")
                        out.append(job("workatastartup", f"yc:{slug}", s_jid, company or slug, title, url,
                                       location=loc, remote=remote, emp=emp, pay=pay, desc=desc))
        except Exception:
            pass

    # 2. YC jobs landing & category/location pages
    yc_paths = [
        '/jobs',
        '/jobs/role/software-engineer', '/jobs/role/designer', '/jobs/role/product-manager',
        '/jobs/role/sales-manager', '/jobs/role/operations', '/jobs/role/marketing',
        '/jobs/role/recruiting-hr', '/jobs/role/science',
        '/jobs/location/san-francisco', '/jobs/location/new-york', '/jobs/location/seattle',
        '/jobs/location/los-angeles', '/jobs/location/austin', '/jobs/location/chicago', '/jobs/location/india'
    ]
    for path in yc_paths:
        try:
            r = S.get(f"https://www.ycombinator.com{path}", timeout=TIMEOUT)
            if r.status_code == 200:
                m = re.search(r'data-page="([^"]+)"', r.text)
                if m:
                    data = json.loads(html.unescape(m.group(1)))
                    postings = data.get("props", {}).get("jobPostings", []) or []
                    for p in postings:
                        if not isinstance(p, dict):
                            continue
                        jid = p.get("id")
                        title = p.get("title")
                        company = p.get("companyName")
                        c_url = p.get("companyUrl") or ""
                        slug = c_url.split("/")[-1] if c_url else "yc"
                        if not jid or not title:
                            continue
                        s_jid = str(jid)
                        if s_jid in seen:
                            continue
                        seen.add(s_jid)
                        loc = p.get("location") or ""
                        remote = "remote" in loc.lower()
                        emp = [p.get("type")] if p.get("type") else []
                        sal = p.get("salaryRange") or ""
                        pay = find_pay(sal) if sal else None
                        j_url = p.get("url") or ""
                        if j_url.startswith("/"):
                            url = f"https://www.ycombinator.com{j_url}"
                        elif j_url:
                            url = j_url
                        else:
                            url = f"https://www.workatastartup.com/jobs/{jid}"
                        desc = text(p.get("companyOneLiner") or "")
                        out.append(job("workatastartup", f"yc:{slug}", s_jid, company or slug, title, url,
                                       location=loc, remote=remote, emp=emp, pay=pay, desc=desc))
        except Exception:
            pass

    # 3. WaaS /jobs/search?q=<letter> public JSON search
    def waas_search(kw):
        try:
            r = S.get("https://www.workatastartup.com/jobs/search", params={"q": kw}, timeout=TIMEOUT)
            return r.json().get("jobs", []) if r.ok else []
        except Exception:
            return []

    for hits in pmap(waas_search, list("abcdefghijklmnopqrstuvwxyz"), 6):
        for j in hits:
            if not isinstance(j, dict):
                continue
            jid = j.get("id")
            title = j.get("title")
            company = j.get("companyName")
            slug = j.get("companySlug") or "yc"
            if not jid or not title:
                continue
            s_jid = str(jid)
            if s_jid in seen:
                continue
            seen.add(s_jid)
            loc = j.get("location") or ""
            remote = "remote" in loc.lower()
            emp = [j.get("jobType")] if j.get("jobType") else []
            sal = j.get("salary") or ""
            pay = find_pay(sal) if sal else None
            url = f"https://www.workatastartup.com/jobs/{jid}"
            desc = text(j.get("companyOneLiner") or "")
            out.append(job("workatastartup", f"yc:{slug}", s_jid, company or slug, title, url,
                           location=loc, remote=remote, emp=emp, pay=pay, desc=desc))

    return out


ATS = dict(greenhouse=greenhouse, lever=lever, ashby=ashby, smartrecruiters=smartrecruiters, workday=workday, microsoft=microsoft, eightfold=eightfold,
           amazon=amazon, apple=apple, teamtailor=teamtailor, personio=personio, recruitee=recruitee, oracle=oracle, workable=workable, phenom=phenom,
           pinpoint=pinpoint, breezy=breezy, bamboohr=bamboohr, rippling=rippling, join=join, adp=adp, jazzhr=jazzhr, jobvite=jobvite, trakstar_hire=trakstar_hire, jobscore=jobscore, zoho=zoho, icims=icims, ultipro=ultipro, dayforce=dayforce, paylocity=paylocity,
           paycom=paycom, applicantpro=applicantpro, isolved=isolved, betterteam=betterteam, gohire=gohire, freshteam=freshteam, gem=gem, recooty=recooty, csod=csod, taleo=taleo, bullhorn=bullhorn,
           successfactors=successfactors, avature=avature, recruiterflow=recruiterflow, manatal=manatal, loxo=loxo, careerplug=careerplug, comeet=comeet,
           crelate=crelate, clearcompany=clearcompany, pageup=pageup,
           catsone=catsone, talentlyft=talentlyft, jobadder=jobadder, jobsoid=jobsoid, tribepad=tribepad, applicantstack=applicantstack,
           factorial=factorial, hireology=hireology, feishu=feishu, moka=moka, beisen=beisen, tencent=tencent, bytedance=bytedance, netease=netease, jd=jd, unitree=unitree, baidu=baidu, yc=yc,
           jibe=jibe, talentbrew=talentbrew)
FEEDS = dict(remotive=remotive, remoteok=remoteok, arbeitnow=arbeitnow, himalayas=himalayas, jobicy=jobicy, weworkremotely=weworkremotely,
             getonbrd=getonbrd, workingnomads=workingnomads, jobtech=jobtech, cryptojobs=cryptojobs, themuse=themuse,
             aidevboard=aidevboard, larajobs=larajobs, fourdayweek=fourdayweek, nodesk=nodesk, remotepython=remotepython, nofluffjobs=nofluffjobs, landingjobs=landingjobs, cryptocurrencyjobs=cryptocurrencyjobs, cryptojobslist=cryptojobslist, golangprojects=golangprojects, arbeitsagentur=arbeitsagentur, mycareersfuture=mycareersfuture, freehire=freehire, workatastartup=workatastartup)
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
            applicantstack="https://{s}.applicantstack.com/x/detail/{r}",
            factorial="https://{s}.factorial.com/job_posting/{r}",
            hireology="https://careers.hireology.com/{s}/{r}/description",
            feishu="https://{s}/position/{r}/detail",
            moka="https://app.mokahr.com/social-recruitment/{s}/job/{r}",
            beisen="https://{s}.zhiye.com/social/jobs",
            tencent="https://careers.tencent.com/jobdesc.html?postId={r}",
            bytedance="https://jobs.bytedance.com/experienced/position/{r}/detail",
            netease="https://hr.163.com/position/detail?id={r}",
            jd="https://zhaopin.jd.com/web/job/job_detail/{r}",
            unitree="https://www.unitree.com/cn/position/{r}",
            baidu="https://talent.baidu.com/jobs/social-detail/{r}",
            arbeitsagentur="https://www.arbeitsagentur.de/jobsuche/jobdetail/{r}",
            mycareersfuture="https://www.mycareersfuture.gov.sg/job/{r}",
            yc="https://www.workatastartup.com/jobs/{r}",
            jibe="https://{s}.jibeapply.com/jobs/{r}",
            talentbrew="https://{s}/job/{r}")
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
LANES = {"greenhouse": 24, "lever": 16, "ashby": 16, "smartrecruiters": 12, "workday": 16, "teamtailor": 4, "oracle": 12, "workable": 2, "personio": 12, "recruitee": 2, "phenom": 4, "apple": 1, "pinpoint": 8, "breezy": 8, "bamboohr": 16, "rippling": 8, "join": 16, "adp": 16, "jazzhr": 16, "jobvite": 8, "trakstar_hire": 8, "jobscore": 8, "zoho": 16, "icims": 8, "ultipro": 16, "dayforce": 16, "paylocity": 16, "paycom": 16, "applicantpro": 8, "isolved": 8, "betterteam": 8, "gohire": 8, "freshteam": 4, "gem": 4, "recooty": 4, "csod": 4, "taleo": 2, "bullhorn": 8, "successfactors": 8, "avature": 8, "recruiterflow": 8, "manatal": 8, "loxo": 8, "careerplug": 8, "comeet": 8, "crelate": 8, "clearcompany": 8, "pageup": 8, "catsone": 8, "talentlyft": 8, "jobadder": 8, "jobsoid": 8, "tribepad": 8, "applicantstack": 8, "factorial": 4, "hireology": 12, "feishu": 12, "moka": 12, "beisen": 8, "tencent": 4, "bytedance": 4, "netease": 4, "jd": 4, "unitree": 2, "baidu": 4, "arbeitsagentur": 4, "mycareersfuture": 4, "freehire": 4, "yc": 16, "workatastartup": 4, "jibe": 8, "talentbrew": 8, "aidevboard": 2, "larajobs": 2, "fourdayweek": 2, "nodesk": 2, "remotepython": 2, "nofluffjobs": 2, "landingjobs": 2, "cryptocurrencyjobs": 2, "cryptojobslist": 2, "golangprojects": 2}
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


def export_parquet(out_dir="kaggle", fmt="parquet", max_files=None, compression="snappy"):
    """Export the crawled dataset to Parquet format (delegates to export_kaggle)."""
    import export_kaggle
    cmd = ["--format", fmt, "--output-dir", str(out_dir), "--compression", compression]
    if max_files is not None:
        cmd.extend(["--max-files", str(max_files)])
    return export_kaggle.main(cmd)


def main():
    parser = argparse.ArgumentParser(description="Argus job scraper")
    parser.add_argument("--shard", type=int, default=0, help="Shard index (0-based)")
    parser.add_argument("--total-shards", type=int, default=1, help="Total number of parallel shards")
    parser.add_argument("--consolidate", action="store_true", help="Merge shards, prune, and log history")
    parser.add_argument("--export", "--parquet", action="store_true", dest="export_parquet", help="Export dataset to Parquet (Kaggle format)")
    parser.add_argument("--budget", type=int, default=RUN_BUDGET, help="Run budget in seconds")
    args, _ = parser.parse_known_args()

    if getattr(args, "export_parquet", False):
        export_parquet()
        return

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
        skills_path = Path("data/skills.csv")

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
            board = (f"trakstar_hire:{fpath.stem[len('trakstar_hire_'):]}"
                     if fpath.stem.startswith("trakstar_hire_")
                     else fpath.stem.replace("_", ":", 1))
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
                if b_open > 0:
                    pulse_counts[board] = [b_open, b_fresher]

        if pulse_counts:
            if pulse.exists():
                p_lines = [line for line in pulse.read_text(encoding="utf-8").splitlines() if line and not line.startswith(today + ",")]
                pulse.write_text("\n".join(p_lines) + ("\n" if p_lines else ""), encoding="utf-8")
            append_csv(pulse, ["date", "board", "open", "fresher"], [[today, b, n, f] for b, (n, f) in sorted(pulse_counts.items())])

        if all_skills:
            if skills_path.exists():
                s_lines = [line for line in skills_path.read_text(encoding="utf-8").splitlines() if line and not line.startswith(today + ",")]
                skills_path.write_text("\n".join(s_lines) + ("\n" if s_lines else ""), encoding="utf-8")
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
    def task_priority(t):
        scope = t[0]
        if scope in FEEDS:
            return -100_000
        if scope not in open_n:
            return -50_000
        return -open_n.get(scope, 0)

    tasks.sort(key=task_priority)  # feeds & new boards first, then biggest boards

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
