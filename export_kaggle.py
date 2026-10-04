"""Build the kaggle/ dataset folder (one CSV + metadata) from data/jobs/*.jsonl."""
import csv
import json
import os
from pathlib import Path

from scraper import load

# Remotive / RemoteOK terms are about attribution and display, so they stay out of a republished dataset.
SKIP = {"remotive", "remoteok"}
COLS = ["id", "source", "company", "title", "location", "remote", "level", "posted", "first_seen", "closed_at", "url"]

rows = sorted((j for j in load().values() if j["source"] not in SKIP), key=lambda j: j["id"])
out = Path("kaggle")
out.mkdir(exist_ok=True)
with open(out / "jobs.csv", "w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, COLS, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)

(out / "dataset-metadata.json").write_text(json.dumps({
    "title": "Job Listings (auto-updated)",
    "id": f"{os.environ.get('KAGGLE_USERNAME', 'user')}/job-listings",
    "licenses": [{"name": "other"}],
    "description": "Open and recently closed job postings scraped from public company ATS boards "
                   "(Greenhouse, Lever, Ashby, SmartRecruiters) and Arbeitnow. `closed_at` empty = still open; "
                   "`url` is the apply link. Refreshed daily by GitHub Actions. Postings belong to their employers.",
}, indent=2), encoding="utf-8")
print(f"{len(rows)} rows -> kaggle/jobs.csv")
