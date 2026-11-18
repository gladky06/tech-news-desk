# Tech News Desk

A self-updating news desk for the companies I cover. No AI calls and no paid services.

How it works:

1. Every 30 minutes a GitHub Actions job runs `scripts/fetch.py`.
2. The script pulls Google News RSS, the company blog, SEC EDGAR filings and daily prices.
3. Each story is scored by how much it can move the model assumptions (see `drivers` in `config.json`). Duplicates are merged and the number of outlets is counted.
4. Results are saved to `docs/data/` and the site in `docs/` is served by GitHub Pages.

To add a company, copy the MSFT block in `config.json` and change the ticker, name, aliases, queries and CIK.
