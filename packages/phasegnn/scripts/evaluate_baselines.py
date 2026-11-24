#!/usr/bin/env python
"""Đánh giá các mô hình cơ sở (B0, B_harm, B1, B_KTTV) trên một tập ngày phát bản tin.

**Cách chạy (CPU, trong Docker).**

    docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
        python packages/phasegnn/scripts/evaluate_baselines.py \
            --split test --out-dir results/E2/baselines

**Đầu ra.**

* `baseline_metrics.csv` — MAE đỉnh (m), MAE giờ (h), số bản ghi, theo mô hình và
  theo trạm.
* `baseline_metrics_by_lead.csv` — MAE đỉnh theo `lead_day`.
* `predictions_<mô hình>.csv` — dự báo theo `schemas/prediction/v1.schema.json`.

**Ghi chú.** B1 cần hồi quy trên tập huấn luyện (không rò rỉ): ta chỉ dùng các ngày
thuộc tập `train` để fit, rồi dự báo cho các ngày thuộc tập `val`/`test`.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

# Cho phép chạy trực tiếp file này mà không cài package.
_SRC = Path(__file__).resolve().parents[1] / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from phasegnn.baselines import (b1_forecast_many, fit_residual_regression,  # noqa: E402
                               harmonic_forecast_many, persistence_forecast_many,
                               write_predictions)
from phasegnn.data import SPLITS, default_paths, load_astro_tide, load_g5  # noqa: E402

from scoring import build_truth, mae_hour as _mae_hour, mae_peak as _mae_peak  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Đánh giá mô hình cơ sở trên tập ngày")
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--t-out", type=int, default=5)
    ap.add_argument("--max-days", type=int, default=None,
                    help="giới hạn số ngày phát bản tin (để chạy nhanh)")
    ap.add_argument("--out-dir", default="results/E2/baselines")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    paths = default_paths(Path(args.data_root) if args.data_root else None)
    df = load_g5(paths=paths)
    astro = load_astro_tide(paths=paths)
    if astro is None:
        astro = (df[["day", "astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h"]]
                 .rename(columns={"astro_h1": "h1", "astro_h2": "h2",
                                  "astro_t1_h": "t1_h", "astro_t2_h": "t2_h"})
                 .drop_duplicates("day"))
    astro = astro.reset_index(drop=True)

    lo, hi = SPLITS[args.split]
    days = sorted(d for d in df["day"].unique() if lo <= d <= hi)
    if args.max_days:
        days = days[:args.max_days]
    print(f"[eval] tập {args.split}: {len(days)} ngày phát bản tin", flush=True)

    # --- quan trọc thực đo dùng để chấm ------------------------------------
    truth = build_truth(df, days)

    # --- B0: persistence + biến thiên thiên văn ----------------------------
    b0: list[dict] = persistence_forecast_many(df, days, astro=astro, t_out=args.t_out)

    # --- B_harm: triều thiên văn --------------------------------------------
    bh: list[dict] = harmonic_forecast_many(astro, days, t_out=args.t_out)

    # --- B1: điều hòa + hồi quy phần dư (fit trên tập train) ----------------
    tr_lo, tr_hi = SPLITS["train"]
    b1: list[dict] = []
    b1_err: list[str] = []
    for st in sorted(df["station"].unique()):
        try:
            reg = fit_residual_regression(df, st, (tr_lo, tr_hi), astro)
        except Exception as exc:  # noqa: BLE001
            b1_err.append(f"{st}: {exc}")
            continue
        b1.extend(b1_forecast_many(df, astro, st, days, reg, t_out=args.t_out))
    # --- chấm điểm -----------------------------------------------------------
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    rows: list[dict] = []
    per_station: list[dict] = []
    for name, recs in (("B0", b0), ("B_harm", bh), ("B1", b1)):
        rows.append({"model": name, "mae_peak_m": _mae_peak(recs, truth),
                     "mae_hour_h": _mae_hour(recs, truth), "n_records": len(recs)})
        for st in sorted({r["station"] for r in recs}):
            sub = [r for r in recs if r["station"] == st]
            per_station.append({"model": name, "station": st,
                                "mae_peak_m": _mae_peak(sub, truth),
                                "mae_hour_h": _mae_hour(sub, truth),
                                "n_records": len(sub)})
        write_predictions(recs, out_dir, filename=f"predictions_{name}.csv")
        print(f"[eval] {name}: MAE đỉnh = {rows[-1]['mae_peak_m']:.4f} m | "
              f"MAE giờ = {rows[-1]['mae_hour_h']:.3f} h | {len(recs)} bản ghi", flush=True)

    metrics = pd.DataFrame(rows)
    metrics.to_csv(out_dir / "baseline_metrics.csv", index=False, encoding="utf-8")
    pd.DataFrame(per_station).to_csv(out_dir / "baseline_metrics_by_station.csv",
                                     index=False, encoding="utf-8")

    # theo lead day
    lead_rows: list[dict] = []
    for name, recs in (("B0", b0), ("B_harm", bh), ("B1", b1)):
        for lead in range(1, args.t_out + 1):
            sub = [r for r in recs if r.get("lead_day") == lead]
            lead_rows.append({"model": name, "lead_day": lead,
                              "mae_peak_m": _mae_peak(sub, truth),
                              "n_records": len(sub)})
    pd.DataFrame(lead_rows).to_csv(out_dir / "baseline_metrics_by_lead.csv",
                                   index=False, encoding="utf-8")

    with open(out_dir / "baseline_eval_summary.json", "w", encoding="utf-8") as fh:
        json.dump({"split": args.split, "n_days": len(days),
                   "metrics": metrics.to_dict(orient="records"),
                   "b1_errors": b1_err}, fh, ensure_ascii=False, indent=1)
        fh.write("\n")

    print(f"[eval] ghi kết quả vào {out_dir}", flush=True)
    if b1_err:
        print(f"[eval] B1 bỏ qua {len(b1_err)} trạm: {b1_err[:3]}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
