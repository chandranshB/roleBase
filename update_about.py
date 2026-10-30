"""Keep the repo's About text on GitHub in step with the data: rewrites the open-job figure in the description.

GITHUB_TOKEN cannot edit repository settings, so scrape.yml runs this only when the ABOUT_TOKEN secret is set
(a fine-grained token with Administration: read and write on this repository).
"""
import subprocess

from scraper import load


def about_text(n):
    if n >= 1_000_000:
        count = f"{n / 1_000_000:.2f}M"
    else:
        count = f"{round(n, -4) // 1000}k"
    return (f"Free, open job search and job-market dataset: ~{count} open jobs from employers' own career pages across "
            "healthcare, retail, finance, education, tech and more. Entry-level, remote and ghost-job filters.")


if __name__ == "__main__":
    n = sum(j["closed_at"] is None for j in load().values())
    subprocess.run(["gh", "repo", "edit", "--description", about_text(n)], check=True)  # repo from GH_REPO or the git remote
    print(f"About text set: {n} open jobs")
