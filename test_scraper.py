import datetime as dt

from scraper import (category, clean_company, company_stats, employment, find_pay, fresher_ok, ghost_flags, level, merge, skills, slim,
                     url_of, years_req)


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
                              "Backend Engineer", "Product Marketing Manager", "Senior Accountant", "Barista"]] == \
    ["sales", "design", "data", "security", "product", "engineering", "marketing", "finance", "other"]
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

print("ok")
