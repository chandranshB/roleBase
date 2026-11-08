"""JSONL and CSV persistence, differential merging, deduplication, and compaction."""
import csv
import datetime as dt
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from rolebase.config import DEFAULTS, DIR, KEEP_CLOSED_DAYS
from rolebase.normalize import category, country, fresher_ok, level, url_of


def load(scopes: Optional[Set[str]] = None, open_only: bool = False) -> Dict[str, Dict[str, Any]]:
    """Stream and reconstruct full job objects from disk (multi-threaded, memory-safe)."""
    db = {}
    if scopes is not None:
        existing = set(os.listdir(DIR)) if DIR.exists() else set()
        target_names = {s.replace(":", "_").replace("/", "_") + ".jsonl" for s in scopes}
        files = [DIR / name for name in (target_names & existing)]
    else:
        files = [DIR / f for f in os.listdir(DIR) if f.endswith(".jsonl")] if DIR.exists() else []

    def read_one(f: Path) -> List[tuple]:
        items = []
        try:
            content = f.read_text(encoding="utf-8")
        except Exception:
            return items
        for l in content.split("\n"):
            l = l.strip()
            if not l:
                continue
            if open_only and '"closed_at":' in l:
                continue
            try:
                j = json.loads(l)
            except Exception:
                continue
            if open_only and j.get("closed_at") is not None:
                continue
            j.setdefault("source", j["id"].split(":")[0])
            j.setdefault("url", url_of(j["id"]))
            j.setdefault("level", level(j["title"], j.get("years")))
            j.setdefault("category", category(j["title"], j.get("team")))
            j.setdefault("country", country(j.get("location", "")))
            for k, v in DEFAULTS.items():
                j.setdefault(k, list(v) if isinstance(v, list) else v)
            items.append((j["id"], j))
        return items

    with ThreadPoolExecutor(max_workers=8) as ex:
        for batch in ex.map(read_one, files):
            for jid, j in batch:
                db[jid] = j
    return db


def slim(j: Dict[str, Any]) -> Dict[str, Any]:
    """Strip empty fields and anything derivable before writing to disk."""
    r = {k: v for k, v in j.items() if v is not None and v is not False and v != "" and v != []}
    r.pop("source", None)
    if not r.get("reposts"):
        r.pop("reposts", None)
    if "url" in r and r["url"] == url_of(j["id"]):
        del r["url"]
    if r.get("level") == level(j["title"], j.get("years")):
        del r["level"]
    if r.get("category") == category(j["title"], j.get("team")):
        del r["category"]
    if r.get("country") == country(j.get("location", "")):
        del r["country"]
    return r


def save(db: Dict[str, Dict[str, Any]], target_scopes: Optional[Set[str]] = None) -> None:
    """Write compact sorted JSONL files to data/jobs/."""
    DIR.mkdir(parents=True, exist_ok=True)
    if target_scopes is not None:
        target_names = {s.replace(":", "_").replace("/", "_") for s in target_scopes}
        shards = {}
        for i in sorted(db):
            name = i.rsplit(":", 1)[0].replace(":", "_").replace("/", "_")
            if name in target_names:
                shards.setdefault(name, []).append(
                    json.dumps(slim(db[i]), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
                )
        for name in target_names:
            lines = shards.get(name, [])
            target_file = DIR / f"{name}.jsonl"
            if lines:
                with open(target_file, "w", encoding="utf-8", newline="\n") as f:
                    f.write("\n".join(lines) + "\n")
            elif target_file.exists():
                target_file.unlink()
        return

    shards = {}
    for i in sorted(db):
        name = i.rsplit(":", 1)[0].replace(":", "_").replace("/", "_")
        shards.setdefault(name, []).append(
            json.dumps(slim(db[i]), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        )
    for f in DIR.glob("*.jsonl"):
        if f.stem not in shards:
            f.unlink()
    for name, lines in shards.items():
        with open(DIR / f"{name}.jsonl", "w", encoding="utf-8", newline="\n") as f:
            f.write("\n".join(lines) + "\n")


def merge(db: Dict[str, Dict[str, Any]], fetched: Dict[str, List[Dict[str, Any]]], today: str,
          pruned: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Dict[str, Any]]:
    """Merge newly crawled board jobs into current state; track closures and reposts."""
    seen = {j["id"] for jobs in fetched.values() for j in jobs}
    tkey = lambda i, o: (i.rsplit(":", 1)[0], re.sub(r"\W", "", o["title"].lower()))
    closed = {}
    for i, o in db.items():
        if o["closed_at"]:
            closed[tkey(i, o)] = closed.get(tkey(i, o), 0) + 1
    for jobs in fetched.values():
        for j in jobs:
            old = db.get(j["id"])
            db[j["id"]] = {
                **j,
                "first_seen": old["first_seen"] if old else today,
                "closed_at": None,
                "reposts": old.get("reposts", 0) if old else closed.get(tkey(j["id"], j), 0)
            }
    for i, o in db.items():
        if o["closed_at"] is None and i not in seen and i.rsplit(":", 1)[0] in fetched:
            o["closed_at"] = today
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
    keep = {i: o for i, o in db.items() if not (o["closed_at"] and o["closed_at"] < cutoff)}
    if pruned is not None:
        pruned.extend(o for i, o in db.items() if i not in keep)
    return keep


def collapse(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Merge identical bulk postings into one row with an openings count."""
    g = {}
    for r in sorted(rows, key=lambda r: (str(r.get("first_seen") or ""), str(r.get("url") or ""))):
        k = (
            str(r.get("company") or ""),
            str(r.get("title") or "").lower().strip(),
            str(r.get("team") or "").lower(),
            str(r.get("location") or "").lower().strip(),
            bool(r.get("closed_at"))
        )
        if k in g:
            g[k]["openings"] += 1
        else:
            g[k] = {**r, "openings": 1}
    return sorted(g.values(), key=lambda r: (str(r.get("company") or ""), str(r.get("title") or ""), str(r.get("url") or "")))


def append_csv(path: Path, header: List[str], rows: List[List[Any]]) -> None:
    """Safely append rows to CSV, creating file and writing header if missing."""
    new = not path.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        if new:
            w.writerow(header)
        w.writerows(rows)


def log_history(db: Dict[str, Dict[str, Any]], pruned: List[Dict[str, Any]], today: str) -> None:
    """Log daily metrics snapshot to pulse.csv and skills.csv, and pruned jobs to archive.csv."""
    pulse = Path("data/pulse.csv")
    if not (pulse.exists() and any(l.startswith(today + ",") for l in pulse.read_text(encoding="utf-8").split("\n") if l)):
        counts = {}
        for i, o in db.items():
            if o["closed_at"] is None:
                c = counts.setdefault(i.rsplit(":", 1)[0], [0, 0])
                c[0] += 1
                c[1] += fresher_ok(o)
        append_csv(pulse, ["date", "board", "open", "fresher"], [[today, b, n, f] for b, (n, f) in sorted(counts.items())])

    skills_path = Path("data/skills.csv")
    if not (skills_path.exists() and any(l.startswith(today + ",") for l in skills_path.read_text(encoding="utf-8").split("\n") if l)):
        live = [o for o in db.values() if o["closed_at"] is None]
        tagged = sum(bool(o["skills"]) for o in live)
        n = {}
        for o in live:
            for k in o["skills"]:
                n[k] = n.get(k, 0) + 1
        append_csv(skills_path, ["date", "skill", "jobs", "tagged"], [[today, k, c, tagged] for k, c in sorted(n.items())])

    if pruned:
        append_csv(
            Path("data/archive.csv"),
            ["id", "company", "level", "category", "posted", "first_seen", "closed_at"],
            [[o["id"], o["company"], o["level"], o["category"], o["posted"] or "", o["first_seen"], o["closed_at"]] for o in pruned]
        )
