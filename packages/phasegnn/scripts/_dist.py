#!/usr/bin/env python
"""Kiểm tra phân phối mục tiêu và dự báo (tạm, dùng để điều tra)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))

from phasegnn.data import SPLITS, default_paths, load_g5  # noqa: E402

df = load_g5(paths=default_paths(ROOT))
df["day"] = df["day"].astype(str)
for split, (lo, hi) in SPLITS.items():
    s = df[(df["day"] >= lo) & (df["day"] <= hi)]
    hi_pk = np.fmax(s["h_peak1"], s["h_peak2"])
    print(f"=== {split} {lo}..{hi}  n={len(s)}")
    print(f"  h_peak1: mean={s['h_peak1'].mean():.3f} std={s['h_peak1'].std():.3f} "
          f"min={s['h_peak1'].min():.2f} max={s['h_peak1'].max():.2f}")
    print(f"  h_peak2: mean={s['h_peak2'].mean():.3f} std={s['h_peak2'].std():.3f} "
          f"min={s['h_peak2'].min():.2f} max={s['h_peak2'].max():.2f}")
    print(f"  max(h1,h2): mean={hi_pk.mean():.3f} std={hi_pk.std():.3f}")
    print(f"  t_peak1_h: mean={s['t_peak1_h'].mean():.3f} std={s['t_peak1_h'].std():.3f}")
    print(f"  t_peak2_h: mean={s['t_peak2_h'].mean():.3f} std={s['t_peak2_h'].std():.3f}")
    print(f"  theo trạm (mean max(h1,h2)): "
          f"{s.groupby('station').apply(lambda g: np.fmax(g['h_peak1'], g['h_peak2']).mean()).round(3).to_dict()}")
    print()

# so sánh baseline
import csv
for name in ["B0", "B1", "B2", "B_harm", "B_KTTV"]:
    p = ROOT / "results" / "baselines" / name / "predictions_test.csv"
    if not p.exists():
        continue
    b = pd.read_csv(p)
    print(f"{name}: n={len(b)} h_q50 mean={b['h_q50'].mean():.3f} std={b['h_q50'].std():.3f}")
