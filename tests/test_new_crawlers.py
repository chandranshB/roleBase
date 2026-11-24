"""Unit tests for newly added ATS and feed crawlers."""
import html
import json
import unittest
from unittest.mock import patch

import scraper


class TestNewCrawlers(unittest.TestCase):
    @patch("scraper.get")
    def test_hireology_parser(self, mock_get):
        mock_get.return_value = {
            "count": 1,
            "data": [{
                "id": 101,
                "name": "Service Technician",
                "organization": {"name": "Test Auto Group"},
                "locations": [{"city": "Austin", "state": "TX"}],
                "job_description": "<p>Looking for a technician with 3+ years experience.</p>",
                "employment_status": "Full Time",
                "job_family": {"name": "Service"},
                "remote": False,
                "created_at": "2026-10-01T12:00:00Z",
                "career_site_url": "https://careers.hireology.com/testauto/101/description",
                "compensation": {
                    "comp_range_min": "60000",
                    "comp_range_max": "90000",
                    "is_comp_range": True
                }
            }]
        }
        jobs = scraper.hireology("testauto", "Test Auto Group")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "hireology:testauto:101")
        self.assertEqual(j["title"], "Service Technician")
        self.assertEqual(j["company"], "Test Auto Group")
        self.assertEqual(j["location"], "Austin, TX")
        self.assertEqual(j["pay"], [60000, 90000, "USD"])
        self.assertFalse(j["remote"])

    @patch("scraper.body")
    @patch.object(scraper.S, "get")
    def test_feishu_parser(self, mock_s_get, mock_body):
        mock_s_get.return_value.text = '<script id="js-websiteInfo">{"website_info": {"path": "index"}}</script>'
        mock_body.return_value = {
            "code": 0,
            "data": {
                "count": 1,
                "job_post_list": [{
                    "id": 7701584,
                    "title": "Autonomous Driving Algorithm Engineer",
                    "city_list": [{"name": "Shanghai"}],
                    "job_function": {"name": "R&D"},
                    "description": "Develop perception models.",
                    "requirement": "PyTorch proficiency.",
                    "publish_time": 1794200000000
                }]
            }
        }
        jobs = scraper.feishu("nio", "NIO")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "feishu:nio:7701584")
        self.assertEqual(j["title"], "Autonomous Driving Algorithm Engineer")
        self.assertEqual(j["company"], "NIO")
        self.assertEqual(j["location"], "Shanghai")
        self.assertIn("nio.jobs.feishu.cn", j["url"])

    @patch("scraper.body")
    def test_beisen_parser(self, mock_body):
        mock_body.return_value = {
            "Count": 1,
            "Data": [{
                "JobAdId": 270531,
                "JobAdName": "Robotics System Engineer",
                "LocNames": ["Suzhou"],
                "Duty": "Design robot hardware architecture.",
                "PostDate": "2026-11-01T10:00:00"
            }]
        }
        jobs = scraper.beisen("dreame", "Dreame")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "beisen:dreame:270531")
        self.assertEqual(j["title"], "Robotics System Engineer")
        self.assertEqual(j["company"], "Dreame")
        self.assertEqual(j["location"], "Suzhou")

    @patch("scraper.get")
    def test_tencent_parser(self, mock_get):
        mock_get.return_value = {
            "Data": {
                "Count": 1,
                "Posts": [{
                    "PostId": "20917053",
                    "RecruitPostName": "Game AI Engineer",
                    "LocationName": "Shenzhen",
                    "BGName": "IEG",
                    "Responsibility": "Build intelligent agents.",
                    "LastUpdateTime": "2026年11月09日",
                    "PostURL": "http://careers.tencent.com/jobdesc.html?postId=20917053"
                }]
            }
        }
        jobs = scraper.tencent("tencent", "Tencent")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "tencent:tencent:20917053")
        self.assertEqual(j["title"], "Game AI Engineer")
        self.assertEqual(j["company"], "Tencent")
        self.assertEqual(j["posted"], "2026-11-09")

    @patch("scraper.get")
    def test_arbeitsagentur_feed(self, mock_get):
        mock_get.return_value = {
            "ergebnisliste": [{
                "referenznummer": "10000-98765",
                "stellenangebotsTitel": "Softwareentwickler Java (m/w/d)",
                "firma": "Siemens AG",
                "stellenlokationen": [{"ort": "München"}],
                "datumErsteVeroeffentlichung": "2026-11-05"
            }]
        }
        jobs = scraper.arbeitsagentur()
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "arbeitsagentur:10000-98765")
        self.assertEqual(j["title"], "Softwareentwickler Java (m/w/d)")
        self.assertEqual(j["company"], "Siemens")
        self.assertEqual(j["location"], "München")

    @patch("scraper.body")
    def test_mycareersfuture_feed(self, mock_body):
        mock_body.return_value = {
            "results": [{
                "uuid": "01784e6b",
                "title": "Cloud Infrastructure Architect",
                "postedCompany": {"name": "Grab Holdings"},
                "salary": {"minimum": 10000, "maximum": 15000, "type": {"salaryType": "Monthly"}},
                "metadata": {"originalPostingDate": "2026-11-01"}
            }]
        }
        jobs = scraper.mycareersfuture()
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "mycareersfuture:01784e6b")
        self.assertEqual(j["title"], "Cloud Infrastructure Architect")
        self.assertEqual(j["company"], "Grab Holdings")
        self.assertEqual(j["pay"], [120000, 180000, "SGD"])

    @patch("scraper.get")
    def test_freehire_feed(self, mock_get):
        mock_get.return_value = {
            "data": [{
                "public_slug": "lead-backend-engineer-acme-12345",
                "title": "Lead Backend Engineer",
                "company": "Acme Corp",
                "cities": ["San Francisco"],
                "work_mode": "remote",
                "posted_at": "2026-11-02",
                "description": "Golang and Kubernetes microservices.",
                "enrichment": {
                    "salary_min": 180000,
                    "salary_max": 240000,
                    "salary_currency": "USD"
                }
            }]
        }
        jobs = scraper.freehire()
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "freehire:lead-backend-engineer-acme-12345")
        self.assertEqual(j["title"], "Lead Backend Engineer")
        self.assertEqual(j["company"], "Acme")
        self.assertTrue(j["remote"])
        self.assertEqual(j["pay"], [180000, 240000, "USD"])


    @patch("scraper.body")
    def test_baidu_parser(self, mock_body):
        mock_body.return_value = {
            "status": 200,
            "data": {
                "total": 1,
                "list": [{
                    "postId": "baidu-post-001",
                    "name": "AI Platform Engineer",
                    "orgName": "Platform and Content Group",
                    "workPlace": "Beijing",
                    "publishDate": "2026-11-01",
                    "workContent": "Build large-scale AI distributed training platform.",
                    "serviceCondition": "Proficiency in Python and PyTorch required.",
                }]
            }
        }
        jobs = scraper.baidu("baidu", "Baidu")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "baidu:baidu:baidu-post-001")
        self.assertEqual(j["title"], "AI Platform Engineer")
        self.assertEqual(j["company"], "Baidu")
        self.assertEqual(j["team"], "Platform and Content Group")
        self.assertEqual(j["location"], "Beijing")
        self.assertEqual(j["posted"], "2026-11-01")
        self.assertEqual(j["url"], "https://talent.baidu.com/jobs/social-detail/baidu-post-001")

    @patch.object(scraper.S, "get")
    def test_yc_parser(self, mock_get):
        mock_resp = unittest.mock.MagicMock()
        mock_resp.status_code = 200
        waas_data = {
            "props": {
                "company": {
                    "name": "Acme Startup",
                    "description": "Building next-gen AI tools with Python.",
                    "jobs": [{
                        "id": 999123,
                        "title": "Senior AI Systems Engineer",
                        "location": "San Francisco, CA / Remote (US)",
                        "jobType": "Full-time",
                        "salaryRange": "$180K - $220K"
                    }]
                }
            }
        }
        mock_resp.text = f'<div id="app" data-page="{html.escape(json.dumps(waas_data))}">'
        mock_get.return_value = mock_resp

        jobs = scraper.yc("acme", "Acme Startup")
        self.assertGreaterEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "yc:acme:999123")
        self.assertEqual(j["title"], "Senior AI Systems Engineer")
        self.assertEqual(j["company"], "Acme Startup")
        self.assertTrue(j["remote"])
        self.assertEqual(j["pay"], [180000, 220000, "USD"])
        self.assertEqual(j["url"], "https://www.workatastartup.com/jobs/999123")

    @patch.object(scraper.S, "get")
    def test_jibe_parser(self, mock_get):
        mock_resp = unittest.mock.MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "jobs": [{
                "id": "item-123",
                "data": {
                    "slug": "484313",
                    "req_id": "REQ-484313",
                    "title": "Network VEND Coordinator",
                    "full_location": "Dallas, Texas",
                    "posted_date": "2026-11-20T01:39:00+0000",
                    "employment_type": "FULL_TIME",
                    "description": "Requires 3+ years experience in Python and AWS.",
                    "remote": False,
                    "category": "Supply Chain",
                    "apply_url": "https://uscareers-pepsico.icims.com/jobs/484313/login"
                }
            }]
        }
        mock_get.return_value = mock_resp
        jobs = scraper.jibe("pepsico", "PepsiCo")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "jibe:pepsico:484313")
        self.assertEqual(j["title"], "Network VEND Coordinator")
        self.assertEqual(j["company"], "PepsiCo")
        self.assertEqual(j["location"], "Dallas, Texas")
        self.assertEqual(j["team"], "Supply Chain")
        self.assertFalse(j["remote"])
        self.assertEqual(j["posted"], "2026-11-20")
        self.assertIn("python", j["skills"])
        self.assertIn("aws", j["skills"])

    @patch.object(scraper.S, "get")
    def test_talentbrew_parser(self, mock_get):
        mock_resp = unittest.mock.MagicMock()
        mock_resp.ok = True
        mock_resp.status_code = 200
        mock_resp.text = '''
        <section id="search-results-list">
            <ul>
                <li class="search-results-list__item">
                    <a class="search-results-list__job-link" data-job-id="103718173632" href="/job/oakland/expert-business-analyst/29673/103718173632">
                        <h2>Expert Business Analyst</h2>
                    </a>
                    <span class="job-location">Oakland, CA</span>
                </li>
            </ul>
        </section>
        '''
        mock_get.return_value = mock_resp
        jobs = scraper.talentbrew("jobs.pge.com", "PG&E")
        self.assertEqual(len(jobs), 1)
        j = jobs[0]
        self.assertEqual(j["id"], "talentbrew:jobs.pge.com:103718173632")
        self.assertEqual(j["title"], "Expert Business Analyst")
        self.assertEqual(j["company"], "PG&E")
        self.assertEqual(j["location"], "Oakland, CA")
        self.assertFalse(j["remote"])


if __name__ == "__main__":
    unittest.main()
