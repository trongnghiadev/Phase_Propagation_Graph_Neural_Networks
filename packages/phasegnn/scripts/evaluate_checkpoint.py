#!/usr/bin/env python
"""Chấm điểm một checkpoint PhaseGNN trên tập `test` (hoặc `val`).

**Cách chạy (CPU, trong Docker).**

    docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
        python packages/phasegnn/scripts/evaluate_checkpoint.py \
            --checkpoint results/E2/phasegnn_d12/checkpoint_seed0.pt \
            --split test --max-days 300 --out-dir results/E2/phasegnn_d12

**Đầu ra.**

* `metrics_test.csv` — MAE đỉnh (m), MAE giờ (h), số bản ghi.
* `metrics_by_station.csv`, `metrics_by_lead.csv` — chi tiết.
* `predictions_test.csv` — dự báo theo `schemas/prediction/v1.schema.json`.

**Lưu ý về `--max-days`.** Mô hình chạy trên CPU nên mỗi mẫu tốn khoảng 0.2–0.5 s;
giới hạn số ngày phát bản tin giúp chạy nhanh khi cần xem số liệu sơ bộ. Muốn chấm
điểm đầy đủ thì bỏ tham số này (chậm hơn nhiều).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

# Cho phép chạy trực tiếp file này mà không cài package.
_HERE = Path(__file__).resolve().parent
_SRC = Path(__file__).resolve().parents[1] / "src"
for _p in (str(_SRC), str(_HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from phasegnn.baselines import write_predictions  # noqa: E402
from phasegnn.data import SPLITS, build_datasets, default_paths, load_g5, load_graph  # noqa: E402
from phasegnn.forecast import load_checkpoint, predict_split  # noqa: E402
from phasegnn.model import seed_everything  # noqa: E402

from scoring import build_truth, summarize  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chấm điểm checkpoint PhaseGNN")
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--model-name", default=None)
    ap.add_argument("--max-days", type=int, default=None,
                    help="giới hạn số ngày phát bản tin (chạy nhanh)")
    ap.add_argument("--stations", default="",
                    help="chỉ chấm các trạm này (cách nhau dấu phẩy; E5 giữ riêng trạm)")
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    paths = default_paths(Path(args.data_root) if args.data_root else None)
    df = load_g5(paths=paths)
    graph = load_graph(paths=paths)
    model, tcfg, _result = load_checkpoint(Path(args.checkpoint))
    seed_everything(tcfg.seed)

    datasets = build_datasets(df, t_in=tcfg.t_in, t_out=tcfg.t_out,
                              holdout_stations=tuple(tcfg.holdout_stations or ()))
    ds = datasets[args.split]

    lo, hi = SPLITS[args.split]
    days = sorted(d for d in df["day"].unique() if lo <= d <= hi)
    if args.max_days:
        days = set(days[:args.max_days])
    # chỉ giữ mẫu có ngày phát bản tin trong tập đã chọn
    ds.samples = [s for s in ds.samples if s.issue_date in days]
    keep_st = {x.strip() for x in args.stations.split(",") if x.strip()}
    if keep_st:
        ds.samples = [s for s in ds.samples if s.station in keep_st]
    print(f"[eval] tập {args.split}: {len(days)} ngày phát bản tin, "
          f"{len(ds.samples)} mẫu", flush=True)

    name = args.model_name or f"{tcfg.variant}-v1"
    records = predict_split(model, tcfg, graph, ds, model_name=name)
    print(f"[eval] {len(records)} bản ghi dự báo", flush=True)

    from phasegnn.baselines import validate_prediction_records
    errors = validate_prediction_records(records)
    if errors:
        print(f"[eval] CẢNH BÁO: {len(errors)} bản ghi không hợp lệ; ví dụ: {errors[0]}",
              flush=True)

    truth = build_truth(df, sorted(days))
    res = summarize(records, truth)

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.checkpoint).parent
    out_dir.mkdir(parents=True, exist_ok=True)
    write_predictions(records, out_dir, filename=f"predictions_{args.split}.csv")

    s = res["summary"]
    pd.DataFrame([s]).to_csv(out_dir / f"metrics_{args.split}.csv", index=False,
                             encoding="utf-8")
    pd.DataFrame(res["by_station"]).to_csv(out_dir / f"metrics_{args.split}_by_station.csv",
                                           index=False, encoding="utf-8")
    pd.DataFrame(res["by_lead"]).to_csv(out_dir / f"metrics_{args.split}_by_lead.csv",
                                        index=False, encoding="utf-8")
    with open(out_dir / f"metrics_{args.split}.json", "w", encoding="utf-8") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")

    print(f"[eval] {s['model']}: MAE đỉnh = {s['mae_peak_m']:.4f} m | "
          f"MAE giờ = {s['mae_hour_h']:.3f} h | {s['n_records']} bản ghi", flush=True)
    print(f"[eval] ghi kết quả vào {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
