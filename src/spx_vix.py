"""VIX-style variance calculation for every usable SPX-family expiration."""

from __future__ import annotations

import math
from datetime import time
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from treasury_rates import TreasuryCurve

CHICAGO = ZoneInfo("America/Chicago")


def _delta_k(strikes: np.ndarray) -> np.ndarray:
    if len(strikes) < 3:
        raise ValueError("At least three strikes are required")
    out = np.zeros(len(strikes), dtype=float)
    out[0] = strikes[1] - strikes[0]
    out[-1] = strikes[-1] - strikes[-2]
    out[1:-1] = (strikes[2:] - strikes[:-2]) / 2.0
    return out


def _settlement_label(root: str) -> str:
    return "AM" if root in {"SPX", "SPXO"} else "PM"


def _expiration_timestamp(root: str, expiration_date: pd.Timestamp) -> pd.Timestamp:
    """Approximate VIX clock convention in America/Chicago.

    Standard AM-settled SPX/SPXO uses 8:30 CT; PM-settled SPXW uses 15:00 CT.
    Early-close special cases are not separately modeled, so results are labeled
    VIX-style rather than official Cboe index values.
    """
    clock = time(8, 30) if root in {"SPX", "SPXO"} else time(15, 0)
    dt = pd.Timestamp.combine(expiration_date.date(), clock)
    return pd.Timestamp(dt, tz=CHICAGO)


def _valuation_timestamp(trade_date: pd.Timestamp) -> pd.Timestamp:
    # The repo uses Cboe's 3:15 p.m. CT end-of-day marking-price file.
    dt = pd.Timestamp.combine(trade_date.date(), time(15, 15))
    return pd.Timestamp(dt, tz=CHICAGO)


def _truncate_zero_bids(
    strikes: list[float],
    mids: pd.Series,
    bids: pd.Series,
) -> dict[float, float]:
    """Apply the VIX two-consecutive-zero-bid stopping rule."""
    selected: dict[float, float] = {}
    consecutive_zeros = 0
    for strike in strikes:
        bid = bids.get(strike, np.nan)
        mid = mids.get(strike, np.nan)
        if pd.isna(bid) or float(bid) <= 0:
            consecutive_zeros += 1
            if consecutive_zeros >= 2:
                break
            continue
        consecutive_zeros = 0
        if pd.notna(mid) and float(mid) >= 0:
            selected[float(strike)] = float(mid)
    return selected


def calculate_single_term(
    group: pd.DataFrame,
    trade_date: pd.Timestamp,
    curve: TreasuryCurve,
) -> dict[str, object] | None:
    root = str(group["Root"].iloc[0])
    expiration_date = pd.Timestamp(group["Expiration_Date"].iloc[0]).normalize()
    valuation_ts = _valuation_timestamp(trade_date)
    expiration_ts = _expiration_timestamp(root, expiration_date)
    seconds = (expiration_ts - valuation_ts).total_seconds()
    if seconds <= 0:
        return None
    t = seconds / (365.0 * 24.0 * 3600.0)
    dte = seconds / (24.0 * 3600.0)
    r = curve.rate_for_days(dte)

    mid = group.pivot_table(
        index="Strike_Price",
        columns="Option_Type",
        values="Mid_Price",
        aggfunc="median",
    ).sort_index()
    bid = group.pivot_table(
        index="Strike_Price",
        columns="Option_Type",
        values="Bid",
        aggfunc="median",
    ).sort_index()
    if not {"Call", "Put"}.issubset(mid.columns):
        return None

    paired = mid.dropna(subset=["Call", "Put"])
    if paired.empty:
        return None
    diff = (paired["Call"] - paired["Put"]).abs()
    forward_strike = float(diff.idxmin())
    call_price = float(paired.loc[forward_strike, "Call"])
    put_price = float(paired.loc[forward_strike, "Put"])
    forward = forward_strike + math.exp(r * t) * (call_price - put_price)

    eligible_k0 = mid.index[mid.index <= forward]
    if len(eligible_k0) == 0:
        return None
    k0 = float(eligible_k0.max())

    q_values: dict[float, float] = {}
    if k0 in mid.index and pd.notna(mid.loc[k0].get("Call")) and pd.notna(mid.loc[k0].get("Put")):
        q_values[k0] = float((mid.loc[k0, "Call"] + mid.loc[k0, "Put"]) / 2.0)
    else:
        return None

    put_strikes = [float(x) for x in mid.index[mid.index < k0]][::-1]
    call_strikes = [float(x) for x in mid.index[mid.index > k0]]
    put_mids = mid.get("Put", pd.Series(dtype=float))
    call_mids = mid.get("Call", pd.Series(dtype=float))
    put_bids = bid.get("Put", pd.Series(dtype=float))
    call_bids = bid.get("Call", pd.Series(dtype=float))

    q_values.update(_truncate_zero_bids(put_strikes, put_mids, put_bids))
    q_values.update(_truncate_zero_bids(call_strikes, call_mids, call_bids))
    q = pd.Series(q_values, dtype=float).replace([np.inf, -np.inf], np.nan).dropna().sort_index()
    q = q.loc[q >= 0]
    if len(q) < 3:
        return None

    strikes = q.index.to_numpy(dtype=float)
    contribution = (
        _delta_k(strikes)
        / np.square(strikes)
        * math.exp(r * t)
        * q.to_numpy(dtype=float)
    )
    sigma_squared = (
        (2.0 / t) * contribution.sum()
        - (1.0 / t) * ((forward / k0) - 1.0) ** 2
    )
    if not math.isfinite(sigma_squared) or sigma_squared <= 0:
        return None

    return {
        "Date": trade_date.strftime("%Y-%m-%d"),
        "Root": root,
        "Settlement": _settlement_label(root),
        "Expiration_Date": expiration_date.strftime("%Y-%m-%d"),
        "Days_to_Exp": round(dte, 6),
        "Hours_to_Exp": round(seconds / 3600.0, 4),
        "Forward": round(forward, 4),
        "K0": round(k0, 4),
        "Risk_Free_Rate": round(r, 8),
        "VIX": round(100.0 * math.sqrt(sigma_squared), 4),
        "Options_Used": int(len(q)),
    }


def calculate_term_structure(
    frame: pd.DataFrame,
    trade_date: pd.Timestamp,
    curve: TreasuryCurve,
) -> pd.DataFrame:
    """Calculate a VIX-style IV for every usable SPX/SPXW/SPXO expiration."""
    records: list[dict[str, object]] = []
    if frame.empty:
        return pd.DataFrame(
            columns=[
                "Date",
                "Root",
                "Settlement",
                "Expiration_Date",
                "Days_to_Exp",
                "Hours_to_Exp",
                "Forward",
                "K0",
                "Risk_Free_Rate",
                "VIX",
                "Options_Used",
            ]
        )
    for (_, _), group in frame.groupby(["Root", "Expiration_Date"], sort=True):
        record = calculate_single_term(group, trade_date, curve)
        if record is not None:
            records.append(record)
    if not records:
        return pd.DataFrame()
    return (
        pd.DataFrame.from_records(records)
        .sort_values(["Days_to_Exp", "Root"])
        .reset_index(drop=True)
    )
