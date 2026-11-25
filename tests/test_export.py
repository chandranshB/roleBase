"""Unit tests for Kaggle Parquet and CSV export functionality."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pyarrow as pa
import pyarrow.parquet as pq

import export_kaggle
import scraper


class TestParquetExport(unittest.TestCase):
    def setUp(self):
        self.test_dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.test_dir, ignore_errors=True)

    def test_schema_generation(self):
        schema = export_kaggle.make_pyarrow_schema(export_kaggle.COLUMNS)
        self.assertIsInstance(schema, pa.Schema)
        self.assertEqual(len(schema), len(export_kaggle.COLUMNS))
        self.assertEqual(schema.field("openings").type, pa.int32())
        self.assertEqual(schema.field("years_experience").type, pa.int32())
        self.assertEqual(schema.field("pay_min").type, pa.float64())
        self.assertEqual(schema.field("remote").type, pa.bool_())
        self.assertEqual(schema.field("company").type, pa.string())

    def test_clean_row_for_pyarrow(self):
        raw_row = {
            "source": "greenhouse",
            "company": "Stripe",
            "title": "Staff Engineer",
            "url": "https://stripe.com/jobs/1",
            "openings": "2",
            "team": "",
            "category": "engineering",
            "level": "senior",
            "years_experience": "6",
            "employment": "full_time",
            "remote": 1,
            "location": "Remote - US",
            "country": "United States",
            "skills": "python;aws",
            "pay_min": "190000.50",
            "pay_max": float("nan"),
            "pay_currency": "USD",
            "posted": "2026-11-01",
            "first_seen": "2026-11-01",
            "closed_at": "",
            "ghost_score": "0",
            "ghost_reasons": "",
            "fresher_friendly": False,
        }
        cleaned = export_kaggle.clean_row_for_pyarrow(raw_row, export_kaggle.COLUMNS)
        self.assertEqual(cleaned["openings"], 2)
        self.assertEqual(cleaned["years_experience"], 6)
        self.assertEqual(cleaned["remote"], True)
        self.assertEqual(cleaned["pay_min"], 190000.5)
        self.assertIsNone(cleaned["pay_max"])  # NaN converted to None
        self.assertIsNone(cleaned["team"])  # Empty string converted to None
        self.assertIsNone(cleaned["closed_at"])  # Empty string converted to None
        self.assertEqual(cleaned["ghost_score"], 0)
        self.assertEqual(cleaned["fresher_friendly"], False)

    def test_get_file_catalog(self):
        pq_cat = export_kaggle.get_file_catalog("parquet")
        self.assertIn("jobs.parquet", pq_cat)
        self.assertIn("jobs_by_company.parquet", pq_cat)
        self.assertNotIn("jobs.csv", pq_cat)

        csv_cat = export_kaggle.get_file_catalog("csv")
        self.assertIn("jobs.csv", csv_cat)
        self.assertNotIn("jobs.parquet", csv_cat)

        both_cat = export_kaggle.get_file_catalog("both")
        self.assertIn("jobs.parquet", both_cat)
        self.assertIn("jobs.csv", both_cat)
        self.assertEqual(len(both_cat), 8)

    def test_export_end_to_end_parquet(self):
        # Create a mock board file
        boards_dir = self.test_dir / "jobs"
        boards_dir.mkdir(parents=True)
        board_file = boards_dir / "greenhouse_stripe.jsonl"
        sample_jobs = [
            {"id": "greenhouse:stripe:101", "company": "Stripe", "title": "Software Engineer", "url": "https://stripe.com/101", "years": 3, "pay": [150000, 200000, "USD"], "skills": ["python", "go"]},
            {"id": "greenhouse:stripe:102", "company": "Stripe", "title": "Data Analyst", "url": "https://stripe.com/102", "years": 1, "closed_at": "2026-11-20"}
        ]
        board_file.write_text("\n".join(json.dumps(j) for j in sample_jobs) + "\n", encoding="utf-8")

        out_dir = self.test_dir / "export_out"

        with patch("export_kaggle.DIR", boards_dir):
            export_kaggle.main(["--format", "parquet", "-o", str(out_dir)])

        self.assertTrue((out_dir / "jobs.parquet").exists())
        self.assertTrue((out_dir / "jobs_by_company.parquet").exists())
        self.assertTrue((out_dir / "open_jobs_daily.parquet").exists())
        self.assertTrue((out_dir / "skill_demand_daily.parquet").exists())
        self.assertFalse((out_dir / "jobs.csv").exists())

        # Validate Parquet file
        pf = pq.ParquetFile(out_dir / "jobs.parquet")
        self.assertEqual(pf.metadata.num_rows, 2)
        table = pf.read()
        self.assertEqual(table.column("company").to_pylist(), ["Stripe", "Stripe"])

        # Validate metadata JSON
        meta = json.loads((out_dir / "dataset-metadata.json").read_text(encoding="utf-8"))
        res_paths = [r["path"] for r in meta["resources"]]
        self.assertIn("jobs.parquet", res_paths)
        self.assertNotIn("jobs.csv", res_paths)

    def test_export_end_to_end_both(self):
        boards_dir = self.test_dir / "jobs"
        boards_dir.mkdir(parents=True)
        board_file = boards_dir / "lever_acme.jsonl"
        sample_jobs = [
            {"id": "lever:acme:201", "company": "Acme", "title": "Product Designer", "url": "https://acme.com/201"}
        ]
        board_file.write_text("\n".join(json.dumps(j) for j in sample_jobs) + "\n", encoding="utf-8")

        out_dir = self.test_dir / "both_out"

        with patch("export_kaggle.DIR", boards_dir):
            export_kaggle.main(["--format", "both", "-o", str(out_dir)])

        self.assertTrue((out_dir / "jobs.parquet").exists())
        self.assertTrue((out_dir / "jobs.csv").exists())
        self.assertTrue((out_dir / "jobs_by_company.parquet").exists())
        self.assertTrue((out_dir / "jobs_by_company.csv").exists())

    def test_scraper_facade_export(self):
        out_dir = self.test_dir / "scraper_out"
        with patch("export_kaggle.main") as mock_export:
            scraper.export_parquet(out_dir=out_dir, fmt="parquet")
            mock_export.assert_called_once_with(["--format", "parquet", "--output-dir", str(out_dir), "--compression", "snappy"])

    def test_site_parquet_bundle_and_pages_compatibility(self):
        import pyarrow as pa
        import pyarrow.parquet as pq

        import build_pages

        site_dir = self.test_dir / "_site"
        site_dir.mkdir(parents=True)
        meta_bytes = json.dumps({"updated": "2026-11-25 00:00 UTC", "levels": ["mid"], "sources": ["lever"], "total": 1}).encode("utf-8")
        schema = pa.schema([
            ("title", pa.string()), ("company", pa.string()), ("location", pa.string()), ("remote", pa.int8()),
            ("level", pa.int16()), ("source", pa.int16()), ("date", pa.string()), ("url", pa.string()),
            ("category", pa.string()), ("employment", pa.string()), ("years", pa.int16()), ("pay_min", pa.float64()),
            ("pay_max", pa.float64()), ("pay_currency", pa.string()), ("skills", pa.string()), ("team", pa.string()),
            ("ghost_score", pa.int16()), ("ghost_reasons", pa.string()), ("fresher_friendly", pa.int8()),
        ], metadata={b"argus_meta": meta_bytes})
        tbl = pa.Table.from_arrays([
            ["Backend Engineer"], ["Acme"], ["Remote"], [1], [0], [0], ["2026-11-25"],
            ["https://jobs.lever.co/acme/1"], ["engineering"], ["full_time"], [1], [120000.0],
            [160000.0], ["USD"], ["python,sql"], ["Core"], [0], [""], [1],
        ], schema=schema)
        pq.write_table(tbl, site_dir / "jobs.parquet", compression="snappy")

        self.assertTrue(Path("web/vendor/hyparquet.mjs").exists())
        html_text = Path("web/index.html").read_text(encoding="utf-8")
        self.assertIn("feed.parquet", html_text)
        self.assertIn("jobs.parquet", html_text)
        self.assertIn("companies.parquet", html_text)
        self.assertIn("./vendor/hyparquet.mjs", html_text)

        with patch.object(build_pages, "SITE", site_dir):
            build_pages.main()
        self.assertTrue((site_dir / "sitemap.xml").exists())


if __name__ == "__main__":
    unittest.main()
