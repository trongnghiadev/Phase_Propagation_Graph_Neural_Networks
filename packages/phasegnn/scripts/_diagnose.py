#!/usr/bin/env python
"""Chẩn đoán sai lệch metric test của PhaseGNN (tạm, dùng để điều tra)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))

from phasegnn.data import default_paths, load_g5  # noqa: E402

PRED = sys.argv[1] if len(sys.argv) > 1 else "results/E2/phasegnn_d12/predictions_test.csv"

pred = pd.read_csv(ROOT / PRED)
pred["t"] = pd.to_datetime(pred["t_peak"])
pred["hour_vn"] = (pred["t"].dt.hour + 7) % 24 + pred["t"].dt.minute / 60

df = load_g5(paths=default_paths(ROOT))
df["day"] = df["day"].astype(str)

m = pred.merge(
    df[["station", "day", "h_peak1", "h_peak2", "t_peak1_h", "t_peak2_h"]],
    left_on=["station", "target_date"], right_on=["station", "day"], how="inner")
print(f"merged rows: {len(m)} (from {len(pred)} predictions)")
print(f"obs t_peak1_h mean={df['t_peak1_h'].mean():.2f} "
      f"t_peak2_h mean={df['t_peak2_h'].mean():.2f}")
print(f"pred hour_vn mean by peak_idx: "
      f"{pred.groupby('peak_idx')['hour_vn'].mean().round(3).to_dict()}")

for p in (0, 1):
    s = m[m["peak_idx"] == p]
    obs = s["h_peak1"] if p == 0 else s["h_peak2"]
    hi = np.maximum(s["h_peak1"], s["h_peak2"])
    lo = np.minimum(s["h_peak1"], s["h_peak2"])
    print(f"\npeak_idx={p}  n={len(s)}")
    print(f"  MAE vs same-index obs   = {(s['h_q50'] - obs).abs().mean():.4f}")
    print(f"  MAE vs taller-peak obs  = {(s['h_q50'] - hi).abs().mean():.4f}")
    print(f"  MAE vs shorter-peak obs = {(s['h_q50'] - lo).abs().mean():.4f}")

# nếu ghép theo quy ước R: chọn đỉnh dự báo cao hơn, so với đỉnh thực đo cao hơn
best = (m.sort_values(["model", "station", "target_date", "lead_day", "h_q50"],
                       ascending=[True, True, True, True, False])
        .groupby(["model", "station", "target_date", "lead_day"], as_index=False).first())
hi = np.maximum(best["h_peak1"], best["h_peak2"])
print(f"\n[quy uoc R] chon dinh du bao cao hon, so voi dinh thuc do cao hon:")
print(f"  MAE = {(best['h_q50'] - hi).abs().mean():.4f}   n={len(best)}")
# gio: dung hour_vn cua dong da chon
dt = ((best["hour_vn"] - np.where(best["h_peak1"] >= best["h_peak2"],
                                  best["t_peak1_h"], best["t_peak2_h"])) % 24)
dt = np.minimum(dt, 24 - dt)
print(f"  MAE_dt = {dt.mean():.4f} h")
