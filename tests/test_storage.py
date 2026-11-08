"""Unit tests for storage, slimming, deduplication, and differential merge."""
import unittest

from rolebase.storage import collapse, merge, slim


class TestStorage(unittest.TestCase):
    def test_slim_removes_derivable_and_empty_fields(self):
        row = {
            "id": "greenhouse:stripe:12345",
            "source": "greenhouse",
            "url": "https://job-boards.greenhouse.io/stripe/jobs/12345",
            "title": "Software Engineer",
            "level": "mid",
            "category": "engineering",
            "country": "United States",
            "location": "New York, NY",
            "remote": False,
            "years": None,
            "skills": [],
            "reposts": 0
        }
        compact = slim(row)
        # Derivable fields and defaults are stripped
        self.assertNotIn("source", compact)
        self.assertNotIn("url", compact)
        self.assertNotIn("level", compact)
        self.assertNotIn("category", compact)
        self.assertNotIn("country", compact)
        self.assertNotIn("skills", compact)
        self.assertNotIn("reposts", compact)
        self.assertEqual(compact["id"], "greenhouse:stripe:12345")
        self.assertEqual(compact["title"], "Software Engineer")

    def test_merge_lifecycle(self):
        def make_job(jid, title="Engineer"):
            return {"id": jid, "company": "Stripe", "title": title}

        # 1. Initial crawl
        db = merge({}, {"greenhouse:stripe": [make_job("greenhouse:stripe:1"), make_job("greenhouse:stripe:2")]}, "2026-01-01")
        self.assertEqual(len(db), 2)
        self.assertIsNone(db["greenhouse:stripe:1"]["closed_at"])
        self.assertEqual(db["greenhouse:stripe:1"]["first_seen"], "2026-01-01")

        # 2. Job 2 closes (disappears from board)
        db = merge(db, {"greenhouse:stripe": [make_job("greenhouse:stripe:1")]}, "2026-01-02")
        self.assertIsNone(db["greenhouse:stripe:1"]["closed_at"])
        self.assertEqual(db["greenhouse:stripe:2"]["closed_at"], "2026-01-02")

        # 3. Job 2 re-opens
        db = merge(db, {"greenhouse:stripe": [make_job("greenhouse:stripe:1"), make_job("greenhouse:stripe:2")]}, "2026-01-03")
        self.assertIsNone(db["greenhouse:stripe:2"]["closed_at"])
        self.assertEqual(db["greenhouse:stripe:2"]["first_seen"], "2026-01-01")  # original first_seen preserved

        # 4. Repost detection: job closes, then new id with same title appears
        db = merge({}, {"greenhouse:stripe": [make_job("greenhouse:stripe:1", "Designer")]}, "2026-01-01")
        db = merge(db, {"greenhouse:stripe": []}, "2026-01-02")
        db = merge(db, {"greenhouse:stripe": [make_job("greenhouse:stripe:9", "Designer")]}, "2026-01-03")
        self.assertEqual(db["greenhouse:stripe:9"]["reposts"], 1)

    def test_collapse_bulk_postings(self):
        rows = [
            {"company": "Amazon", "title": "Warehouse Associate", "team": "Ops", "location": "Seattle, WA", "closed_at": None, "url": "https://a.com/1"},
            {"company": "Amazon", "title": "Warehouse Associate", "team": "Ops", "location": "Seattle, WA", "closed_at": None, "url": "https://a.com/2"},
            {"company": "Amazon", "title": "Warehouse Associate", "team": "Ops", "location": "Seattle, WA", "closed_at": None, "url": "https://a.com/3"},
            {"company": "Amazon", "title": "Software Engineer", "team": "AWS", "location": "Seattle, WA", "closed_at": None, "url": "https://a.com/4"},
        ]
        collapsed = collapse(rows)
        self.assertEqual(len(collapsed), 2)
        warehouse_row = next(r for r in collapsed if r["title"] == "Warehouse Associate")
        self.assertEqual(warehouse_row["openings"], 3)

    def test_merge_with_malformed_closed_at(self):
        # Malformed closed_at values should not raise TypeError during merge or pruning
        db = {
            "a:1": {"id": "a:1", "title": "Job 1", "closed_at": True, "first_seen": "2026-01-01"},
            "a:2": {"id": "a:2", "title": "Job 2", "closed_at": 1730000000, "first_seen": "2026-01-01"},
            "a:3": {"id": "a:3", "title": "Job 3", "closed_at": float("nan"), "first_seen": "2026-01-01"},
            "a:4": {"id": "a:4", "title": "Job 4", "closed_at": "2025-01-01", "first_seen": "2025-01-01"},  # >90 days old
            "a:5": {"id": "a:5", "title": "Job 5", "closed_at": "2026-03-01", "first_seen": "2026-02-01"},  # recent closure
        }
        pruned = []
        keep = merge(db, {"a": []}, "2026-03-05", pruned=pruned)
        # a:4 should be pruned because "2025-01-01" < "2025-12-05" (90-day cutoff)
        self.assertNotIn("a:4", keep)
        self.assertIn("a:5", keep)
        self.assertEqual(len(pruned), 1)
        self.assertEqual(pruned[0]["id"], "a:4")

    def test_collapse_with_heterogeneous_types(self):
        # Should gracefully sort and group even if fields contain None or float nan
        rows = [
            {"company": None, "title": "Engineer", "team": None, "location": None, "first_seen": float("nan"), "url": None},
            {"company": "Google", "title": None, "team": "Cloud", "location": "NYC", "first_seen": "2026-01-01", "url": "https://g.co/1"},
            {"company": "Google", "title": None, "team": "Cloud", "location": "NYC", "first_seen": "2026-01-01", "url": "https://g.co/2"},
        ]
        collapsed = collapse(rows)
        self.assertEqual(len(collapsed), 2)


if __name__ == "__main__":
    unittest.main()
