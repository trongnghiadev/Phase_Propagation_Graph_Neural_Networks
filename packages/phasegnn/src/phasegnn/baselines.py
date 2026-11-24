"""Các mô hình cơ sở để so sánh với PhaseGNN (đề cương 5.2.7, checklist GĐ5).

| Mã | Mô hình | Nguồn dữ liệu | Ghi chú |
|---|---|---|---|
| `B0` | Persistence theo chu kỳ | G5 | đỉnh ngày `d+k` = đỉnh ngày `d` + biến thiên thiên văn giữa `d` và `d+k` |
| `B_KTTV` | Dự báo nghiệp vụ Đài KTTV Nam Bộ | `official_forecasts` (MongoDB) | chỉ Phú An, Nhà Bè, Thủ Dầu Một (từ giữa 2025) |
| `B_harm` | Điều hòa thiên văn | `data/processed/features/astro_tide.csv` | triều thuần túy |
| `B1` | Điều hòa + hồi quy phần dư theo xả hồ có độ trễ | G5 + astro | hồi quy tuyến tính có lag hồ chứa |
| `B2` | LSTM tại từng trạm | G5 | không đồ thị, học sâu |

**Định dạng đầu ra chung.** Mọi mô hình sinh bản ghi theo
`schemas/prediction/v1.schema.json`: `model`, `station`, `issued_date`, `target_date`,
`lead_day`, `peak_idx`, `h_q10`, `h_q50`, `h_q90`, `t_peak`.

**Lưu ý rò rỉ.** Tất cả mô hình chỉ dùng dữ liệu có trước thời điểm phát bản tin
(ngày dự báo thứ 1 = `issued_date`); định nghĩa chân trời giống nhau cho mọi mô hình
(như B_KTTV): ngày phát bản tin là ngày dự báo thứ 1.
"""
from __future__ import annotations

import math
import os
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Sequence

import numpy as np
import pandas as pd
import torch
from torch import nn

from .data import (KTTV_STATIONS, MAIN_STATIONS, PEAK_COLS, PEAK_HOUR_COLS, SPLITS,
                   load_astro_tide, load_g5)

#: Số phân vị đầu ra.
QUANTILE_LEVELS: tuple[float, ...] = (0.1, 0.5, 0.9)


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------
def _iso(day: str) -> str:
    return day


def _add_days(day: str, n: int) -> str:
    d = date.fromisoformat(day)
    return (d + timedelta(days=n)).isoformat()


def _hours_to_iso(day: str, hour: float) -> str | None:
    """Giờ trong ngày (giờ VN) → chuỗi ISO 8601 UTC (khớp schema).

    Giờ Việt Nam = UTC + 7 ⇒ giờ UTC = (giờ VN − 7) mod 24; nếu giờ VN < 7 thì thuộc
    ngày **hôm trước** (theo UTC).
    """
    if hour is None or not np.isfinite(hour):
        return None
    total = int(round(float(hour) * 60.0))       # phút
    total = total % (24 * 60)
    hh, mm = divmod(total, 60)
    y, m, d = (int(x) for x in day.split("-"))
    # giờ VN = UTC + 7
    dt = date(y, m, d)
    hh_utc = (hh - 7) % 24
    if hh < 7:
        dt = dt - timedelta(days=1)
    return f"{dt.isoformat()}T{hh_utc:02d}:{mm:02d}:00Z"


def _ts_to_iso(ts) -> str | None:
    """Chuẩn hoá mốc thời gian MongoDB (`datetime`) → ISO 8601 UTC kèm `Z`.

    PyMongo trả `datetime` **naive** (không có tzinfo) vì BSON Date lưu epoch UTC và
    driver mặc định bỏ tzinfo ⇒ ta coi là UTC và gắn `Z` (schema bắt buộc).
    """
    if ts is None:
        return None
    if hasattr(ts, "isoformat"):
        s = ts.isoformat()
        if s.endswith("+00:00"):
            s = s[:-6] + "Z"
        elif not s.endswith("Z"):
            s = s + "Z"
        return s
    return str(ts)


def _quantile_from_spread(mean: float, spread: float) -> tuple[float, float, float]:
    """Từ giá trị trung vị và độ rộng → (q10, q50, q90).

    Mô hình cơ sở đơn giản không học phân vị, nên ta dùng một độ rộng cố định
    (mặc định 0,15 m cho đỉnh triều — xấp xỉ sai số quan sát đỉnh tại các trạm
    nghiệp vụ). Hằng số này nằm trong `config/` nếu cần hiệu chỉnh.
    """
    return (mean - spread, mean, mean + spread)


#: Độ rộng phân vị mặc định (m) cho các mô hình cơ sở không học phân vị.
DEFAULT_SPREAD_M = 0.15


# ---------------------------------------------------------------------------
# B0 — Persistence theo chu kỳ
# ---------------------------------------------------------------------------
def persistence_forecast(df: pd.DataFrame, issued_date: str, t_out: int = 5,
                          astro: pd.DataFrame | None = None,
                          spread_m: float = DEFAULT_SPREAD_M) -> list[dict]:
    """B0: đỉnh ngày `d+k` = đỉnh ngày `d` + biến thiên thiên văn giữa `d` và `d+k`.

    Tham số
    -------
    df : bảng G5 (đã nạp).
    issued_date : ngày phát bản tin; quan trọc dùng tới ngày `issued_date − 1`.
    astro : bảng triều thiên văn (`day`, `h1`, `h2`, `t1_h`, `t2_h`) — dùng để tính biến
        thiên triều. Nếu `None` → dùng cột `astro_*` của G5 (đã ghép).

    Trả về danh sách bản ghi dự báo (theo schema chung).
    """
    out: list[dict] = []
    prev_day = _add_days(issued_date, -1)
    if astro is None:
        astro = df[["day", "astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h"]].rename(
            columns={"astro_h1": "h1", "astro_h2": "h2",
                     "astro_t1_h": "t1_h", "astro_t2_h": "t2_h"}).drop_duplicates("day")
    astro_map = {r["day"]: r for _, r in astro.iterrows()}

    for st, sub in df.groupby("station"):
        sub = sub.set_index("day")
        if prev_day not in sub.index:
            continue
        row = sub.loc[prev_day]
        h1 = float(row["h_peak1"]) if pd.notna(row["h_peak1"]) else np.nan
        h2 = float(row["h_peak2"]) if pd.notna(row["h_peak2"]) else np.nan
        t1 = float(row["t_peak1_h"]) if pd.notna(row["t_peak1_h"]) else np.nan
        t2 = float(row["t_peak2_h"]) if pd.notna(row["t_peak2_h"]) else np.nan
        a_prev = astro_map.get(prev_day)
        for k in range(1, t_out + 1):
            target = _add_days(issued_date, k - 1)
            a_now = astro_map.get(target)
            dh1 = dh2 = 0.0
            if a_prev is not None and a_now is not None:
                prev1 = a_prev.get("h1")
                prev2 = a_prev.get("h2")
                now1 = a_now.get("h1")
                now2 = a_now.get("h2")
                if all(v is not None and pd.notna(v) for v in (prev1, now1)):
                    dh1 = float(now1) - float(prev1)
                if all(v is not None and pd.notna(v) for v in (prev2, now2)):
                    dh2 = float(now2) - float(prev2)
            for idx, (h, t, dh) in enumerate(((h1, t1, dh1), (h2, t2, dh2))):
                if h is None or not np.isfinite(h):
                    continue
                pred = h + dh
                q10, q50, q90 = _quantile_from_spread(pred, spread_m)
                out.append({
                    "model": "B0", "station": str(st), "issued_date": issued_date,
                    "target_date": target, "lead_day": k, "peak_idx": idx,
                    "h_q10": q10, "h_q50": q50, "h_q90": q90,
                    "t_peak": _hours_to_iso(target, t),
                })
    return out


def persistence_forecast_many(df: pd.DataFrame, issued_dates: Sequence[str],
                              astro: pd.DataFrame | None = None,
                              t_out: int = 5,
                              spread_m: float = DEFAULT_SPREAD_M) -> list[dict]:
    """B0 cho **nhiều ngày phát bản tin** — tương đương gọi `persistence_forecast` từng ngày.

    Tối ưu: bản đồ triều và bảng G5 theo trạm dựng **một lần** cho toàn bộ ngày.
    """
    if astro is None:
        astro = df[["day", "astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h"]].rename(
            columns={"astro_h1": "h1", "astro_h2": "h2",
                     "astro_t1_h": "t1_h", "astro_t2_h": "t2_h"}).drop_duplicates("day")
    astro_map = {r["day"]: r for _, r in astro.iterrows()}
    by_station: dict[str, pd.DataFrame] = {
        str(st): sub.set_index("day") for st, sub in df.groupby("station")
    }
    out: list[dict] = []
    for issued_date in issued_dates:
        prev_day = _add_days(issued_date, -1)
        a_prev = astro_map.get(prev_day)
        for st, sub in by_station.items():
            if prev_day not in sub.index:
                continue
            row = sub.loc[prev_day]
            h1 = float(row["h_peak1"]) if pd.notna(row["h_peak1"]) else np.nan
            h2 = float(row["h_peak2"]) if pd.notna(row["h_peak2"]) else np.nan
            t1 = float(row["t_peak1_h"]) if pd.notna(row["t_peak1_h"]) else np.nan
            t2 = float(row["t_peak2_h"]) if pd.notna(row["t_peak2_h"]) else np.nan
            for k in range(1, t_out + 1):
                target = _add_days(issued_date, k - 1)
                a_now = astro_map.get(target)
                dh1 = dh2 = 0.0
                if a_prev is not None and a_now is not None:
                    prev1, prev2 = a_prev.get("h1"), a_prev.get("h2")
                    now1, now2 = a_now.get("h1"), a_now.get("h2")
                    if all(v is not None and pd.notna(v) for v in (prev1, now1)):
                        dh1 = float(now1) - float(prev1)
                    if all(v is not None and pd.notna(v) for v in (prev2, now2)):
                        dh2 = float(now2) - float(prev2)
                for idx, (h, t, dh) in enumerate(((h1, t1, dh1), (h2, t2, dh2))):
                    if h is None or not np.isfinite(h):
                        continue
                    pred = h + dh
                    q10, q50, q90 = _quantile_from_spread(pred, spread_m)
                    out.append({
                        "model": "B0", "station": st, "issued_date": issued_date,
                        "target_date": target, "lead_day": k, "peak_idx": idx,
                        "h_q10": q10, "h_q50": q50, "h_q90": q90,
                        "t_peak": _hours_to_iso(target, t),
                    })
    return out


def harmonic_forecast_many(astro: pd.DataFrame, issued_dates: Sequence[str],
                           t_out: int = 5, stations: Sequence[str] = MAIN_STATIONS,
                           spread_m: float = DEFAULT_SPREAD_M) -> list[dict]:
    """B_harm cho **nhiều ngày phát bản tin** — tương đương gọi `harmonic_forecast` từng ngày.

    Tối ưu: bản đồ triều dựng **một lần** cho toàn bộ ngày.
    """
    amap = {r["day"]: r for _, r in astro.iterrows()}
    out: list[dict] = []
    for issued_date in issued_dates:
        for k in range(1, t_out + 1):
            target = _add_days(issued_date, k - 1)
            row = amap.get(target)
            if row is None:
                continue
            h1, h2 = row.get("h1"), row.get("h2")
            t1, t2 = row.get("t1_h"), row.get("t2_h")
            for st in stations:
                for idx, (h, t) in enumerate(((h1, t1), (h2, t2))):
                    if h is None or not np.isfinite(h):
                        continue
                    q10, q50, q90 = _quantile_from_spread(float(h), spread_m)
                    out.append({
                        "model": "B_harm", "station": str(st), "issued_date": issued_date,
                        "target_date": target, "lead_day": k, "peak_idx": idx,
                        "h_q10": q10, "h_q50": q50, "h_q90": q90,
                        "t_peak": _hours_to_iso(target, t),
                    })
    return out


def harmonic_forecast(astro: pd.DataFrame, issued_date: str, t_out: int = 5,
                      stations: Sequence[str] = MAIN_STATIONS,
                      spread_m: float = DEFAULT_SPREAD_M) -> list[dict]:
    """B_harm: dự báo bằng triều thiên văn dự tính (không hiệu chỉnh theo trạm).

    Triều thiên văn trong `astro_tide.csv` là triều ở **Vũng Tàu**; mô hình giả định
    độ cao đỉnh tương đối giống nhau giữa các trạm trong lưu vực (đây là mô hình điều
    hòa thuần túy, đúng như đề cương 5.2.7).
    """
    out: list[dict] = []
    amap = {r["day"]: r for _, r in astro.iterrows()}
    for k in range(1, t_out + 1):
        target = _add_days(issued_date, k - 1)
        row = amap.get(target)
        if row is None:
            continue
        h1 = row.get("h1")
        h2 = row.get("h2")
        t1 = row.get("t1_h")
        t2 = row.get("t2_h")
        for st in stations:
            for idx, (h, t) in enumerate(((h1, t1), (h2, t2))):
                if h is None or not np.isfinite(h):
                    continue
                q10, q50, q90 = _quantile_from_spread(float(h), spread_m)
                out.append({
                    "model": "B_harm", "station": str(st), "issued_date": issued_date,
                    "target_date": target, "lead_day": k, "peak_idx": idx,
                    "h_q10": q10, "h_q50": q50, "h_q90": q90,
                    "t_peak": _hours_to_iso(target, t),
                })
    return out


# ---------------------------------------------------------------------------
# B_KTTV — dự báo nghiệp vụ
# ---------------------------------------------------------------------------
def kttv_forecast_from_collection(collection, issued_date: str, t_out: int = 5,
                                  stations: Sequence[str] = KTTV_STATIONS) -> list[dict]:
    """B_KTTV: đọc `official_forecasts` trong MongoDB và chuyển sang định dạng chung.

    `collection` là collection `official_forecasts` của `vngis`. Mỗi tài liệu có
    `issued_date`, `target_date`, `lead_day`, `station`, `high[]`, `low[]` (mỗi phần tử có
    `ts` (BSON Date, UTC) và `value`).
    """
    out: list[dict] = []
    st_set = set(stations)
    cursor = collection.find({"issued_date": issued_date})
    for doc in cursor:
        st = str(doc.get("station"))
        if st not in st_set:
            continue
        target = str(doc.get("target_date"))
        lead = int(doc.get("lead_day") or 1)
        if lead > t_out:
            continue
        highs = list(doc.get("high") or [])
        # sắp xếp theo giờ tăng dần → đỉnh 1, đỉnh 2 (bỏ qua `ts` null)
        highs = sorted(highs, key=lambda h: (h.get("ts") is None, h.get("ts")))
        for idx, h in enumerate(highs[:2]):
            v = h.get("value")
            if v is None:
                continue
            ts = h.get("ts")
            t_iso = _ts_to_iso(ts)
            q10, q50, q90 = _quantile_from_spread(float(v), DEFAULT_SPREAD_M)
            out.append({
                "model": "B_KTTV", "station": st, "issued_date": issued_date,
                "target_date": target, "lead_day": lead, "peak_idx": idx,
                "h_q10": q10, "h_q50": q50, "h_q90": q90,
                "t_peak": t_iso,
            })
    return out


def kttv_forecast_many_from_collection(collection, issued_dates: Sequence[str],
                                       t_out: int = 5,
                                       stations: Sequence[str] = KTTV_STATIONS) -> list[dict]:
    """B_KTTV cho **nhiều ngày phát bản tin** — tương đương gọi `kttv_forecast_from_collection` từng ngày.

    Tối ưu: **một** truy vấn MongoDB cho toàn bộ ngày (`issued_date` ∈ tập ngày).
    """
    out: list[dict] = []
    st_set = set(stations)
    days = sorted(set(issued_dates))
    if not days:
        return out
    cursor = collection.find({"issued_date": {"$in": days}})
    for doc in cursor:
        st = str(doc.get("station"))
        if st not in st_set:
            continue
        issued = str(doc.get("issued_date"))
        target = str(doc.get("target_date"))
        lead = int(doc.get("lead_day") or 1)
        if lead > t_out:
            continue
        highs = sorted(list(doc.get("high") or []),
                       key=lambda h: (h.get("ts") is None, h.get("ts")))
        for idx, h in enumerate(highs[:2]):
            v = h.get("value")
            if v is None:
                continue
            ts = h.get("ts")
            t_iso = _ts_to_iso(ts)
            q10, q50, q90 = _quantile_from_spread(float(v), DEFAULT_SPREAD_M)
            out.append({
                "model": "B_KTTV", "station": st, "issued_date": issued,
                "target_date": target, "lead_day": lead, "peak_idx": idx,
                "h_q10": q10, "h_q50": q50, "h_q90": q90,
                "t_peak": t_iso,
            })
    return out


def kttv_forecast_from_dataframe(rows: Iterable[dict], issued_date: str,
                                  t_out: int = 5,
                                  stations: Sequence[str] = KTTV_STATIONS) -> list[dict]:
    """B_KTTV từ danh sách dict (dùng khi không có MongoDB — ví dụ trong test)."""
    out: list[dict] = []
    st_set = set(stations)
    for doc in rows:
        if str(doc.get("issued_date")) != issued_date:
            continue
        st = str(doc.get("station"))
        if st not in st_set:
            continue
        target = str(doc.get("target_date"))
        lead = int(doc.get("lead_day") or 1)
        if lead > t_out:
            continue
        highs = sorted(list(doc.get("high") or []), key=lambda h: h.get("ts", ""))
        for idx, h in enumerate(highs[:2]):
            v = h.get("value")
            if v is None:
                continue
            q10, q50, q90 = _quantile_from_spread(float(v), DEFAULT_SPREAD_M)
            out.append({
                "model": "B_KTTV", "station": st, "issued_date": issued_date,
                "target_date": target, "lead_day": lead, "peak_idx": idx,
                "h_q10": q10, "h_q50": q50, "h_q90": q90,
                "t_peak": h.get("t_peak"),
            })
    return out


# ---------------------------------------------------------------------------
# B1 — điều hòa + hồi quy phần dư theo xả hồ có độ trễ
# ---------------------------------------------------------------------------
@dataclass
class ResidualRegressor:
    """Hồi quy tuyến tính phần dư: `residual ≈ β₀ + Σ βᵢ·xᵢ(t − lagᵢ)`.

    Dùng `numpy.linalg.lstsq` (không phụ thuộc `scikit-learn`) để giữ image tối giản.
    """

    coefficients: np.ndarray
    intercept: float
    feature_names: list[str]
    lags: list[int]

    @classmethod
    def fit(cls, x: np.ndarray, y: np.ndarray, feature_names: Sequence[str],
            lags: Sequence[int]) -> "ResidualRegressor":
        x = np.asarray(x, dtype=np.float64)
        y = np.asarray(y, dtype=np.float64)
        mask = np.isfinite(y) & np.isfinite(x).all(axis=1)
        if mask.sum() < 5:
            raise ValueError(f"không đủ mẫu hợp lệ để hồi quy ({int(mask.sum())})")
        xm, ym = x[mask], y[mask]
        design = np.concatenate([np.ones((len(xm), 1)), xm], axis=1)
        beta, *_ = np.linalg.lstsq(design, ym, rcond=None)
        return cls(coefficients=beta[1:], intercept=float(beta[0]),
                   feature_names=list(feature_names), lags=list(lags))

    def predict(self, x: np.ndarray) -> np.ndarray:
        x = np.asarray(x, dtype=np.float64)
        return x @ self.coefficients + self.intercept


def build_lagged_features(series: pd.Series, days: list[str], lag: int) -> np.ndarray:
    """Tạo ma trận đặc trưng có độ trễ: giá trị tại `day − lag`.

    Trả về ``[len(days), 1]`` với NaN cho ngày thiếu dữ liệu.
    """
    s = series.dropna()
    out = np.full((len(days), 1), np.nan)
    lookup = {d: v for d, v in s.items()}
    for i, d in enumerate(days):
        lag_day = _add_days(d, -lag)
        if lag_day in lookup:
            out[i, 0] = float(lookup[lag_day])
    return out


def fit_residual_regression(df: pd.DataFrame, station: str, train_range: tuple[str, str],
                             astro: pd.DataFrame, lag_hours: int = 24,
                             lag_reservoir_days: int = 1,
                             lag_rain_days: int = 1) -> ResidualRegressor:
    """B1: hồi quy phần dư theo xả hồ có độ trễ.

    Mô hình: ``peak(d) = harm(d) + β₀ + β₁·res_inflow(d − lag_reservoir_days)
    + β₂·res_spill(d − lag_reservoir_days) + β₃·rain(d − lag_rain_days)``.

    `lag_hours` là độ trễ (giờ) giữa đỉnh thiên văn và đỉnh thực tế tại trạm — dùng để
    cân chỉnh phần điều hòa (xem đề cương 5.2.7: "trễ cố định").
    """
    sub = df[df["station"] == station].set_index("day")
    amap = {r["day"]: r for _, r in astro.iterrows()}
    lo, hi = train_range
    days = sorted(d for d in sub.index if lo <= d <= hi)

    # Tra cứu bằng dict thay vì `sub.loc[d]` trong vòng lặp — nhanh hơn nhiều khi
    # dữ liệu có hàng nghìn ngày × nhiều trạm.
    h1_map = sub["h_peak1"].to_dict()
    inflow_map = sub["res_inflow_m3s"].to_dict() if "res_inflow_m3s" in sub else {}
    spill_map = sub["res_spill_m3s"].to_dict() if "res_spill_m3s" in sub else {}
    rain_map = sub["rain_mm"].to_dict() if "rain_mm" in sub else {}

    xs: list[list[float]] = []
    ys: list[float] = []
    for d in days:
        h1 = h1_map.get(d)
        if h1 is None or not pd.notna(h1):
            continue
        a = amap.get(d)
        if a is None:
            continue
        harm = a.get("h1")
        if harm is None or not pd.notna(harm):
            continue
        d_res = _add_days(d, -lag_reservoir_days)
        d_rain = _add_days(d, -lag_rain_days)
        v_in = float(inflow_map.get(d_res, np.nan))
        v_sp = float(spill_map.get(d_res, np.nan))
        v_rn = float(rain_map.get(d_rain, np.nan))
        if not np.isfinite(v_in):
            v_in = 0.0
        if not np.isfinite(v_sp):
            v_sp = 0.0
        if not np.isfinite(v_rn):
            v_rn = 0.0
        xs.append([v_in, v_sp, v_rn])
        ys.append(float(h1) - float(harm))
    if len(xs) < 10:
        raise ValueError(f"không đủ mẫu để hồi quy B1 tại {station} ({len(xs)})")
    return ResidualRegressor.fit(np.asarray(xs), np.asarray(ys),
                                 ["res_inflow", "res_spill", "rain"],
                                 [lag_hours, lag_reservoir_days, lag_rain_days])


def b1_forecast(df: pd.DataFrame, astro: pd.DataFrame, station: str, issued_date: str,
                model: ResidualRegressor, t_out: int = 5,
                spread_m: float = DEFAULT_SPREAD_M,
                _sub: pd.DataFrame | None = None,
                _amap: dict | None = None) -> list[dict]:
    """B1: dự báo = triều thiên văn + phần dư hồi quy (kèm trễ cố định).

    **Tham số nội bộ.** `_sub` (bảng đã lọc theo trạm) và `_amap` (bản đồ ngày →
    triều) cho phép gọi lặp nhiều ngày mà không lọc lại `df` — dùng bởi
    `b1_forecast_many` để chạy nhanh trên tập lớn.
    """
    out: list[dict] = []
    sub = _sub if _sub is not None else df[df["station"] == station].set_index("day")
    amap = _amap if _amap is not None else {r["day"]: r for _, r in astro.iterrows()}
    for k in range(1, t_out + 1):
        target = _add_days(issued_date, k - 1)
        a = amap.get(target)
        if a is None:
            continue
        harm = a.get("h1")
        if harm is None or not pd.notna(harm):
            continue
        d_res = _add_days(_add_days(issued_date, -1), -model.lags[1])
        inflow = float(sub["res_inflow_m3s"].get(d_res, 0.0)) if "res_inflow_m3s" in sub else 0.0
        spill = float(sub["res_spill_m3s"].get(d_res, 0.0)) if "res_spill_m3s" in sub else 0.0
        rain = float(sub["rain_mm"].get(d_res, 0.0)) if "rain_mm" in sub else 0.0
        for v in (inflow, spill, rain):
            if not np.isfinite(v):
                pass
        inflow = inflow if np.isfinite(inflow) else 0.0
        spill = spill if np.isfinite(spill) else 0.0
        rain = rain if np.isfinite(rain) else 0.0
        resid = model.predict(np.asarray([[inflow, spill, rain]]))[0]
        pred = float(harm) + resid
        q10, q50, q90 = _quantile_from_spread(pred, spread_m)
        out.append({
            "model": "B1", "station": station, "issued_date": issued_date,
            "target_date": target, "lead_day": k, "peak_idx": 0,
            "h_q10": q10, "h_q50": q50, "h_q90": q90,
            "t_peak": _hours_to_iso(target, a.get("t1_h")),
        })
    return out


def b1_forecast_many(df: pd.DataFrame, astro: pd.DataFrame, station: str,
                      issued_dates: Sequence[str], model: ResidualRegressor,
                      t_out: int = 5, spread_m: float = DEFAULT_SPREAD_M) -> list[dict]:
    """B1 cho **nhiều ngày phát bản tin** — tương đương gọi `b1_forecast` từng ngày.

    Tối ưu: lọc bảng theo trạm và dựng bản đồ triều **một lần** thay vì mỗi ngày.
    """
    sub = df[df["station"] == station].set_index("day")
    amap = {r["day"]: r for _, r in astro.iterrows()}
    out: list[dict] = []
    for d in issued_dates:
        out.extend(b1_forecast(df, astro, station, d, model, t_out=t_out,
                               spread_m=spread_m, _sub=sub, _amap=amap))
    return out


# ---------------------------------------------------------------------------
# B2 — LSTM tại từng trạm
# ---------------------------------------------------------------------------
class StationLSTM(nn.Module):
    """B2: LSTM tuần tự tại từng trạm (không dùng đồ thị).

    Đầu vào: chuỗi đặc trưng động `T_in` ngày gần nhất (đã chuẩn hoá).
    Đầu ra: 2 đỉnh × 3 phân vị + 2 giờ, cho `T_out` ngày tới.

    **Giải mã tuần tự (không teacher forcing).** Mỗi bước dự báo dùng **dự báo của chính
    bước trước** làm đầu vào của bước sau (qua lớp nối `feedback`), nhờ vậy không rò rỉ
    nhãn tương lai khi huấn luyện lẫn khi dự báo. Trạng thái LSTM được truyền qua các
    bước, nên mô hình nắm được quỹ đạo của chuỗi dự báo.
    """

    def __init__(self, in_dim: int, hidden: int = 64, t_out: int = 5,
                 n_quantiles: int = 3) -> None:
        super().__init__()
        self.in_dim = int(in_dim)
        self.hidden = int(hidden)
        self.t_out = int(t_out)
        self.n_quantiles = int(n_quantiles)
        self.lstm = nn.LSTM(self.in_dim, self.hidden, num_layers=1, batch_first=True)
        # nối dự báo (phân vị trung vị của 2 đỉnh) vào đầu vào bước kế tiếp
        self.feedback = nn.Linear(2, self.in_dim)
        self.head_h = nn.Linear(self.hidden, 2 * self.n_quantiles)
        self.head_t = nn.Linear(self.hidden, 2)
        self.head_l = nn.Linear(self.hidden, 2 * self.n_quantiles)

    def forward(self, x, steps: int | None = None):
        """`x`: ``[B, T_in, in_dim]`` → dict tensor.

        Nếu `steps` được chỉ định (> 1), hàm giải mã tuần tự `steps` ngày và trả về
        tensor có thêm trục thời gian: ``peak`` ``[B, steps, 2, Q]``.
        """
        steps = self.t_out if steps is None else int(steps)
        b, t_in, _ = x.shape
        h, (state, cell) = self.lstm(x)
        last = h[:, -1, :]                          # [B, H]
        peak = self.head_h(last).view(-1, 2, self.n_quantiles)
        hour = self.head_t(last)
        low = self.head_l(last).view(-1, 2, self.n_quantiles)

        if steps <= 1:
            return {"peak": peak, "hour": hour, "low": low}

        # --- giải mã tuần tự -------------------------------------------------
        peaks, hours, lows = [peak], [hour], [low]
        ctx = x[:, -1, :]                           # đặc trưng của ngày cuối
        cur = peak[:, :, 1]                         # q50 của 2 đỉnh [B, 2]
        for _ in range(steps - 1):
            fb = self.feedback(cur)                 # [B, in_dim]
            inp = (ctx + fb).unsqueeze(1)           # [B, 1, in_dim]
            out_h, (state, cell) = self.lstm(inp, (state, cell))
            last = out_h[:, -1, :]
            p = self.head_h(last).view(-1, 2, self.n_quantiles)
            peaks.append(p)
            hours.append(self.head_t(last))
            lows.append(self.head_l(last).view(-1, 2, self.n_quantiles))
            cur = p[:, :, 1]
        return {
            "peak": torch.stack(peaks, dim=1),      # [B, steps, 2, Q]
            "hour": torch.stack(hours, dim=1),      # [B, steps, 2]
            "low": torch.stack(lows, dim=1),        # [B, steps, 2, Q]
        }

    def fit(self, x: np.ndarray, y: np.ndarray, epochs: int = 30, batch_size: int = 64,
            lr: float = 1e-3, seed: int = 0, verbose: bool = False) -> dict:
        """Huấn luyện B2. `x`: ``[N, T_in, F]``, `y`: ``[N, T_out, 2]`` (NaN = bỏ qua)."""
        torch.manual_seed(seed)
        xt = torch.as_tensor(x, dtype=torch.float32)
        yt = torch.as_tensor(y, dtype=torch.float32)
        t_out = int(yt.shape[1])
        opt = torch.optim.AdamW(self.parameters(), lr=lr)
        lossf = torch.nn.SmoothL1Loss(reduction="none")
        n = len(xt)
        history: list[float] = []
        for ep in range(epochs):
            perm = torch.randperm(n)
            total = 0.0
            cnt = 0
            for i in range(0, n, batch_size):
                idx = perm[i:i + batch_size]
                out = self.forward(xt[idx], steps=t_out)
                pred = out["peak"]                     # [B, T_out, 2, Q]
                tgt = yt[idx]                          # [B, T_out, 2]
                # dùng phân vị trung vị để so với nhãn
                pred_med = pred[..., 1]                # [B, T_out, 2]
                # NaN ở nhãn (không quan trắc) ⇒ thay trước khi nhân với mặt nạ,
                # vì NaN·0 vẫn là NaN và sẽ làm hỏng toàn bộ gradient.
                mask = torch.isfinite(tgt)
                tgt_safe = torch.where(mask, tgt, torch.zeros_like(tgt))
                loss = lossf(pred_med, tgt_safe)
                loss = loss * mask.to(loss.dtype)
                denom = torch.clamp(mask.sum(), min=1).to(loss.dtype)
                l = loss.sum() / denom
                opt.zero_grad()
                l.backward()
                torch.nn.utils.clip_grad_norm_(self.parameters(), 1.0)
                opt.step()
                total += float(l.detach()) * len(idx)
                cnt += len(idx)
            history.append(total / max(cnt, 1))
            if verbose and (ep % 10 == 0 or ep == epochs - 1):
                print(f"  [B2] epoch {ep}: loss = {history[-1]:.5f}", flush=True)
        return {"loss_history": history, "final_loss": history[-1] if history else float("nan")}


# ---------------------------------------------------------------------------
# Ghi kết quả
# ---------------------------------------------------------------------------
def write_predictions(records: Sequence[dict], out_dir: str | os.PathLike,
                      filename: str = "predictions.csv") -> Path:
    """Ghi danh sách bản ghi dự báo ra CSV theo định dạng chung."""
    from pathlib import Path

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    cols = ["model", "station", "issued_date", "target_date", "lead_day", "peak_idx",
            "h_q10", "h_q50", "h_q90", "t_peak"]
    df = pd.DataFrame(list(records), columns=cols)
    path = out / filename
    df.to_csv(path, index=False, encoding="utf-8", lineterminator="\n")
    return path


def validate_prediction_records(records: Sequence[dict]) -> list[str]:
    """Kiểm tra bản ghi dự báo theo `schemas/prediction/v1.schema.json` (không cần jsonschema).

    Trả về danh sách lỗi (rỗng = hợp lệ).
    """
    import re

    errors: list[str] = []
    date_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
    ts_re = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    for i, r in enumerate(records):
        for k in ("model", "station", "issued_date", "target_date", "lead_day", "peak_idx"):
            if k not in r:
                errors.append(f"bản ghi {i}: thiếu khóa {k}")
        if not date_re.match(str(r.get("issued_date", ""))):
            errors.append(f"bản ghi {i}: issued_date không đúng định dạng")
        if not date_re.match(str(r.get("target_date", ""))):
            errors.append(f"bản ghi {i}: target_date không đúng định dạng")
        lead = r.get("lead_day")
        if not isinstance(lead, int) or not 1 <= lead <= 5:
            errors.append(f"bản ghi {i}: lead_day phải là số nguyên trong [1, 5]")
        pidx = r.get("peak_idx")
        if not isinstance(pidx, int) or not 0 <= pidx <= 1:
            errors.append(f"bản ghi {i}: peak_idx phải là số nguyên trong [0, 1]")
        q10, q50, q90 = r.get("h_q10"), r.get("h_q50"), r.get("h_q90")
        if q10 is None and q50 is None and q90 is None:
            errors.append(f"bản ghi {i}: cả 3 phân vị đều null")
        vals = [v for v in (q10, q50, q90) if v is not None]
        if len(vals) == 3 and not (q10 <= q50 <= q90):
            errors.append(f"bản ghi {i}: phân vị không đơn điệu ({q10}, {q50}, {q90})")
        t = r.get("t_peak")
        if t is not None and not ts_re.match(str(t)):
            errors.append(f"bản ghi {i}: t_peak không đúng định dạng ISO 8601 UTC")
    return errors
