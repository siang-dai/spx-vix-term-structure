import math

import numpy as np
import pandas as pd

from spx_vix import calculate_term_structure
from treasury_rates import TreasuryCurve


def _black76_price(f, k, t, r, sigma, is_call):
    # Normal CDF without adding another dependency in the test logic.
    def n(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    d1 = (math.log(f / k) + 0.5 * sigma * sigma * t) / (sigma * math.sqrt(t))
    d2 = d1 - sigma * math.sqrt(t)
    disc = math.exp(-r * t)
    if is_call:
        return disc * (f * n(d1) - k * n(d2))
    return disc * (k * n(-d2) - f * n(-d1))


def test_calculate_term_structure_returns_one_expiry():
    trade_date = pd.Timestamp("2026-09-15")
    expiry = pd.Timestamp("2026-10-16")
    f = 6000.0
    r = 0.04
    t = 31 / 365
    sigma = 0.20
    rows = []
    for strike in np.arange(5400, 6601, 100):
        for option_type in ["Call", "Put"]:
            mid = _black76_price(f, float(strike), t, r, sigma, option_type == "Call")
            rows.append(
                {
                    "Root": "SPXW",
                    "Expiration_Date": expiry,
                    "Option_Type": option_type,
                    "Strike_Price": float(strike),
                    "Bid": max(0.05, mid - 0.05),
                    "Ask": mid + 0.05,
                    "Mid_Price": mid,
                }
            )
    curve = TreasuryCurve(
        rate_date=trade_date,
        tenors_days=np.array([30.0, 90.0, 365.0]),
        yields_decimal=np.array([r, r, r]),
        source="test",
    )
    out = calculate_term_structure(pd.DataFrame(rows), trade_date, curve)
    assert len(out) == 1
    assert out.iloc[0]["Root"] == "SPXW"
    assert abs(out.iloc[0]["Forward"] - f) < 5
    assert 10 < out.iloc[0]["VIX"] < 35
    assert out.iloc[0]["Options_Used"] >= 3
