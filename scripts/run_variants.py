#!/usr/bin/env python
"""Chay luatien cac bien the PhaseGNN (checklist 6.7): phasegnn, B3, B4, B5a, B5b.

**Cach chay.**

    python -X utf8 scripts/run_variants.py --epochs 30 --seeds 0 1 2 3 4 \
        --delta-h 12.42 --out-root results/E2

Moi bien the x 5 seed (checklist 6.7: "Moi bien the 5 seed, cung tap du lieu").
Ket qua ghi vao `results/E2/<variant>/` (checkpoint + log JSON) va bang tong hop
`results/E2/variants_summary.csv`.

**Luong chay.** Voi mot bien the, chay lien tie cac seed (tai su dung lai bo du lieu
da nap trong RAM); sau do sinh du bao tap test va ghi CSV theo dinh dang chung
(`schemas/prediction/v1.schema.json`) vao `results/E2/<variant>/predictions_test.csv`.
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))

from phasegnn.data import build_datasets, default_paths, load_g5, load_g5_meta, load_graph  # noqa: E402
from phasegnn.forecast import predict_split  # noqa: E402
from phasegnn.model import configure_threads, seed_everything  # noqa: E402
from phasegnn.train import TrainConfig, VARIANTS, train_model  # noqa: E402

#: Thu tu chay (mo hinh de xuat truoc).
VARIANT_ORDER = ("phasegnn", "B3", "B4", "B5a", "B5b")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chay cac bien the PhaseGNN")
    ap.add_argument("--variants", nargs="*", default=list(VARIANT_ORDER))
    ap.add_argument("--seeds", nargs="*", type=int, default=[0, 1, 2, 3, 4])
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--patience", type=int, default=10)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden-dim", type=int, default=64)
    ap.add_argument("--num-gnn-layers", type=int, default=2)
    ap.add_argument("--delta-h", type=float, default=12.42)
    ap.add_argument("--t-in", type=int, default=14)
    ap.add_argument("--t-out", type=int, default=5)
    ap.add_argument("--num-threads", type=int, default=1)
    ap.add_argument("--out-root", default="results/E2")
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--no-mlflow", action="store_true")
    ap.add_argument("--no-register", action="store_true")
    ap.add_argument("--max-train-samples", type=int, default=None)
    ap.add_argument("--max-val-samples", type=int, default=None)
    args = ap.parse_args(argv)

    configure_threads(args.num_threads)
    paths = default_paths(Path(args.data_root) if args.data_root else None)
    print(f"[run] doc du lieu tu {paths['g5']}", flush=True)
    df = load_g5(paths=paths)
    meta = load_g5_meta(paths=paths)
    graph = load_graph(paths=paths)
    datasets = build_datasets(df, t_in=args.t_in, t_out=args.t_out)
    print(f"[run] G5: {len(df)} dong; do thi: {graph.num_nodes} nut, {graph.num_edges} canh",
          flush=True)
    for name, ds in datasets.items():
        print(f"[run] tap {name}: {len(ds)} mau", flush=True)

    out_root = Path(args.out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    rows: list[dict] = []

    for variant in args.variants:
        if variant not in VARIANTS:
            print(f"[run] BO QUA bien the khong hop le: {variant}", flush=True)
            continue
        for seed in args.seeds:
            t0 = time.time()
            tcfg = TrainConfig(
                variant=variant, epochs=args.epochs, batch_size=args.batch_size,
                lr=args.lr, seed=seed, t_in=args.t_in, t_out=args.t_out,
                hidden_dim=args.hidden_dim, num_gnn_layers=args.num_gnn_layers,
                delta_h=args.delta_h, patience=args.patience,
                max_train_samples=args.max_train_samples,
                max_val_samples=args.max_val_samples,
                num_threads=args.num_threads,
                use_mlflow=not args.no_mlflow,
                register_to_db=not args.no_register,
            )
            model, result = train_model(datasets, graph, tcfg)
            result.data_snapshot = meta.get("data_snapshot", {})

            out_dir = out_root / variant
            out_dir.mkdir(parents=True, exist_ok=True)
            import torch

            ckpt = out_dir / f"checkpoint_seed{seed}.pt"
            torch.save({"model_state": model.state_dict(),
                        "config": {"hidden_dim": args.hidden_dim,
                                   "num_gnn_layers": args.num_gnn_layers,
                                   "delta_h": args.delta_h,
                                   "learn_tau": VARIANTS[variant]["learn_tau"],
                                   "directions": list(VARIANTS[variant]["directions"]),
                                   "model_kind": VARIANTS[variant]["model_kind"]},
                        "train_config": tcfg.__dict__,
                        "dims": result.dims,
                        "result": result.to_dict()},
                       ckpt)
            with open(out_dir / f"train_log_seed{seed}.json", "w", encoding="utf-8") as fh:
                json.dump(result.to_dict(), fh, ensure_ascii=False, indent=1, default=str)
                fh.write("\n")

            # --- dự báo tập test -------------------------------------------
            seed_everything(seed)
            records = predict_split(model, tcfg, graph, datasets["test"],
                                     model_name=f"{variant}-seed{seed}")
            from phasegnn.baselines import validate_prediction_records, write_predictions

            errors = validate_prediction_records(records)
            pred_path = out_dir / f"predictions_test_seed{seed}.csv"
            write_predictions(records, out_dir, filename=pred_path.name)
            if errors:
                print(f"[run]   {len(errors)} ban ghi khong hop le; vi du: {errors[0]}",
                      flush=True)

            val = result.history[-1].get("val") if result.history else {}
            rows.append({
                "variant": variant, "seed": seed,
                "val_mae_peak": result.best_val_mae,
                "val_mae_hour": (val or {}).get("mae_hour"),
                "val_pinball": (val or {}).get("pinball"),
                "best_epoch": result.best_epoch,
                "n_pred_test": len(records),
                "n_invalid": len(errors),
                "seconds": round(time.time() - t0, 1),
            })
            print(f"[run] {variant} seed {seed}: val MAE = {result.best_val_mae:.4f} m; "
                  f"{len(records)} du bao test ({time.time() - t0:.0f}s)", flush=True)

    # --- bảng tổng hợp -----------------------------------------------------
    summary = out_root / "variants_summary.csv"
    if rows:
        cols = ["variant", "seed", "val_mae_peak", "val_mae_hour", "val_pinball",
                "best_epoch", "n_pred_test", "n_invalid", "seconds"]
        with open(summary, "w", encoding="utf-8", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=cols, lineterminator="\n")
            w.writeheader()
            w.writerows(rows)
        print(f"[run] bang tong hop: {summary}", flush=True)

        # --- trung bình theo biến thể -------------------------------------
        print("\n[run] trung binh theo bien the (val MAE dinh, m):", flush=True)
        by_var: dict[str, list[float]] = {}
        for r in rows:
            by_var.setdefault(r["variant"], []).append(float(r["val_mae_peak"]))
        for v in args.variants:
            vals = by_var.get(v)
            if vals:
                mean = sum(vals) / len(vals)
                print(f"[run]   {v:10s} n={len(vals)}  mean={mean:.4f}  "
                      f"min={min(vals):.4f}  max={max(vals):.4f}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
