#!/usr/bin/env python
"""Chấm điểm các file dự báo (định dạng `schemas/prediction/v1.schema.json`) bằng `phasegnn.evaluate`.

Cùng hàm chấm với `scripts/run_baselines.py` (MAE, RMSE, bias, NSE, KGE, MAE_dt, độ phủ 10–90 %)
→ số liệu của PhaseGNN/B3/B4/B5 so được trực tiếp với `results/baselines/summary.csv`.

**Cách chạy (trong Docker).**

    docker run --rm -v "$PWD:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
        python -X utf8 scripts/summarize_predictions.py --out results/E2/summary_test_full.csv \
            results/E2/phasegnn_d12/test_full/predictions_test.csv ...
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))

from phasegnn.data import default_paths, load_g5  # noqa: E402
from phasegnn.evaluate import evaluate_records  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chấm điểm file dự báo bằng phasegnn.evaluate")
    ap.add_argument("preds", nargs="+", help="file CSV dự báo (có cột `model`)")
    ap.add_argument("--out", required=True, help="CSV tổng hợp (một dòng cho mỗi mô hình)")
    ap.add_argument("--split", default="test")
    args = ap.parse_args(argv)

    g5 = load_g5(paths=default_paths())
    rows = []
    for p in args.preds:
        df = pd.read_csv(p)
        for name, sub in df.groupby("model"):
            recs = sub.where(sub.notna(), None).to_dict("records")
            met = evaluate_records(recs, g5, model=str(name), split=args.split)
            rows.append({"model": name, "source": p, "n_predictions": len(recs), **met.to_row()})
            print(f"[summary] {name}: MAE={met.mae:.4f} m  NSE={met.nse:.4f}  "
                  f"MAE_dt={met.mae_dt:.3f} h  phủ 10–90={met.to_row().get('coverage_10_90')}",
                  flush=True)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8")
    print(f"[summary] ghi {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
