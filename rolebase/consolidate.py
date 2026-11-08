"""Consolidation of shard artifacts, analytics precomputation, and metrics caching."""
import datetime as dt
import json
import os
import shutil
import sys
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List

from rolebase.config import DIR, HEALTH, KEEP_CLOSED_DAYS
from rolebase.normalize import category, clean_company, fresher_ok, level
from rolebase.storage import append_csv


def company_stats(jobs: List[Dict[str, Any]], min_roles: int = 5) -> List[Dict[str, Any]]:
    """Per company: open roles, fresher-friendly count, inflated junior roles, and years stated share."""
    by = {}
    for j in jobs:
        c = j.get("company", "")
        if c:
            by.setdefault(c, []).append(j)
    out = [
        dict(
            company=c,
            open_roles=len(js),
            fresher_roles=sum(map(fresher_ok, js)),
            fresher_share=round(sum(map(fresher_ok, js)) / len(js), 3),
            junior_asking_3plus=sum(j.get("level") in ("intern", "junior") and (j.get("years") or 0) >= 3 for j in js),
            years_stated_share=round(sum(j.get("years") is not None for j in js) / len(js), 2)
        )
        for c, js in by.items() if len(js) >= min_roles
    ]
    return sorted(out, key=lambda r: (-r["fresher_share"], -r["open_roles"]))


def consolidate_shards(today: str = None) -> None:
    """Merge shard outputs, update health logs, prune aged jobs, compute analytics, and update charts_cache.json."""
    t_start = time.time()
    today = today or dt.date.today().isoformat()
    print("Consolidating shards...")

    shards_dir = Path("data/shards")
    if shards_dir.exists():
        for sdir in sorted(shards_dir.glob("shard-*")):
            for jf in sdir.glob("*.jsonl"):
                shutil.copy(jf, DIR / jf.name)

    all_health = {}
    if HEALTH.exists():
        for line in HEALTH.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    all_health[r.pop("b")] = r
                except Exception:
                    pass

    health_sources = list(shards_dir.glob("shard-*/health.json")) + list(Path("data").glob("health-*.json"))
    for hf in health_sources:
        for line in hf.read_text(encoding="utf-8").splitlines():
            if line.strip():
                try:
                    r = json.loads(line)
                    all_health[r.pop("b")] = r
                except Exception:
                    pass

    if all_health:
        HEALTH.write_text(
            "\n".join(json.dumps({"b": k, **v}, sort_keys=True) for k, v in sorted(all_health.items())) + "\n",
            encoding="utf-8", newline="\n"
        )

    shutil.rmtree(shards_dir, ignore_errors=True)
    for hf in Path("data").glob("health-*.json"):
        hf.unlink(missing_ok=True)

    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
    pulse = Path("data/pulse.csv")
    pulse_exists_today = pulse.exists() and any(line.startswith(today + ",") for line in pulse.read_text(encoding="utf-8").split("\n") if line)
    skills_path = Path("data/skills.csv")
    skills_exists_today = skills_path.exists() and any(line.startswith(today + ",") for line in skills_path.read_text(encoding="utf-8").split("\n") if line)

    pulse_counts = {}
    all_skills = Counter()
    tagged_total = 0
    pruned_rows = []
    tot_open = 0
    tot_closed = 0
    all_levels = Counter()
    all_categories = Counter()
    all_companies = Counter()
    all_comp_fresh = Counter()
    all_comp_jr3 = Counter()
    all_comp_years = Counter()

    files = [DIR / f for f in os.listdir(DIR) if f.endswith(".jsonl")] if DIR.exists() else []

    def process_board_file(fpath: Path):
        try:
            content = fpath.read_text(encoding="utf-8")
        except Exception:
            return None
        board = fpath.stem.replace("_", ":", 1)
        lines_to_keep = []
        modified = False
        b_pruned = []
        b_open = 0
        b_fresher = 0
        b_closed = 0
        b_levels = Counter()
        b_categories = Counter()
        b_companies = Counter()
        b_comp_fresh = Counter()
        b_comp_jr3 = Counter()
        b_comp_years = Counter()
        b_skills = Counter()
        b_tagged = 0

        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                j = json.loads(line)
            except Exception:
                continue

            closed_at = j.get("closed_at")
            if closed_at and closed_at < cutoff:
                modified = True
                b_pruned.append([
                    j.get("id", ""), j.get("company", ""), j.get("level", ""), j.get("category", ""),
                    j.get("posted") or "", j.get("first_seen", ""), closed_at
                ])
                continue

            lines_to_keep.append(line)

            if closed_at is None:
                b_open += 1
                y = j.get("years")
                lvl = j.get("level") or level(j.get("title", ""), y)
                b_levels[lvl] += 1
                cat = j.get("category") or category(j.get("title", ""), j.get("team"))
                b_categories[cat] += 1
                is_fresh = (lvl in ("intern", "junior", "mid")) and (y <= 2 if y is not None else lvl != "mid")
                if is_fresh:
                    b_fresher += 1
                comp = clean_company(j.get("company", ""))
                if comp and comp.lower() not in ("unknown", "none", "non renseigné", "non renseigne", "not specified", "null", "n/a"):
                    b_companies[comp] += 1
                    if is_fresh:
                        b_comp_fresh[comp] += 1
                    if lvl in ("intern", "junior") and (y or 0) >= 3:
                        b_comp_jr3[comp] += 1
                    if y is not None:
                        b_comp_years[comp] += 1
                sks = j.get("skills", [])
                if sks:
                    b_tagged += 1
                    for s in sks:
                        b_skills[s] += 1
            else:
                b_closed += 1

        if modified:
            if lines_to_keep:
                fpath.write_text("\n".join(lines_to_keep) + "\n", encoding="utf-8", newline="\n")
            else:
                fpath.unlink(missing_ok=True)

        return (board, b_open, b_fresher, b_closed, b_pruned, b_levels, b_categories, b_companies, b_comp_fresh, b_comp_jr3, b_comp_years, b_skills, b_tagged)

    print(f"Processing {len(files)} boards for pruning, metrics, and charts...")
    sys.stdout.flush()
    with ThreadPoolExecutor(max_workers=8) as ex:
        for res in ex.map(process_board_file, files):
            if not res:
                continue
            board, b_open, b_fresher, b_closed, b_pruned, b_levels, b_categories, b_companies, b_comp_fresh, b_comp_jr3, b_comp_years, b_skills, b_tagged = res
            tot_open += b_open
            tot_closed += b_closed
            all_levels.update(b_levels)
            all_categories.update(b_categories)
            all_companies.update(b_companies)
            all_comp_fresh.update(b_comp_fresh)
            all_comp_jr3.update(b_comp_jr3)
            all_comp_years.update(b_comp_years)
            all_skills.update(b_skills)
            tagged_total += b_tagged
            if b_pruned:
                pruned_rows.extend(b_pruned)
            if not pulse_exists_today and b_open > 0:
                pulse_counts[board] = [b_open, b_fresher]

    if not pulse_exists_today and pulse_counts:
        append_csv(pulse, ["date", "board", "open", "fresher"], [[today, b, n, f] for b, (n, f) in sorted(pulse_counts.items())])

    if not skills_exists_today and all_skills:
        append_csv(skills_path, ["date", "skill", "jobs", "tagged"], [[today, k, c, tagged_total] for k, c in sorted(all_skills.items())])

    if pruned_rows:
        append_csv(Path("data/archive.csv"), ["id", "company", "level", "category", "posted", "first_seen", "closed_at"], pruned_rows)

    company_stats_list = [
        [c, n, all_comp_fresh.get(c, 0), round(all_comp_fresh.get(c, 0) / n, 3), all_comp_jr3.get(c, 0), round(all_comp_years.get(c, 0) / n, 2)]
        for c, n in all_companies.items() if n >= 5
    ]
    company_stats_list.sort(key=lambda r: (-r[3], -r[1]))
    charts_cache = {
        "tot_open": tot_open,
        "levels": dict(all_levels),
        "categories": dict(all_categories),
        "skills": dict(all_skills),
        "companies": dict(all_companies),
        "comp_fresh": dict(all_comp_fresh),
        "company_stats": company_stats_list,
    }
    Path("data/charts_cache.json").write_text(json.dumps(charts_cache), encoding="utf-8")

    print(f"Consolidation complete: {tot_open:,} active jobs, {tot_closed:,} closed jobs | {time.time() - t_start:.1f}s")
    sys.stdout.flush()
