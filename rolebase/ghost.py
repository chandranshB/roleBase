"""Ghost job heuristics, repost auditing, and staleness detection."""
from collections import Counter
import datetime as dt
from typing import Any, Dict, List, Optional, Tuple

from rolebase.config import EVERGREEN_RX, HIDDEN_RX
from rolebase.normalize import _clean_title


def ghost_flags(jobs: List[Dict[str, Any]], today: Optional[dt.date] = None) -> Dict[str, List[Tuple[str, int]]]:
    """Calculate ghost risk triggers and point penalties per job id.
    
    Points scale:
      0 = standard posting
      2 = possibly stale/ghost
      3+ = likely ghost or perpetual talent pool
    """
    today = today or dt.date.today()
    same = {}
    for j in jobs:
        c = j.get("company", "")
        t = _clean_title(j.get("title"))
        k = (c, t)
        same[k] = same.get(k, 0) + 1

    out = {}
    for j in jobs:
        c = j.get("company", "")
        t = _clean_title(j.get("title"))
        r = []
        posted = j.get("posted") or j.get("first_seen")
        if posted:
            try:
                age = (today - dt.date.fromisoformat(str(posted))).days
                if age >= 365:
                    r.append(("open for over a year", 2))
                elif age >= 180:
                    r.append(("open for over 6 months", 1))
            except Exception:
                pass
        if EVERGREEN_RX.search(j.get("title", "")):
            r.append(("talent-pool / expression-of-interest wording", 3))
        if HIDDEN_RX.search(c):
            r.append(("agency or hidden employer", 1))
        n = same.get((c, t), 0)
        if n >= 8:
            r.append((f"{n} identical postings", 1))
        if j.get("reposts"):
            r.append((f"reposted {j['reposts']}x", 1 if j["reposts"] < 3 else 2))
        out[j["id"]] = r
    return out


def board_ghost_flags(jobs: List[Dict[str, Any]], today_date: Optional[dt.date] = None) -> Dict[str, List[Tuple[str, int]]]:
    """Board-level alias for ghost score calculation."""
    return ghost_flags(jobs, today=today_date)
