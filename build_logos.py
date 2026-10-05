"""Fetch each tracked company's icon for the website at build time. Cached, never committed, never in the Kaggle data.

Order per company: the logo on its Lever board page, else the best icon on its own homepage (domain from domains.json).
Only sharp icons are kept (PNG/ICO at least MIN px, or SVG); anything else falls back to the letter avatar on the site.
Run after build_site.py: it writes _site/logos/* and _site/logos.json ({company name: file})."""
import io
import json
import re
import shutil
import struct
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin

import requests
from PIL import Image, ImageChops

from scraper import load

CACHE, SITE = Path("logo-cache"), Path("_site")
MIN, MAX_BYTES, FRESH, RETRY = 48, 120_000, 30 * 86400, 7 * 86400  # px, bytes, seconds before re-fetching a hit / a miss
HEADERS = {"User-Agent": "roleBase-job-scraper (icon fetch)"}
BAD = []
VER = 2  # bump when the lookup recipe gains a source, so earlier misses get another try


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
    if not r.ok and "google.com/s2" in url and len(BAD) < 5:
        BAD.append(f"{r.status_code} {url}")  # shown at the end, to tell "no icon" from "we are being blocked"
    return r.content if r.ok and len(r.content) <= MAX_BYTES else None


TLDS = (".com", ".io", ".co", ".ai", ".org", ".de", ".net")


def guess_domain(name):
    """Companies we have no domain for (job-feed employers): try <name>.<tld>, accept only if the homepage's title / site name
    contains the name, so a squatter or a different company with the same word never gets its icon on someone else's jobs."""
    base = re.sub(r"[^a-z0-9]", "", name.lower())
    if len(base) < 8:  # short names are usually common words (Kraken, Sierra, Moss): a wrong logo is worse than a letter
        return None
    for tld in TLDS:
        try:
            r = requests.get(f"https://{base}{tld}/", headers=HEADERS, timeout=8, allow_redirects=True)
        except Exception:
            continue
        if not r.ok:
            continue
        head = r.text[:60000]
        found = re.findall(r"<title[^>]*>(.*?)</title>", head, re.I | re.S) + re.findall(r"og:site_name[^>]*content=[\"']([^\"']*)", head, re.I)
        if base in re.sub(r"[^a-z0-9]", "", " ".join(found).lower()):
            return f"{base}{tld}"


_SI = {}


def simple_icon(name):
    """Brand mark from the Simple Icons library (CC0 files, vector, brand colour), only on an exact name match. Used when the
    company's own site gave no usable icon. Brand names stay the property of their owners; this is just to show who posted a job."""
    if not _SI:
        try:
            data = requests.get("https://cdn.jsdelivr.net/npm/simple-icons@latest/data/simple-icons.json", headers=HEADERS, timeout=30).json()
            for e in data:
                for t in [e["title"], *(e.get("aliases", {}).get("aka", []))]:
                    _SI.setdefault(re.sub(r"[^a-z0-9]", "", t.lower()), (e["slug"], e["hex"]))
        except Exception as e:
            print(f"  simple-icons: {type(e).__name__}")
        _SI.setdefault("", None)
    key = re.sub(r"[^a-z0-9]", "", re.sub(r"\b(inc|llc|ltd|plc|corp|corporation|group|the)\b", "", name.lower()))
    hit = len(key) >= 4 and _SI.get(key)
    if not hit:
        return None
    try:
        b = fetch(f"https://cdn.jsdelivr.net/npm/simple-icons@latest/icons/{hit[0]}.svg")
        if b and kind(b) == "svg":
            return b.replace(b"<svg ", f'<svg fill="#{hit[1]}" '.encode(), 1)
    except Exception as e:
        print(f"  {name}: {type(e).__name__}")


# Last resort for companies with no usable logo: a generic sector icon from Tabler Icons (MIT, same set the site already uses),
# chosen by the field most of the company's jobs are in. No brand involved, so no permission needed.
SECTOR = dict(engineering="code", data="chart-bar", sales="briefcase", marketing="speakerphone", finance="coin", healthcare="stethoscope",
              education="school", science="flask", retail="shopping-cart", manufacturing="building-factory-2", logistics="truck",
              protective="shield", operations="settings", legal="scale", people="users", design="palette", product="box",
              support="headset", security="lock", other="building")


def sector_icon(cat):
    f = CACHE / f"_sector_{cat}.svg"
    if not f.exists():
        r = requests.get(f"https://cdn.jsdelivr.net/npm/@tabler/icons@latest/icons/outline/{SECTOR.get(cat, 'building')}.svg", headers=HEADERS, timeout=30)
        if not r.ok:
            return None
        f.write_bytes(r.content.replace(b"currentColor", b"#6b7280"))
    return f


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
    si = simple_icon(name)
    if si:
        return si, "svg"
    try:  # many big sites refuse bots: ask Google's icon service for the domain's favicon (build time only, never from the visitor's browser)
        b = domain and fetch(f"https://www.google.com/s2/favicons?domain={domain}&sz=128")
        if b and kind(b) == "png" and max(struct.unpack(">II", b[16:24])) >= 32:  # smaller than MIN, but a real logo beats a letter
            return b, "png"
    except Exception as e:
        print(f"  {name}: {type(e).__name__}")


def tidy(b):
    """Crop the empty margin many favicons carry (transparent, or plain white), so the logo fills its tile. -> (PNG bytes, fills the tile)"""
    im = Image.open(io.BytesIO(b)).convert("RGBA")
    a = im.getchannel("A")
    if a.getextrema()[0] < 250:  # transparent margins
        box = a.point(lambda v: 255 if v > 16 else 0).getbbox()
    elif sum(im.getpixel((0, 0))[:3]) >= 3 * 240:  # opaque but white margins; a coloured background is part of the logo, so it stays
        box = ImageChops.difference(im, Image.new("RGBA", im.size, im.getpixel((0, 0)))).convert("L").point(lambda v: 255 if v > 24 else 0).getbbox()
    else:
        box = None
    if box and box[2] - box[0] >= 16 and box[3] - box[1] >= 16:
        im = im.crop(box)
    out = io.BytesIO()
    im.save(out, "PNG", optimize=True)
    w, h = im.size
    # an opaque, coloured, squarish icon is itself the tile: the site should let it fill the tile edge to edge instead of floating inside white padding
    full = a.getextrema()[0] >= 240 and sum(im.getpixel((0, 0))[:3]) < 3 * 240 and .8 <= w / h <= 1.25
    return out.getvalue(), full


def main():
    cfg = json.loads(Path("companies.json").read_text(encoding="utf-8"))
    domains = json.loads(Path("domains.json").read_text(encoding="utf-8"))
    todo = [(n, re.sub(r"\W+", "-", n.lower()).strip("-"), s if src == "lever" else None, domains.get(n))
            for src, boards in cfg.items() for s, n in boards.items()]
    seen, counts = {t[0] for t in todo}, {}
    for j in load().values():  # employers that only come from the open job feeds (Arbeitnow etc.), with 3+ open jobs
        if j["closed_at"] is None and j["company"] not in seen:
            counts[j["company"]] = counts.get(j["company"], 0) + 1
    for n, c in counts.items():
        slug = re.sub(r"\W+", "-", n.lower()).strip("-")
        if c >= 3 and slug:
            todo.append((n, slug, None, domains.get(n)))
    CACHE.mkdir(exist_ok=True)
    idx_file = CACHE / "index.json"
    idx = json.loads(idx_file.read_text(encoding="utf-8")) if idx_file.exists() else {}
    now = time.time()

    def one(t):
        name, slug, lever, domain = t
        e = idx.get(name)
        if e and now - e["t"] < (FRESH if e["file"] else RETRY) and (e["file"] or e.get("v") == VER):  # misses from an older lookup recipe are retried
            return name, e
        domain = domain or (e or {}).get("domain") or guess_domain(name) or (re.sub(r"[^a-z0-9]", "", name.lower()) + ".com" if name in seen else None)  # curated names: plain .com is a safe last guess
        got = find_logo(name, slug, lever, domain) or (time.sleep(2), find_logo(name, slug, lever, domain))[1]  # one retry for flaky networks
        if got:
            (CACHE / f"{slug}.{got[1]}").write_bytes(got[0])
        return name, {"file": f"{slug}.{got[1]}" if got else None, "t": now, "v": VER, **({"domain": domain} if domain else {})}

    with ThreadPoolExecutor(16) as ex:
        idx.update(dict(ex.map(one, todo)))
    idx_file.write_text(json.dumps(idx, indent=1), encoding="utf-8")

    out = SITE / "logos"
    out.mkdir(parents=True, exist_ok=True)
    mapping = {}
    for name, *_ in todo:
        f = (idx.get(name) or {}).get("file")
        if f and (CACHE / f).exists():
            if f.endswith((".png", ".ico")):
                try:
                    data, full = tidy((CACHE / f).read_bytes())
                    (out / (Path(f).stem + ".png")).write_bytes(data)
                    mapping[name] = f"logos/{Path(f).stem}.png" + ("#fb" if full else "")  # "#fb" = full bleed, read by the site
                    continue
                except Exception as e:
                    print(f"  {name}: tidy {type(e).__name__}")
            shutil.copy(CACHE / f, out / f)
            mapping[name] = f"logos/{f}"
    cats = {}  # company -> {field: open jobs}
    for j in load().values():
        if j["closed_at"] is None:
            c = cats.setdefault(j["company"], {})
            c[j["category"]] = c.get(j["category"], 0) + 1
    real = len(mapping)
    for name, *_ in todo:
        if name in mapping or name not in cats:
            continue
        c = cats[name]
        top = max((k for k in c if k != "other"), key=c.get, default="other")  # "other" only if nothing better
        try:
            f = sector_icon(top)
        except Exception as e:
            print(f"  sector icon {top}: {type(e).__name__}")
            f = None
        if f:
            shutil.copy(f, out / f.name)
            mapping[name] = f"logos/{f.name}"
    (SITE / "logos.json").write_text(json.dumps(mapping, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    print(f"logos: {real} real logos + {len(mapping) - real} sector icons of {len(todo)} companies; letter avatar for the other {len(todo) - len(mapping)}")
    if BAD:
        print("icon service refusals:", *BAD, sep="\n  ")


if __name__ == "__main__":
    main()
