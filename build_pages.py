"""Static, crawlable pages for the website: one per field, per company, plus entry-level / remote / internship pages,
hubs, sitemap.xml and robots.txt. The app itself is client-rendered (the jobs load from jobs.json), which search engines see
as an almost empty page; these pages put real, useful content in plain HTML. Run after build_site.py and build_logos.py.

Each page has a unique title and description, real numbers computed from the data, links to the original postings (nofollow),
and links to its siblings. No JobPosting markup: we don't host the postings, so we don't claim to."""
import datetime as dt
import shutil
import html
import json
import re
import urllib.parse
from collections import Counter
from pathlib import Path

from scraper import company_stats, fresher_ok, ghost_flags, load

BASE = "https://chandranshb.github.io/roleBase"
SITE = Path("_site")
TODAY = dt.date.today().isoformat()
FIELDS = dict(
    engineering="Engineering and software", data="Data and analytics", sales="Sales", marketing="Marketing and communications",
    product="Product management", design="Design", support="Customer support", finance="Finance, banking and insurance",
    legal="Legal and compliance", people="HR and recruiting", operations="Operations and supply chain", security="Cybersecurity",
    healthcare="Healthcare", education="Education and teaching", science="Science and research", retail="Retail, food and hospitality",
    manufacturing="Manufacturing and skilled trades", logistics="Logistics, warehouse and driving",
    protective="Security guards and protective services", administrative="Administrative and office",
    consulting="Consulting and strategy", social="Social services and community")
esc = lambda s: html.escape(str(s), quote=True)
slug = lambda s: re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-") or "company"

CSS = """:root{color-scheme:light dark;--bg:#f4f5f7;--card:#fff;--ink:#14171c;--sub:#555c68;--line:#e2e5ea;--acc:#2f5bea}
@media(prefers-color-scheme:dark){:root{--bg:#0d0f12;--card:#16191e;--ink:#eceef2;--sub:#a0a7b3;--line:#262a31;--acc:#7c9cff}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:860px;margin:0 auto;padding:24px 16px 64px}nav.bc{font-size:14px;color:var(--sub);margin-bottom:12px}a{color:var(--acc)}
h1{font-size:28px;line-height:1.2;margin:.2em 0 .4em}h2{font-size:18px;margin:1.6em 0 .5em}p.lead{color:var(--sub);max-width:70ch}
ul.jobs{list-style:none;margin:0;padding:0;display:grid;gap:8px}ul.jobs li{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 14px}
ul.jobs b{display:block}ul.jobs small{color:var(--sub)}ul.cols{columns:2 240px;list-style:none;padding:0;margin:0}ul.cols li{margin:0 0 6px}
.cta{display:inline-block;margin:10px 0 0;padding:10px 16px;border-radius:999px;background:var(--acc);color:#fff;text-decoration:none;font-weight:600}
img.lg{width:40px;height:40px;object-fit:contain;border-radius:8px;background:#fff;vertical-align:middle;margin-right:10px}footer{margin-top:40px;color:var(--sub);font-size:13px}"""


def page(path, title, desc, body, crumbs):
    """Write _site/<path>/index.html. crumbs: [(name, url)] ending with this page."""
    ld = {"@context": "https://schema.org", "@type": "BreadcrumbList",
          "itemListElement": [{"@type": "ListItem", "position": i + 1, "name": n, "item": u} for i, (n, u) in enumerate(crumbs)]}
    url = crumbs[-1][1]
    nav = " › ".join(f'<a href="{esc(u)}">{esc(n)}</a>' for n, u in crumbs[:-1] + [(crumbs[-1][0], url)])
    doc = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><meta name="description" content="{esc(desc)}"><link rel="canonical" href="{esc(url)}">
<meta property="og:type" content="website"><meta property="og:title" content="{esc(title)}"><meta property="og:description" content="{esc(desc)}">
<meta property="og:url" content="{esc(url)}"><meta property="og:site_name" content="RoleBase"><meta property="og:image" content="{BASE}/og.png"><meta name="twitter:card" content="summary_large_image"><meta name="twitter:image" content="{BASE}/og.png">
<link rel="icon" href="{BASE}/favicon.svg" type="image/svg+xml"><style>{CSS}</style>
<script type="application/ld+json">{json.dumps(ld, ensure_ascii=False)}</script></head>
<body><main><nav class="bc" aria-label="Breadcrumb">{nav}</nav>{body}
<footer><p>Listings belong to their employers and link to the original posting. Level, field, skills and pay are read from the posting text automatically and can be wrong.
Updated {TODAY}. <a href="{BASE}/">RoleBase</a> is open source: <a href="https://github.com/chandranshB/roleBase">code and data</a>.</p></footer></main></body></html>
"""
    d = SITE / path
    d.mkdir(parents=True, exist_ok=True)
    (d / "index.html").write_text(doc, encoding="utf-8")


def top(counter, n, fmt=lambda k: k):
    return [fmt(k) for k, _ in counter.most_common(n)]


def join(xs):
    xs = list(xs)
    return ", ".join(xs[:-1]) + " and " + xs[-1] if len(xs) > 1 else (xs[0] if xs else "")


def listing(jobs, cmap, per_company=3, limit=60):
    """Newest first, at most `per_company` per employer, so one big employer doesn't fill the page."""
    out, seen = [], Counter()
    for j in sorted(jobs, key=lambda j: j["posted"] or j["first_seen"], reverse=True):
        if seen[j["company"]] >= per_company:
            continue
        seen[j["company"]] += 1
        out.append(j)
        if len(out) >= limit:
            break
    items = []
    for j in out:
        co = esc(j["company"])
        co = f'<a href="{BASE}/companies/{cmap[j["company"]]}/">{co}</a>' if j["company"] in cmap else co
        bits = [b for b in (j["location"], j["level"].capitalize() + " level", j["posted"] or j["first_seen"]) if b]
        items.append(f'<li><b>{esc(j["title"])}</b>{co}<br><small>{esc(" · ".join(bits))} · '
                     f'<a href="{esc(j["url"])}" rel="nofollow noopener">View the original posting</a></small></li>')
    return '<ul class="jobs">' + "".join(items) + "</ul>"


def stats_text(jobs, label, company=None):
    n = len(jobs)
    fresh = sum(map(fresher_ok, jobs))
    remote = sum(bool(j["remote"]) for j in jobs)
    paid = sum(bool(j["pay"]) for j in jobs)
    emp = Counter(j["company"] for j in jobs)
    loc = Counter(j["location"].split(",")[0].strip() for j in jobs if j["location"] and not re.search(r"remote|locations?|^[0-9]", j["location"].lower()))
    sk = Counter(s for j in jobs for s in j["skills"])
    who = f"at {company}" if company else f"from {len(emp):,} employers"
    t = (f"{n:,} open {label} listings {who}, as of {TODAY}. {100 * fresh // n}% are open to people with up to two years of experience, "
         f"{100 * remote // n}% can be done remotely and {100 * paid // n}% state a salary.")
    if emp and not company:
        t += f" The employers with the most openings are {join(top(emp, 3))}."
    if len(loc) > 2:
        t += f" The most common locations are {join(top(loc, 3))}."
    if sk:
        t += f" Skills mentioned most often: {join(top(sk, 4, lambda k: k.replace('_', ' ')))}."
    short = f"{n:,} open {label} listings {who}; {100 * fresh // n}% suit beginners, {100 * remote // n}% are remote."
    if emp and not company and len(short) + len(join(top(emp, 2))) < 140:
        short += f" Hiring most: {join(top(emp, 2))}."
    return t, emp, short


def main():
    jobs = [j for j in load().values() if j["closed_at"] is None]
    flags = ghost_flags(jobs)
    jobs = [j for j in jobs if sum(p for _, p in flags.get(j["id"], [])) < 2 and j["url"]]  # no likely ghost jobs on pages we want indexed
    cs = company_stats(jobs, min_roles=5)
    cmap, used = {}, set()
    for c in cs:
        s = slug(c["company"])
        while s in used:
            s += "-2"
        used.add(s)
        cmap[c["company"]] = s
    logos = {}
    if (SITE / "logos.json").exists():
        logos = json.loads((SITE / "logos.json").read_text(encoding="utf-8"))
    urls = [f"{BASE}/", f"{BASE}/fields/", f"{BASE}/companies/"]
    home = ("RoleBase", f"{BASE}/")
    by_cat = {}
    for j in jobs:
        by_cat.setdefault(j["category"], []).append(j)

    # collections people actually search for, then every field
    special = [
        ("entry-level-jobs", "Entry-level jobs", "entry-level", [j for j in jobs if fresher_ok(j)],
         "Roles open to people with up to two years of experience: stated as two years or less, or intern and junior roles that state nothing. Junior roles that quietly ask for three or more years are left out."),
        ("remote-jobs", "Remote jobs", "remote", [j for j in jobs if j["remote"]], "Roles the employer lists as remote or work-from-anywhere."),
        ("internships", "Internships", "internship", [j for j in jobs if j["level"] == "intern" or j["employment"] == "internship"],
         "Internships, co-ops and student placements."),
    ]
    pages = [(s, t, lab, js, intro, None) for s, t, lab, js, intro in special]
    pages += [(f"fields/{c}", f"{name} jobs", name.lower(), by_cat[c], "", c) for c, name in FIELDS.items() if len(by_cat.get(c, [])) >= 20]
    for path, h1, label, js, intro, cat in pages:
        if not js:
            continue
        text, emp, short = stats_text(js, label)
        url = f"{BASE}/{path}/"
        crumbs = [home] + ([("Jobs by field", f"{BASE}/fields/")] if cat else []) + [(h1, url)]
        employers = "".join(f'<li><a href="{BASE}/companies/{cmap[c]}/">{esc(c)}</a> <small>({n:,})</small></li>' for c, n in emp.most_common(12) if c in cmap)
        others = "".join(f'<li><a href="{BASE}/{p2}/">{esc(h2)}</a></li>' for p2, h2, *_ in pages if p2 != path)
        q = {"entry-level-jobs": "fresher", "remote-jobs": "remote", "internships": "internship"}.get(path, label.split(" and ")[0].split(",")[0])
        body = (f"<h1>{esc(h1)}: {len(js):,} open roles</h1><p class='lead'>{esc(text)} {esc(intro)}</p>"
                f'<a class="cta" href="{BASE}/#q={esc(q)}&amp;all=1">Search and filter these in the app</a>'
                f"<h2>Latest {esc(label)} listings</h2>{listing(js, cmap)}"
                + (f"<h2>Employers hiring most</h2><ul class='cols'>{employers}</ul>" if employers else "")
                + f"<h2>Other ways to browse</h2><ul class='cols'>{others}</ul>")
        page(path, f"{h1}: {len(js):,} open roles | RoleBase", short, body, crumbs)
        urls.append(url)

    # hubs
    items = "".join(f'<li><a href="{BASE}/fields/{c}/">{esc(n)} jobs</a> <small>({len(by_cat[c]):,})</small></li>' for c, n in FIELDS.items() if len(by_cat.get(c, [])) >= 20)
    page("fields", "Jobs by field: healthcare, retail, finance, engineering and more | RoleBase",
         "Browse open jobs by field: healthcare, retail and hospitality, finance, education, manufacturing, logistics, engineering and more.",
         f"<h1>Jobs by field</h1><p class='lead'>Open jobs from employers' own career pages, grouped by what the work is, not only by technology. Pick a field to see the latest listings and who is hiring.</p><ul class='cols'>{items}</ul>"
         f"<h2>Also popular</h2><ul class='cols'><li><a href='{BASE}/entry-level-jobs/'>Entry-level jobs</a></li><li><a href='{BASE}/remote-jobs/'>Remote jobs</a></li><li><a href='{BASE}/internships/'>Internships</a></li></ul>",
         [home, ("Jobs by field", f"{BASE}/fields/")])
    ci = "".join(f'<li><a href="{BASE}/companies/{cmap[c["company"]]}/">{esc(c["company"])}</a> <small>({c["open_roles"]:,})</small></li>' for c in sorted(cs, key=lambda c: c["company"].lower()))
    page("companies", f"Companies hiring now: {len(cs):,} employers with open jobs | RoleBase",
         f"{len(cs):,} employers with five or more open jobs, from hospitals and banks to retailers and tech. See who is hiring and how many roles suit beginners.",
         f"<h1>Companies hiring now</h1><p class='lead'>{len(cs):,} employers with five or more open roles. Each page shows the latest listings and how many are open to people with up to two years of experience.</p><ul class='cols'>{ci}</ul>",
         [home, ("Companies", f"{BASE}/companies/")])

    # one page per company
    for c in cs:
        name, s = c["company"], cmap[c["company"]]
        js = [j for j in jobs if j["company"] == name]
        text, _, short = stats_text(js, "job", name)
        cats = Counter(j["category"] for j in js)
        byf = join(('<a href="%s/fields/%s/">%s</a> (%d)' % (BASE, c, esc(FIELDS[c].lower()), v)) if c in FIELDS else "other (%d)" % v for c, v in cats.most_common(4))
        logo = f'<img class="lg" src="{BASE}/{logos[name].split("#")[0]}" alt="{esc(name)} logo" width="40" height="40">' if name in logos else ""
        body = (f"<h1>{logo}Jobs at {esc(name)}</h1><p class='lead'>{esc(text)} By field: {byf}.</p>"
                f'<a class="cta" href="{BASE}/#q=%22{esc(urllib.parse.quote(name))}%22&amp;all=1">Search {esc(name)} jobs in the app</a>'
                f"<h2>Latest openings</h2>{listing(js, {}, per_company=40, limit=40)}")
        url = f"{BASE}/companies/{s}/"
        page(f"companies/{s}", f"Jobs at {name}: {len(js):,} open roles | RoleBase", short, body,
             [home, ("Companies", f"{BASE}/companies/"), (name, url)])
        urls.append(url)

    sm = "".join(f"<url><loc>{esc(u)}</loc><lastmod>{TODAY}</lastmod></url>" for u in urls)
    (SITE / "sitemap.xml").write_text(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{sm}</urlset>', encoding="utf-8")
    (SITE / "robots.txt").write_text(f"User-agent: *\nAllow: /\nSitemap: {BASE}/sitemap.xml\n", encoding="utf-8")
    if Path("kaggle_assets/dataset-cover-image.png").exists():
        shutil.copy("kaggle_assets/dataset-cover-image.png", SITE / "og.png")  # social preview image
    print(f"pages: {len(urls)} URLs in sitemap ({len(pages)} collections, {len(cs)} companies)")


if __name__ == "__main__":
    main()
