"""Chạy toàn bộ thí nghiệm E2: huấn luyện PhaseGNN + các biến thể, xuất dự báo, đánh giá.

**Cách chạy (CPU).**

    python -X utf8 packages/phasegnn/scripts/run_experiments.py --out-dir results/E2

**Nội dung.**

1. Huấn luyện `phasegnn`, `B3`, `B4`, `B5a`, `B5b` trên tập `train`, dừng sớm theo
   MAE đỉnh ngày trên `val` (checklist 6.6). Mỗi biến thể chạy **nhiều seed**
   (`--seeds`), seed cố định để tái lập được.
2. Huấn luyện `B2` (LSTM từng trạm) — cùng cửa sổ `T_in = 14`, `T_out = 5` và cùng
   chia tập với PhaseGNN.
3. Fit `B1` (điều hòa + hồi quy phần dư theo xả hồ có độ trễ) cho từng trạm trên
   tập `train`.
4. Xuất dự báo theo `schemas/prediction/v1.schema.json` cho mọi mô hình.
5. Đánh giá trên tập `test`: MAE / RMSE / NSE / KGE / MAE_dt.

**Chia tập** (giữ nguyên theo G5, `schemas/features/v1.md`):

* train 2017-01-01 → 2022-12-31
* val   2023-01-01 → 2024-06-30
* test  2024-07-01 → 2026-11-23

**Ghi kết quả.**

    results/E2/metrics_test.csv          — một dòng cho mỗi (mô hình, seed)
    results/E2/metrics_by_lead.csv       — theo chân trời
    results/E2/metrics_by_station.csv    — theo trạm
    results/E2/predictions_test.csv      — tất cả dự báo (định dạng chung)
    results/E2/train_log_<model>_seed<N>.json
    results/E2/checkpoints/<model>_seed<N>.pt
    results/E2/run_config.json           — cấu hình + ngày chạy + môi trường
    results/E2/README.md                 — mô tả cấu hình và kết quả
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "packages" / "phasegnn" / "src"))

from phasegnn.baselines import (ResidualRegressor, b1_forecast, fit_residual_regression,  # noqa: E402
                                harmonic_forecast, persistence_forecast,
                                validate_prediction_records, write_predictions)
from phasegnn.data import (DEFAULT_T_IN, DEFAULT_T_OUT, SPLITS, build_datasets,  # noqa: E402
                           check_leakage, default_paths, load_astro_tide, load_g5,
                           load_g5_meta, load_graph)
from phasegnn.evaluate import (compute_metrics, evaluate_records, format_summary,  # noqa: E402
                               match_predictions, observed_daily)
from phasegnn.model import seed_everything  # noqa: E402
from phasegnn.train import (TrainConfig, TrainResult, VARIANTS, _make_model_cfg,  # noqa: E402
                            train_model)

#: Thư mục kết quả.
DEFAULT_OUT = REPO_ROOT / "results" / "E2"
DEFAULT_BASELINE_OUT = REPO_ROOT / "results" / "baselines"


def log(msg: str) -> None:
    print(f"[{datetime.now().strftime('%H:%M:%S')}] {msg}", flush=True)


def _rel(path: Path) -> str:
    """Đường dẫn tương đối so với repo (fallback sang đường dẫn tuyệt đối)."""
    try:
        return str(Path(path).resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


# ---------------------------------------------------------------------------
# Môi trường
# ---------------------------------------------------------------------------
def environment_info() -> dict:
    """Thông tin môi trường chạy (đưa vào `run_config.json`)."""
    info = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "machine": platform.machine(),
        "processor": platform.processor(),
        "cpu_count": os.cpu_count(),
    }
    try:
        import torch
        info["torch"] = torch.__version__
        info["torch_threads"] = torch.get_num_threads()
        info["cuda_available"] = bool(torch.cuda.is_available())
    except Exception:  # noqa: BLE001
        pass
    for mod in ("numpy", "pandas", "pyarrow", "mlflow"):
        try:
            info[mod] = __import__(mod).__version__
        except Exception:  # noqa: BLE001
            info[mod] = None
    try:
        import torch_geometric
        info["torch_geometric"] = torch_geometric.__version__
    except Exception:  # noqa: BLE001
        info["torch_geometric"] = None
    return info


# ---------------------------------------------------------------------------
# Dự báo từ checkpoint
# ---------------------------------------------------------------------------
def predict_with_model(model, tcfg, graph, dataset, model_name: str) -> list[dict]:
    """Sinh bản ghi dự báo cho một tập mẫu (dùng chung logic với `forecast.py`)."""
    from phasegnn.forecast import predict_split
    return predict_split(model, tcfg, graph, dataset, model_name=model_name)


# ---------------------------------------------------------------------------
# B2 — LSTM từng trạm
# ---------------------------------------------------------------------------
def train_b2(datasets, out_dir: Path, seeds: list[int], epochs: int, hidden: int,
             batch_size: int, lr: float, log_fn=log) -> tuple[list[dict], list[dict]]:
    """Huấn luyện B2 (LSTM từng trạm) và sinh dự báo trên tập test.

    Trả về `(records, logs)`.
    """
    from phasegnn.baselines import StationLSTM

    train_set = datasets["train"]
    test_set = datasets["test"]
    val_set = datasets.get("val")

    # Gom mẫu theo trạm (B2 không dùng đồ thị).
    def _by_station(ds):
        out: dict[str, list] = {}
        for s in ds.samples:
            out.setdefault(s.station, []).append(s)
        return out

    tr_by = _by_station(train_set)
    te_by = _by_station(test_set)
    va_by = _by_station(val_set) if val_set else {}

    in_dim = len(train_set.feature_names)
    records: list[dict] = []
    logs: list[dict] = []

    for seed in seeds:
        seed_everything(seed)
        t0 = time.time()
        for st in sorted(tr_by):
            tr = tr_by[st]
            x = np.stack([s.features for s in tr]).astype(np.float32)
            y = np.stack([s.targets for s in tr]).astype(np.float32)
            model = StationLSTM(in_dim=in_dim, hidden=hidden, t_out=train_set.samples[0].targets.shape[0])
            hist = model.fit(x, y, epochs=epochs, batch_size=batch_size, lr=lr, seed=seed)

            # dự báo tập test (giải mã tuần tự `t_out` ngày)
            te = te_by.get(st, [])
            if not te:
                continue
            xt = np.stack([s.features for s in te]).astype(np.float32)
            import torch
            t_out = train_set.samples[0].targets.shape[0]
            with torch.no_grad():
                out = model.forward(torch.as_tensor(xt), steps=t_out)
            peak = out["peak"].numpy()            # [B, T_out, 2, Q]
            hour = out["hour"].numpy()            # [B, T_out, 2]
            for i, s in enumerate(te):
                for d in range(t_out):
                    target = _add_days(s.issue_date, d)
                    for pidx in range(2):
                        q = peak[i, d, pidx]
                        q10, q50, q90 = float(min(q)), float(q[1]), float(max(q))
                        if q10 > q50:
                            q10, q50 = q50, q10
                        if q50 > q90:
                            q50, q90 = q90, q50
                        records.append({
                            "model": "B2", "station": st, "issued_date": s.issue_date,
                            "target_date": target, "lead_day": d + 1, "peak_idx": pidx,
                            "h_q10": q10, "h_q50": q50, "h_q90": q90,
                            "t_peak": _hours_to_iso(target, float(hour[i, d, pidx])),
                        })
        logs.append({"model": "B2", "seed": seed, "seconds": time.time() - t0,
                     "stations": sorted(tr_by.keys())})
        log(f"[B2] seed={seed} xong sau {time.time() - t0:.1f}s")
    return records, logs


def _add_days(day: str, n: int) -> str:
    from datetime import date, timedelta
    d = date.fromisoformat(day)
    return (d + timedelta(days=n)).isoformat()


def _hours_to_iso(day: str, hour: float) -> str | None:
    """Giờ trong ngày (giờ VN) → ISO 8601 UTC."""
    from datetime import date, timedelta
    if hour is None or not np.isfinite(hour):
        return None
    total = int(round(float(hour) * 60.0)) % (24 * 60)
    hh, mm = divmod(total, 60)
    y, m, d = (int(x) for x in day.split("-"))
    dt = date(y, m, d)
    shift, hh_utc = divmod(hh - 7, 24)
    if shift < 0:
        dt -= timedelta(days=1)
        hh_utc += 24
    elif shift > 0:
        dt += timedelta(days=1)
        hh_utc -= 24
    return f"{dt.isoformat()}T{hh_utc:02d}:{mm:02d}:00Z"


# ---------------------------------------------------------------------------
# B1 — điều hòa + hồi quy phần dư
# ---------------------------------------------------------------------------
def run_b1(df, astro, splits, out_dir: Path, log_fn=log) -> tuple[list[dict], list[dict]]:
    """Fit B1 trên tập train và dự báo cho các ngày phát bản tin trong tập test."""
    from phasegnn.baselines import b1_forecast_many

    records: list[dict] = []
    info: list[dict] = []
    train_range = splits["train"]
    lo, hi = splits["test"]
    for st in sorted(df["station"].unique().tolist()):
        try:
            model = fit_residual_regression(df, st, train_range, astro)
        except ValueError as exc:
            log(f"[B1] bỏ qua trạm {st}: {exc}")
            continue
        info.append({"station": st,
                     "intercept": model.intercept,
                     "coefficients": dict(zip(model.feature_names,
                                             [float(c) for c in model.coefficients])),
                     "lags": model.lags})
        # các ngày phát bản tin trong tập test (ngày dự báo thứ 1)
        sub = df[df["station"] == st]
        days = sorted(d for d in sub["day"].unique().tolist() if lo <= d <= hi)
        records.extend(b1_forecast_many(df, astro, st, days, model))
    return records, info


# ---------------------------------------------------------------------------
# B0 / B_harm — tham chiếu
# ---------------------------------------------------------------------------
def run_b0_bharm(df, astro, splits, log_fn=log) -> list[dict]:
    """B0 (persistence) và B_harm (điều hòa) trên các ngày phát bản tin của tập test."""
    from phasegnn.baselines import harmonic_forecast_many, persistence_forecast_many

    records: list[dict] = []
    lo, hi = splits["test"]
    days = sorted(d for d in df["day"].unique().tolist() if lo <= d <= hi)
    records.extend(persistence_forecast_many(df, days, astro=astro, t_out=DEFAULT_T_OUT))
    records.extend(harmonic_forecast_many(astro, days, t_out=DEFAULT_T_OUT))
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chạy thí nghiệm E2 (PhaseGNN + biến thể)")
    ap.add_argument("--out-dir", default=str(DEFAULT_OUT))
    ap.add_argument("--baseline-out-dir", default=str(DEFAULT_BASELINE_OUT))
    ap.add_argument("--seeds", default="0,1,2", help="danh sách seed (phân tách bằng dấu phẩy)")
    ap.add_argument("--variants", default="phasegnn,B3,B4,B5a,B5b",
                    help="danh sách biến thể cần huấn luyện")
    ap.add_argument("--epochs", type=int, default=12)
    ap.add_argument("--patience", type=int, default=4)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--hidden-dim", type=int, default=32)
    ap.add_argument("--num-gnn-layers", type=int, default=2)
    ap.add_argument("--delta-h", type=float, default=12.42,
                    help="bước thời gian nội bộ Δ (giờ); 12.42 = nửa chu kỳ M2")
    ap.add_argument("--t-in", type=int, default=DEFAULT_T_IN)
    ap.add_argument("--t-out", type=int, default=DEFAULT_T_OUT)
    ap.add_argument("--max-train-samples", type=int, default=None)
    ap.add_argument("--max-val-samples", type=int, default=None)
    ap.add_argument("--b2-epochs", type=int, default=8)
    ap.add_argument("--b2-hidden", type=int, default=48)
    ap.add_argument("--skip-b2", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    seeds = [int(s) for s in args.seeds.split(",") if s.strip()]
    variants = [v.strip() for v in args.variants.split(",") if v.strip()]
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "checkpoints").mkdir(exist_ok=True)
    baseline_dir = Path(args.baseline_out_dir)
    baseline_dir.mkdir(parents=True, exist_ok=True)

    run_started = datetime.now(timezone.utc)
    log(f"bắt đầu chạy; seeds={seeds}; biến thể={variants}")
    log(f"môi trường: {environment_info()}")

    # --- nạp dữ liệu -------------------------------------------------------
    paths = default_paths(Path(args.data_root) if args.data_root else None)
    t0 = time.time()
    df = load_g5(paths=paths)
    graph = load_graph(paths=paths)
    meta = load_g5_meta(paths=paths)
    astro = load_astro_tide(paths=paths)
    log(f"G5: {len(df)} dòng, {df['station'].nunique()} trạm; "
        f"đồ thị: {graph.num_nodes} nút / {graph.num_edges} cạnh "
        f"({time.time() - t0:.1f}s)")

    datasets = build_datasets(df, t_in=args.t_in, t_out=args.t_out)
    split_info = {}
    for name, ds in datasets.items():
        leaks = check_leakage(ds.samples)
        days = sorted({s.issue_date for s in ds.samples})
        split_info[name] = {
            "n_samples": len(ds), "n_issue_dates": len(days),
            "issue_date_min": days[0] if days else None,
            "issue_date_max": days[-1] if days else None,
            "n_leakage": len(leaks),
        }
        log(f"tập {name}: {len(ds)} mẫu, {len(days)} ngày phát bản tin "
            f"({days[0] if days else '-'} → {days[-1] if days else '-'}), rò rỉ: {len(leaks)}")
        if leaks:
            log(f"CẢNH BÁO rò rỉ: {leaks[:3]}")

    # --- huấn luyện các biến thể ------------------------------------------
    import torch

    all_records: list[dict] = []
    train_logs: list[dict] = []
    per_model_metrics: list[dict] = []

    for variant in variants:
        if variant not in VARIANTS:
            log(f"[{variant}] BIẾT THỂ KHÔNG HỢP LỆ — bỏ qua")
            continue
        for seed in seeds:
            tcfg = TrainConfig(
                variant=variant, epochs=args.epochs, batch_size=args.batch_size,
                lr=args.lr, seed=seed, t_in=args.t_in, t_out=args.t_out,
                hidden_dim=args.hidden_dim, num_gnn_layers=args.num_gnn_layers,
                delta_h=args.delta_h, patience=args.patience,
                max_train_samples=args.max_train_samples,
                max_val_samples=args.max_val_samples,
            )
            log(f"=== [{variant}] seed={seed} ===")
            t0 = time.time()
            model, result = train_model(datasets, graph, tcfg, log=log)
            train_seconds = time.time() - t0
            result.data_snapshot = meta.get("data_snapshot", {})
            log(f"[{variant}] seed={seed} xong sau {train_seconds:.1f}s; "
                f"val MAE đỉnh = {result.best_val_mae:.4f} m "
                f"(epoch {result.best_epoch})")

            # lưu checkpoint
            ck_path = out_dir / "checkpoints" / f"{variant}_seed{seed}.pt"
            torch.save({
                "model_state": model.state_dict(),
                "config": _make_model_cfg(tcfg).to_dict(),
                "train_config": tcfg.__dict__,
                "dims": {"in_dim": len(datasets["train"].feature_names),
                         "static_dim": graph.static_attr.shape[1],
                         "edge_dim": graph.edge_attr.shape[1]},
                "result": result.to_dict(),
            }, ck_path)

            # lưu log huấn luyện
            log_path = out_dir / f"train_log_{variant}_seed{seed}.json"
            with open(log_path, "w", encoding="utf-8") as fh:
                json.dump(result.to_dict(), fh, ensure_ascii=False, indent=1, default=str)
                fh.write("\n")
            train_logs.append({
                "variant": variant, "seed": seed, "seconds": train_seconds,
                "best_val_mae": result.best_val_mae, "best_epoch": result.best_epoch,
                "epochs_run": len(result.history),
                "checkpoint": _rel(ck_path),
            })

            # dự báo tập test
            model_name = f"{variant}-s{seed}"
            recs = predict_with_model(model, tcfg, graph, datasets["test"], model_name)
            errs = validate_prediction_records(recs)
            log(f"[{variant}] seed={seed}: {len(recs)} bản ghi dự báo; "
                f"{len(errs)} bản ghi không hợp lệ schema")
            if errs:
                log(f"   ví dụ: {errs[0]}")
            all_records.extend(recs)

            # đánh giá riêng từng seed
            met = evaluate_records(recs, df, model=model_name, split="test")
            per_model_metrics.append({"variant": variant, "seed": seed,
                                       "model": model_name, **met.to_row()})
            log(f"[{variant}] seed={seed} test: MAE={met.mae:.4f} RMSE={met.rmse:.4f} "
                f"NSE={met.nse:.4f} KGE={met.kge:.4f} MAE_dt={met.mae_dt:.4f} "
                f"(n={met.n})")

    # --- B2 -----------------------------------------------------------------
    if not args.skip_b2:
        log("=== [B2] LSTM từng trạm ===")
        b2_records, b2_logs = train_b2(datasets, out_dir, seeds, epochs=args.b2_epochs,
                                       hidden=args.b2_hidden, batch_size=64,
                                       lr=args.lr, log_fn=log)
        all_records.extend(b2_records)
        train_logs.extend(b2_logs)
        if b2_records:
            met = evaluate_records(b2_records, df, model="B2", split="test")
            per_model_metrics.append({"variant": "B2", "seed": None, "model": "B2",
                                       **met.to_row()})
            log(f"[B2] test: MAE={met.mae:.4f} RMSE={met.rmse:.4f} NSE={met.nse:.4f} "
                f"KGE={met.kge:.4f} MAE_dt={met.mae_dt:.4f} (n={met.n})")

    # --- B1 / B0 / B_harm ---------------------------------------------------
    log("=== [B1] điều hòa + hồi quy phần dư ===")
    b1_records, b1_info = run_b1(df, astro, SPLITS, out_dir, log_fn=log)
    all_records.extend(b1_records)
    with open(out_dir / "b1_coefficients.json", "w", encoding="utf-8") as fh:
        json.dump(b1_info, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")
    if b1_records:
        met = evaluate_records(b1_records, df, model="B1", split="test")
        per_model_metrics.append({"variant": "B1", "seed": None, "model": "B1",
                                   **met.to_row()})
        log(f"[B1] test: MAE={met.mae:.4f} RMSE={met.rmse:.4f} NSE={met.nse:.4f} "
            f"KGE={met.kge:.4f} MAE_dt={met.mae_dt:.4f} (n={met.n})")

    log("=== [B0/B_harm] mô hình tham chiếu ===")
    ref_records = run_b0_bharm(df, astro, SPLITS, log_fn=log)
    all_records.extend(ref_records)
    for name in ("B0", "B_harm"):
        sub = [r for r in ref_records if r["model"] == name]
        if sub:
            met = evaluate_records(sub, df, model=name, split="test")
            per_model_metrics.append({"variant": name, "seed": None, "model": name,
                                       **met.to_row()})
            log(f"[{name}] test: MAE={met.mae:.4f} RMSE={met.rmse:.4f} NSE={met.nse:.4f} "
                f"KGE={met.kge:.4f} MAE_dt={met.mae_dt:.4f} (n={met.n})")

    # --- ghi kết quả -------------------------------------------------------
    # dự báo (tất cả mô hình, định dạng chung)
    pred_path = write_predictions(all_records, out_dir, filename="predictions_test.csv")
    log(f"ghi {len(all_records)} bản ghi dự báo → {pred_path}")
    errs = validate_prediction_records(all_records)
    log(f"kiểm tra schema: {len(errs)} bản ghi không hợp lệ")

    # dự báo riêng cho từng mô hình cơ sở (results/baselines/)
    for name in ("B0", "B_harm", "B1", "B2"):
        sub = [r for r in all_records if r["model"] == name]
        if sub:
            p = write_predictions(sub, baseline_dir, filename=f"predictions_{name}_test.csv")
            log(f"ghi dự báo {name} → {p}")

    # chỉ số tổng hợp (theo biến thể, gộp seed)
    met_rows = []
    for variant in list(dict.fromkeys(r["variant"] for r in per_model_metrics)):
        rows = [r for r in per_model_metrics if r["variant"] == variant]
        arr = {k: np.array([r[k] for r in rows], dtype=float)
               for k in ("mae", "rmse", "nse", "kge", "mae_dt")}
        met_rows.append({
            "variant": variant, "n_seeds": len(rows),
            "n": int(np.mean([r["n"] for r in rows])),
            "mae": float(np.nanmean(arr["mae"])),
            "mae_std": float(np.nanstd(arr["mae"])),
            "rmse": float(np.nanmean(arr["rmse"])),
            "rmse_std": float(np.nanstd(arr["rmse"])),
            "nse": float(np.nanmean(arr["nse"])),
            "nse_std": float(np.nanstd(arr["nse"])),
            "kge": float(np.nanmean(arr["kge"])),
            "kge_std": float(np.nanstd(arr["kge"])),
            "mae_dt": float(np.nanmean(arr["mae_dt"])),
            "mae_dt_std": float(np.nanstd(arr["mae_dt"])),
        })
    metrics_df = pd.DataFrame(met_rows)
    metrics_df.to_csv(out_dir / "metrics_test.csv", index=False, encoding="utf-8")
    per_model_metrics and pd.DataFrame(per_model_metrics).to_csv(
        out_dir / "metrics_test_by_seed.csv", index=False, encoding="utf-8")

    # theo chân trời và theo trạm
    obs = observed_daily(df)
    merged_all = match_predictions(all_records, obs)
    lead_rows, station_rows = [], []
    for (model, lead), sub in merged_all.groupby(["model", "lead_day"]):
        met = compute_metrics(sub, str(model), "test")
        lead_rows.append({"model": model, "lead_day": int(lead), **met.to_row()})
    for (model, st), sub in merged_all.groupby(["model", "station"]):
        met = compute_metrics(sub, str(model), "test")
        station_rows.append({"model": model, "station": str(st), **met.to_row()})
    pd.DataFrame(lead_rows).to_csv(out_dir / "metrics_by_lead.csv", index=False,
                                    encoding="utf-8")
    pd.DataFrame(station_rows).to_csv(out_dir / "metrics_by_station.csv", index=False,
                                       encoding="utf-8")
    merged_all.to_csv(out_dir / "matched_samples_test.csv", index=False, encoding="utf-8")

    # cấu hình + ngày chạy
    run_config = {
        "run_started_utc": run_started.isoformat(),
        "run_finished_utc": datetime.now(timezone.utc).isoformat(),
        "environment": environment_info(),
        "seeds": seeds,
        "variants": variants,
        "args": vars(args),
        "splits": {k: list(v) for k, v in SPLITS.items()},
        "split_info": split_info,
        "train_logs": train_logs,
        "data_snapshot": meta.get("data_snapshot", {}),
        "n_prediction_records": len(all_records),
        "n_schema_errors": len(errs),
    }
    with open(out_dir / "run_config.json", "w", encoding="utf-8") as fh:
        json.dump(run_config, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")

    # --- báo cáo cuối -------------------------------------------------------
    print("\n=== KẾT QUẢ TẬP TEST ===")
    print(metrics_df.to_string(index=False))
    log(f"kết thúc. Kết quả tại {out_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
