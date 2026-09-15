"""Inspect the current Cboe marking-price CSV before the first production run."""

from cboe_data import download_marking_prices, normalize_spx_options

raw, trade_date, _ = download_marking_prices()
print(f"Trade date inferred: {trade_date:%Y-%m-%d}")
print("Raw columns:")
for column in raw.columns:
    print(f"  - {column}")
print(f"Raw rows: {len(raw):,}")

normalized = normalize_spx_options(raw)
print(f"Normalized SPX-family rows: {len(normalized):,}")
print(f"Roots: {sorted(normalized['Root'].unique().tolist())}")
print(f"Expirations: {normalized['Expiration_Date'].nunique()}")
print(normalized.head(10).to_string(index=False))
