from scraper import level, merge


def j(i, **kw):
    return {"id": i, "company": "c", "title": "t", **kw}


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

assert [level(t) for t in ["Software Engineering Intern", "Sr. Engineer", "Staff Engineer", "Engineering Manager", "Engineer"]] == \
    ["intern", "senior", "staff+", "manager+", "mid"]
print("ok")
