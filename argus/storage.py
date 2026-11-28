"""JSONL and CSV persistence, differential merging, deduplication, and compaction."""
import csv
import datetime as dt
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from argus.config import DEFAULTS, DIR, KEEP_CLOSED_DAYS
from argus.normalize import category, country, fresher_ok, level, url_of


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
        for line in content.split("\n"):
            line = line.strip()
            if not line:
                continue
            if open_only and '"closed_at":' in line:
                continue
            try:
                j = json.loads(line)
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
    def tkey(i: str, o: Dict[str, Any]) -> tuple:
        return (i.rsplit(":", 1)[0], re.sub(r"\W", "", str(o.get("title") or "").lower()))
    closed = {}
    for i, o in db.items():
        if o.get("closed_at"):
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
        if o.get("closed_at") is None and i not in seen and i.rsplit(":", 1)[0] in fetched:
            o["closed_at"] = today
    cutoff = (dt.date.fromisoformat(today) - dt.timedelta(KEEP_CLOSED_DAYS)).isoformat()
    keep = {i: o for i, o in db.items() if not (o.get("closed_at") and isinstance(o["closed_at"], str) and o["closed_at"] < cutoff)}
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


HISTORY_SPECS = {
    "pulse": [("date", "str"), ("board", "str"), ("open", "int"), ("fresher", "int")],
    "skills": [("date", "str"), ("skill", "str"), ("jobs", "int"), ("tagged", "int")],
    "archive": [("id", "str"), ("company", "str"), ("level", "str"), ("category", "str"), ("posted", "str"), ("first_seen", "str"), ("closed_at", "str")],
}


def _history_schema(kind: str):
    import pyarrow as pa

    return pa.schema([
        (col, pa.int32() if typ == "int" else pa.string())
        for col, typ in HISTORY_SPECS[kind]
    ])


def read_history_table(kind: str):
    """Read data/{kind}.parquet (or legacy data/{kind}.csv) as a PyArrow Table."""
    import pyarrow as pa
    import pyarrow.parquet as pq

    pq_path = Path(f"data/{kind}.parquet")
    csv_path = Path(f"data/{kind}.csv")
    schema = _history_schema(kind)
    if pq_path.exists():
        return pq.read_table(pq_path)
    if csv_path.exists():
        spec = HISTORY_SPECS[kind]
        cols: Dict[str, List[Any]] = {c: [] for c, _ in spec}
        with open(csv_path, encoding="utf-8", errors="replace", newline="") as f:
            for r in csv.DictReader(f):
                for col, typ in spec:
                    val = r.get(col)
                    if typ == "int":
                        try:
                            cols[col].append(int(val or 0))
                        except (ValueError, TypeError):
                            cols[col].append(0)
                    else:
                        cols[col].append(str(val or ""))
        return pa.Table.from_pydict(cols, schema=schema)
    return pa.Table.from_pydict({c: [] for c, _ in HISTORY_SPECS[kind]}, schema=schema)


def write_history_parquet(kind: str, rows: List[List[Any]], replace_date: str = None) -> None:
    """Append or date-upsert rows into ZSTD-compressed data/{kind}.parquet (migrating any legacy CSV)."""
    import pyarrow as pa
    import pyarrow.compute as pc
    import pyarrow.parquet as pq

    if not rows:
        return
    pq_path = Path(f"data/{kind}.parquet")
    pq_path.parent.mkdir(parents=True, exist_ok=True)
    schema = _history_schema(kind)
    spec = HISTORY_SPECS[kind]

    existing = read_history_table(kind)
    if existing.num_rows > 0 and replace_date and "date" in existing.column_names:
        mask = pc.not_equal(existing["date"], replace_date)
        existing = existing.filter(mask)

    new_cols: Dict[str, List[Any]] = {c: [] for c, _ in spec}
    for row in rows:
        for idx, (col, typ) in enumerate(spec):
            val = row[idx] if idx < len(row) else None
            if typ == "int":
                try:
                    new_cols[col].append(int(val or 0))
                except (ValueError, TypeError):
                    new_cols[col].append(0)
            else:
                new_cols[col].append(str(val or ""))
    new_table = pa.Table.from_pydict(new_cols, schema=schema)
    combined = pa.concat_tables([existing, new_table]) if existing.num_rows > 0 else new_table
    pq.write_table(combined, pq_path, compression="zstd")


def log_history(db: Dict[str, Dict[str, Any]], pruned: List[Dict[str, Any]], today: str) -> None:
    """Log daily metrics snapshot to pulse.parquet and skills.parquet, and pruned jobs to archive.parquet."""
    counts = {}
    for i, o in db.items():
        if o["closed_at"] is None:
            c = counts.setdefault(i.rsplit(":", 1)[0], [0, 0])
            c[0] += 1
            c[1] += fresher_ok(o)
    if counts:
        write_history_parquet("pulse", [[today, b, n, f] for b, (n, f) in sorted(counts.items())], replace_date=today)

    live = [o for o in db.values() if o["closed_at"] is None]
    tagged = sum(bool(o["skills"]) for o in live)
    n = {}
    for o in live:
        for k in o["skills"]:
            n[k] = n.get(k, 0) + 1
    if n:
        write_history_parquet("skills", [[today, k, c, tagged] for k, c in sorted(n.items())], replace_date=today)

    if pruned:
        write_history_parquet(
            "archive",
            [[o["id"], o["company"], o["level"], o["category"], o["posted"] or "", o["first_seen"], o["closed_at"]] for o in pruned],
        )
