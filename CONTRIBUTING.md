# Contributing

Thanks for helping. Good first contributions:

- **Add or fix an employer**: edit `companies.json` (name, ATS type, board slug). Check that the board URL loads and returns jobs.
- **Fix a scraper or the site**: see `scraper.py`, `build_site.py` and `web/`.
- **Report bad data**: wrong level, field, pay or a dead apply link, via an issue.

## Setup

```bash
pip install -r requirements.txt   # if present; the scraper mostly uses the standard library
python -m unittest test_scraper
```

## Pull requests

1. Open an issue first for anything bigger than a small fix.
2. Keep each PR to one change and run the tests above.
3. Don't commit generated output (`_site/`, `logo-cache/`, `kaggle/`). Data commits (`data: ...`) come from the scheduled workflow.

By contributing you agree your work is released under the [AGPL-3.0 License](LICENSE).
