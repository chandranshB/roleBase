"""Crawler registry and adapter dispatch for 50+ applicant tracking systems (ATS)."""
from typing import Callable, Dict

import scraper


# Dictionary mapping ATS platform slug -> crawler function(board_slug, company_display_name)
ATS: Dict[str, Callable] = scraper.ATS

# Dictionary mapping Feed slug -> crawler function()
FEEDS: Dict[str, Callable] = scraper.FEEDS

# Windowed / capped platforms where disappeared jobs cannot be assumed closed
CAPPED = scraper.CAPPED

# Per-provider concurrency limits and lane mapping
LANES = scraper.LANES
LANE_OF = scraper.LANE_OF


def get_crawler(platform: str) -> Callable:
    """Retrieve the scraper function for a given ATS platform or feed."""
    if platform in ATS:
        return ATS[platform]
    if platform in FEEDS:
        return FEEDS[platform]
    raise KeyError(f"No crawler registered for platform: {platform}")
