"""Normalization, classification, entity extraction, and heuristics."""
import functools
import re
from typing import Any, Dict, List, Optional, Tuple, Union

from rolebase.client import day, text
from rolebase.config import (
    ALIAS,
    AU_STATES,
    CA_PROVINCES,
    COUNTRY_MAP,
    EMP,
    LEGAL_RX,
    LEVELS,
    MAJOR_CITIES,
    PAY_RX,
    RE_CA_PROVINCES,
    RE_CATEGORIES,
    RE_COUNTRY_MAP,
    RE_COUNTRY_SPLIT,
    RE_LEVELS,
    RE_MAJOR_CITIES,
    RE_US_STATE_END,
    RE_US_STATES,
    SKILL_RX,
    URLS,
    US_STATES,
    YEARS_RX,
)


@functools.lru_cache(maxsize=32768)
def _clean_title(t: Optional[str]) -> str:
    """Normalize a job title for identity and repost checks."""
    return re.sub(r"\W", "", (t or "").lower())


def clean_company(n: Optional[str]) -> str:
    """Strip legal suffixes (Inc., LLC, GmbH, etc.) and excess whitespace from company names."""
    if not n or not isinstance(n, str):
        return ""
    n = " ".join(n.split())
    while (m := LEGAL_RX.sub("", n)) != n and m:
        n = m
    return n


@functools.lru_cache(maxsize=32768)
def level(title: Optional[str], years: Optional[int] = None, hint: Optional[str] = None) -> str:
    """Classify seniority level into intern, junior, mid, senior, lead, staff+, or manager+."""
    t = (title or "").lower()
    for name, rx in RE_LEVELS:
        if rx.search(t):
            return name
    if hint:
        return hint
    return "mid" if years is None else "junior" if years <= 1 else "mid" if years <= 4 else "senior"


@functools.lru_cache(maxsize=32768)
def category(*texts: Optional[str]) -> str:
    """Classify domain/industry category based on title, team, and department."""
    for t in texts:
        if not t:
            continue
        tl = t.lower()
        for name, rx in RE_CATEGORIES:
            if rx.search(tl):
                return name
    return "other"


def years_req(desc: str) -> Optional[int]:
    """Extract minimum years of required experience following requirement headings."""
    if not desc:
        return None
    m = re.search(r"requirements|qualifications|about you|what you.ll bring|must have|minimum", desc, re.I)
    seg = desc[m.start():] if m else desc
    for y in YEARS_RX.finditer(seg):
        if int(y[1]) <= 20 and "experience" in seg[max(0, y.start() - 100):y.end() + 100].lower():
            return int(y[1])
    return None


def skills(t: str) -> List[str]:
    """Extract up to 8 normalized technical or business skill tokens from text."""
    if not t:
        return []
    out = []
    for m in SKILL_RX.finditer(t):
        s = m[1].lower()
        s = ALIAS.get(s, s)
        if s not in out:
            out.append(s)
    return out[:8]


def employment(title: str, *vals: Any) -> str:
    """Identify employment type (full_time, part_time, contract, internship)."""
    for v in vals:
        for x in v if isinstance(v, list) else [v]:
            e = EMP.get(re.sub(r"[\s_-]", "", str(x or "").lower()))
            if e:
                return e
    t = (title or "").lower()
    if re.search(LEVELS[0][1], t):
        return "internship"
    return "contract" if re.search(r"\bcontract(or)?\b|\bfreelance\b|\btemporary\b|\bfixed[- ]term\b", t) \
        else "part_time" if re.search(r"\bpart[- ]time\b", t) else ""


def money(s: str) -> int:
    """Convert currency string (e.g. '120k' or '120,000') into integer."""
    s = s.replace(",", "").replace(" ", "").lower()
    return int(s[:-1]) * 1000 if s.endswith("k") else int(s)


def find_pay(t: str) -> Optional[List[Union[int, str]]]:
    """Extract yearly compensation range [min, max, currency] if stated in text."""
    if not t:
        return None
    for m in PAY_RX.finditer(t):
        lo, hi = money(m[2]), money(m[3])
        if 20000 <= lo <= hi <= 2_000_000:
            return [lo, hi, {"$": "USD", "£": "GBP", "€": "EUR"}[m[1]]]
    return None


@functools.lru_cache(maxsize=32768)
def country(loc: Optional[str]) -> str:
    """Normalize location strings to standard country names."""
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


def fresher_ok(j: Dict[str, Any]) -> bool:
    """True if job is accessible to candidates with 0-2 years of experience.
    Junior roles demanding 3+ years are rejected; senior/lead roles never qualify."""
    y = j.get("years")
    lvl = j.get("level") or level(j.get("title", ""), y)
    return lvl in ("intern", "junior", "mid") and (y <= 2 if y is not None else lvl != "mid")


def url_of(id: str) -> Optional[str]:
    """Derive standard apply link URL from job ID when formulaic."""
    src, *rest = id.split(":")
    return URLS[src].format(s=rest[0], r=":".join(rest[1:])) if src in URLS else None


def job(source: str, scope: str, rid: Any, company: Optional[str], title: Optional[str], url: Optional[str],
        location: str = "", remote: bool = False, posted: Any = None, desc: str = "", team: str = "",
        emp: Any = (), pay: Any = None, tags: Any = (), hint: Optional[str] = None,
        country: Optional[str] = None) -> Dict[str, Any]:
    """Construct an enriched, validated job record dictionary."""
    location, team, title = (location or "").strip(), (team or "").strip(), (title or "").strip()
    yrs = years_req(desc)
    cntry = (country or "").strip() or infer_country(location)
    return dict(
        id=f"{scope}:{rid}",
        source=source,
        company=company or "",
        title=title,
        team=team,
        location=location,
        country=cntry,
        remote=bool(remote) or "remote" in location.lower(),
        level=level(title, yrs, hint),
        category=category(title, team),
        employment=employment(title, *emp),
        years=yrs,
        skills=skills(f"{title} {' '.join(tags)} {desc}"),
        pay=pay or find_pay(desc),
        url=url,
        posted=day(posted)
    )
