#!/usr/bin/env python3
"""Sync and deduplicate canonical historical jobs from recent first-party ATS partitions into data/jobs/*.jsonl.
Strict zero-redundancy: deduplicates by posting ID, apply URL, and (company, title, location).
"""
import argparse
import datetime as dt
import io
import json
import os
import re
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pandas as pd
import pyarrow.parquet as pq

from scraper import clean_company, level, category, employment, skills, slim

DIR = Path("data/jobs")
COMPANIES_PARQUET = Path("scratch/companies.parquet")
if not COMPANIES_PARQUET.exists():
    COMPANIES_PARQUET = Path(r"C:\Users\chand\.gemini\antigravity\brain\6efbe87d-c42b-4ca3-9164-6cf50468e501\scratch\companies.parquet")

HF_BASE = "https://huggingface.co/datasets/scholarweave/Jobs-Dataset-v2/resolve/main"


def fetch_file(fpath, company_lookup):
    url = f"{HF_BASE}/{fpath}"
    req = urllib.request.Request(url, headers={"User-Agent": "roleBase-sync"})
    try:
        with urllib.request.urlopen(req, timeout=45) as resp:
            buf = io.BytesIO(resp.read())
        table = pq.read_table(buf)
        df = table.to_pandas()
    except Exception as e:
        print(f"Warning: {fpath} failed: {e}", flush=True)
        return []

    converted = []
    for _, row in df.iterrows():
        raw_pid = row.get("posting_id") if pd.notna(row.get("posting_id")) else row.get("id")
        if not raw_pid or pd.isna(raw_pid):
            continue
        pid = str(raw_pid).strip()

        cid = row.get("company_id")
        c_info = company_lookup.get(cid)
        if not c_info:
            cats = row.get("source_system") or pid.split(":")[0]
            cname = pid.split(":")[1].split(".")[0].replace("-", " ").capitalize() if ":" in pid else ""
        else:
            cname, cslug, cats = c_info
            cname = str(cname).strip() if pd.notna(cname) else ""

        title = str(row.get("title") or "").strip()
        if not title:
            continue

        apply_url = str(row.get("apply_url") or "").strip()
        loc_parts = [str(p).strip() for p in [row.get("location_city"), row.get("location_region"), row.get("country")]
                     if pd.notna(p) and str(p).strip()]
        loc = ", ".join(loc_parts)

        remote = bool(row.get("is_remote")) or "remote" in loc.lower() or row.get("workplace_type") == "remote"
        pay = None
        if pd.notna(row.get("salary_min")) and pd.notna(row.get("salary_max")):
            curr = row.get("salary_currency") or "USD"
            try:
                pay = [int(row["salary_min"]), int(row["salary_max"]), str(curr)]
            except (ValueError, TypeError):
                pay = None

        posted = str(row.get("posted_at"))[:10] if pd.notna(row.get("posted_at")) else None
        first_seen = str(row.get("first_seen_at") or row.get("fetched_time"))[:10] if (pd.notna(row.get("first_seen_at")) or pd.notna(row.get("fetched_time"))) else posted or dt.date.today().isoformat()
        closed_at = str(row.get("closed_at") or row.get("close_time"))[:10] if (pd.notna(row.get("closed_at")) or pd.notna(row.get("close_time"))) else None
        dept = str(row.get("department") or "") if pd.notna(row.get("department")) else ""
        emp_type = [str(row.get("employment_type") or "")] if pd.notna(row.get("employment_type")) else []

        item = {
            "id": pid,
            "source": cats,
            "company": cname,
            "title": title,
            "team": dept,
            "location": loc,
            "remote": remote,
            "level": level(title),
            "category": category(title, dept),
            "employment": employment(title, emp_type),
            "years": None,
            "skills": skills(f"{title} {row.get('description_summary') or ''}"),
            "pay": pay,
            "url": apply_url,
            "posted": posted,
            "first_seen": first_seen,
            "closed_at": closed_at,
            "reposts": 0
        }
        converted.append(item)

    return converted


def get_partition_files(dates):
    import requests
    out_files = []
    print(f"Discovering parquet file paths for {len(dates)} dates...", flush=True)
    for d in dates:
        try:
            r = requests.get(f"https://huggingface.co/api/datasets/scholarweave/Jobs-Dataset-v2/tree/main/data/closed/minimal/date={d}", timeout=10)
            if r.status_code == 200:
                entries = r.json()
                for entry in entries:
                    if entry.get("type") == "file" and entry.get("path", "").endswith(".parquet"):
                        out_files.append(entry["path"])
                    elif entry.get("type") == "directory":
                        r2 = requests.get(f"https://huggingface.co/api/datasets/scholarweave/Jobs-Dataset-v2/tree/main/{entry['path']}", timeout=10)
                        if r2.status_code == 200:
                            for f in r2.json():
                                if f.get("path", "").endswith(".parquet"):
                                    out_files.append(f["path"])
        except Exception as e:
            print(f"Error querying date={d}: {e}", flush=True)
    return out_files


def main():
    parser = argparse.ArgumentParser(description="Sync canonical closed jobs without redundancy")
    parser.add_argument("--num-dates", type=int, default=30, help="Number of recent dates to sync (up to 70)")
    parser.add_argument("--start-date-idx", type=int, default=0, help="Starting date index")
    parser.add_argument("--workers", type=int, default=8, help="Parallel download workers")
    args = parser.parse_args()

    t0 = time.time()
    print("Loading companies relational lookup...", flush=True)
    cdf = pd.read_parquet(COMPANIES_PARQUET)
    company_lookup = {row["id"]: (row["name"], row["slug"], row["ats"]) for _, row in cdf.iterrows()}
    print(f"Loaded {len(company_lookup):,} companies.", flush=True)

    import requests
    r = requests.get("https://huggingface.co/api/datasets/scholarweave/Jobs-Dataset-v2/tree/main/data/closed/minimal")
    items = r.json()
    all_dates = [it["path"].split("date=")[-1] for it in items if "date=" in it["path"]]
    all_dates.sort(reverse=True)

    target_dates = all_dates[args.start_date_idx: args.start_date_idx + args.num_dates]
    print(f"Targeting {len(target_dates)} dates: {target_dates[0]} to {target_dates[-1]}", flush=True)

    files_to_download = get_partition_files(target_dates)
    print(f"Discovered {len(files_to_download):,} parquet files to download.", flush=True)

    boards_to_write = {}
    seen_ids = set()
    seen_urls = set()
    norm = lambda c, t, l: re.sub(r"\W", "", (clean_company(c) + t + l)).lower()
    seen_norm = set()
    total_downloaded = 0

    print(f"Processing {len(files_to_download)} files with {args.workers} workers...", flush=True)
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_file, fpath, company_lookup): fpath for fpath in files_to_download}
        completed_count = 0
        for fut in as_completed(futs):
            fpath = futs[fut]
            completed_count += 1
            try:
                jobs = fut.result()
                total_downloaded += len(jobs)
                added_this_file = 0
                for j in jobs:
                    pid = j["id"]
                    if pid in seen_ids:
                        continue
                    url = j.get("url")
                    if url and url in seen_urls:
                        continue
                    n_sig = norm(j["company"], j["title"], j.get("location", ""))
                    if n_sig in seen_norm:
                        continue

                    seen_ids.add(pid)
                    if url:
                        seen_urls.add(url)
                    seen_norm.add(n_sig)

                    raw_bname = pid.rsplit(":", 1)[0]
                    bname = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', raw_bname).strip('. ')
                    if not bname:
                        bname = "unknown"
                    boards_to_write.setdefault(bname, []).append(j)
                    added_this_file += 1

                if completed_count % 16 == 0 or completed_count == len(files_to_download):
                    print(f"  [{completed_count}/{len(files_to_download)}] Raw jobs: {total_downloaded:,} -> Unique deduplicated: {len(seen_ids):,}", flush=True)
            except Exception as e:
                print(f"  File {fpath} error: {e}", flush=True)

    print(f"\nMerging {len(seen_ids):,} new unique jobs into {len(boards_to_write):,} target boards (32 parallel disk workers)...", flush=True)
    DIR.mkdir(parents=True, exist_ok=True)

    def write_board(item):
        bname, new_jobs = item
        bfile = DIR / f"{bname}.jsonl"
        board_ids = set()
        board_urls = set()
        board_lines = []

        if bfile.exists():
            try:
                for line in bfile.read_text(encoding="utf-8").split("\n"):
                    line = line.strip()
                    if not line:
                        continue
                    board_lines.append(line)
                    try:
                        parsed = json.loads(line)
                        if parsed.get("id"):
                            board_ids.add(parsed["id"])
                        if parsed.get("url"):
                            board_urls.add(parsed["url"])
                    except Exception:
                        continue
            except Exception:
                pass

        new_for_board = 0
        skipped = 0
        for j in new_jobs:
            if j["id"] in board_ids:
                skipped += 1
                continue
            if j.get("url") and j["url"] in board_urls:
                skipped += 1
                continue
            board_ids.add(j["id"])
            if j.get("url"):
                board_urls.add(j["url"])
            board_lines.append(json.dumps(slim(j), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            new_for_board += 1

        if new_for_board > 0:
            board_lines = sorted(set(board_lines))
            try:
                with open(bfile, "w", encoding="utf-8", newline="\n") as fp:
                    fp.write("\n".join(board_lines) + "\n")
            except Exception:
                return 0, skipped
        return new_for_board, skipped

    t_merge = time.time()
    with ThreadPoolExecutor(max_workers=32) as ex:
        results = list(ex.map(write_board, boards_to_write.items()))

    total_written = sum(r[0] for r in results)
    total_skipped_on_disk = sum(r[1] for r in results)

    print(f"\nZero-redundancy sync complete in {time.time() - t0:.1f}s (disk merge took {time.time() - t_merge:.1f}s)!", flush=True)
    print(f"  Downloaded from canonical files:  {total_downloaded:,}", flush=True)
    print(f"  Unique stream items:              {len(seen_ids):,}", flush=True)
    print(f"  Skipped as already on disk:       {total_skipped_on_disk:,}", flush=True)
    print(f"  Net new jobs written to disk:     {total_written:,}", flush=True)


if __name__ == "__main__":
    main()
