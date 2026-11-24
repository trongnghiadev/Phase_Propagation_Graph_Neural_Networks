"""Đánh giá mô hình trên tập kiểm tra (checklist 6.9).

**Chỉ số.**

| Ký hiệu | Tên | Đơn vị | Ý nghĩa |
|---|---|---|---|
| `MAE` | Mean Absolute Error | m | sai số tuyệt đối trung bình độ cao đỉnh |
| `RMSE` | Root Mean Squared Error | m | nhạy với sai số lớn |
| `NSE` | Nash–Sutcliffe Efficiency | — | so với trung bình quan sát (1 = hoàn hảo) |
| `KGE` | Kling–Gupta Efficiency | — | so với mô hình 1:1 + phương sai |
| `MAE_dt` | MAE thời điểm đỉnh | h | sai số giờ đỉnh (khoảng cách vòng 24 h) |

**KGE** (`Gupta et al. 2009`):

    KGE = 1 − sqrt( (r − 1)² + (α − 1)² + (β − 1)² )

với `r` hệ số tương quan, `α = σ_p / σ_o` (tỉ lệ độ lệch chuẩn), `β = μ_p / μ_o`
(tỉ lệ trung bình). KGE = 1 là dự báo hoàn hảo.

**So khớp (matching).** Với mỗi bản ghi dự báo `(station, target_date, peak_idx)` ta
nối với quan trọc thực đo cùng khoá. Nhãn dùng để đánh giá:

* độ cao đỉnh ngày = `max(h_peak1, h_peak2)` (bỏ qua NaN),
* giờ đỉnh ngày = giờ của đỉnh có độ cao lớn hơn.

Dự báo dùng `h_q50` (phân vị trung vị) và `t_peak` (ISO 8601 UTC → giờ trong ngày
theo giờ Việ Nam, `UTC+7`).
"""
from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Iterable, Sequence

import numpy as np
import pandas as pd


# ---------------------------------------------------------------------------
# Cấu trúc kết quả
# ---------------------------------------------------------------------------
@dataclass
class Metrics:
    """Bộ chỉ số của một mô hình trên một tập."""

    model: str
    split: str = "test"
    n: int = 0
    mae: float = float("nan")
    rmse: float = float("nan")
    nse: float = float("nan")
    kge: float = float("nan")
    mae_dt: float = float("nan")
    bias: float = float("nan")
    pearson_r: float = float("nan")
    coverage_10_90: float = float("nan")
    by_lead: dict[str, dict[str, float]] = field(default_factory=dict)
    by_station: dict[str, dict[str, float]] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return asdict(self)

    def to_row(self) -> dict:
        """Dòng phẳng để ghi CSV."""
        return {
            "model": self.model, "split": self.split, "n": self.n,
            "mae": self.mae, "rmse": self.rmse, "nse": self.nse, "kge": self.kge,
            "mae_dt": self.mae_dt, "bias": self.bias, "pearson_r": self.pearson_r,
            "coverage_10_90": self.coverage_10_90,
        }


# ---------------------------------------------------------------------------
# Chỉ số cơ bản
# ---------------------------------------------------------------------------
def mae(p: np.ndarray, o: np.ndarray) -> float:
    """MAE (bỏ qua mẫu không hữu hạn)."""
    m = np.isfinite(p) & np.isfinite(o)
    if not m.any():
        return float("nan")
    return float(np.mean(np.abs(p[m] - o[m])))


def rmse(p: np.ndarray, o: np.ndarray) -> float:
    """RMSE (bỏ qua mẫu không hữu hạn)."""
    m = np.isfinite(p) & np.isfinite(o)
    if not m.any():
        return float("nan")
    return float(np.sqrt(np.mean((p[m] - o[m]) ** 2)))


def bias(p: np.ndarray, o: np.ndarray) -> float:
    """Độ lệch có hệ thống (thiên hướng dự báo − quan trọc)."""
    m = np.isfinite(p) & np.isfinite(o)
    if not m.any():
        return float("nan")
    return float(np.mean(p[m] - o[m]))


def nse(p: np.ndarray, o: np.ndarray) -> float:
    """Nash–Sutcliffe Efficiency (so với trung bình quan sát)."""
    m = np.isfinite(p) & np.isfinite(o)
    if m.sum() < 2:
        return float("nan")
    pp, oo = p[m], o[m]
    denom = float(np.sum((oo - oo.mean()) ** 2))
    if denom == 0.0:
        return float("nan")
    return float(1.0 - np.sum((oo - pp) ** 2) / denom)


def kge(p: np.ndarray, o: np.ndarray) -> float:
    """Kling–Gupta Efficiency (Gupta et al. 2009)."""
    m = np.isfinite(p) & np.isfinite(o)
    if m.sum() < 2:
        return float("nan")
    pp, oo = p[m], o[m]
    if oo.std() == 0.0 or pp.std() == 0.0:
        return float("nan")
    r = float(np.corrcoef(pp, oo)[0, 1])
    alpha = float(pp.std() / oo.std())
    beta = float(pp.mean() / oo.mean()) if oo.mean() != 0.0 else float("nan")
    if not math.isfinite(beta):
        return float("nan")
    return float(1.0 - math.sqrt((r - 1.0) ** 2 + (alpha - 1.0) ** 2 + (beta - 1.0) ** 2))


def pearson_r(p: np.ndarray, o: np.ndarray) -> float:
    """Hệ số tương quan Pearson."""
    m = np.isfinite(p) & np.isfinite(o)
    if m.sum() < 2:
        return float("nan")
    pp, oo = p[m], o[m]
    if pp.std() == 0.0 or oo.std() == 0.0:
        return float("nan")
    return float(np.corrcoef(pp, oo)[0, 1])


def circular_mae_h(p: np.ndarray, o: np.ndarray, period: float = 24.0) -> float:
    """MAE giờ theo khoảng cách vòng (23:30 và 00:15 cách nhau 0,75 h)."""
    m = np.isfinite(p) & np.isfinite(o)
    if not m.any():
        return float("nan")
    d = np.remainder(p[m] - o[m] + period / 2.0, period) - period / 2.0
    return float(np.mean(np.abs(d)))


# ---------------------------------------------------------------------------
# Chuẩn bị dữ liệu so khớp
# ---------------------------------------------------------------------------
def _iso_to_hour_vn(ts: str) -> float:
    """ISO 8601 UTC → giờ trong ngày theo giờ Việ Nam (UTC+7)."""
    try:
        dt = pd.Timestamp(ts)
        if dt.tzinfo is None:
            dt = dt.tz_localize("UTC")
        vn = dt.tz_convert("Asia/Ho_Chi_Minh")
        return float(vn.hour + vn.minute / 60.0)
    except Exception:  # noqa: BLE001
        return float("nan")


def observed_daily(g5: pd.DataFrame) -> pd.DataFrame:
    """Quan trọc thực đo theo ngày: độ cao đỉnh ngày và giờ đỉnh ngày.

    Trả về DataFrame có cột `station`, `day`, `h_obs`, `t_obs`.
    """
    g5 = g5.copy()
    g5["day"] = g5["day"].astype(str)
    h1 = g5["h_peak1"].astype(float)
    h2 = g5["h_peak2"].astype(float)
    t1 = g5["t_peak1_h"].astype(float)
    t2 = g5["t_peak2_h"].astype(float)

    h_obs = np.where(np.isfinite(h1) & np.isfinite(h2), np.maximum(h1, h2),
                     np.where(np.isfinite(h1), h1,
                              np.where(np.isfinite(h2), h2, np.nan)))
    # giờ của đỉnh cao hơn (h2 thiếu hoặc h1 >= h2 → đỉnh 1). Trước 24/11/2026 điều kiện là
    # `isfinite(h2) | ...` nên khi có đủ 2 đỉnh luôn lấy giờ đỉnh 1, kể cả khi đỉnh 2 cao hơn.
    take1 = np.isfinite(h1) & (~np.isfinite(h2) | (h1 >= h2))
    t_obs = np.where(take1, t1, t2)
    t_obs = np.where(np.isfinite(h_obs), t_obs, np.nan)

    out = pd.DataFrame({"station": g5["station"].astype(str), "day": g5["day"],
                        "h_obs": h_obs, "t_obs": t_obs})
    return out.drop_duplicates(subset=["station", "day"]).reset_index(drop=True)


def match_predictions(records: Sequence[dict], obs: pd.DataFrame) -> pd.DataFrame:
    """Nối bản ghi dự báo với quan trọc thực đo.

    Với mỗi bản ghi dự báo `(station, target_date, peak_idx)`:
    * độ cao dự báo: `h_q50`,
    * giờ dự báo: `t_peak` (ISO 8601 UTC) → giờ trong ngày giờ Việ Nam.

    Lưu ý: bản ghi có `peak_idx` 0 và 1 (hai đỉnh trong ngày) nhưng quan trọc thực đo
    chỉ có **một** đỉnh ngày (đỉnh cao nhất). Vì vậy ta dùng **đỉnh dự báo cao hơn**
    (`h_q50` lớn nhất giữa 2 `peak_idx`) để so với đỉnh thực đo — đây là đối chiếu
    công bằng cho mọi mô hình (B0/B_harm cũng chỉ sinh 2 bản ghi mỗi ngày).
    """
    if not records:
        return pd.DataFrame(columns=["station", "day", "h_pred", "t_pred",
                                     "lead_day", "model", "h_q10", "h_q90"])
    df = pd.DataFrame(list(records))
    df["station"] = df["station"].astype(str)
    df["target_date"] = df["target_date"].astype(str)
    df["lead_day"] = df["lead_day"].astype(int)
    df["model"] = df["model"].astype(str)

    df["t_pred"] = df["t_peak"].map(lambda s: _iso_to_hour_vn(s) if s is not None
                                    and isinstance(s, str) else np.nan)
    df["h_q10"] = pd.to_numeric(df.get("h_q10"), errors="coerce")
    df["h_q90"] = pd.to_numeric(df.get("h_q90"), errors="coerce")
    df["h_pred"] = pd.to_numeric(df["h_q50"], errors="coerce")

    # Chọn NGUYÊN MỘT DÒNG (đỉnh dự báo cao hơn) trong mỗi (model, station, target_date,
    # lead_day): sắp `h_pred` giảm dần (NaN cuối) rồi `drop_duplicates`. Không dùng
    # `groupby().first()` — hàm đó lấy giá trị khác NaN đầu tiên của TỪNG cột, nên `t_pred`
    # và `h_pred` có thể đến từ hai bản ghi khác nhau.
    keys = ["model", "station", "target_date", "lead_day"]
    df = df.sort_values(keys + ["h_pred"], ascending=[True] * len(keys) + [False],
                        na_position="last", kind="mergesort")
    best = df.drop_duplicates(subset=keys, keep="first").reset_index(drop=True)

    obs = obs.copy()
    obs["day"] = obs["day"].astype(str)
    merged = best.merge(obs, left_on=["station", "target_date"], right_on=["station", "day"],
                        how="inner")
    return merged.reset_index(drop=True)


# ---------------------------------------------------------------------------
# Đánh giá
# ---------------------------------------------------------------------------
def compute_metrics(merged: pd.DataFrame, model: str, split: str = "test") -> Metrics:
    """Tính bộ chỉ số từ bảng đã so khớp."""
    p = merged["h_pred"].to_numpy(dtype=float)
    o = merged["h_obs"].to_numpy(dtype=float)
    tp = merged["t_pred"].to_numpy(dtype=float)
    to = merged["t_obs"].to_numpy(dtype=float)

    q10 = merged["h_q10"].to_numpy(dtype=float) if "h_q10" in merged else None
    q90 = merged["h_q90"].to_numpy(dtype=float) if "h_q90" in merged else None
    cov = float("nan")
    if q10 is not None and q90 is not None:
        m = np.isfinite(q10) & np.isfinite(q90) & np.isfinite(o)
        if m.any():
            cov = float(np.mean((o[m] >= q10[m]) & (o[m] <= q90[m])))

    met = Metrics(model=model, split=split, n=int(len(merged)),
                  mae=mae(p, o), rmse=rmse(p, o), nse=nse(p, o), kge=kge(p, o),
                  mae_dt=circular_mae_h(tp, to), bias=bias(p, o),
                  pearson_r=pearson_r(p, o), coverage_10_90=cov)

    # theo chân trời
    by_lead: dict[str, dict[str, float]] = {}
    if "lead_day" in merged.columns:
        for lead, sub in merged.groupby("lead_day"):
            sp = sub["h_pred"].to_numpy(dtype=float)
            so = sub["h_obs"].to_numpy(dtype=float)
            stp = sub["t_pred"].to_numpy(dtype=float)
            sto = sub["t_obs"].to_numpy(dtype=float)
            by_lead[str(int(lead))] = {
                "n": int(len(sub)), "mae": mae(sp, so), "rmse": rmse(sp, so),
                "nse": nse(sp, so), "kge": kge(sp, so),
                "mae_dt": circular_mae_h(stp, sto),
            }
    met.by_lead = by_lead

    # theo trạm
    by_st: dict[str, dict[str, float]] = {}
    for st, sub in merged.groupby("station"):
        sp = sub["h_pred"].to_numpy(dtype=float)
        so = sub["h_obs"].to_numpy(dtype=float)
        stp = sub["t_pred"].to_numpy(dtype=float)
        sto = sub["t_obs"].to_numpy(dtype=float)
        by_st[str(st)] = {
            "n": int(len(sub)), "mae": mae(sp, so), "rmse": rmse(sp, so),
            "nse": nse(sp, so), "kge": kge(sp, so),
            "mae_dt": circular_mae_h(stp, sto),
        }
    met.by_station = by_st
    return met


def evaluate_records(records: Sequence[dict], g5: pd.DataFrame,
                     model: str | None = None, split: str = "test") -> Metrics:
    """Đánh giá một danh sách bản ghi dự báo trên quan trọc thực đo."""
    obs = observed_daily(g5)
    merged = match_predictions(records, obs)
    name = model or (records[0].get("model") if records else "unknown")
    return compute_metrics(merged, str(name), split)


def metrics_table(metrics: Iterable[Metrics]) -> pd.DataFrame:
    """Ghép nhiều bộ chỉ số thành bảng (một dòng cho mỗi mô hình)."""
    rows = [m.to_row() for m in metrics]
    return pd.DataFrame(rows)


def format_summary(metrics: Iterable[Metrics]) -> str:
    """Bảng chữ cho báo cáo."""
    rows = metrics
    lines = [f"{'model':<18} {'n':>7} {'MAE':>8} {'RMSE':>8} {'NSE':>8} "
             f"{'KGE':>8} {'MAE_dt':>8} {'bias':>8} {'r':>7}"]
    lines.append("-" * len(lines[0]))
    for m in rows:
        lines.append(
            f"{m.model:<18} {m.n:>7} {m.mae:>8.4f} {m.rmse:>8.4f} {m.nse:>8.4f} "
            f"{m.kge:>8.4f} {m.mae_dt:>8.4f} {m.bias:>8.4f} {m.pearson_r:>7.4f}")
    return "\n".join(lines)
