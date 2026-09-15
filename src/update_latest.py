"""Fetch the latest Cboe SPX options and publish a VIX-style term structure."""

from __future__ import annotations

import json
from pathlib import Path

import matplotlib.pyplot as plt
import pandas as pd

from cboe_data import (
    CBOE_EOD_315_URL,
    download_marking_prices,
    normalize_spx_options,
    save_raw_debug_copy,
)
from spx_vix import calculate_term_structure
from treasury_rates import get_treasury_curve

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
OUTPUT_DIR = ROOT / "output"
HISTORY_PATH = DATA_DIR / "vix_term_structure_history.csv"
LATEST_CSV_PATH = DATA_DIR / "latest_term_structure.csv"
TREASURY_CACHE_PATH = DATA_DIR / "latest_treasury_curve.csv"
PNG_PATH = OUTPUT_DIR / "latest_term_structure.png"
SVG_PATH = OUTPUT_DIR / "latest_term_structure.svg"
JSON_PATH = OUTPUT_DIR / "latest.json"


def update_history(latest: pd.DataFrame) -> pd.DataFrame:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    if HISTORY_PATH.exists():
        history = pd.read_csv(HISTORY_PATH)
    else:
        history = pd.DataFrame(columns=latest.columns)
    old_dates = history["Date"].nunique() if (not history.empty and "Date" in history.columns) else 0
    combined = pd.concat([history, latest], ignore_index=True)
    combined = combined.drop_duplicates(
        subset=["Date", "Root", "Expiration_Date"],
        keep="last",
    ).sort_values(["Date", "Days_to_Exp", "Root"])
    new_dates = combined["Date"].nunique()
    if new_dates < old_dates:
        raise RuntimeError(f"History unexpectedly shrank: {old_dates} dates -> {new_dates} dates")
    combined.to_csv(HISTORY_PATH, index=False)
    return combined


def draw_chart(latest: pd.DataFrame, as_of_date: str) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    figure, axis = plt.subplots(figsize=(12.5, 5.2))
    axis.plot(latest["Days_to_Exp"], latest["VIX"], marker="o", linewidth=1.8, markersize=4)
    axis.set_title(f"SPX VIX-style Term Structure — {as_of_date}")
    axis.set_xlabel("Days to expiration")
    axis.set_ylabel("Annualized implied volatility (%)")
    axis.grid(True, alpha=0.25)
    axis.margins(x=0.02, y=0.10)
    note = (
        "Research estimate from Cboe 3:15 p.m. CT marking-price data; "
        "includes every usable SPX-family expiration; not an official Cboe index."
    )
    figure.text(0.5, 0.01, note, ha="center", fontsize=8)
    figure.tight_layout(rect=(0, 0.04, 1, 1))
    figure.savefig(PNG_PATH, dpi=180, bbox_inches="tight")
    figure.savefig(SVG_PATH, bbox_inches="tight")
    plt.close(figure)


def write_metadata(
    latest: pd.DataFrame,
    as_of_date: str,
    treasury_rate_date: str,
    treasury_source: str,
) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    metadata = {
        "as_of_date": as_of_date,
        "generated_at_chicago": pd.Timestamp.now(tz="America/Chicago").isoformat(timespec="seconds"),
        "timezone": "America/Chicago",
        "number_of_expirations": int(len(latest)),
        "minimum_dte": float(latest["Days_to_Exp"].min()),
        "maximum_dte": float(latest["Days_to_Exp"].max()),
        "roots": sorted(latest["Root"].unique().tolist()),
        "treasury_rate_date": treasury_rate_date,
        "treasury_source": treasury_source,
        "option_data_source": CBOE_EOD_315_URL,
        "official_index": False,
        "method_note": (
            "VIX-style single-term variance by expiration using Cboe end-of-day quotes, "
            "two-consecutive-zero-bid truncation, and interpolated U.S. Treasury CMT rates."
        ),
    }
    JSON_PATH.write_text(json.dumps(metadata, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    raw, trade_date, raw_bytes = download_marking_prices()
    raw_path = save_raw_debug_copy(raw_bytes, trade_date, RAW_DIR)
    options = normalize_spx_options(raw)
    if options.empty:
        raise RuntimeError("Cboe CSV contained no usable SPX/SPXW/SPXO option rows")

    curve = get_treasury_curve(trade_date, cache_path=TREASURY_CACHE_PATH)
    latest = calculate_term_structure(options, trade_date, curve)
    if latest.empty:
        raise RuntimeError("No SPX-family expirations produced a valid VIX-style variance")

    as_of_text = trade_date.strftime("%Y-%m-%d")
    latest.to_csv(LATEST_CSV_PATH, index=False)
    update_history(latest)
    draw_chart(latest, as_of_text)
    write_metadata(
        latest,
        as_of_text,
        curve.rate_date.strftime("%Y-%m-%d"),
        curve.source,
    )

    print(latest.to_string(index=False))
    print(f"Raw debug copy (git-ignored): {raw_path}")
    print(f"Updated {len(latest)} expirations for {as_of_text}")


if __name__ == "__main__":
    main()
