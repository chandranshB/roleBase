"""Unit tests for pipeline orchestration, scrape guard verdicts, and company analytics."""
import unittest

import scraper
from rolebase.consolidate import company_stats
from rolebase.normalize import url_of


class TestPipeline(unittest.TestCase):
    def test_url_of_derivation(self):
        cases = [
            ("greenhouse:stripe:123", "https://job-boards.greenhouse.io/stripe/jobs/123"),
            ("lever:netflix:456", "https://jobs.lever.co/netflix/456/apply"),
            ("ashby:openai:789", "https://jobs.ashbyhq.com/openai/789/application"),
            ("smartrecruiters:visa:abc", "https://jobs.smartrecruiters.com/visa/abc"),
            ("workday:myworkdayjobs.com/acme:/job/123", "https://myworkdayjobs.com/acme/job/123"),
            ("apple:apple:200313970", "https://jobs.apple.com/en-us/details/200313970"),
            ("catsone:brainsworkgroup:9549/jobs/123", "https://brainsworkgroup.catsone.com/careers/9549/jobs/123"),
            ("talentlyft:bornfight:tNLagN", "https://bornfight.talentlyft.com/o/tNLagN"),
            ("jobadder:84381/eq8-recruit:619389", "https://clientapps.jobadder.com/84381/eq8-recruit/619389"),
            ("jobsoid:demo:135389", "https://demo.jobsoid.com/apply/135389"),
            ("tribepad:twinkl:1624", "https://twinkl.tribepad.com/members/modules/job/detail.php?record=1624"),
            ("applicantstack:gnostech:a27b9ukxyj7o", "https://gnostech.applicantstack.com/x/detail/a27b9ukxyj7o"),
            ("factorial:shippypro:324080", "https://shippypro.factorial.com/job_posting/324080"),
            ("remotive:7", None),  # Non-derivable feed URL
            ("aidevboard:123", None),
            ("larajobs:456", None),
            ("fourdayweek:789", None),
            ("nodesk:abc", None),
            ("remotepython:def", None),
            ("nofluffjobs:123", None),
            ("landingjobs:456", None),
            ("cryptocurrencyjobs:789", None),
            ("cryptojobslist:abc", None),
            ("golangprojects:def", None),
        ]
        for jid, expected in cases:
            with self.subTest(jid=jid):
                self.assertEqual(url_of(jid), expected)

    def test_scrape_guard_verdict(self):
        verdict = scraper.verdict
        # Healthy board
        self.assertIsNone(verdict([1] * 50, None, 50, 0))
        self.assertIsNone(verdict([1] * 10, None, 0, 0))

        # Errors are always distrusted
        self.assertTrue(verdict(None, RuntimeError("timeout"), 50, 5).startswith("error"))

        # Empty answers distrusted until patience runs out
        self.assertEqual(verdict([], None, 10, 0), "empty answer")
        self.assertIsNone(verdict([], None, 10, 2))  # Believed after 2 strikes

        # Large drops distrusted initially
        self.assertTrue(verdict([1] * 5, None, 50, 0).startswith("dropped"))
        self.assertIsNone(verdict([1] * 5, None, 50, 2))

    def test_company_stats_ranking(self):
        jobs = [
            {"company": "Acme", "level": "junior", "years": 1},
            {"company": "Acme", "level": "intern", "years": 0},
            {"company": "Acme", "level": "junior", "years": 2},
            {"company": "Acme", "level": "mid", "years": 3},
            {"company": "Acme", "level": "senior", "years": 7},
        ]
        stats = company_stats(jobs, min_roles=5)
        self.assertEqual(len(stats), 1)
        s = stats[0]
        self.assertEqual(s["company"], "Acme")
        self.assertEqual(s["open_roles"], 5)
        self.assertEqual(s["fresher_roles"], 3)
        self.assertEqual(s["fresher_share"], 0.6)
        self.assertEqual(s["junior_asking_3plus"], 0)
        self.assertEqual(s["years_stated_share"], 1.0)


if __name__ == "__main__":
    unittest.main()
