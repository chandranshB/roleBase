#!/usr/bin/env python3
"""Sync and deduplicate open canonical jobs from first-party ATS datasets into data/jobs/*.jsonl.
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


def fetch_shard(shard_idx, company_lookup):
    shard_str = f"shard_{shard_idx:04d}.parquet"
    url = f"{HF_BASE}/data/active/minimal/current/{shard_str}"
    req = urllib.request.Request(url, headers={"User-Agent": "roleBase-sync"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            buf = io.BytesIO(resp.read())
        table = pq.read_table(buf)
        df = table.to_pandas()
    except Exception as e:
        print(f"Warning: shard {shard_idx} failed: {e}", flush=True)
        return []

    converted = []
    for _, row in df.iterrows():
        pid = row["posting_id"]
        if not pid:
            continue

        cid = row.get("company_id")
        c_info = company_lookup.get(cid)
        if not c_info:
            continue
        cname, cslug, cats = c_info

        title = (row.get("title") or "").strip()
        if not title:
            continue

        apply_url = row.get("apply_url") or ""
        loc_parts = [str(p).strip() for p in [row.get("location_city"), row.get("location_region"), row.get("country")]
                     if pd.notna(p) and str(p).strip()]
        loc = ", ".join(loc_parts)

        remote = bool(row.get("is_remote")) or "remote" in loc.lower()
        pay = None
        if pd.notna(row.get("salary_min")) and pd.notna(row.get("salary_max")):
            curr = row.get("salary_currency") or "USD"
            try:
                pay = [int(row["salary_min"]), int(row["salary_max"]), str(curr)]
            except (ValueError, TypeError):
                pay = None

        posted = str(row.get("posted_at"))[:10] if pd.notna(row.get("posted_at")) else None
        first_seen = str(row.get("first_seen_at"))[:10] if pd.notna(row.get("first_seen_at")) else posted or dt.date.today().isoformat()
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
            "closed_at": None,
            "reposts": 0
        }
        converted.append(item)

    return converted


def main():
    parser = argparse.ArgumentParser(description="Sync canonical open jobs without redundancy")
    parser.add_argument("--shards", type=int, default=16, help="Number of shards to sync (up to 64)")
    parser.add_argument("--start-shard", type=int, default=0, help="Starting shard index")
    parser.add_argument("--workers", type=int, default=8, help="Parallel download workers")
    args = parser.parse_args()

    t0 = time.time()
    print("Loading companies relational lookup...", flush=True)
    cdf = pd.read_parquet(COMPANIES_PARQUET)
    company_lookup = {row["id"]: (row["name"], row["slug"], row["ats"]) for _, row in cdf.iterrows()}
    print(f"Loaded {len(company_lookup):,} companies.", flush=True)

    shards_to_process = list(range(args.start_shard, min(args.start_shard + args.shards, 64)))
    print(f"Processing {len(shards_to_process)} shards with {args.workers} workers...", flush=True)

    boards_to_write = {}
    seen_ids = set()
    seen_urls = set()
    norm = lambda c, t, l: re.sub(r"\W", "", (clean_company(c) + t + l)).lower()
    seen_norm = set()
    total_downloaded = 0

    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        futs = {ex.submit(fetch_shard, s, company_lookup): s for s in shards_to_process}
        for fut in as_completed(futs):
            shard_idx = futs[fut]
            try:
                jobs = fut.result()
                total_downloaded += len(jobs)
                added_this_shard = 0
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

                    bname = pid.rsplit(":", 1)[0].replace(":", "_").replace("/", "_")
                    boards_to_write.setdefault(bname, []).append(j)
                    added_this_shard += 1

                print(f"  Shard {shard_idx:02d}: {len(jobs):,} raw -> +{added_this_shard:,} stream-deduped jobs", flush=True)
            except Exception as e:
                print(f"  Shard {shard_idx:02d} error: {e}", flush=True)

    print(f"\nMerging {len(seen_ids):,} new unique jobs into target boards on disk (checking board files for disk dedup)...", flush=True)
    DIR.mkdir(parents=True, exist_ok=True)
    total_written = 0
    total_skipped_on_disk = 0

    for bname, new_jobs in boards_to_write.items():
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
        for j in new_jobs:
            if j["id"] in board_ids:
                total_skipped_on_disk += 1
                continue
            if j.get("url") and j["url"] in board_urls:
                total_skipped_on_disk += 1
                continue
            board_ids.add(j["id"])
            if j.get("url"):
                board_urls.add(j["url"])
            board_lines.append(json.dumps(slim(j), sort_keys=True, ensure_ascii=False, separators=(",", ":")))
            new_for_board += 1

        if new_for_board > 0:
            board_lines = sorted(set(board_lines))
            with open(bfile, "w", encoding="utf-8", newline="\n") as fp:
                fp.write("\n".join(board_lines) + "\n")
            total_written += new_for_board

    print(f"\nZero-redundancy sync complete in {time.time() - t0:.1f}s!", flush=True)
    print(f"  Downloaded from canonical shards: {total_downloaded:,}", flush=True)
    print(f"  Unique stream items:              {len(seen_ids):,}", flush=True)
    print(f"  Skipped as already on disk:       {total_skipped_on_disk:,}", flush=True)
    print(f"  Net new jobs written to disk:     {total_written:,}", flush=True)


if __name__ == "__main__":
    main()
