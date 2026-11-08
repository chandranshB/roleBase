"""Unit tests for heuristic ghost job risk scoring."""
import datetime as dt
import unittest

from rolebase.ghost import ghost_flags


class TestGhostScore(unittest.TestCase):
    def setUp(self):
        self.today = dt.date(2026, 11, 8)

    def test_evergreen_phrasing(self):
        jobs = [{
            "id": "1",
            "company": "Acme",
            "title": "General Application / Future Opportunities",
            "posted": "2026-11-01"
        }]
        res = ghost_flags(jobs, today=self.today)
        self.assertTrue(any("talent-pool" in reason for reason, _ in res["1"]))
        self.assertGreaterEqual(sum(p for _, p in res["1"]), 3)

    def test_stale_postings(self):
        # Over 1 year old
        j_old = {
            "id": "2",
            "company": "Acme",
            "title": "Software Engineer",
            "posted": "2025-01-01"
        }
        res = ghost_flags([j_old], today=self.today)
        self.assertTrue(any("open for over a year" in reason for reason, _ in res["2"]))
        self.assertEqual(sum(p for _, p in res["2"]), 2)

    def test_hidden_employer(self):
        j_stealth = {
            "id": "3",
            "company": "Confidential Staffing Agency",
            "title": "Java Developer",
            "posted": "2026-11-01"
        }
        res = ghost_flags([j_stealth], today=self.today)
        self.assertTrue(any("agency or hidden employer" in reason for reason, _ in res["3"]))

    def test_reposts_scoring(self):
        j_repost = {
            "id": "4",
            "company": "Acme",
            "title": "Designer",
            "posted": "2026-11-01",
            "reposts": 4
        }
        res = ghost_flags([j_repost], today=self.today)
        self.assertTrue(any("reposted 4x" in reason for reason, _ in res["4"]))
        self.assertEqual(sum(p for _, p in res["4"]), 2)


if __name__ == "__main__":
    unittest.main()
