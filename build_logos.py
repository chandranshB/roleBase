"""Fetch each tracked company's icon for the website at build time. Cached, never committed, never in the Kaggle data.

Order per company: the logo on its Lever board page, else the best icon on its own homepage (domain from domains.json).
Only sharp icons are kept (PNG/ICO at least MIN px, or SVG); anything else falls back to the letter avatar on the site.
Run after build_site.py: it writes _site/logos/* and _site/logos.json ({company name: file})."""
import json
import re
import shutil
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests

CACHE, SITE = Path("logo-cache"), Path("_site")
MIN, MAX_BYTES, FRESH, RETRY = 48, 120_000, 30 * 86400, 7 * 86400  # px, bytes, seconds before re-fetching a hit / a miss
HEADERS = {"User-Agent": "roleBase-job-scraper (icon fetch)"}


def kind(b):
    """Image type from magic bytes, or None for anything we don't use."""
    if b[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if b[:4] == b"\x00\x00\x01\x00":
        return "ico"
    if b.lstrip()[:1] == b"<" and b"<svg" in b[:2000].lower():
        return "svg"


def size(b, ext):
    """Largest pixel dimension; SVG is vector, so it always counts as sharp."""
    if ext == "svg":
        return 9999
    if ext == "png":
        return max(struct.unpack(">II", b[16:24]))
    n = struct.unpack("<H", b[4:6])[0]
    return max((max(b[6 + 16 * i] or 256, b[7 + 16 * i] or 256) for i in range(n)), default=0)


def wide(png):
    w, h = struct.unpack(">II", png[16:24])
    return w / h


def candidates(html, base):
    """Icon URLs from <link rel=...icon...> tags, best first (touch icons, SVG, bigger sizes), then /favicon.ico."""
    out = []
    for tag in re.findall(r"<link\b[^>]*>", html, re.I):
        attr = dict((k.lower(), v) for k, v in re.findall(r'([\w-]+)\s*=\s*["\']([^"\']*)["\']', tag))
        rel, href = attr.get("rel", "").lower(), attr.get("href")
        if not href or "icon" not in rel or "mask" in rel:
            continue
        px = max([int(n) for n in re.findall(r"(\d+)x\d+", attr.get("sizes", ""))] or [0])
        svg = "svg" in attr.get("type", "") or href.split("?")[0].endswith(".svg")
        out.append(((200 if "apple-touch" in rel else 150 if svg else 100) + min(px, 99), urljoin(base, href)))
    out.append((1, urljoin(base, "/favicon.ico")))
    return [u for _, u in sorted(out, key=lambda x: -x[0])]


def fetch(url):
    r = requests.get(url, headers=HEADERS, timeout=15, allow_redirects=True)
    return r.content if r.ok and len(r.content) <= MAX_BYTES else None


def find_logo(name, slug, lever_slug, domain):
    """-> (bytes, ext) or None"""
    try:
        if lever_slug:
            m = re.search(r'<img[^>]+src="(https://lever-client-logos[^"]+)"', requests.get(f"https://jobs.lever.co/{lever_slug}", headers=HEADERS, timeout=15).text)
            b = m and fetch(m[1])
            if b and kind(b) == "png" and max(struct.unpack(">II", b[16:24])) >= MIN and wide(b) < 1.6:  # wordmarks are unreadable in a small square
                return b, "png"
        if domain:
            page = requests.get(f"https://{domain}/", headers=HEADERS, timeout=15, allow_redirects=True)
            for u in candidates(page.text if page.ok else "", page.url)[:5]:
                b = fetch(u)
                if b and kind(b) and size(b, kind(b)) >= MIN:
                    return b, kind(b)
    except Exception as e:
        print(f"  {name}: {type(e).__name__}")


def main():
    cfg = json.loads(Path("companies.json").read_text(encoding="utf-8"))
    domains = json.loads(Path("domains.json").read_text(encoding="utf-8"))
    todo = [(n, re.sub(r"\W+", "-", n.lower()).strip("-"), s if src == "lever" else None, domains.get(n))
            for src, boards in cfg.items() for s, n in boards.items()]
    CACHE.mkdir(exist_ok=True)
    idx_file = CACHE / "index.json"
    idx = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.exists() else {}
    now = time.time()

    def one(t):
        name, slug, lever, domain = t
        e = idx.get(name)
        if e and now - e["t"] < (FRESH if e["file"] else RETRY):
            return name, e
        got = find_logo(name, slug, lever, domain) or (time.sleep(2), find_logo(name, slug, lever, domain))[1]  # one retry for flaky networks
        if got:
            (CACHE / f"{slug}.{got[1]}").write_bytes(got[0])
        return name, {"file": f"{slug}.{got[1]}" if got else None, "t": now}

    with ThreadPoolExecutor(8) as ex:
        idx.update(dict(ex.map(one, todo)))
    idx_file.write_text(json.dumps(idx, indent=1), encoding="utf-8")

    out = SITE / "logos"
    out.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for name, *_ in todo:
        f = (idx.get(name) or {}).get("file")
        if f and (CACHE / f).exists():
            shutil.copy(CACHE / f, out / f)
            mapping[name] = f"logos/{f}"
    (SITE / "logos.json").write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    missing = [n for n, *_ in todo if n not in mapping]
    print(f"logos: {len(mapping)} of {len(todo)} companies" + (f"; letter avatar for {', '.join(missing)}" if missing else ""))


if __name__ == "__main__":
    main()
