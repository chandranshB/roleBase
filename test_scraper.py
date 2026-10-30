import datetime as dt

from scraper import (ATS, CAPPED, FEEDS, apple, bamboohr, breezy, category, clean_company, company_stats, employment, find_pay, fresher_ok, ghost_flags, level, merge, skills, slim,
                     phenom_job, pinpoint, rippling, run_all, themuse, url_of, usajobs_job, verdict, wd_posted, years_req, adp, jazzhr, jobvite, join, trakstar_hire, jobscore, zoho, icims, ultipro, dayforce, paylocity,
                     paycom, applicantpro, isolved, betterteam, gohire, freshteam, gem, recooty, csod, taleo)
from update_about import about_text


def j(i, **kw):
    return {"id": i, "company": "c", "title": "t", **kw}


# --- merge: open / close / reopen / prune ---
db = merge({}, {"a:x": [j("a:x:1"), j("a:x:2")], "b": [j("b:9")]}, "2026-01-01")
assert all(o["closed_at"] is None and o["first_seen"] == "2026-01-01" for o in db.values())

# job 2 vanishes from a:x -> closed; b not fetched (failed) -> untouched
db = merge(db, {"a:x": [j("a:x:1")]}, "2026-01-02")
assert db["a:x:2"]["closed_at"] == "2026-01-02" and db["b:9"]["closed_at"] is None

# reappears -> reopened, first_seen kept
db = merge(db, {"a:x": [j("a:x:1"), j("a:x:2")]}, "2026-01-03")
assert db["a:x:2"]["closed_at"] is None and db["a:x:2"]["first_seen"] == "2026-01-01"

# closed long ago -> pruned
db = merge(db, {"a:x": [j("a:x:1")]}, "2026-01-04")
db = merge(db, {"a:x": [j("a:x:1")]}, "2026-06-01")
assert "a:x:2" not in db

# repost: a job closes, then a new id with the same title appears on the same board
db = merge({}, {"a:x": [j("a:x:1")]}, "2026-01-01")
db = merge(db, {"a:x": []}, "2026-01-02")
db = merge(db, {"a:x": [j("a:x:5")]}, "2026-01-03")
assert db["a:x:5"]["reposts"] == 1 and db["a:x:1"]["closed_at"] == "2026-01-02"
db = merge(db, {"a:x": [j("a:x:5")]}, "2026-01-04")
assert db["a:x:5"]["reposts"] == 1  # unchanged on later runs

# aged-out rows are handed back for archiving
p = []
merge({"a:x:1": {"id": "a:x:1", "title": "t", "closed_at": "2026-01-01", "first_seen": "2025-12-01"}}, {"a:x": []}, "2026-09-01", p)
assert [o["id"] for o in p] == ["a:x:1"]

# --- fresher-friendliness ---
fj = lambda level, years, company="C": {"level": level, "years": years, "company": company}
assert fresher_ok(fj("junior", None)) and fresher_ok(fj("intern", 0)) and fresher_ok(fj("mid", 2)) and fresher_ok(fj("junior", 2))
assert not fresher_ok(fj("junior", 3))  # "junior" asking 3+ years
assert not fresher_ok(fj("mid", None)) and not fresher_ok(fj("senior", 1)) and not fresher_ok(fj("manager+", 0))
s = company_stats([fj("junior", None), fj("junior", 4), fj("mid", 1), fj("senior", 8), fj("mid", None)], min_roles=5)[0]
assert (s["open_roles"], s["fresher_roles"], s["junior_asking_3plus"], s["years_stated_share"]) == (5, 2, 1, 0.6)
assert company_stats([fj("junior", 0)], min_roles=5) == []

# --- ghost jobs ---
d = dt.date(2026, 10, 4)
mk = lambda i, title, posted, **kw: {"id": i, "company": "Acme", "title": title, "posted": posted, "first_seen": posted, **kw}
g = ghost_flags([mk("1", "Talent Pool", "2026-09-30"), mk("2", "Dev", "2024-01-01"), mk("3", "Data Pipeline Engineer", "2026-10-01"),
                 mk("4", "Dev", "2026-01-01", reposts=3)], d)
assert sum(p for _, p in g["1"]) == 3 and sum(p for _, p in g["2"]) == 2 and g["3"] == [] and sum(p for _, p in g["4"]) == 3

# --- understanding ---
assert [level(t) for t in ["Software Engineering Intern", "Sr. Engineer", "Staff Engineer", "Engineering Manager", "Engineer",
                           "Associate Director, Ops", "Junior Developer", "Werkstudent Marketing"]] == \
    ["intern", "senior", "staff+", "manager+", "mid", "manager+", "junior", "intern"]
assert [level(t) for t in ["Product Manager", "Account Manager", "Engineering Manager", "Director of Sales"]] == \
    ["mid", "mid", "manager+", "manager+"]
assert category("RTL Design Engineer") == "engineering"
assert level("Engineer", 0) == "junior" and level("Engineer", 3) == "mid" and level("Engineer", 6) == "senior"
assert level("Engineer", None, "junior") == "junior" and level("Senior Engineer", None, "junior") == "senior"

assert [category(t) for t in ["Sales Engineer", "Product Designer", "Data Engineer", "Security Engineer", "Technical Program Manager",
                              "Backend Engineer", "Product Marketing Manager", "Senior Accountant", "Barista", "Registered Nurse", "Store Manager"]] == \
    ["sales", "design", "data", "security", "product", "engineering", "marketing", "finance", "retail", "healthcare", "retail"]
assert category("Weird Title", "Legal & Compliance") == "legal"

assert years_req("We are 10 years old. Requirements: 5+ years of experience in Go; 8 years experience preferred") == 5
assert years_req("3-5 years of professional experience") == 3 and years_req("no numbers here") is None
assert years_req("Founded 15 years ago") is None

assert skills("Senior Python/Go dev, AWS and Kubernetes (k8s), JavaScript, not Javascripty") == ["python", "aws", "kubernetes", "javascript"]
assert skills("golang postgresql node.js c++ c#") == ["go", "postgres", "node.js", "c++", "c#"]

assert find_pay("Pay range: $150,000 - $200,000 USD") == [150000, 200000, "USD"]
assert find_pay("£60k to £80k a year") == [60000, 80000, "GBP"] and find_pay("$20 - $30 per hour") is None

assert employment("Dev", "Full-time") == "full_time" and employment("Dev", ["Teilzeit"]) == "part_time"
assert employment("Marketing Intern") == "internship" and employment("Contract Dev") == "contract" and employment("Dev") == ""

assert clean_company("Stripe, Inc.") == "Stripe" and clean_company("Acme GmbH") == "Acme" and clean_company("Inc") == "Inc"

# --- storage: slim drops empties and derivable bits, load() would restore them ---
row = {"id": "ashby:acme:u1", "source": "ashby", "url": url_of("ashby:acme:u1"), "remote": False, "years": 0, "skills": [], "team": "",
       "title": "Engineer", "level": "junior", "category": "engineering"}
assert slim(row) == {"id": "ashby:acme:u1", "years": 0, "title": "Engineer"}
assert slim({**row, "level": "manager+"})["level"] == "manager+"  # level from an API hint is not derivable, so it stays
assert url_of("remotive:7") is None
assert url_of("apple:apple:200313970") == "https://jobs.apple.com/en-us/details/200313970"
assert url_of("pinpoint:article:c5a4fa70-cb2d") == "https://article.pinpointhq.com/en/postings/c5a4fa70-cb2d"
assert url_of("breezy:zero-hash:cd390cdcd5ca") == "https://zero-hash.breezy.hr/p/cd390cdcd5ca"
assert url_of("bamboohr:acme:123") == "https://acme.bamboohr.com/careers/123"
assert url_of("rippling:acme:abc") == "https://ats.rippling.com/acme/jobs/abc"
assert url_of("join:acme:123") == "https://join.com/companies/acme/jobs/123"
assert url_of("adp:acme:123") == "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?cid=acme&jobId=123"
assert url_of("jazzhr:acme:123") == "https://acme.applytojob.com/apply/123"
assert url_of("jobvite:acme:123") == "https://jobs.jobvite.com/acme/job/123"
assert url_of("trakstar_hire:acme:123") == "https://acme.hire.trakstar.com/jobs/123"
assert url_of("jobscore:acme:123") == "https://careers.jobscore.com/jobs/acme/123"
assert url_of("zoho:acme:123") == "https://acme/jobs/Careers/123"
assert url_of("icims:acme:123") == "https://acme/jobs/123/job"
assert url_of("ultipro:tenant/guid:123") == "https://recruiting2.ultipro.com/tenant/guid/OpportunityDetail?opportunityId=123"
assert url_of("dayforce:client/board:123") == "https://jobs.dayforcehcm.com/en-US/client/board/jobs/123/apply"
assert url_of("paylocity:guid:123") == "https://recruiting.paylocity.com/recruiting/jobs/Details/123"
assert url_of("paycom:portal:123") == "https://www.paycomonline.net/v4/ats/web.php/portal/portal/jobs/123"
assert url_of("applicantpro:acme:123") == "https://acme.applicantpro.com/jobs/123"
assert url_of("isolved:acme:123") == "https://acme.isolvedhire.com/jobs/123"
assert url_of("betterteam:acme.betterteam.com:123") == "https://acme.betterteam.com/123"
assert url_of("gohire:acme:123") == "https://jobs.gohire.io/acme/123"
assert url_of("freshteam:acme:123") == "https://acme.freshteam.com/jobs/123"
assert url_of("gem:acme:123") == "https://jobs.gem.com/acme/123"
assert url_of("recooty:acme:123") == "https://careerspage.io/acme/123"
assert url_of("csod:acme:123") == "https://acme.csod.com/ux/ats/careersite/4/home/requisition/123?c=acme"
assert url_of("taleo:acme:123") == "https://acme.taleo.net/careersection/ex/jobdetail.ftl?job=123"
assert "apple" in CAPPED and ATS["apple"] == apple
assert ATS["pinpoint"] == pinpoint and ATS["breezy"] == breezy and ATS["bamboohr"] == bamboohr and ATS["rippling"] == rippling and ATS["join"] == join and ATS["adp"] == adp and ATS["jazzhr"] == jazzhr and ATS["jobvite"] == jobvite and ATS["trakstar_hire"] == trakstar_hire and ATS["jobscore"] == jobscore and ATS["zoho"] == zoho and ATS["icims"] == icims and ATS["ultipro"] == ultipro and ATS["dayforce"] == dayforce and ATS["paylocity"] == paylocity
assert ATS["paycom"] == paycom and ATS["applicantpro"] == applicantpro and ATS["isolved"] == isolved and ATS["betterteam"] == betterteam and ATS["gohire"] == gohire and ATS["freshteam"] == freshteam and ATS["gem"] == gem and ATS["recooty"] == recooty and ATS["csod"] == csod and ATS["taleo"] == taleo
assert "themuse" in FEEDS and FEEDS["themuse"] == themuse and "themuse" in CAPPED

# --- logos: icon detection and ordering ---
import struct

from build_logos import candidates, kind, size, wide

png = b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 180, 180)
ico = b"\x00\x00\x01\x00\x02\x00" + bytes([16, 16]) + b"\0" * 14 + bytes([0, 0]) + b"\0" * 14  # 16px and 256px entries
assert (kind(png), size(png, "png")) == ("png", 180) and (kind(ico), size(ico, "ico")) == ("ico", 256)
assert wide(png) == 1 and wide(b"\x89PNG\r\n\x1a\n" + b"\0\0\0\rIHDR" + struct.pack(">II", 400, 100)) == 4
assert kind(b'<?xml version="1.0"?><svg xmlns="x"/>') == "svg" and kind(b"<html><body>404</body></html>") is None and kind(b"GIF89a") is None
html = '''<link rel="icon" href="/f.png" sizes="32x32"><link rel="apple-touch-icon" sizes="180x180" href="/t.png">
<link rel="icon" type="image/svg+xml" href="/m.svg"><link rel="mask-icon" href="/mask.svg"><link rel="stylesheet" href="/a.css">'''
assert candidates(html, "https://x.com/") == ["https://x.com/t.png", "https://x.com/m.svg", "https://x.com/f.png", "https://x.com/favicon.ico"]

# --- charts: valid XML, labels escaped, empty and single-day data do not crash ---
import xml.etree.ElementTree as ET

from make_charts import bars, lines

for s in (bars("T <&>", "sub", [("A & B <x>", 3), ("c" * 40, 1)]), bars("empty", "s", []),
          lines("L", "s", [("a", 1, [("2026-10-04", 5)])]), lines("L", "s", [("a", 1, [("2026-10-04", 5), ("2026-10-06", 9)])]), lines("L", "s", [])):
    ET.fromstring(s)
assert "A &amp; B &lt;x&gt;" in bars("t", "s", [("A & B <x>", 3)])



# --- workday "Posted N Days Ago" text -> date ---
_t = dt.date.today()
assert wd_posted("Posted Today") == _t.isoformat()
assert wd_posted("Posted Yesterday") == (_t - dt.timedelta(days=1)).isoformat()
assert wd_posted("Posted 30+ Days Ago") is None

# --- scrape guard: distrust errors / empty / collapsed answers, but believe them once they repeat ---
assert verdict([1] * 50, None, 50, 0) is None and verdict([1] * 10, None, 0, 0) is None  # healthy, or a new board
assert verdict(None, RuntimeError("boom"), 50, 5).startswith("error")  # errors are never believed
assert verdict([], None, 10, 0) == "empty answer" and verdict([], None, 10, 2) is None
assert verdict([1] * 5, None, 50, 0).startswith("dropped") and verdict([1] * 5, None, 50, 2) is None
assert verdict([1] * 30, None, 50, 0) is None  # a 40% drop is normal churn

# --- usajobs: one API item -> job (fixture shaped like the documented response) ---
u = usajobs_job({"MatchedObjectId": "123", "MatchedObjectDescriptor": {
    "PositionTitle": "Registered Nurse", "OrganizationName": "Veterans Health Administration", "PositionURI": "https://www.usajobs.gov/job/123",
    "ApplyURI": ["https://www.usajobs.gov/job/123/apply"], "PositionLocationDisplay": "Boise, Idaho", "PublicationStartDate": "2026-10-01T00:00:00.0000",
    "PositionRemuneration": [{"MinimumRange": "70000.00", "MaximumRange": "95000.00", "RateIntervalCode": "PA"}],
    "PositionSchedule": [{"Name": "Full-time"}], "JobCategory": [{"Name": "Nurse"}], "UserArea": {"Details": {"RemoteIndicator": "False", "JobSummary": "Care for patients."}}}})
assert u["id"] == "usajobs:123" and u["category"] == "healthcare" and u["pay"] == [70000, 95000, "USD"] and not u["remote"] and u["posted"] == "2026-10-01"

# --- companies.json: no two boards of a system differ only by letter case (same jobs twice on Linux, one shared data file on Windows) ---
import json
for system, boards in json.load(open("companies.json", encoding="utf-8")).items():
    keys = [k.lower() for k in boards]
    assert len(keys) == len(set(keys)), (system, sorted(k for k in set(keys) if keys.count(k) > 1))

# --- phenom: one search record (shaped like talent.lowes.com's) -> job; the Workday apply link becomes the posting link ---
ph = phenom_job("talent.lowes.com", "Lowe's", {
    "jobId": "JR-02676490", "title": "Part Time - Receiver/Stocker - Flexible", "type": "Part time", "category": "Store Operations", "department": "Receiving",
    "cityStateCountry": "Glen Allen, Virginia, United States of America", "postedDate": "2026-10-11T00:00:00.000+0000",
    "applyUrl": "https://lowes.wd5.myworkdayjobs.com/LWS_External_CS/job/Glen-Allen-VA/Part-Time---Receiver-Stocker_JR-02676490/apply",
    "descriptionTeaser": "Receive freight and stock shelves.", "ml_skills": ["inventory management", "forklift operation"]})
assert ph["id"] == "phenom:talent.lowes.com:JR-02676490" and ph["employment"] == "part_time" and ph["posted"] == "2026-10-11" and not ph["remote"]
assert ph["url"].endswith("_JR-02676490") and ph["team"] == "Receiving" and "Glen Allen" in ph["location"]
sf = phenom_job("careers.allianz.com", "Allianz", {"jobId": "97277", "title": "Data Analyst", "remote": "Hybrid working",
                                                    "applyUrl": "https://career5.successfactors.eu/careers?company=AZ&career_job_req_id=97277&career_ns=job_application"})
assert sf["url"].endswith("job_application") and not sf["remote"]  # a non-Workday apply link is kept whole
assert phenom_job("x", "X", {"jobId": "1", "title": "", "applyUrl": "https://x"}) is None  # no title: skipped, not a failed board

# --- run_all: each provider is its own lane with its own concurrency cap, a hung source is cut off at the budget and everything else still finishes ---
import threading
import time

live, peak, lock = 0, 0, threading.Lock()


def lane_task():
    global live, peak
    with lock:
        live += 1
        peak = max(peak, live)
    time.sleep(.2)
    with lock:
        live -= 1
    return ["job"]


t0 = time.time()
tasks = [(f"workable:{i}", lane_task) for i in range(6)] + [(f"greenhouse:{i}", lambda: ["job"]) for i in range(5)] + [("hang", lambda: time.sleep(3))]
res, unfinished = run_all(tasks, budget=1.5)
assert peak == 2, peak  # the workable lane runs two boards at a time, however many are queued
assert unfinished == ["hang"] and time.time() - t0 < 2.5  # cut off at the budget, not after the hang
by = {r[0]: r for r in res}
assert len(res) == 12 and all(by[f"workable:{i}"][1] == ["job"] for i in range(6)) and isinstance(by["hang"][2], TimeoutError)

# a connection cut mid-body is retried (urllib3's own retry does not cover it), and gives up after three tries
import requests
import scraper

cuts, orig = [], requests.Session.request
scraper.S.waits = (0, 0)


def flaky(self, *a, **kw):
    cuts.append(1)
    if len(cuts) <= 2:
        raise requests.exceptions.ChunkedEncodingError("IncompleteRead")
    return "ok"


requests.Session.request = flaky
assert scraper.S.get("https://x.test") == "ok" and len(cuts) == 3
cuts.clear()
requests.Session.request = lambda self, *a, **kw: cuts.append(1) or (_ for _ in ()).throw(requests.exceptions.ChunkedEncodingError("cut"))
try:
    scraper.S.get("https://x.test")
    raise SystemExit("should have raised")
except requests.exceptions.ChunkedEncodingError:
    assert len(cuts) == 3
requests.Session.request = orig

# an empty or cut-off JSON body is retried too (a rate limit often answers 200 with nothing), and gives up after three tries
class Resp:
    def __init__(self, ok):
        self.ok = ok

    def raise_for_status(self):
        pass

    def json(self):
        if not self.ok:
            raise ValueError("Expecting value")
        return {"n": 1}


real_sleep, scraper.time.sleep = scraper.time.sleep, lambda s: None
seq = iter([Resp(False), Resp(False), Resp(True)])
assert scraper.body(lambda: next(seq)) == {"n": 1}
try:
    scraper.body(lambda: Resp(False))
    raise SystemExit("should have raised")
except ValueError:
    pass

# body retries transient connection errors
seq_conn = iter([lambda: (_ for _ in ()).throw(requests.exceptions.ConnectionError("flaky")), lambda: Resp(True)])
assert scraper.body(lambda: next(seq_conn)()) == {"n": 1}

# job safely handles None title without raising
assert scraper.job("greenhouse", "greenhouse:test", "1", "Co", None, "http://apply")["title"] == ""
scraper.time.sleep = real_sleep

# Microsoft's careers site is Eightfold-hosted, so it shares that lane: never two of them at once
live = peak = 0
res, unfinished = run_all([("microsoft:a", lane_task), ("eightfold:b", lane_task), ("eightfold:c", lane_task), ("microsoft:d", lane_task)], budget=5)
assert not unfinished and peak == 1, peak

# providers do not queue behind each other: 3 providers x 3 slow boards, one worker each -> about 3 boards deep, not 9
seen, mx = {}, {}


def slow(lane):
    with lock:
        seen[lane] = seen.get(lane, 0) + 1
        mx[lane] = max(mx.get(lane, 0), seen[lane])
    time.sleep(.2)
    with lock:
        seen[lane] -= 1
    return ["job"]


t0 = time.time()
res, unfinished = run_all([(f"{lane}:{i}", lambda lane=lane: slow(lane)) for i in range(3) for lane in ("prov_a", "prov_b", "prov_c")], budget=5)
assert not unfinished and len(res) == 9 and mx == {"prov_a": 1, "prov_b": 1, "prov_c": 1}, mx  # an unlisted provider fetches one board at a time
assert time.time() - t0 < .9, time.time() - t0  # 0.6s when the three providers run side by side, 1.8s if they queued

# About text: the figure is rounded to 10k so it does not change on every run, and the description stays under GitHub's 350 characters
assert "~480k open jobs" in about_text(480764) and "~70k open jobs" in about_text(72300) and len(about_text(480764)) < 350

print("ok")
