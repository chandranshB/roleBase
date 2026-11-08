"""Unit tests for entity extraction, normalization, and classification logic."""
import unittest

from rolebase.normalize import (
    category,
    clean_company,
    country,
    employment,
    find_pay,
    fresher_ok,
    job,
    level,
    skills,
    years_req,
)


class TestClassifiers(unittest.TestCase):
    def test_level_classification(self):
        cases = [
            ("Software Engineering Intern", "intern"),
            ("Werkstudent Marketing", "intern"),
            ("Junior Developer", "junior"),
            ("Associate Software Engineer", "junior"),
            ("Software Engineer", "mid"),
            ("Product Manager", "mid"),
            ("Account Manager", "mid"),
            ("Senior Python Developer", "senior"),
            ("Sr. Data Analyst", "senior"),
            ("Lead Systems Architect", "lead"),
            ("Staff Site Reliability Engineer", "staff+"),
            ("Principal Scientist", "staff+"),
            ("Engineering Manager", "manager+"),
            ("Director of Sales", "manager+"),
            ("Associate Director, Ops", "manager+"),
            ("Head of Product", "manager+"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(level(title), expected)

    def test_level_with_years_and_hints(self):
        self.assertEqual(level("Engineer", 0), "junior")
        self.assertEqual(level("Engineer", 3), "mid")
        self.assertEqual(level("Engineer", 6), "senior")
        self.assertEqual(level("Engineer", None, "junior"), "junior")
        self.assertEqual(level("Senior Engineer", None, "junior"), "senior")

    def test_category_classification(self):
        cases = [
            ("Sales Engineer", "sales"),
            ("Product Designer", "design"),
            ("Data Engineer", "data"),
            ("Security Engineer", "security"),
            ("Technical Program Manager", "product"),
            ("Backend Engineer", "engineering"),
            ("Product Marketing Manager", "marketing"),
            ("Senior Accountant", "finance"),
            ("Payroll Specialist", "finance"),
            ("Registered Nurse", "healthcare"),
            ("Barista", "retail"),
            ("Store Manager", "retail"),
            ("Warehouse Associate", "logistics"),
            ("Delivery Driver", "logistics"),
            ("Executive Assistant", "administrative"),
            ("Social Worker", "social"),
            ("Strategy Consultant", "consulting"),
            ("Operations Lead", "operations"),
        ]
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(category(title), expected)

        # Team fallback
        self.assertEqual(category("Weird Title", "Legal & Compliance"), "legal")
        self.assertEqual(category("Unknown"), "other")

    def test_country_normalization(self):
        cases = [
            ("New York, NY", "United States"),
            ("San Francisco, CA; Remote", "United States"),
            ("Austin, TX 78701", "United States"),
            ("Toronto, ON, Canada", "Canada"),
            ("Calgary, AB", "Canada"),
            ("London, UK", "United Kingdom"),
            ("Edinburgh, Scotland", "United Kingdom"),
            ("Berlin, Germany", "Germany"),
            ("Munich, Bavaria", "Germany"),
            ("Bengaluru, Karnataka, India", "India"),
            ("Sydney, Australia", "Australia"),
            ("Paris, France", "France"),
            ("Amsterdam, Netherlands", "Netherlands"),
            ("Tokyo, Japan", "Japan"),
            ("Remote", ""),
            ("", ""),
        ]
        for loc, expected in cases:
            with self.subTest(loc=loc):
                self.assertEqual(country(loc), expected)

    def test_skills_extraction(self):
        text = "Looking for Senior Python/Golang developer experienced in AWS, Kubernetes (k8s), and Docker."
        extracted = skills(text)
        self.assertEqual(extracted, ["python", "go", "aws", "kubernetes", "docker"])

        # Non-IT skills
        text2 = "Must have strong Customer Service, MS Excel, Project Management, and CPR certification."
        extracted2 = skills(text2)
        self.assertEqual(extracted2, ["customer_service", "excel", "project_management", "cpr"])

    def test_employment_type(self):
        self.assertEqual(employment("Dev", "Full-time"), "full_time")
        self.assertEqual(employment("Dev", ["Teilzeit"]), "part_time")
        self.assertEqual(employment("Marketing Intern"), "internship")
        self.assertEqual(employment("Contract Dev"), "contract")
        self.assertEqual(employment("Dev"), "")

    def test_years_experience_extraction(self):
        self.assertEqual(years_req("Requirements: 5+ years of experience in Go"), 5)
        self.assertEqual(years_req("Qualifications: 3-5 years of professional experience"), 3)
        self.assertIsNone(years_req("We are 10 years old."))
        self.assertIsNone(years_req("No requirements stated."))

    def test_find_pay_yearly(self):
        self.assertEqual(find_pay("Pay range: $150,000 - $200,000 USD"), [150000, 200000, "USD"])
        self.assertEqual(find_pay("£60k to £80k a year"), [60000, 80000, "GBP"])
        self.assertEqual(find_pay("€70,000 - €90,000"), [70000, 90000, "EUR"])
        self.assertIsNone(find_pay("$20 - $30 per hour"))

    def test_clean_company_name(self):
        self.assertEqual(clean_company("Stripe, Inc."), "Stripe")
        self.assertEqual(clean_company("Acme GmbH"), "Acme")
        self.assertEqual(clean_company("RoleBase LLC"), "RoleBase")
        self.assertEqual(clean_company("OpenAI Ltd."), "OpenAI")
        self.assertEqual(clean_company("Inc"), "Inc")

    def test_fresher_friendly_check(self):
        self.assertTrue(fresher_ok({"level": "junior", "years": None}))
        self.assertTrue(fresher_ok({"level": "intern", "years": 0}))
        self.assertTrue(fresher_ok({"level": "junior", "years": 2}))
        self.assertTrue(fresher_ok({"level": "mid", "years": 2}))

        # Junior requiring 3+ years is not fresher friendly
        self.assertFalse(fresher_ok({"level": "junior", "years": 3}))
        self.assertFalse(fresher_ok({"level": "senior", "years": 1}))
        self.assertFalse(fresher_ok({"level": "manager+", "years": 0}))

    def test_job_factory(self):
        j = job(
            source="greenhouse",
            scope="greenhouse:acme",
            rid="12345",
            company="Acme Corp",
            title="Junior Software Engineer",
            url="https://job-boards.greenhouse.io/acme/jobs/12345",
            location="New York, NY",
            desc="Requirements: 1 year experience in Python. Salary: $100,000 - $120,000."
        )
        self.assertEqual(j["id"], "greenhouse:acme:12345")
        self.assertEqual(j["company"], "Acme Corp")
        self.assertEqual(j["title"], "Junior Software Engineer")
        self.assertEqual(j["level"], "junior")
        self.assertEqual(j["category"], "engineering")
        self.assertEqual(j["country"], "United States")
        self.assertEqual(j["years"], 1)
        self.assertEqual(j["pay"], [100000, 120000, "USD"])
        self.assertIn("python", j["skills"])


if __name__ == "__main__":
    unittest.main()
