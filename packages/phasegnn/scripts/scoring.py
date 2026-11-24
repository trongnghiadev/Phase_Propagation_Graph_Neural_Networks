#!/usr/bin/env python
"""Hàm chấm điểm dùng chung cho các mô hình (đánh giá theo checklist 6.7).

Dùng để chấm mọi mô hình theo **cùng một** quy ước:

* **MAE đỉnh (m)** — trung bình |`h_q50` dự báo − độ cao đỉnh quan trọc thực đo|.
* **MAE giờ (h)** — trung bình sai số giờ đỉnh, tính **quanh vòng 24 h**
  (`d = 0.5·|((Δ + 12) mod 24) − 12|`), nên đỉnh lúc 23:30 và 00:15 chỉ cách nhau 0.75 h.

Dùng chung cho `evaluate_baselines.py` và `evaluate_checkpoint.py` để mọi mô hình
được so sánh trên cùng tập quan trọc thực đo.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

#: Các cột quan trọc thực đo cần dùng để chấm.
TRUTH_COLS = ["station", "target_date", "peak_idx", "h_peak", "t_peak_h"]


def build_truth(df: pd.DataFrame, days: list[str] | None = None) -> pd.DataFrame:
    """Dựng bảng quan trọc thực đo (2 đỉnh/ngày/trạm).

    `days` chỉ dùng để **lọc theo ngày phát bản tin**; bảng kết quả vẫn phải chứa
    mọi ngày mà dự báo nhắm tới (`target_date`), vì `target_date = issued_date +
    lead_day − 1` có thể vượt ra ngoài tập ngày đã chọn. Vì vậy ta giữ **toàn bộ**
    ngày có quan trọc thực đo; tham số `days` được giữ cho tương thích.
    """
    sub = df[["station", "day", "h_peak1", "h_peak2",
              "t_peak1_h", "t_peak2_h"]].copy()
    sub["target_date"] = sub["day"]
    sub["peak_idx"] = 0
    sub["h_peak"] = sub["h_peak1"]
    sub["t_peak_h"] = sub["t_peak1_h"]
    t2 = sub[["station", "day", "h_peak2", "t_peak2_h"]].copy()
    t2["target_date"] = t2["day"]
    t2["peak_idx"] = 1
    t2["h_peak"] = t2["h_peak2"]
    t2["t_peak_h"] = t2["t_peak2_h"]
    return pd.concat([sub[TRUTH_COLS], t2[TRUTH_COLS]], ignore_index=True)


def _lookup(truth: pd.DataFrame, key: tuple) -> object:
    """Tra cứu nhanh trong bảng quan trọc thực đo (đã lập chỉ mục).

    Bảng được lập chỉ mục **một lần** rồi lưu vào `truth.attrs` (pandas không cho
    gán thuộc tính mới trực tiếp lên DataFrame).
    """
    tv = truth.attrs.get("_indexed")
    if tv is None:
        tv = truth.set_index(["station", "target_date", "peak_idx"])
        truth.attrs["_indexed"] = tv
    if key not in tv.index:
        return None
    return tv.loc[key]


def mae_peak(records: list[dict], truth: pd.DataFrame) -> float:
    """MAE độ cao đỉnh (m)."""
    if not records:
        return float("nan")
    errs: list[float] = []
    for r in records:
        obs = _lookup(truth, (r["station"], r["target_date"], r["peak_idx"]))
        if obs is None:
            continue
        if isinstance(obs, pd.Series):
            obs = obs.iloc[0]
        if obs is None or pd.isna(obs):
            continue
        errs.append(abs(float(r["h_q50"]) - float(obs)))
    return float(np.mean(errs)) if errs else float("nan")


def mae_hour(records: list[dict], truth: pd.DataFrame) -> float:
    """MAE giờ đỉnh (giờ), tính quanh vòng 24 h."""
    if not records:
        return float("nan")
    errs: list[float] = []
    for r in records:
        obs = _lookup(truth, (r["station"], r["target_date"], r["peak_idx"]))
        if obs is None:
            continue
        if isinstance(obs, pd.Series):
            obs = obs.iloc[0]
        if obs is None or pd.isna(obs):
            continue
        h = r.get("t_peak")
        if h is None:
            continue
        # ISO 8601 UTC → giờ trong ngày Việt Nam (UTC+7)
        hh = int(h[11:13])
        mm = int(h[14:16])
        ph = (hh + 7) % 24 + mm / 60.0
        d = 0.5 * abs(((ph - float(obs) + 12.0) % 24.0) - 12.0)
        errs.append(d)
    return float(np.mean(errs)) if errs else float("nan")


def summarize(records: list[dict], truth: pd.DataFrame) -> dict:
    """Tóm tắt metrics tổng thể + theo trạm + theo lead day."""
    out: dict = {"model": records[0]["model"] if records else "",
                 "mae_peak_m": mae_peak(records, truth),
                 "mae_hour_h": mae_hour(records, truth),
                 "n_records": len(records)}
    by_station: list[dict] = []
    for st in sorted({r["station"] for r in records}):
        sub = [r for r in records if r["station"] == st]
        by_station.append({"model": out["model"], "station": st,
                           "mae_peak_m": mae_peak(sub, truth),
                           "mae_hour_h": mae_hour(sub, truth),
                           "n_records": len(sub)})
    by_lead: list[dict] = []
    for lead in sorted({r.get("lead_day", 1) for r in records}):
        sub = [r for r in records if r.get("lead_day", 1) == lead]
        by_lead.append({"model": out["model"], "lead_day": lead,
                        "mae_peak_m": mae_peak(sub, truth),
                        "mae_hour_h": mae_hour(sub, truth),
                        "n_records": len(sub)})
    return {"summary": out, "by_station": by_station, "by_lead": by_lead}
