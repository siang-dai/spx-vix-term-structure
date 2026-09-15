"""Download and normalize Cboe SPX/SPXW end-of-day marking-price data."""

from __future__ import annotations

import io
import re
import time
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd
import requests

CBOE_EOD_315_URL = (
    "https://cdn.cboe.com/resources/marking_prices/"
    "eod_marking_prices_late_list.csv"
)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
)

# SPXO is included for forward compatibility with Cboe's announced rollout of
# AM-settled daily SPX expirations. It is harmless when absent from the CSV.
SPX_ROOTS = {"SPX", "SPXW", "SPXO"}


class CboeDownloadError(RuntimeError):
    """Raised when the Cboe marking-price file cannot be downloaded or parsed."""


@dataclass(frozen=True)
class DownloadConfig:
    timeout_seconds: int = 45
    max_attempts: int = 4
    retry_wait_seconds: float = 5.0


def _norm(value: object) -> str:
    return re.sub(r"[^a-z0-9]+", "", str(value).strip().lower())


def _column_lookup(frame: pd.DataFrame) -> dict[str, str]:
    return {_norm(column): str(column) for column in frame.columns}


def _find_column(frame: pd.DataFrame, aliases: list[str]) -> str | None:
    lookup = _column_lookup(frame)
    for alias in aliases:
        key = _norm(alias)
        if key in lookup:
            return lookup[key]
    return None


def _to_numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(
        series.astype(str)
        .str.strip()
        .str.replace(",", "", regex=False)
        .replace({"": np.nan, "-": np.nan, "--": np.nan, "nan": np.nan}),
        errors="coerce",
    )


def _parse_osi_symbol(value: object) -> tuple[str, pd.Timestamp, str, float] | None:
    """Parse OCC/OSI symbols such as SPXW260918C06000000.

    The root is variable length, followed by YYMMDD, C/P, then an 8-digit
    strike in thousandths of an index point.
    """

    compact = re.sub(r"\s+", "", str(value).upper())
    match = re.fullmatch(
        r"(?P<root>[A-Z0-9]{1,6})(?P<date>\d{6})(?P<cp>[CP])(?P<strike>\d{8})",
        compact,
    )
    if not match:
        return None
    expiry = pd.to_datetime(match.group("date"), format="%y%m%d", errors="coerce")
    if pd.isna(expiry):
        return None
    option_type = "Call" if match.group("cp") == "C" else "Put"
    strike = int(match.group("strike")) / 1000.0
    return match.group("root"), pd.Timestamp(expiry), option_type, strike


def _trade_date_from_headers(headers: requests.structures.CaseInsensitiveDict) -> pd.Timestamp:
    last_modified = headers.get("Last-Modified")
    if last_modified:
        timestamp = parsedate_to_datetime(last_modified)
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=ZoneInfo("UTC"))
        chicago = timestamp.astimezone(ZoneInfo("America/Chicago"))
        return pd.Timestamp(chicago.date())
    return pd.Timestamp(datetime.now(ZoneInfo("America/Chicago")).date())


def download_marking_prices(
    config: DownloadConfig | None = None,
) -> tuple[pd.DataFrame, pd.Timestamp, bytes]:
    """Download the current Cboe 3:15 p.m. CT end-of-day marking-price CSV.

    Returns the raw frame, a best-effort trading date, and the raw bytes.
    The endpoint contains only the latest file, so this project accumulates
    history prospectively on each scheduled run.
    """

    config = config or DownloadConfig()
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/csv,text/plain,*/*",
        "Referer": "https://www.cboe.com/",
    }
    last_error: Exception | None = None
    for attempt in range(1, config.max_attempts + 1):
        try:
            response = requests.get(
                CBOE_EOD_315_URL,
                headers=headers,
                timeout=config.timeout_seconds,
            )
            response.raise_for_status()
            raw_bytes = response.content
            if not raw_bytes.strip():
                raise CboeDownloadError("Cboe returned an empty marking-price file")
            text = raw_bytes.decode("utf-8-sig", errors="replace")
            frame = pd.read_csv(io.StringIO(text), dtype=str)
            if frame.empty:
                raise CboeDownloadError("Cboe marking-price CSV parsed as an empty table")

            date_column = _find_column(
                frame,
                ["date", "trade date", "trading date", "business date"],
            )
            if date_column:
                parsed = pd.to_datetime(frame[date_column], errors="coerce")
                parsed = parsed.dropna()
                trade_date = parsed.max().normalize() if not parsed.empty else _trade_date_from_headers(response.headers)
            else:
                trade_date = _trade_date_from_headers(response.headers)
            return frame, trade_date, raw_bytes
        except (requests.RequestException, pd.errors.ParserError, UnicodeError, CboeDownloadError) as exc:
            last_error = exc
            if attempt < config.max_attempts:
                time.sleep(config.retry_wait_seconds * attempt)
    raise CboeDownloadError(
        f"Unable to download Cboe marking-price CSV after {config.max_attempts} attempts: {last_error}"
    )


def normalize_spx_options(raw: pd.DataFrame) -> pd.DataFrame:
    """Normalize the Cboe file into one row per SPX-family option quote.

    The current public Cboe marking-price file is a *wide* table: each row
    identifies one root/expiration/strike and carries separate Call and Put
    quote columns. Older/alternate schemas may instead contain one option per
    row. This parser supports both layouts.

    For the current wide schema we deliberately use the last disseminated
    market BBO rather than the final indicative marking-price columns. Those
    actual bid/ask quotes are the appropriate inputs for this VIX-style
    calculation and preserve the zero-bid information needed by the VIX wing
    truncation rule.
    """

    frame = raw.copy()
    frame.columns = [str(column).strip().lstrip("\ufeff") for column in frame.columns]

    root_col = _find_column(
        frame,
        [
            "OSI Root Symbol",
            "OSI Root",
            "Root Symbol",
            "Option Root",
            "Root",
            "Class",
            "Underlying Symbol",
        ],
    )
    expiry_col = _find_column(
        frame,
        ["Expiration Date", "Expiration", "Expiry", "Exercise Date", "Expiration Day"],
    )
    strike_col = _find_column(frame, ["Strike Price", "Strike", "Exercise Price"])

    # ------------------------------------------------------------------
    # Current Cboe public marking-price schema (wide Call + Put columns).
    # ------------------------------------------------------------------
    call_bid_col = _find_column(
        frame,
        [
            "call_last_disseminated_market_bid",
            "Call Last Disseminated Market Bid",
        ],
    )
    call_ask_col = _find_column(
        frame,
        [
            "call_last_disseminated_market_ask",
            "Call Last Disseminated Market Ask",
        ],
    )
    put_bid_col = _find_column(
        frame,
        [
            "put_last_disseminated_market_bid",
            "Put Last Disseminated Market Bid",
        ],
    )
    put_ask_col = _find_column(
        frame,
        [
            "put_last_disseminated_market_ask",
            "Put Last Disseminated Market Ask",
        ],
    )

    has_wide_quotes = all(
        [
            root_col,
            expiry_col,
            strike_col,
            call_bid_col,
            call_ask_col,
            put_bid_col,
            put_ask_col,
        ]
    )

    if has_wide_quotes:
        root = frame[root_col].astype(str).str.strip().str.upper()
        expiry = pd.to_datetime(frame[expiry_col], errors="coerce")
        strike = _to_numeric(frame[strike_col])

        common = pd.DataFrame(
            {
                "Root": root,
                "Expiration_Date": expiry,
                "Strike_Price": strike,
            },
            index=frame.index,
        )

        calls = common.copy()
        calls["Option_Type"] = "Call"
        calls["Bid"] = _to_numeric(frame[call_bid_col])
        calls["Ask"] = _to_numeric(frame[call_ask_col])

        puts = common.copy()
        puts["Option_Type"] = "Put"
        puts["Bid"] = _to_numeric(frame[put_bid_col])
        puts["Ask"] = _to_numeric(frame[put_ask_col])

        normalized = pd.concat([calls, puts], ignore_index=True)

    else:
        # --------------------------------------------------------------
        # Fallback: one-option-per-row schemas / OSI-symbol schemas.
        # --------------------------------------------------------------
        symbol_col = _find_column(
            frame,
            ["OSI Symbol", "Option Symbol", "Symbol", "Series Symbol", "Contract Symbol"],
        )
        cp_col = _find_column(
            frame,
            ["Put or Call", "Put/Call", "Call/Put", "C/P", "Option Type", "Type"],
        )

        bid_col = _find_column(
            frame,
            [
                "Actual BBO Bid",
                "Actual Bid",
                "BBO Bid",
                "Best Bid",
                "Bid Price",
                "Bid",
            ],
        )
        ask_col = _find_column(
            frame,
            [
                "Actual BBO Ask",
                "Actual Ask",
                "BBO Ask",
                "Best Ask",
                "Ask Price",
                "Ask",
            ],
        )

        parsed_symbol: pd.Series | None = None
        if symbol_col:
            parsed_symbol = frame[symbol_col].map(_parse_osi_symbol)

        if root_col:
            root = frame[root_col].astype(str).str.strip().str.upper()
        elif parsed_symbol is not None:
            root = parsed_symbol.map(lambda item: item[0] if item else np.nan)
        else:
            raise ValueError(
                "Could not identify an SPX root-symbol column or full OSI symbol. "
                f"Observed columns: {list(frame.columns)}"
            )

        if expiry_col:
            expiry = pd.to_datetime(frame[expiry_col], errors="coerce")
        elif parsed_symbol is not None:
            expiry = parsed_symbol.map(lambda item: item[1] if item else pd.NaT)
        else:
            expiry = pd.Series(pd.NaT, index=frame.index)

        if cp_col:
            option_type = (
                frame[cp_col]
                .astype(str)
                .str.strip()
                .str.upper()
                .map(
                    {
                        "C": "Call",
                        "CALL": "Call",
                        "CALLS": "Call",
                        "P": "Put",
                        "PUT": "Put",
                        "PUTS": "Put",
                    }
                )
            )
        elif parsed_symbol is not None:
            option_type = parsed_symbol.map(lambda item: item[2] if item else np.nan)
        else:
            option_type = pd.Series(np.nan, index=frame.index)

        if strike_col:
            strike = _to_numeric(frame[strike_col])
        elif parsed_symbol is not None:
            strike = pd.to_numeric(
                parsed_symbol.map(lambda item: item[3] if item else np.nan),
                errors="coerce",
            )
        else:
            strike = pd.Series(np.nan, index=frame.index)

        if not bid_col or not ask_col:
            raise ValueError(
                "Could not identify bid/ask columns in the Cboe CSV. "
                f"Observed columns: {list(frame.columns)}"
            )

        normalized = pd.DataFrame(
            {
                "Root": root,
                "Expiration_Date": expiry,
                "Option_Type": option_type,
                "Strike_Price": strike,
                "Bid": _to_numeric(frame[bid_col]),
                "Ask": _to_numeric(frame[ask_col]),
            }
        )

    normalized = normalized.loc[normalized["Root"].isin(SPX_ROOTS)].copy()

    valid_market = (
        normalized["Bid"].notna()
        & normalized["Ask"].notna()
        & normalized["Bid"].ge(0)
        & normalized["Ask"].ge(normalized["Bid"])
    )
    normalized["Mid_Price"] = np.where(
        valid_market,
        (normalized["Bid"] + normalized["Ask"]) / 2.0,
        np.nan,
    )

    # Keep zero-bid rows: the VIX methodology needs them in order to detect
    # the two-consecutive-zero-bid stopping point. Invalid/crossed quotes are
    # discarded because they cannot supply a usable midpoint.
    normalized = normalized.dropna(
        subset=["Expiration_Date", "Option_Type", "Strike_Price", "Mid_Price"]
    )
    normalized = normalized.loc[
        normalized["Option_Type"].isin(["Call", "Put"])
        & normalized["Strike_Price"].gt(0)
    ].copy()
    normalized["Expiration_Date"] = pd.to_datetime(
        normalized["Expiration_Date"], errors="coerce"
    ).dt.normalize()

    return normalized.sort_values(
        ["Expiration_Date", "Root", "Strike_Price", "Option_Type"]
    ).reset_index(drop=True)


def save_raw_debug_copy(raw_bytes: bytes, trade_date: pd.Timestamp, raw_dir: Path) -> Path:
    """Save a local raw copy. data/raw/*.csv is intentionally git-ignored."""

    raw_dir.mkdir(parents=True, exist_ok=True)
    path = raw_dir / f"cboe_marking_prices_{trade_date:%Y-%m-%d}.csv"
    path.write_bytes(raw_bytes)
    return path
