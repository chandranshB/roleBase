"""Resilient HTTP client and request session manager with exponential backoff."""
import datetime as dt
import html
import re
import time
from typing import Any, Callable, Dict, Optional, Union

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from rolebase.config import TIMEOUT


class Client(requests.Session):
    """Session subclass handling transient socket drops and chunked read interruptions."""
    waits = (2, 6)  # seconds before 2nd and 3rd retry

    def request(self, *a, **kw):
        for w in (*self.waits, None):
            try:
                return super().request(*a, **kw)
            except (requests.exceptions.ChunkedEncodingError,
                    requests.exceptions.ConnectionError,
                    requests.exceptions.ReadTimeout):
                if w is None:
                    raise
                time.sleep(w)


# Global default scraper session singleton
S = Client()
S.headers["User-Agent"] = "roleBase-job-scraper"
S.headers["Accept-Encoding"] = "gzip, deflate"
S.mount(
    "https://",
    HTTPAdapter(
        pool_maxsize=128,
        max_retries=Retry(
            total=5,
            backoff_factor=2,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=None  # Retry POST requests as well (e.g., read-only Workday queries)
        ),
    ),
)


def body(call: Callable[[], requests.Response], tries: int = 3) -> Any:
    """Execute request callback and parse JSON body, retrying decode errors, 429s and 5xxs."""
    for i in range(tries):
        try:
            r = call()
            r.raise_for_status()
            return r.json()
        except (ValueError, requests.exceptions.RequestException) as e:
            if isinstance(e, requests.exceptions.HTTPError) and e.response is not None and 400 <= e.response.status_code < 500 and e.response.status_code != 429:
                raise
            if i == tries - 1:
                raise
            time.sleep(2 * (i + 1))


def get(url: str, **kw) -> Any:
    """Convenience helper to fetch and parse JSON using the global session."""
    return body(lambda: S.get(url, timeout=TIMEOUT, **kw))


def text(h: Optional[str]) -> str:
    """Convert HTML (possibly entity-escaped) into clean, unescaped plain text."""
    return html.unescape(re.sub(r"<[^>]+>", " ", html.unescape(h or "")))


def day(v: Any) -> Optional[str]:
    """Parse epoch milliseconds, epoch seconds, or ISO date strings into YYYY-MM-DD."""
    if not v:
        return None
    if isinstance(v, (int, float)) or str(v).isdigit():
        v = float(v)
        v = v / 1000 if v > 1e11 else v
        return dt.datetime.fromtimestamp(v, dt.timezone.utc).date().isoformat()
    return str(v)[:10]
