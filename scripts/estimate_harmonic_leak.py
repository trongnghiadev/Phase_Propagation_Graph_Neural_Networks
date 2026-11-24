"""Ước lượng ảnh hưởng rò rỉ của harmonic.R (bản port Python, KHÔNG thay pipeline R chính thức).

    docker run --rm -v "$PWD:/work:ro" -v "<thư mục ghi>:/out" -w /work phasegnn:local \\
        python -X utf8 scripts/estimate_harmonic_leak.py --out /out

Dùng khi chưa chạy lại được `pipelines/r/harmonic.R` (không có image Docker có R). Bản port tái lập bảng
`results/E1/astro_tide_by_station.csv` của R tới ~1e-5 m (in `port_vs_R.csv`).
  A. Chấm lại dự báo baseline đã có (results/baselines/*/predictions_test.csv) bằng evaluate mới.
  B. Port LSQ điều hòa của pipelines/r/R/harmonics.R (11 thành phần + xu hướng), fit
     (old) 2017-2026 như FIT_YEARS cũ, (new) chỉ train theo config/splits.json; sinh triều
     theo trạm (quét phút, bỏ 20' đầu/cuối, 2 cực đại lớn nhất); chấm B_harm và B1 trên test.
"""
from __future__ import annotations

import json
import sys
import time
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))
from phasegnn.baselines import (b1_forecast_many, fit_residual_regression,  # noqa: E402
                                harmonic_forecast_many)
from phasegnn.data import SPLITS, default_paths, load_g5  # noqa: E402
from phasegnn.evaluate import evaluate_records  # noqa: E402

PER = {"M2": 12.4206012, "S2": 12.0, "N2": 12.6583475, "K1": 23.9344695, "O1": 25.8193387,
       "P1": 24.0658877, "Q1": 26.86835, "M4": 6.2106015, "MS4": 6.103339, "M6": 4.1402011,
       "MK3": 8.1771399}
OM = np.array([2 * np.pi / (p * 3600) for p in PER.values()])
D0 = date(2000, 1, 1)


def X_of(t):
    t = np.asarray(t, dtype=np.float64)
    wt = np.outer(t, OM)
    return np.column_stack([np.ones_like(t), np.column_stack(
        [c for k in range(len(OM)) for c in (np.cos(wt[:, k]), np.sin(wt[:, k]))]), t / 1e6])


def points(df):
    rows = []
    for h, th, off in (("h_peak1", "t_peak1_h", "peak1_day_offset"), ("h_peak2", "t_peak2_h", "peak2_day_offset"),
                       ("h_low1", "t_low1_h", None), ("h_low2", "t_low2_h", None)):
        sub = df[["station", "day", h, th] + ([off] if off else [])].dropna(subset=[h, th])
        dd = pd.to_datetime(sub["day"]).dt.date
        days = np.array([(d - D0).days for d in dd], dtype=np.float64)
        o = sub[off].fillna(0).to_numpy(dtype=float) if off else 0.0
        t = (days + o) * 86400 + sub[th].to_numpy(dtype=float) * 3600
        rows.append(pd.DataFrame({"station": sub["station"].values, "day": sub["day"].values,
                                  "t": t, "y": sub[h].to_numpy(dtype=float)}))
    return pd.concat(rows, ignore_index=True)


def fit(pts):
    X = X_of(pts["t"].to_numpy())
    cf, *_ = np.linalg.lstsq(X, pts["y"].to_numpy(), rcond=None)
    return cf


def astro_days(cf, days):
    mins = np.arange(20, 1421)  # 20'..23:40 như R (ok = 20*60 .. 86400-20*60)
    out = []
    for chunk in np.array_split(np.array(days), max(1, len(days) // 200)):
        base = np.array([(date.fromisoformat(d) - D0).days * 86400.0 for d in chunk])
        t = (base[:, None] + mins[None, :] * 60.0).ravel()
        pv = (X_of(t) @ cf).reshape(len(chunk), len(mins))
        left = np.concatenate([np.full((len(chunk), 1), -np.inf), pv[:, :-1]], axis=1)
        right = np.concatenate([pv[:, 1:], np.full((len(chunk), 1), -np.inf)], axis=1)
        loc = (pv >= left) & (pv >= right)
        for i, d in enumerate(chunk):
            li = np.flatnonzero(loc[i])
            if li.size > 2:
                li = li[np.argsort(-pv[i, li], kind="stable")[:2]]
            li = np.sort(li)
            hh = mins[li] / 60.0
            out.append({"day": d, "h1": pv[i, li[0]], "h2": pv[i, li[-1]], "t1_h": hh[0], "t2_h": hh[-1]})
    return pd.DataFrame(out)


def score(df, astro_by_st, label):
    lo, hi = SPLITS["test"]
    test_days = sorted(d for d in df["day"].unique() if lo <= d <= hi)
    bh, b1 = [], []
    for st, a in astro_by_st.items():
        bh.extend(harmonic_forecast_many(a, test_days, t_out=5, stations=[st]))
        m = fit_residual_regression(df, st, SPLITS["train"], a)
        days = sorted(d for d in df[df["station"] == st]["day"].unique() if lo <= d <= hi)
        b1.extend(b1_forecast_many(df, a, st, days, m, t_out=5))
    rows = []
    for name, recs in (("B_harm", bh), ("B1", b1)):
        met = evaluate_records(recs, df, model=name, split="test")
        rows.append({"variant": label, "model": name, **met.to_row()})
    return rows


def main():
    import argparse
    global OUT
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True, help="thư mục ghi before_after.csv, port_vs_R.csv, port_fits.csv")
    OUT = Path(ap.parse_args().out)
    OUT.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    df = load_g5(paths=default_paths(REPO))
    res = []

    # ---- A. chấm lại dự báo đã có bằng evaluate mới --------------------------------
    for name in ("B0", "B_harm", "B1", "B2", "B_KTTV"):
        p = Path(f"{REPO}/results/baselines/{name}/predictions_test.csv")
        if not p.exists():
            continue
        pr = pd.read_csv(p, dtype={"issued_date": str, "target_date": str, "t_peak": str})
        pr = pr.where(pd.notna(pr), None)
        met = evaluate_records(pr.to_dict("records"), df, model=name, split="test")
        res.append({"variant": "A_rescore_existing", "model": name, **met.to_row()})
    print("A xong", time.time() - t0, flush=True)

    # ---- B. port điều hòa ----------------------------------------------------------
    pts = points(df)
    tr_lo, tr_hi = SPLITS["train"]
    days_all = [(date(2016, 12, 31) + timedelta(days=i)).isoformat()
                for i in range((date(2026, 11, 15) - date(2016, 12, 31)).days + 1)]
    old_by, new_by = {}, {}
    fits = []
    for st in sorted(pts["station"].unique()):
        p = pts[pts["station"] == st]
        p_old = p[(p["day"] >= "2017-01-01") & (p["day"] <= "2026-12-31")]
        p_new = p[(p["day"] >= tr_lo) & (p["day"] <= tr_hi)]
        assert p_new["day"].max() < SPLITS["test"][0]
        cf_o, cf_n = fit(p_old), fit(p_new)
        old_by[st] = astro_days(cf_o, days_all)
        new_by[st] = astro_days(cf_n, days_all)
        fits.append({"station": st, "n_old": len(p_old), "n_new": len(p_new),
                     "a0_old": cf_o[0], "a0_new": cf_n[0],
                     "M2_old": float(np.hypot(cf_o[1], cf_o[2])), "M2_new": float(np.hypot(cf_n[1], cf_n[2]))})
        print(st, "fit xong", time.time() - t0, flush=True)

    # kiểm chứng bản port: so với bảng R hiện có (fit 2017-2026)
    r = pd.read_csv(f"{REPO}/results/E1/astro_tide_by_station.csv", dtype={"day": str})
    val = []
    for st, a in old_by.items():
        m = r[r["station"] == st].merge(a, on="day", suffixes=("_r", "_py"))
        dt = np.abs(m["t1_h_r"] - m["t1_h_py"]) % 24
        val.append({"station": st, "n": len(m), "mae_h1_m": float(np.mean(np.abs(m["h1_r"] - m["h1_py"]))),
                    "mae_t1_h": float(np.mean(np.minimum(dt, 24 - dt)))})
    pd.DataFrame(val).to_csv(OUT / "port_vs_R.csv", index=False)
    pd.DataFrame(fits).to_csv(OUT / "port_fits.csv", index=False)

    r_by = {st: g[["day", "h1", "h2", "t1_h", "t2_h"]].drop_duplicates("day") for st, g in r.groupby("station")}
    res += score(df, r_by, "B_R_existing_fit2017_2026")
    res += score(df, old_by, "B_port_fit2017_2026")
    res += score(df, new_by, "B_port_fit_train_only")
    out = pd.DataFrame(res)
    out.to_csv(OUT / "before_after.csv", index=False)
    print(out[["variant", "model", "n", "mae", "rmse", "bias", "nse", "kge", "mae_dt"]].to_string(index=False))
    print(pd.DataFrame(val).to_string(index=False))
    print("tong", time.time() - t0)


if __name__ == "__main__":
    main()
