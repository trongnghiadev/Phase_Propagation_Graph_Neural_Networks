"""Kiểm thử `phasegnn.evaluate` (so khớp dự báo – thực đo)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from phasegnn.evaluate import match_predictions, observed_daily


def _rec(peak_idx, h, t_peak, lead=1, model="M"):
    return {"model": model, "station": "PHU_AN", "issued_date": "2024-07-01",
            "target_date": "2024-07-01", "lead_day": lead, "peak_idx": peak_idx,
            "h_q10": None if h is None else h - 0.1, "h_q50": h,
            "h_q90": None if h is None else h + 0.1, "t_peak": t_peak}


def _obs():
    return pd.DataFrame({"station": ["PHU_AN"], "day": ["2024-07-01"],
                         "h_obs": [1.5], "t_obs": [20.0]})


def test_match_predictions_keeps_one_whole_row():
    """Đỉnh cao hơn không có `t_peak`: KHÔNG được ghép giờ của bản ghi kia vào nó."""
    recs = [_rec(0, 1.2, "2024-07-01T00:00:00Z"),   # 07:00 VN, đỉnh thấp, có giờ
            _rec(1, 1.6, None)]                       # đỉnh cao, không có giờ
    m = match_predictions(recs, _obs())
    assert len(m) == 1
    row = m.iloc[0]
    assert row["h_pred"] == 1.6
    assert np.isnan(row["t_pred"])       # groupby().first() cũ cho 7.0 (lấy từ dòng khác)
    assert row["h_q10"] == 1.6 - 0.1
    assert row["peak_idx"] == 1


def test_match_predictions_one_row_per_lead():
    recs = [_rec(0, 1.2, "2024-07-01T00:00:00Z", lead=1), _rec(1, 1.4, "2024-07-01T12:00:00Z", lead=1),
            _rec(0, 1.3, "2024-07-01T01:00:00Z", lead=2), _rec(1, 1.1, "2024-07-01T13:00:00Z", lead=2)]
    m = match_predictions(recs, _obs()).sort_values("lead_day")
    assert m["lead_day"].tolist() == [1, 2]
    assert m["h_pred"].tolist() == [1.4, 1.3]
    assert m["t_pred"].tolist() == [19.0, 8.0]


def test_hours_to_iso_before_7h_vn_is_previous_utc_day():
    """06:30 giờ VN = 23:30Z hôm trước (bản chép cũ trong scripts sinh `T41:..`)."""
    from phasegnn.baselines import _hours_to_iso
    assert _hours_to_iso("2024-07-15", 6.5) == "2024-07-14T23:30:00Z"
    assert _hours_to_iso("2024-07-15", 19.5) == "2024-07-15T12:30:00Z"
    from phasegnn.evaluate import _iso_to_hour_vn
    assert _iso_to_hour_vn(_hours_to_iso("2024-07-15", 0.25)) == 0.25


def test_observed_daily_hour_is_of_higher_peak():
    """Giờ đỉnh ngày = giờ của đỉnh CAO HƠN (trước đây luôn lấy giờ đỉnh 1 khi có đủ 2 đỉnh)."""
    g5 = pd.DataFrame({"station": ["S", "S"], "day": ["2024-07-01", "2024-07-02"],
                       "h_peak1": [1.0, 1.6], "h_peak2": [1.4, 1.2],
                       "t_peak1_h": [6.0, 7.0], "t_peak2_h": [18.0, 19.0]})
    o = observed_daily(g5).sort_values("day")
    assert o["h_obs"].tolist() == [1.4, 1.6]
    assert o["t_obs"].tolist() == [18.0, 7.0]
