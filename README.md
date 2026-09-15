# SPX VIX-style Term Structure

This repository downloads Cboe's free **3:15 p.m. CT End-of-Day Marking Prices** file and calculates a VIX-style single-term implied volatility for **every usable SPX-family expiration** found in the file.

Included roots:

- `SPX` — standard AM-settled SPX expirations
- `SPXW` — PM-settled daily/weekly/monthly/EOM/EOQ expirations
- `SPXO` — reserved for Cboe's announced AM-settled daily SPX rollout when/if it appears in the public file

The result is a research term structure, **not an official Cboe index**.

## Data sources

Option quotes:

- Cboe Proprietary Index Marking Prices
- Current 3:15 p.m. CT file: `https://cdn.cboe.com/resources/marking_prices/eod_marking_prices_late_list.csv`

Risk-free rates:

- U.S. Treasury Daily Treasury Par Yield Curve Rates (CMT)
- The program uses the latest Treasury curve on or before the option trade date and linearly interpolates by maturity.

The raw Cboe CSV is saved only to `data/raw/` for local debugging and is git-ignored. Before redistributing raw Cboe data, review the applicable Cboe data terms. Derived term-structure values are stored in `data/`.

## Calculation outline

For each `(Root, Expiration_Date)`:

1. Use bid/ask midpoint for option price.
2. Find the strike where `|Call - Put|` is smallest.
3. Infer the forward with put-call parity: `F = K + exp(rT) * (C - P)`.
4. Set `K0` to the largest strike not greater than `F`.
5. Use OTM puts below `K0`, OTM calls above `K0`, and the average call/put price at `K0`.
6. Stop each OTM wing after two consecutive zero-bid strikes.
7. Apply the VIX single-term variance formula.
8. Repeat for every usable expiration.

Clock conventions in this research implementation:

- valuation snapshot: 3:15 p.m. CT
- `SPX` / `SPXO` AM settlement approximation: 8:30 a.m. CT on expiration date
- `SPXW` PM settlement approximation: 3:00 p.m. CT on expiration date

Early-close special cases and Cboe's exact bounded-cubic-spline rate methodology are not fully replicated; therefore the output is labeled **VIX-style**.

## Repository structure

```text
src/cboe_data.py          Cboe downloader + flexible CSV parser
src/treasury_rates.py     Treasury CMT curve downloader/interpolator
src/spx_vix.py            VIX-style calculation for all expirations
src/update_latest.py      daily update, history, chart, metadata

data/vix_term_structure_history.csv
data/latest_term_structure.csv
data/latest_treasury_curve.csv       generated after first run
data/raw/                            ignored raw Cboe files

output/latest_term_structure.png
output/latest_term_structure.svg
output/latest.json

.github/workflows/update.yml         automated weekday refresh
tests/                              parser and calculation tests
website/                            Quarto page files/snippets
```

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
python -m pytest -q
python src/inspect_cboe.py
python src/update_latest.py
```

The first successful run creates/updates:

```text
data/latest_term_structure.csv
data/vix_term_structure_history.csv
data/latest_treasury_curve.csv
output/latest_term_structure.png
output/latest_term_structure.svg
output/latest.json
```

## Historical limitation

The free Cboe marking-price URL is a **latest-file endpoint**, not a free day-by-day historical archive. This repository therefore accumulates history prospectively from the first successful scheduled run.

For historical backfill you would need a licensed historical source (for example, Cboe DataShop) or historical files you are otherwise authorized to use.

## GitHub Actions timing

Cboe states that the 3:15 p.m. CT marking-price file is posted by roughly 4:45 p.m. CT. The workflow runs twice on U.S. weekdays (`22:15 UTC` and `23:15 UTC`) so that one run lands after publication in both daylight-saving and standard time. Duplicate trading dates are safely deduplicated.
