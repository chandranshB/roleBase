"""Unit tests for newly added ATS and feed crawlers."""
import html
import json
import unittest
from unittest.mock import MagicMock, patch

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

    def test_paycom_mantle_jwt_parser(self):
        cfg = {
            "sessionJWT": "Bearer test-jwt",
            "libConfig": json.dumps({"atsPortalMantleServiceUrl": "https://portal.paycom.test/"}),
        }
        html_resp = MagicMock(ok=True, text=f"<script>var configsFromHost = {json.dumps(cfg)};</script>")
        api_payload = {
            "jobPostingPreviewsCount": 1,
            "jobPostingPreviews": [{
                "jobId": 9012,
                "jobTitle": "Senior Controls Engineer",
                "positionType": "Full Time",
                "remoteType": "Remote",
                "locations": "Chicago, IL",
                "description": "Python and PLC automation with 3+ years experience.",
                "postedOn": "2026-11-10",
            }],
        }
        with patch.object(scraper.S, "get", return_value=html_resp), patch("scraper.body", return_value=api_payload):
            jobs = scraper.paycom("TESTKEY", "TestCo")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "paycom:TESTKEY:9012")
        self.assertEqual(jobs[0]["company"], "TestCo")
        self.assertTrue(jobs[0]["remote"])
        self.assertEqual(jobs[0]["posted"], "2026-11-10")

    @patch.object(scraper.S, "get")
    def test_paylocity_pagedata_parser(self, mock_get):
        pd = {
            "CompanyName": "Intentsify",
            "Jobs": [{
                "JobId": 2381833,
                "JobTitle": "Staff Backend Engineer",
                "LocationName": "Remote, US",
                "PublishedDate": "2026-11-12T10:00:00Z",
                "Description": "Rust and Python.",
                "HiringDepartment": "Engineering",
                "IsRemote": True,
                "IndeedRemoteType": 1,
            }],
        }
        mock_get.return_value = MagicMock(ok=True, text=f"<script>window.pageData = {json.dumps(pd)};</script>")
        jobs = scraper.paylocity("guid-1", "")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "paylocity:guid-1:2381833")
        self.assertEqual(jobs[0]["company"], "Intentsify")
        self.assertTrue(jobs[0]["remote"])
        self.assertEqual(jobs[0]["team"], "Engineering")

    @patch.object(scraper.S, "get")
    def test_betterteam_turbostream_parser(self, mock_get):
        # React Router v7 turbo-stream array; note bool False must not resolve to arr[0]
        arr = [
            {"_1": 2},
            "loaderData",
            {"_3": 4},
            "routes/CareersPage",
            {"_5": 6},
            "jobs",
            [7],
            {"_8": 9, "_10": 11, "_12": 13, "_14": False, "_15": 16},
            "title", "Internal Audit Manager",
            "slug", "internal-audit-manager",
            "type", "Fulltime",
            "isRemote",
            "location",
            {"_17": 18, "_19": 20},
            "city", "Los Angeles",
            "state", "California",
        ]
        encoded = json.dumps(json.dumps(arr))
        mock_get.return_value = MagicMock(ok=True, text=f"<script>window.__reactRouterContext.streamController.enqueue({encoded});</script>")
        jobs = scraper.betterteam("99ranchmarket.betterteam.com", "99 Ranch")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "betterteam:99ranchmarket.betterteam.com:internal-audit-manager")
        self.assertEqual(jobs[0]["location"], "Los Angeles, California")
        self.assertFalse(jobs[0]["remote"])

    def test_join_graphql_pagination(self):
        nd = {
            "props": {"pageProps": {"pageData": {
                "company": {"id": 42},
                "jobs": {
                    "items": [{"idParam": f"p{i}", "title": f"Role {i}"} for i in range(5)],
                    "pagination": {"total": 7},
                },
            }}}
        }
        gql_resp = MagicMock(ok=True)
        gql_resp.json.return_value = {
            "data": {"publicJobs": {
                "items": [{"idParam": f"g{i}", "title": f"GQL Role {i}", "workplaceType": "REMOTE"} for i in range(7)],
                "pageInfo": {"page": 1, "pageCount": 1, "pageSize": 100, "rowCount": 7},
            }}
        }
        html_resp = MagicMock(text=f'<script id="__NEXT_DATA__">{json.dumps(nd)}</script>')
        with patch.object(scraper.S, "get", return_value=html_resp), patch.object(scraper.S, "post", return_value=gql_resp):
            jobs = scraper.join("zielfoto", "Zielfoto")
        self.assertEqual(len(jobs), 7)
        self.assertEqual(jobs[0]["id"], "join:zielfoto:g0")

    @patch.object(scraper.S, "get")
    def test_rippling_v1_board_api(self, mock_get):
        resp = MagicMock(ok=True)
        resp.json.return_value = [{
            "uuid": "abc-123",
            "name": "Robotics Engineer",
            "department": {"label": "Hardware"},
            "workLocation": {"label": "Devens, MA"},
            "url": "https://ats.rippling.com/vulcan/jobs/abc-123",
        }]
        mock_get.return_value = resp
        jobs = scraper.rippling("vulcan", "Vulcan")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "rippling:vulcan:abc-123")
        self.assertEqual(jobs[0]["team"], "Hardware")

    @patch.object(scraper.S, "post")
    def test_ultipro_preserves_lowercase_jobboard_scope(self, mock_post):
        resp = MagicMock(ok=True)
        resp.json.return_value = {
            "opportunities": [{
                "Id": "opp-1",
                "Title": "Field Tech",
                "JobCategoryName": "Ops",
                "Locations": [{"Address": {"City": "Austin", "State": {"Code": "TX"}, "Country": {"Name": "USA"}}}],
            }]
        }
        mock_post.return_value = resp
        jobs = scraper.ultipro("ten1/jobboard/guid1", "Tenant One")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "ultipro:ten1/jobboard/guid1:opp-1")
        self.assertIn("/JobBoard/guid1/", mock_post.call_args.args[0])

    def test_csod_multi_site_id_fallback(self):
        ctx_html = MagicMock(ok=True, text='<script>csod.context = {"token": "tok", "cultureID": 1, "cultureName": "en-US"};</script>')

        def fake_post(url, headers=None, json=None, timeout=None):
            sid = json["careerSiteId"]
            r = MagicMock(ok=True)
            if sid == 2:
                r.json.return_value = {"data": {"requisitions": [{"requisitionId": "555", "displayJobTitle": "Site Engineer"}]}}
            else:
                r.json.return_value = {"data": {"requisitions": []}}
            return r

        with patch.object(scraper.S, "get", return_value=ctx_html), patch.object(scraper.S, "post", side_effect=fake_post):
            jobs = scraper.csod("strabag", "Strabag")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "csod:strabag:555")
        self.assertIn("/careersite/2/", jobs[0]["url"])

    @patch.object(scraper.S, "get")
    def test_avature_steps_by_actual_page_size(self, mock_get):
        seen_offsets = []

        def fake_get(url, timeout=None):
            seen_offsets.append(url)
            r = MagicMock(ok=True)
            if "jobOffset=0" in url:
                r.text = "".join(f'<a href="/careers/JobDetail/slug/{i}">Role {i}</a>' for i in range(1, 13))
            elif "jobOffset=12" in url:
                r.text = '<a href="/careers/JobDetail/slug/13">Role 13</a>'
            else:
                r.text = ""
            return r

        mock_get.side_effect = fake_get
        jobs = scraper.avature("bloomberg", "Bloomberg")
        self.assertEqual(len(jobs), 13)
        self.assertTrue(any("jobOffset=12" in u for u in seen_offsets))

    def test_oracle_and_phenom_partitioning_over_10k(self):
        # Oracle > 10000 partitions via facetsList
        def fake_get(url, params=None):
            finder = (params or {}).get("finder", "")
            if "selectedCategoriesFacet=c1" in finder:
                return {"items": [{"TotalJobsCount": 1, "requisitionList": [{"Id": "o1", "Title": "Oracle Eng"}]}]}
            if "selectedCategoriesFacet=c2" in finder:
                return {"items": [{"TotalJobsCount": 1, "requisitionList": [{"Id": "o2", "Title": "Oracle PM"}]}]}
            return {"items": [{"TotalJobsCount": 11000, "categoriesFacet": [{"Id": "c1", "TotalCount": 6000}, {"Id": "c2", "TotalCount": 5000}]}]}

        with patch("scraper.get", side_effect=fake_get):
            ojobs = scraper.oracle("acme.fa.us2.oraclecloud.com/CX", "Acme")
        self.assertEqual({j["id"] for j in ojobs}, {"oracle:acme.fa.us2.oraclecloud.com/CX:o1", "oracle:acme.fa.us2.oraclecloud.com/CX:o2"})

    @patch.object(scraper.S, "get")
    def test_trakstar_hire_uses_p_pagination_param(self, mock_get):
        seen_urls = []

        def fake_get(url, params=None, timeout=None):
            seen_urls.append((url, params))
            r = MagicMock(ok=True)
            if not params:
                r.text = ''.join(f'<a href="/jobs/j{i}">Role {i}</a>' for i in range(1, 26)) + "next_page_url: '?p=2'"
            else:
                r.text = '<a href="/jobs/j26">Role 26</a>'
            return r

        mock_get.side_effect = fake_get
        jobs = scraper.trakstar_hire("whiteoak", "White Oak")
        self.assertEqual(len(jobs), 26)
        self.assertEqual(seen_urls, [("https://whiteoak.hire.trakstar.com/", None), ("https://whiteoak.hire.trakstar.com/", {"p": 2})])

    @patch("scraper.get")
    def test_aidevboard_follows_total_pages(self, mock_get):
        def fake_get(url, params=None):
            if params["page"] == 1:
                return {"jobs": [{"id": "a1", "company_name": "Acme", "title": "ML Eng"}], "total_pages": 2, "has_next": True}
            return {"jobs": [{"id": "a2", "company_name": "Acme", "title": "AI Eng"}], "total_pages": 2, "has_next": False}

        mock_get.side_effect = fake_get
        jobs = scraper.aidevboard()
        self.assertEqual({j["id"] for j in jobs}, {"aidevboard:a1", "aidevboard:a2"})

    @patch("scraper.body")
    def test_mycareersfuture_partitions_over_10k_without_418(self, mock_body):
        import itertools
        seq = itertools.count(1)

        def fake_body(fn):
            n = next(seq)
            if n == 1:
                return {"total": 12000, "results": [{"uuid": "u0", "title": "Root Job"}]}
            return {"total": 1, "results": [{"uuid": f"u{n}", "title": "Cat Job"}]}

        mock_body.side_effect = fake_body
        jobs = scraper.mycareersfuture()
        self.assertEqual(len(jobs), 43)
        self.assertTrue(all(j["id"].startswith("mycareersfuture:u") for j in jobs))

    @patch.object(scraper.S, "get")
    def test_talentbrew_single_page_total_pages(self, mock_get):
        resp = MagicMock(ok=True, text='<section id="search-results-list" data-total-pages="1"><a href="/job/p/role/99"><h2>Role 99</h2></a></section>')
        mock_get.return_value = resp
        jobs = scraper.talentbrew("jobs.example.com", "Example")
        self.assertEqual([j["id"] for j in jobs], ["talentbrew:jobs.example.com:99"])

    def test_teamtailor_show_more_past_100(self):
        rss_items = "".join(
            f'<item><guid>g{i}</guid><title>Role {i}</title><link>https://career.ex.com/jobs/{i}-role</link></item>'
            for i in range(1, 101)
        )
        rss_xml = f"<rss><channel>{rss_items}</channel></rss>".encode()
        stream_p2 = '<turbo-stream action="append"><template><li><a href="https://career.ex.com/jobs/101-staff-eng">Staff Eng</a><div><span>Eng</span><span>Remote</span></div></li></template></turbo-stream>'

        def fake_get(url, params=None, headers=None, timeout=None):
            if url.endswith("/jobs.rss"):
                return MagicMock(ok=True, content=rss_xml)
            if (params or {}).get("page") == 2:
                return MagicMock(ok=True, text=stream_p2)
            return MagicMock(ok=True, text="")

        with patch.object(scraper.S, "get", side_effect=fake_get):
            jobs = scraper.teamtailor("career.ex.com", "Ex")
        self.assertEqual(len(jobs), 101)
        self.assertEqual(jobs[-1]["id"], "teamtailor:career.ex.com:101")
        self.assertTrue(jobs[-1]["remote"])

    def test_taleo_faceted_search_rest_and_dot_host(self):
        fs_html = "<script>config: { 'fs/FacetedSearchSettings': { queryString: 'portal=8140753014' } }</script>"
        seen_urls = []

        def fake_get(url, timeout=None):
            seen_urls.append(url)
            if "default.ftl" in url:
                return MagicMock(ok=True, url="https://textron.taleo.net/careersection/textron/default.ftl", text=fs_html)
            return MagicMock(ok=True, url=url, text="")

        def fake_post(url, json=None, headers=None, timeout=None):
            seen_urls.append(url)
            return MagicMock(ok=True, json=lambda: {
                "pagingData": {"currentPageNo": 1, "pageSize": 25, "totalCount": 1},
                "requisitionList": [{
                    "jobId": "1546790", "linkedColumn": 0, "locationsColumns": [1],
                    "column": ["Project Manager", '["US-Massachusetts-Wilmington"]'],
                }],
            })

        with patch.object(scraper.S, "get", side_effect=fake_get), patch.object(scraper.S, "post", side_effect=fake_post):
            jobs = scraper.taleo("textron.taleo.net", "Textron")
        self.assertEqual(len(jobs), 1)
        self.assertEqual(jobs[0]["id"], "taleo:textron.taleo.net:1546790")
        self.assertTrue(all(".taleo.net.taleo.net" not in u for u in seen_urls))


if __name__ == "__main__":
    unittest.main()
