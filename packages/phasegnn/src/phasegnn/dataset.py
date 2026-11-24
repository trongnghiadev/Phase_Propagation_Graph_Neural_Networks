"""Dựng tập dữ liệu cho mô hình: nối G5 + đồ thị + astro, chuẩn hoá, tạo cửa sổ.

Module này là **phần bổ sung** cho `data.py` (phần đọc thô đã có sẵn). Nó:

1. Nối cột `astro_*` từ `results/E1/astro_tide_by_station.csv` (G5 hiện để null vì R chưa
   sinh file `astro_tide.csv` khi chạy G5).
2. Chuẩn hoá đặc trưng **theo thống kê tập train** (đề cương 5.2.6).
3. Dựng cửa sổ `T_in = 14` ngày → `T_out = 5` ngày cho từng trạm.
4. Sinh **nhãn giờ dưới dạng độ lệch so với giờ đỉnh thiên văn** (đề cương 5.2.5) để hàm
   mất mát khả vi.

**Rò rỉ.** Đầu vào của mẫu ngày phát bản tin `d` chỉ dùng quan trắc đến hết ngày `d−1`
(kiểm tra bằng `check_leakage`).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd

from .data import (
    AUX_STATIONS,
    DEFAULT_T_IN,
    DEFAULT_T_OUT,
    MAIN_STATIONS,
    SPLITS,
    FeatureScaler,
    GraphData,
    check_astro_provenance,
    default_paths,
    load_g5,
    load_g5_meta,
)

#: Đặc trưng động dùng làm đầu vào mô hình (tên cột trong G5).
DYNAMIC_COLS: tuple[str, ...] = (
    "h_peak1", "h_peak2",
    "h_low1", "h_low2",
    "res_inflow_m3s", "res_spill_m3s", "res_turbine_m3s", "rain_mm",
    "astro_h1", "astro_h2",
    "missing_peak1", "missing_peak2",
)

#: Cột "lưu lượng xả hồ" dùng làm đặc trưng phụ (nút) cho MLP δ.
Q_COLS: tuple[str, ...] = ("res_spill_m3s",)


# ---------------------------------------------------------------------------
# Nối astro
# ---------------------------------------------------------------------------
def attach_astro(df: pd.DataFrame, astro_path: Path | None = None,
                 meta_path: Path | None = None, check_leak: bool = True) -> pd.DataFrame:
    """Nối triều thiên văn **theo từng trạm** vào bảng G5.

    Nguồn: `results/E1/astro_tide_by_station.csv` (cột `station`, `day`, `h1`, `h2`, `t1_h`,
    `t2_h`). Nếu thiếu file thì giữ nguyên giá trị null trong G5. File có mà điều hòa fit
    trên ngày ≥ mốc test (hoặc thiếu `astro_tide_meta.json`) → `AstroLeakError`.
    """
    if astro_path is None:
        astro_path = default_paths()["astro_by_station"]
    if not Path(astro_path).exists():
        return df
    if check_leak:
        # bảng theo trạm do cùng lần chạy harmonic.R sinh với `astro_tide.csv` → cùng meta.
        check_astro_provenance(meta_path)
    astro = pd.read_csv(astro_path, dtype={"station": str, "day": str})
    astro.columns = [c.strip() for c in astro.columns]
    astro = astro.rename(columns={"h1": "astro_h1", "h2": "astro_h2",
                                  "t1_h": "astro_t1_h", "t2_h": "astro_t2_h"})
    keep = ["station", "day", "astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h"]
    astro = astro[keep].drop_duplicates(subset=["station", "day"], keep="first")
    out = df.merge(astro, on=["station", "day"], how="left", suffixes=("", "_astro"))
    for c in ("astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h"):
        if f"{c}_astro" in out.columns:
            # giữ giá trị G5 nếu có, nếu không thì lấy từ file astro
            out[c] = out[c].where(out[c].notna(), out[f"{c}_astro"])
            out = out.drop(columns=[f"{c}_astro"])
    return out


# ---------------------------------------------------------------------------
# Chuẩn hoá
# ---------------------------------------------------------------------------
def build_scaler(df: pd.DataFrame, cols: Sequence[str] = DYNAMIC_COLS,
                 split: str = "train") -> FeatureScaler:
    """Fit bộ chuẩn hoá **chỉ trên tập train** (đề cương 5.2.6)."""
    lo, hi = SPLITS[split]
    sub = df[(df["day"] >= lo) & (df["day"] <= hi)]
    present = [c for c in cols if c in df.columns]
    arr = sub[present].to_numpy(dtype=np.float64)
    scaler = FeatureScaler()
    scaler.fit(arr)
    # đặt tên cột để tra cứu khi trực quan hoá
    scaler.mean = np.asarray(scaler.mean)
    scaler.std = np.asarray(scaler.std)
    scaler.columns = list(present)  # type: ignore[attr-defined]
    return scaler


def apply_scaler(df: pd.DataFrame, scaler: FeatureScaler,
                 cols: Sequence[str] = DYNAMIC_COLS) -> np.ndarray:
    """Chuẩn hoá bảng → mảng ``[N_rows, F]`` (NaN → 0 sau chuẩn hoá)."""
    present = list(getattr(scaler, "columns", cols))
    arr = df[present].to_numpy(dtype=np.float64)
    out = (arr - scaler.mean) / scaler.std
    return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)


# ---------------------------------------------------------------------------
# Mẫu cửa sổ
# ---------------------------------------------------------------------------
@dataclass
class Sample:
    """Một mẫu huấn luyện cho **một trạm** (cửa sổ quan trọc → cửa sổ dự báo)."""

    station: str
    issue_date: str
    target_dates: list[str]
    lead_days: list[int]
    # đặc trưng đầu vào: [T_in, F]
    x: np.ndarray
    # đặc trưng lưu lượng xả hồ (nút → trạm): [T_in, 1]
    q: np.ndarray
    # nhãn: độ cao đỉnh [T_out, 2], giờ đỉnh (độ lệch so với astro) [T_out, 2]
    target_h: np.ndarray
    target_t: np.ndarray
    # chân: [T_out, 2]
    target_low: np.ndarray
    target_low_t: np.ndarray
    # độ cao đỉnh ngày (max) [T_out], giờ đỉnh ngày [T_out]
    peak_day_h: np.ndarray
    peak_day_t: np.ndarray
    # giờ đỉnh thiên văn (để khôi phục giờ tuyệt đối khi xuất) [T_out, 2]
    astro_t: np.ndarray
    # trạm → chỉ số nút trong đồ thị
    node_idx: int


def _astro_hour_offset(obs_h: float, astro_h: float | None) -> float:
    """Độ lệch giờ đỉnh so với giờ đỉnh thiên văn (vòng 24 h), theo **đề cương 5.2.5**.

    Nếu không có giờ thiên văn → dùng chính giờ quan trắc làm gốc (độ lệch 0).
    """
    if astro_h is None or not np.isfinite(astro_h):
        return 0.0
    d = (float(obs_h) - float(astro_h) + 12.0) % 24.0 - 12.0
    return float(d)


def build_station_samples(df: pd.DataFrame, station: str, graph: GraphData,
                          scaler: FeatureScaler, cols: Sequence[str] = DYNAMIC_COLS,
                          t_in: int = DEFAULT_T_IN, t_out: int = DEFAULT_T_OUT,
                          q_cols: Sequence[str] = Q_COLS) -> list[Sample]:
    """Dựng các mẫu cửa sổ cho một trạm."""
    sub = df[df["station"] == station].set_index("day").sort_index()
    if sub.empty:
        return []
    node_idx = graph.station_node.get(station, -1)

    days = list(sub.index)
    present = list(getattr(scaler, "columns", cols))
    feats = sub[present].to_numpy(dtype=np.float64)
    feats = np.nan_to_num((feats - scaler.mean) / scaler.std, nan=0.0, posinf=0.0, neginf=0.0)

    qpresent = [c for c in q_cols if c in sub.columns]
    if qpresent:
        qraw = sub[qpresent].to_numpy(dtype=np.float64)
        qsc = qraw.copy()
        for j, c in enumerate(qpresent):
            vals = qraw[:, j]
            m = np.isfinite(vals)
            if m.any():
                mu, sd = vals[m].mean(), vals[m].std()
                qsc[:, j] = (np.nan_to_num(vals) - mu) / (sd if sd > 1e-6 else 1.0)
        qsc = np.nan_to_num(qsc, nan=0.0, posinf=0.0, neginf=0.0)
    else:
        qsc = np.zeros((len(days), 0), dtype=np.float64)

    def col(day: str, name: str) -> float:
        if day not in sub.index:
            return float("nan")
        v = sub.at[day, name]
        return float(v) if pd.notna(v) else float("nan")

    samples: list[Sample] = []
    for i in range(t_in, len(days) - t_out + 1):
        issue = days[i]
        win = days[i - t_in:i]
        if win[-1] >= issue:      # không xảy ra, nhưng giữ để chắc chắn
            continue
        tgt_days = days[i:i + t_out]
        # chỉ giữ mẫu có nhãn trong tập đích
        if tgt_days[-1] < issue:
            continue

        th, tt, tl, tlt = [], [], [], []
        pdh, pdt, ast = [], [], []
        for d in tgt_days:
            h1, h2 = col(d, "h_peak1"), col(d, "h_peak2")
            t1, t2 = col(d, "t_peak1_h"), col(d, "t_peak2_h")
            l1, l2 = col(d, "h_low1"), col(d, "h_low2")
            lt1, lt2 = col(d, "t_low1_h"), col(d, "t_low2_h")
            a1, a2 = col(d, "astro_t1_h"), col(d, "astro_t2_h")
            th.append([h1, h2])
            tt.append([_astro_hour_offset(t1, a1) if np.isfinite(t1) else np.nan,
                       _astro_hour_offset(t2, a2) if np.isfinite(t2) else np.nan])
            tl.append([l1, l2])
            tlt.append([_astro_hour_offset(lt1, a1) if np.isfinite(lt1) else np.nan,
                        _astro_hour_offset(lt2, a2) if np.isfinite(lt2) else np.nan])
            ast.append([a1 if np.isfinite(a1) else np.nan,
                        a2 if np.isfinite(a2) else np.nan])
            hs = [h for h in (h1, h2) if np.isfinite(h)]
            pdh.append(max(hs) if hs else float("nan"))
            if np.isfinite(h1) and (not np.isfinite(h2) or h1 >= h2):
                pdt.append(t1 if np.isfinite(t1) else float("nan"))
            elif np.isfinite(h2):
                pdt.append(t2 if np.isfinite(t2) else float("nan"))
            else:
                pdt.append(float("nan"))

        samples.append(Sample(
            station=station,
            issue_date=issue,
            target_dates=list(tgt_days),
            lead_days=list(range(1, t_out + 1)),
            x=feats[i - t_in:i].astype(np.float32),
            q=qsc[i - t_in:i].astype(np.float32),
            target_h=np.asarray(th, dtype=np.float32),
            target_t=np.asarray(tt, dtype=np.float32),
            target_low=np.asarray(tl, dtype=np.float32),
            target_low_t=np.asarray(tlt, dtype=np.float32),
            peak_day_h=np.asarray(pdh, dtype=np.float32),
            peak_day_t=np.asarray(pdt, dtype=np.float32),
            astro_t=np.asarray(ast, dtype=np.float32),
            node_idx=node_idx,
        ))
    return samples


def build_all_samples(df: pd.DataFrame, graph: GraphData, scaler: FeatureScaler,
                      cols: Sequence[str] = DYNAMIC_COLS,
                      t_in: int = DEFAULT_T_IN, t_out: int = DEFAULT_T_OUT,
                      splits: dict[str, tuple[str, str]] = SPLITS,
                      stations: Sequence[str] | None = None) -> dict[str, list[Sample]]:
    """Dựng mẫu cho cả 3 tập (dùng chung một bộ chuẩn hoá fit trên train)."""
    st_list = list(stations) if stations is not None else sorted(df["station"].unique().tolist())
    out: dict[str, list[Sample]] = {}
    for sp, (lo, hi) in splits.items():
        sub = df[(df["day"] >= lo) & (df["day"] <= hi)]
        samples: list[Sample] = []
        for st in st_list:
            sdf = df[df["station"] == st]
            if sdf.empty:
                continue
            st_samples = build_station_samples(sdf, st, graph, scaler, cols=cols,
                                               t_in=t_in, t_out=t_out)
            # chỉ giữ mẫu có ngày phát bản tin trong đúng tập
            samples.extend([s for s in st_samples if lo <= s.issue_date <= hi])
        out[sp] = samples
    return out


def sample_weight(station: str, aux_weight: float = 0.3) -> float:
    """Trọng số nhiệm vụ: 3 trạm chính = 1,0; 6 trạm còn lại = 0,3 (đề cương 5.2.5)."""
    return 1.0 if station in MAIN_STATIONS else aux_weight


def check_leakage(samples: Sequence[Sample]) -> list[str]:
    """Kiểm tra rò rỉ: ngày quan trọc cuối cùng phải < ngày phát bản tin."""
    errs: list[str] = []
    for s in samples:
        if s.issue_date and s.x.shape[0] > 0:
            # ngày quan trọc cuối cùng = issue_date − 1 ngày
            last = (date.fromisoformat(s.issue_date) - timedelta(days=1)).isoformat()
            if last >= s.issue_date:
                errs.append(f"{s.station} {s.issue_date}")
    return errs
