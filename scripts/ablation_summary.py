#!/usr/bin/env python
"""Tổng hợp ablation nhiều seed (E2/E3): trung bình ± độ lệch qua seed và kiểm định ghép cặp.

**Đầu vào.** Mỗi mô hình là một thư mục `results/E2/<model>_abl/` chứa
`test_full_seed<k>/predictions_test.csv` (do `evaluate_checkpoint.py` ghi).

**Đầu ra** (trong `--out-dir`):

* `ablation_by_seed.csv` — chỉ số `phasegnn.evaluate` (cùng hàm chấm với
  `results/baselines/summary.csv`) cho từng (mô hình, seed).
* `ablation_mean_sd.csv` — trung bình và độ lệch chuẩn (ddof = 1) qua seed.
* `ablation_tests.csv` — kiểm định ghép cặp mô hình tham chiếu (`--ref`) với từng biến thể:
  - `pairing = seedmean`: dự báo **trung bình qua seed** (q10/q50/q90 trung bình cộng; giờ đỉnh
    trung bình vòng 24 h) của mỗi mô hình — đây là kiểm định chính;
  - `pairing = seed<k>`: cùng kiểm định trên từng seed (xem chiều của khác biệt có ổn định không).
  Ghép theo `(station, target_date, lead_day)` (đỉnh ngày, như `phasegnn.evaluate`).
  Wilcoxon ký hạng trên `|e_ref| − |e_var|`; Diebold–Mariano trên chuỗi **trung bình theo ngày
  phát bản tin** của `|e_ref| − |e_var|` (gộp trạm và chân trời ⇒ một chuỗi thời gian), phương
  sai Newey–West với độ trễ `T_out − 1 = 4` (dự báo nhiều bước chồng lấn), phân phối chuẩn.
  Hiệu chỉnh Holm trong từng `pairing` (số so sánh = số biến thể).
  `mean_diff` < 0 ⇒ mô hình tham chiếu chính xác hơn.
* `<model>_abl/test_full_seedmean/predictions_test.csv` — dự báo trung bình qua seed, tên
  mô hình `<model>-abl` (đưa vào `pipelines/r/evaluation.R`).

**Cách chạy (trong Docker).**

    docker run --rm -v "$PWD:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
        python -X utf8 scripts/ablation_summary.py --root results/E2 --out-dir results/E2 \
            --models phasegnn B4 B5a B5b B3 --seeds 0 1 2
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))

from phasegnn.data import default_paths, load_g5  # noqa: E402
from phasegnn.evaluate import compute_metrics, match_predictions, observed_daily  # noqa: E402

KEY = ["station", "issued_date", "target_date", "lead_day", "peak_idx"]


def _hour_of(ts: pd.Series) -> np.ndarray:
    t = pd.to_datetime(ts, utc=True, errors="coerce")
    return (t.dt.hour + t.dt.minute / 60.0).to_numpy(dtype=float)


def seed_mean(frames: list[pd.DataFrame], name: str) -> pd.DataFrame:
    """Dự báo trung bình qua seed (ghép theo KEY, chỉ giữ khóa có ở mọi seed)."""
    base = None
    for k, df in enumerate(frames):
        d = df[KEY + ["h_q10", "h_q50", "h_q90", "t_peak"]].copy()
        d["hour_utc"] = _hour_of(d["t_peak"])
        d = d.rename(columns={c: f"{c}_{k}" for c in ("h_q10", "h_q50", "h_q90", "t_peak",
                                                       "hour_utc")})
        base = d if base is None else base.merge(d, on=KEY, how="inner")
    n = len(frames)
    out = base[KEY].copy()
    for c in ("h_q10", "h_q50", "h_q90"):
        out[c] = base[[f"{c}_{k}" for k in range(n)]].mean(axis=1)
    # bảo đảm q10 <= q50 <= q90 (trung bình của các bộ đơn điệu vẫn đơn điệu; giữ cho chắc)
    out["h_q10"] = np.minimum(out["h_q10"], out["h_q50"])
    out["h_q90"] = np.maximum(out["h_q90"], out["h_q50"])
    # giờ đỉnh: trung bình vòng 24 h trên giờ UTC; ngày UTC lấy theo seed đầu rồi hiệu chỉnh
    # khi trung bình vượt nửa đêm UTC so với seed đầu.
    ang = np.stack([base[f"hour_utc_{k}"].to_numpy() for k in range(n)], axis=1) * 2 * np.pi / 24
    mh = (np.degrees(np.arctan2(np.nanmean(np.sin(ang), axis=1),
                                np.nanmean(np.cos(ang), axis=1))) / 15.0) % 24.0
    t0 = pd.to_datetime(base["t_peak_0"], utc=True, errors="coerce")
    h0 = base["hour_utc_0"].to_numpy()
    shift = np.where(mh - h0 > 12, -1, np.where(h0 - mh > 12, 1, 0))
    day = (t0.dt.normalize() + pd.to_timedelta(shift, unit="D"))
    ts = day + pd.to_timedelta(np.round(mh * 60), unit="min")
    out["t_peak"] = ts.dt.strftime("%Y-%m-%dT%H:%M:00Z").where(t0.notna(), None)
    out.insert(0, "model", name)
    return out


def _abs_err(df: pd.DataFrame, obs: pd.DataFrame) -> pd.DataFrame:
    m = match_predictions(df.where(df.notna(), None).to_dict("records"), obs)
    m = m[np.isfinite(m["h_pred"]) & np.isfinite(m["h_obs"])]
    m = m.assign(ae=(m["h_pred"] - m["h_obs"]).abs())
    return m[["station", "issued_date", "target_date", "lead_day", "ae"]]


def dm_test(d_by_day: np.ndarray, lag: int = 4) -> tuple[float, float]:
    """Diebold–Mariano (Newey–West, `lag`) trên chuỗi chênh lệch tổn thất theo ngày."""
    from scipy.stats import norm

    d = np.asarray(d_by_day, dtype=float)
    d = d[np.isfinite(d)]
    n = d.size
    if n < 10:
        return float("nan"), float("nan")
    u = d - d.mean()
    var = float(u @ u) / n
    for k in range(1, min(lag, n - 1) + 1):
        w = 1.0 - k / (lag + 1.0)
        var += 2.0 * w * float(u[k:] @ u[:-k]) / n
    if var <= 0:
        return float("nan"), float("nan")
    t = d.mean() / np.sqrt(var / n)
    return float(t), float(2.0 * norm.sf(abs(t)))


def holm(p: list[float]) -> list[float]:
    p = np.asarray(p, dtype=float)
    order = np.argsort(p)
    m = len(p)
    adj = np.empty(m)
    run = 0.0
    for i, idx in enumerate(order):
        run = max(run, min(1.0, (m - i) * p[idx]))
        adj[idx] = run
    return adj.tolist()


def paired(ref: pd.DataFrame, var: pd.DataFrame, obs: pd.DataFrame, lag: int) -> dict:
    from scipy.stats import wilcoxon

    a = _abs_err(ref, obs)
    b = _abs_err(var, obs)
    m = a.merge(b, on=["station", "issued_date", "target_date", "lead_day"], suffixes=("_a", "_b"))
    d = (m["ae_a"] - m["ae_b"]).to_numpy()
    nz = d[d != 0]
    w_p = float(wilcoxon(nz).pvalue) if nz.size > 10 else float("nan")
    daily = m.assign(d=d).groupby("issued_date")["d"].mean().sort_index().to_numpy()
    t, p = dm_test(daily, lag=lag)
    return {"n_pairs": int(len(m)), "n_days": int(len(daily)),
            "mae_ref": float(m["ae_a"].mean()), "mae_var": float(m["ae_b"].mean()),
            "mean_diff": float(d.mean()), "median_ae_ref": float(m["ae_a"].median()),
            "median_ae_var": float(m["ae_b"].median()),
            "wilcoxon_p": w_p, "dm_t": t, "dm_p": p}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Tổng hợp ablation nhiều seed")
    ap.add_argument("--root", default="results/E2")
    ap.add_argument("--out-dir", default="results/E2")
    ap.add_argument("--models", nargs="+", default=["phasegnn", "B4", "B5a", "B5b", "B3"])
    ap.add_argument("--seeds", nargs="+", type=int, default=[0, 1, 2])
    ap.add_argument("--ref", default="phasegnn")
    ap.add_argument("--suffix", default="_abl")
    ap.add_argument("--dm-lag", type=int, default=4)
    args = ap.parse_args(argv)

    root, out_dir = Path(args.root), Path(args.out_dir)
    g5 = load_g5(paths=default_paths())
    obs = observed_daily(g5)

    preds: dict[str, dict] = {}
    rows = []
    for mdl in args.models:
        d = root / f"{mdl}{args.suffix}"
        preds[mdl] = {}
        for k in args.seeds:
            f = d / f"test_full_seed{k}" / "predictions_test.csv"
            df = pd.read_csv(f)
            preds[mdl][k] = df
            met = compute_metrics(match_predictions(df.where(df.notna(), None)
                                                    .to_dict("records"), obs),
                                  f"{mdl}{args.suffix}-s{k}")
            rows.append({**met.to_row(), "run": met.model, "model": mdl, "seed": k})
            print(f"[abl] {mdl} seed {k}: MAE={met.mae:.4f}", flush=True)
        sm = seed_mean([preds[mdl][k] for k in args.seeds], f"{mdl}{args.suffix.replace('_', '-')}")
        smd = d / "test_full_seedmean"
        smd.mkdir(parents=True, exist_ok=True)
        sm.to_csv(smd / "predictions_test.csv", index=False, encoding="utf-8")
        preds[mdl]["seedmean"] = sm
        met = compute_metrics(match_predictions(sm.where(sm.notna(), None).to_dict("records"),
                                                obs), f"{mdl}{args.suffix}-seedmean")
        rows.append({**met.to_row(), "run": met.model, "model": mdl, "seed": "seedmean"})

    by_seed = pd.DataFrame(rows)
    out_dir.mkdir(parents=True, exist_ok=True)
    by_seed.to_csv(out_dir / "ablation_by_seed.csv", index=False, encoding="utf-8")
    num = [c for c in by_seed.columns if c not in ("model", "seed", "split")
           and pd.api.types.is_numeric_dtype(by_seed[c])]
    ps = by_seed[by_seed["seed"] != "seedmean"]
    agg = ps.groupby("model", sort=False)[num].agg(["mean", "std"])
    agg.columns = [f"{a}_{b}" for a, b in agg.columns]
    agg = agg.reset_index()
    agg.insert(1, "n_seeds", len(args.seeds))
    agg.to_csv(out_dir / "ablation_mean_sd.csv", index=False, encoding="utf-8")

    tests = []
    for pairing in ["seedmean"] + [k for k in args.seeds]:
        block = []
        for mdl in args.models:
            if mdl == args.ref:
                continue
            r = paired(preds[args.ref][pairing], preds[mdl][pairing], obs, args.dm_lag)
            block.append({"pairing": "seedmean" if pairing == "seedmean" else f"seed{pairing}",
                          "ref": args.ref, "variant": mdl, **r})
        for key in ("wilcoxon_p", "dm_p"):
            adj = holm([b[key] for b in block])
            for b, a in zip(block, adj):
                b[f"{key}_holm"] = a
        tests.extend(block)
        for b in block:
            print(f"[abl] {b['pairing']} {args.ref} vs {b['variant']}: diff={b['mean_diff']:+.4f} "
                  f"DM t={b['dm_t']:.2f} p_holm={b['dm_p_holm']:.3g} "
                  f"W p_holm={b['wilcoxon_p_holm']:.3g}", flush=True)
    pd.DataFrame(tests).to_csv(out_dir / "ablation_tests.csv", index=False, encoding="utf-8")
    print(f"[abl] ghi {out_dir}/ablation_*.csv", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
