#!/usr/bin/env python
"""Chứng minh: val metric (best-of-2) vs metric xuất CSV (theo peak_idx)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))

from phasegnn.data import default_paths, load_g5  # noqa: E402

df = load_g5(paths=default_paths(ROOT))
df["day"] = df["day"].astype(str)

for cand in ("phasegnn_long", "phasegnn_d12", "B3_long", "B3_d12"):
    p = ROOT / "results" / "E2" / cand / "predictions_test.csv"
    if p.exists():
        pred = pd.read_csv(p)
        print(f"[dùng] {p.relative_to(ROOT)}  n={len(pred)}")
        break
else:
    raise SystemExit("không tìm thấy predictions_test.csv nào trong results/E2")

m = pred.merge(df[["station", "day", "h_peak1", "h_peak2", "t_peak1_h", "t_peak2_h"]],
               left_on=["station", "target_date"], right_on=["station", "day"], how="inner")

# --- (A) best-of-2 (như evaluate() trong train.py) ---
g = m.groupby(["station", "target_date", "lead_day"])
best_of2 = g.apply(lambda d: np.minimum((d["h_q50"] - d["h_peak1"]).abs(),
                                         (d["h_q50"] - d["h_peak2"]).abs()).min(),
                   include_groups=False)
print(f"(A) best-of-2 MAE (giống evaluate()): {best_of2.mean():.4f} m   n={len(best_of2)}")

# (A2) max(q50) của 2 slot vs max(h1,h2)
mx = g.apply(lambda d: max(d["h_q50"].max(), np.nanmax([d["h_peak1"].iloc[0], d["h_peak2"].iloc[0]])),
             include_groups=False)
# cách đúng: max q50 của 2 slot vs max(h1,h2)
rows = []
for _, d in g:
    obs = np.fmax(d["h_peak1"].iloc[0], d["h_peak2"].iloc[0])
    rows.append(abs(d["h_q50"].max() - obs))
print(f"(A2) max(q50 slot) vs max(h1,h2):     {np.mean(rows):.4f} m   n={len(rows)}")

# --- (B) theo peak_idx (như CSV export) ---
for p in (0, 1):
    s = m[m["peak_idx"] == p]
    obs = s["h_peak1"] if p == 0 else s["h_peak2"]
    print(f"(B) peak_idx={p} vs cùng chỉ số:      {(s['h_q50'] - obs).abs().mean():.4f} m")

# --- (C) quy ước R: đỉnh dự báo cao nhất vs đỉnh thực đo cao nhất ---
best = m.sort_values(["station", "target_date", "lead_day", "h_q50"],
                     ascending=[True, True, True, False]).groupby(
    ["station", "target_date", "lead_day"], as_index=False).first()
hi = np.fmax(best["h_peak1"], best["h_peak2"])
print(f"(C) đỉnh cao nhất vs đỉnh cao nhất:    {(best['h_q50'] - hi).abs().mean():.4f} m")

# --- (D) MAE của dự báo trung vị toàn cục (hằng) ---
const = df[(df["day"] >= "2024-07-01") & (df["day"] <= "2026-11-23")]
print(f"(D) hằng số = mean(max(h1,h2)) = {np.fmax(const['h_peak1'], const['h_peak2']).mean():.4f} m")
print(f"    hằng số = median = {np.fmax(const['h_peak1'], const['h_peak2']).median():.4f} m")
