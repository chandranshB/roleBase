"""Render the README charts as plain SVG files: no plotting library, no external service.
The scrape workflow runs this after every scrape and commits docs/charts/ (SVGs follow the viewer's light/dark setting)."""
import csv
import datetime as dt
import html
from collections import Counter
from pathlib import Path

from scraper import company_stats, load

OUT = Path("docs/charts")
W = 720
STYLE = """<style>
text{font-family:-apple-system,BlinkMacSystemFont,"SF Pro Text","Helvetica Neue","Segoe UI",Roboto,sans-serif;fill:#000}
.sub,.val,.axis{fill:#6c6c70}.bar{fill:#007aff}.track{fill:#e5e5ea}.grid{stroke:#d1d1d6}
.l1{fill:none;stroke:#007aff;stroke-width:2.5}.d1{fill:#007aff}.l2{fill:none;stroke:#ff9500;stroke-width:2.5}.d2{fill:#ff9500}
.l3{fill:none;stroke:#34c759;stroke-width:2.5}.d3{fill:#34c759}.l4{fill:none;stroke:#af52de;stroke-width:2.5}.d4{fill:#af52de}
.l5{fill:none;stroke:#ff3b30;stroke-width:2.5}.d5{fill:#ff3b30}.l6{fill:none;stroke:#00b8d4;stroke-width:2.5}.d6{fill:#00b8d4}
@media(prefers-color-scheme:dark){text{fill:#fff}.sub,.val,.axis{fill:#98989d}.bar{fill:#0a84ff}.track{fill:#2c2c2e}.grid{stroke:#38383a}
.l1{stroke:#0a84ff}.d1{fill:#0a84ff}.l2{stroke:#ff9f0a}.d2{fill:#ff9f0a}
.l3{stroke:#30d158}.d3{fill:#30d158}.l4{stroke:#bf5af2}.d4{fill:#bf5af2}.l5{stroke:#ff453a}.d5{fill:#ff453a}.l6{stroke:#40cbe0}.d6{fill:#40cbe0}}
</style>"""
SK = {"ml": "ML", "aws": "AWS", "gcp": "GCP", "sql": "SQL", "nosql": "NoSQL", "llm": "LLM", "dbt": "dbt", "php": "PHP", "sap": "SAP",
      "ci/cd": "CI/CD", "graphql": "GraphQL", "javascript": "JavaScript", "typescript": "TypeScript", "mongodb": "MongoDB",
      "mysql": "MySQL", "pytorch": "PyTorch", "tensorflow": "TensorFlow", "node.js": "Node.js", "spring-boot": "Spring Boot",
      "hubspot": "HubSpot", "excel": "Excel", "ms_office": "MS Office", "cpr": "CPR", "bls": "BLS", "acls": "ACLS", "ehr": "EHR", "emr": "EMR",
      "hipaa": "HIPAA", "osha": "OSHA", "cdl": "CDL", "haccp": "HACCP", "hris": "HRIS", "pmp": "PMP", "crm": "CRM", "erp": "ERP", "gaap": "GAAP",
      "ifrs": "IFRS", "seo": "SEO", "autocad": "AutoCAD", "solidworks": "SolidWorks", "quickbooks": "QuickBooks", "powerpoint": "PowerPoint"}
SK = {**SK, **{k: k.replace("_", " ").capitalize() for k in ("customer_service", "project_management", "six_sigma", "financial_modeling",
      "accounts_payable", "accounts_receivable", "data_entry", "supply_chain", "social_media", "google_analytics", "adobe_creative_suite", "patient_care",
      "medical_coding", "preventive_maintenance", "food_safety", "cash_handling", "inventory_management", "lesson_planning", "talent_acquisition",
      "employee_relations", "public_speaking", "time_management")}}
TECH = set("python java javascript typescript rust c++ c# ruby php swift kotlin scala sql nosql react vue angular node.js django flask spring-boot aws azure gcp "
           "kubernetes docker terraform postgres mysql mongodb redis kafka spark snowflake databricks dbt airflow pytorch tensorflow llm ml graphql linux git ci/cd "
           "figma tableau salesforce hubspot jira sap go".split())
esc = lambda s: html.escape(str(s), quote=True)


def svg(h, body, title, sub, w=W):
    return (f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" role="img" aria-label="{esc(title)}">'
            f'{STYLE}<text x="24" y="34" font-size="18" font-weight="600">{esc(title)}</text>'
            f'<text class="sub" x="24" y="56" font-size="12">{esc(sub)}</text>{body}</svg>\n')


def bars(title, sub, rows, fmt=lambda v: f"{v:,}"):
    """rows: [(label, value)] shown top to bottom."""
    top = max((v for _, v in rows), default=0) or 1
    x0, x1 = 214, W - 90
    body = ""
    for i, (label, v) in enumerate(rows):
        y = 78 + i * 28
        label = label if len(label) <= 28 else label[:27] + "…"
        body += (f'<text x="{x0 - 10}" y="{y + 14}" font-size="13" text-anchor="end">{esc(label)}</text>'
                 f'<rect class="track" x="{x0}" y="{y}" width="{x1 - x0}" height="18" rx="4"/>'
                 f'<rect class="bar" x="{x0}" y="{y}" width="{max(2, (x1 - x0) * v / top):.1f}" height="18" rx="4"/>'
                 f'<text class="val" x="{x1 + 8}" y="{y + 14}" font-size="12">{esc(fmt(v))}</text>')
    return svg(78 + len(rows) * 28 + 16, body, title, sub)


def lines(title, sub, series, w=W, unit=""):
    """series: [(name, css index, [(date, value)])]; x positions follow the dates, so gaps in history show as gaps."""
    days = sorted({d for _, _, p in series for d, _ in p})
    top = max((v for _, _, p in series for _, v in p), default=0) or 1
    x0, x1, y0, y1 = 64, w - 30, (118 if len(series) > 2 else 96), 250
    days_n = [dt.date.fromisoformat(d).toordinal() for d in days]
    span = (days_n[-1] - days_n[0]) if days else 0
    gx = lambda d: x0 + (x1 - x0) * ((dt.date.fromisoformat(d).toordinal() - days_n[0]) / span if span else 0.5)
    gy = lambda v: y1 - (y1 - y0) * v / top
    body = ""
    for k in range(5):
        v = top * k / 4
        body += (f'<line class="grid" x1="{x0}" x2="{x1}" y1="{gy(v):.1f}" y2="{gy(v):.1f}"/>'
                 f'<text class="axis" x="{x0 - 8}" y="{gy(v) + 4:.1f}" font-size="11" text-anchor="end">{v:,.0f}{unit}</text>')
    for d in (days[:1] + days[-1:] if len(days) > 1 else days):
        body += f'<text class="axis" x="{gx(d):.1f}" y="{y1 + 20}" font-size="11" text-anchor="middle">{d}</text>'
    for i, (name, c, pts) in enumerate(series):
        xy = [(gx(d), gy(v)) for d, v in pts]
        if len(xy) > 1:
            body += f'<polyline class="l{c}" points="{" ".join(f"{x:.1f},{y:.1f}" for x, y in xy)}"/>'
        body += "".join(f'<circle class="d{c}" cx="{x:.1f}" cy="{y:.1f}" r="3.5"/>' for x, y in xy)
        lx, ly = (24 + i * 130, 82) if len(series) > 2 else (W - 230 + i * 120, 46)  # many series: legend on its own row under the subtitle
        body += (f'<circle class="d{c}" cx="{lx + 5}" cy="{ly}" r="5"/>'
                 f'<text x="{lx + 16}" y="{ly + 4}" font-size="12">{esc(name)}</text>')
    if len(days) < 2:
        body += f'<text class="sub" x="{w / 2}" y="{y1 + 48}" font-size="12" text-anchor="middle">History builds up daily; the line fills in as the scraper keeps running.</text>'
    return svg(300, body, title, sub, w)


def pulse():
    p, open_, fresher = Path("data/pulse.csv"), Counter(), Counter()
    if p.exists():
        for r in csv.DictReader(open(p, encoding="utf-8")):
            open_[r["date"]] += int(r["open"])
            fresher[r["date"]] += int(r["fresher"])
    return sorted(open_.items()), sorted(fresher.items())


def skill_trends():
    """Share of skill-tagged open postings that mention each skill, per day (data/skills.csv); the 6 skills that lead today."""
    p, share = Path("data/skills.csv"), {}
    if p.exists():
        for r in csv.DictReader(open(p, encoding="utf-8")):
            if int(r["tagged"]):
                share.setdefault(r["skill"], []).append((r["date"], 100 * int(r["jobs"]) / int(r["tagged"])))
    lead = sorted(share, key=lambda s: -share[s][-1][1])
    top = [s for s in lead if s not in TECH][:3] + [s for s in lead if s in TECH][:3]  # three non-IT skills and three IT ones, so no field drowns the chart
    return [(SK.get(s, s.capitalize()), i + 1, share[s]) for i, s in enumerate(top)]


def main():
    jobs = [j for j in load().values() if j["closed_at"] is None]
    stamp = f"{len(jobs):,} open jobs · updated {dt.date.today()}"
    levels = Counter(j["level"] for j in jobs)
    skills = Counter(s for j in jobs for s in j["skills"])
    fresh = company_stats(jobs, min_roles=20)
    open_pts, fresher_pts = pulse()
    charts = {
        "jobs_by_level": bars("Open jobs by level", stamp, [(l, levels[l]) for l in ("intern", "junior", "mid", "senior", "lead", "staff+", "manager+")]),
        "jobs_by_field": bars("Open jobs by field", stamp, Counter(j["category"] for j in jobs).most_common()),
        "top_skills": bars("Most requested skills", stamp, [(SK.get(s, s.capitalize()), n) for s, n in skills.most_common(15)]),
        "top_companies": bars("Companies with the most open jobs", stamp, Counter(j["company"] for j in jobs).most_common(12)),
        "fresher_friendly": bars("Most fresher-friendly companies (roles open to 0–2 years)", f"companies with 20+ open jobs · {stamp}",
                                 [(f"{c['company']} ({c['fresher_roles']}/{c['open_roles']})", c["fresher_share"] * 100)
                                  for c in fresh[:12]], fmt=lambda v: f"{v:.0f}%"),
        "skill_trends": lines("Skill demand trend", "share of skill-tagged open postings that mention each skill: the 3 leading non-IT and 3 leading IT skills · " + stamp, skill_trends(), w=1200, unit="%"),
        "open_jobs_over_time": lines("Open jobs over time", stamp, [("Open jobs", 1, open_pts), ("Fresher-friendly", 2, fresher_pts)]),
    }
    OUT.mkdir(parents=True, exist_ok=True)
    for name, s in charts.items():
        f = OUT / f"{name}.svg"
        if not f.exists() or f.read_text(encoding="utf-8") != s:  # leave unchanged files alone so git sees no diff
            with open(f, "w", encoding="utf-8", newline="\n") as fh:
                fh.write(s)
    print(f"{len(charts)} charts -> {OUT}")


if __name__ == "__main__":
    main()
