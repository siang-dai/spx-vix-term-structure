import pandas as pd

from cboe_data import normalize_spx_options


def test_normalize_separate_columns():
    raw = pd.DataFrame(
        {
            "OSI Root Symbol": ["SPXW", "SPXW", "VIX"],
            "Expiration Date": ["2026-10-16", "2026-10-16", "2026-10-21"],
            "Put or Call": ["C", "P", "C"],
            "Strike Price": [6000, 6000, 20],
            "Bid": [100, 95, 2],
            "Ask": [102, 97, 3],
        }
    )
    out = normalize_spx_options(raw)
    assert len(out) == 2
    assert set(out["Root"]) == {"SPXW"}
    assert set(out["Option_Type"]) == {"Call", "Put"}
    assert out["Mid_Price"].tolist() == [101.0, 96.0]


def test_normalize_full_osi_symbol_fallback():
    raw = pd.DataFrame(
        {
            "Symbol": ["SPXW261016C06000000", "SPXW261016P06000000"],
            "Bid Price": [100, 95],
            "Ask Price": [102, 97],
        }
    )
    out = normalize_spx_options(raw)
    assert set(out["Root"]) == {"SPXW"}
    assert out["Expiration_Date"].dt.strftime("%Y-%m-%d").unique().tolist() == ["2026-10-16"]
    assert set(out["Strike_Price"]) == {6000.0}
