"""U.S. Treasury CMT curve loader and maturity interpolation."""

from __future__ import annotations

import io
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
import requests

USER_AGENT = "Mozilla/5.0 (compatible; spx-vix-term-structure/1.0)"
TREASURY_URL = (
    "https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
    "TextView?type=daily_treasury_yield_curve&field_tdr_date_value={year}"
)

TENOR_DAYS = {
    "1 Mo": 365.25 / 12.0,
    "1.5 Mo": 365.25 * 1.5 / 12.0,
    "2 Mo": 365.25 * 2.0 / 12.0,
    "3 Mo": 365.25 * 3.0 / 12.0,
    "4 Mo": 365.25 * 4.0 / 12.0,
    "6 Mo": 365.25 * 6.0 / 12.0,
    "1 Yr": 365.25,
    "2 Yr": 365.25 * 2,
    "3 Yr": 365.25 * 3,
    "5 Yr": 365.25 * 5,
    "7 Yr": 365.25 * 7,
    "10 Yr": 365.25 * 10,
    "20 Yr": 365.25 * 20,
    "30 Yr": 365.25 * 30,
}


@dataclass(frozen=True)
class TreasuryCurve:
    rate_date: pd.Timestamp
    tenors_days: np.ndarray
    yields_decimal: np.ndarray
    source: str

    def rate_for_days(self, days: float) -> float:
        """Linearly interpolate CMT yields by calendar days and cap at endpoints."""
        return float(
            np.interp(
                float(days),
                self.tenors_days,
                self.yields_decimal,
                left=self.yields_decimal[0],
                right=self.yields_decimal[-1],
            )
        )


def _flatten_columns(frame: pd.DataFrame) -> pd.DataFrame:
    if isinstance(frame.columns, pd.MultiIndex):
        frame = frame.copy()
        frame.columns = [
            " ".join(str(part) for part in column if str(part) != "nan").strip()
            for column in frame.columns
        ]
    else:
        frame = frame.copy()
        frame.columns = [str(column).strip() for column in frame.columns]
    return frame


def _extract_curve_table(html: str) -> pd.DataFrame:
    tables = pd.read_html(io.StringIO(html))
    for table in tables:
        table = _flatten_columns(table)
        columns = set(table.columns)
        if "Date" in columns and any(name in columns for name in TENOR_DAYS):
            return table
    raise ValueError("Could not find the Daily Treasury Par Yield Curve table")


def _curve_from_table(table: pd.DataFrame, trade_date: pd.Timestamp) -> TreasuryCurve:
    working = table.copy()
    working["Date"] = pd.to_datetime(working["Date"], errors="coerce")
    working = working.dropna(subset=["Date"]).sort_values("Date")
    eligible = working.loc[working["Date"] <= trade_date.normalize()]
    if eligible.empty:
        raise ValueError(f"No Treasury curve available on or before {trade_date:%Y-%m-%d}")
    row = eligible.iloc[-1]
    rate_date = pd.Timestamp(row["Date"]).normalize()

    days: list[float] = []
    yields: list[float] = []
    for column, tenor_days in TENOR_DAYS.items():
        if column not in working.columns:
            continue
        value = pd.to_numeric(pd.Series([row[column]]), errors="coerce").iloc[0]
        if pd.isna(value):
            continue
        days.append(float(tenor_days))
        yields.append(float(value) / 100.0)
    if len(days) < 2:
        raise ValueError("Treasury curve contains fewer than two usable CMT tenors")
    order = np.argsort(days)
    return TreasuryCurve(
        rate_date=rate_date,
        tenors_days=np.asarray(days, dtype=float)[order],
        yields_decimal=np.asarray(yields, dtype=float)[order],
        source="U.S. Treasury CMT",
    )


def save_curve_cache(curve: TreasuryCurve, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(
        {
            "Date": curve.rate_date.strftime("%Y-%m-%d"),
            "Tenor_Days": curve.tenors_days,
            "Yield_Decimal": curve.yields_decimal,
        }
    ).to_csv(path, index=False)


def load_curve_cache(path: Path) -> TreasuryCurve:
    frame = pd.read_csv(path)
    if frame.empty:
        raise ValueError("Treasury curve cache is empty")
    return TreasuryCurve(
        rate_date=pd.to_datetime(frame["Date"].iloc[0]).normalize(),
        tenors_days=frame["Tenor_Days"].to_numpy(dtype=float),
        yields_decimal=frame["Yield_Decimal"].to_numpy(dtype=float),
        source="cached U.S. Treasury CMT",
    )


def get_treasury_curve(trade_date: pd.Timestamp, cache_path: Path | None = None) -> TreasuryCurve:
    """Fetch the official Treasury CMT table; fall back to the last cached curve."""
    url = TREASURY_URL.format(year=trade_date.year)
    try:
        response = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=45)
        response.raise_for_status()
        curve = _curve_from_table(_extract_curve_table(response.text), trade_date)
        if cache_path is not None:
            save_curve_cache(curve, cache_path)
        return curve
    except Exception:
        if cache_path is not None and cache_path.exists():
            return load_curve_cache(cache_path)
        raise
