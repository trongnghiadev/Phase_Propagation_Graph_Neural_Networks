#!/usr/bin/env python
"""Chay GĐ5: 5 mo hinh co so (B0, B_KTTV, B_harm, B1, B2) tren tap test.

**Cach chay.**

    python -X utf8 scripts/run_baselines.py --seeds 0 1 2 3 4 --out-dir results/baselines

**Ket qua.**

| Mo hinh | Nguon du bao | File du bao |
|---|---|---|
| `B0` | persistence theo chu ky + bien thien trieu thien van | `results/baselines/B0/predictions_test.csv` |
| `B_harm` | trieu thien van du tinh | `results/baselines/B_harm/predictions_test.csv` |
| `B1` | dieu hoa + hoi quy phan du (fit tren tap train) | `results/baselines/B1/predictions_test.csv` |
| `B2` | LSTM tai tung tram (cung cua so/chia tap voi PhaseGNN) | `results/baselines/B2/predictions_test.csv` |
| `B_KTTV` | du bao chinh thuc tu MongoDB `official_forecasts` | `results/baselines/B_KTTV/predictions_test.csv` |

Tat ca dung dinh dang chung `schemas/prediction/v1.schema.json` nen `pipelines/r/evaluation.R`
danh gia duoc ngay. Ban tong hop chi so: `results/baselines/summary.csv`.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))

from phasegnn.baselines import (ResidualRegressor, b1_forecast, b1_forecast_many,  # noqa: E402
                                fit_residual_regression, harmonic_forecast,
                                harmonic_forecast_many, kttv_forecast_from_collection,
                                kttv_forecast_many_from_collection,
                                persistence_forecast, persistence_forecast_many,
                                validate_prediction_records, write_predictions)
from phasegnn.data import (SPLITS, build_datasets, check_astro_provenance,  # noqa: E402
                           default_paths, load_astro_tide, load_g5)
from phasegnn.evaluate import evaluate_records, match_predictions, observed_daily  # noqa: E402
from phasegnn.model import configure_threads, seed_everything  # noqa: E402


def log(msg: str) -> None:
    print(msg, flush=True)


def _load_astro_by_station(repo: Path) -> dict[str, pd.DataFrame]:
    """Nạp triều thiên văn **theo trạm** (nếu có) → `{station: DataFrame[day, h1, h2, t1_h, t2_h]}`.

    Bảng `data/processed/features/astro_tide.csv` chỉ chứa triều ở Vũng Tàu (mực ~3.9 m),
    dùng cho mọi trạm sẽ tạo sai số hệ thống lớn. Vì vậy ưu tiên bảng theo trạm do E1 sinh
    (`results/E1/astro_tide_by_station.csv`), đúng như `pipelines/r/evaluation.R` làm.
    """
    p = repo / "results" / "E1" / "astro_tide_by_station.csv"
    if not p.exists():
        return {}
    # từ chối bảng triều fit trên cả tập test (rò rỉ) — xem phasegnn.data.check_astro_provenance
    check_astro_provenance(default_paths(repo)["astro_meta"])
    df = pd.read_csv(p)
    df["day"] = df["day"].astype(str).str.slice(0, 10)
    out: dict[str, pd.DataFrame] = {}
    for st, sub in df.groupby("station"):
        out[str(st)] = sub[["day", "h1", "h2", "t1_h", "t2_h"]].drop_duplicates("day")
    return out


def _add_days(day: str, n: int) -> str:
    from datetime import date, timedelta
    return (date.fromisoformat(day) + timedelta(days=n)).isoformat()


def _hours_to_iso(day: str, hour: float) -> str | None:
    """Giờ trong ngày (giờ VN) → ISO 8601 UTC — dùng chung `phasegnn.baselines._hours_to_iso`.

    (Bản chép cũ ở đây cộng thêm 24 giờ khi giờ VN < 7 → chuỗi sai kiểu `T41:04:00Z`, làm
    `t_peak` của B2/PhaseGNN không đọc được; sửa 24/11/2026.)
    """
    from phasegnn.baselines import _hours_to_iso as _impl
    return _impl(day, hour)


# ---------------------------------------------------------------------------
# B2 — LSTM tai tung tram
# ---------------------------------------------------------------------------
def train_b2(datasets, seeds: list[int], epochs: int, hidden: int,
              stations: list[str] | None = None) -> list[dict]:
    """Huan luyen B2 (LSTM tung tram) va sinh du bao tap test.

    Cung cua so (`T_in`/`T_out`) va cung chia tap voi PhaseGNN (dung bo du lieu
    `build_datasets`).
    """
    import torch

    from phasegnn.baselines import StationLSTM

    train_set, test_set = datasets["train"], datasets["test"]

    def _by_station(ds):
        out: dict[str, list] = {}
        for s in ds.samples:
            out.setdefault(s.station, []).append(s)
        return out

    tr_by, te_by = _by_station(train_set), _by_station(test_set)
    if stations:
        tr_by = {k: v for k, v in tr_by.items() if k in stations}
        te_by = {k: v for k, v in te_by.items() if k in stations}

    in_dim = len(train_set.feature_names)
    t_out = train_set.samples[0].targets.shape[0]
    records: list[dict] = []

    for seed in seeds:
        seed_everything(seed)
        t0 = time.time()
        for st in sorted(tr_by):
            tr = tr_by[st]
            x = np.stack([s.features for s in tr]).astype(np.float32)
            y = np.stack([s.targets for s in tr]).astype(np.float32)
            model = StationLSTM(in_dim=in_dim, hidden=hidden, t_out=t_out)
            model.fit(x, y, epochs=epochs, batch_size=64, lr=1e-3, seed=seed)

            te = te_by.get(st, [])
            if not te:
                continue
            xt = np.stack([s.features for s in te]).astype(np.float32)
            with torch.no_grad():
                out = model.forward(torch.as_tensor(xt))
            peak = out["peak"].numpy()          # [B, T_out, 2, Q]
            hour = out["hour"].numpy()          # [B, T_out, 2]
            for i, s in enumerate(te):
                for d in range(t_out):
                    target = _add_days(s.issue_date, d)
                    for pidx in range(2):
                        q = peak[i, d, pidx]
                        q10, q50, q90 = float(min(q)), float(q[1]), float(max(q))
                        if q10 > q50:
                            q10 = q50
                        if q90 < q50:
                            q90 = q50
                        records.append({
                            "model": "B2", "station": st,
                            "issued_date": s.issue_date, "target_date": target,
                            "lead_day": d + 1, "peak_idx": pidx,
                            "h_q10": q10, "h_q50": q50, "h_q90": q90,
                            "t_peak": _hours_to_iso(target, float(hour[i, d, pidx])),
                        })
        log(f"[B2] seed={seed} xong sau {time.time() - t0:.1f}s")
    return records


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Chay GĐ5 — mo hinh co so")
    ap.add_argument("--out-dir", default="results/baselines")
    ap.add_argument("--seeds", nargs="*", type=int, default=[0, 1, 2, 3, 4])
    ap.add_argument("--b2-epochs", type=int, default=8)
    ap.add_argument("--b2-hidden", type=int, default=48)
    ap.add_argument("--b2-stations", nargs="*", default=None,
                    help="gioi han tram cho B2 (mac dinh: 3 tram chinh)")
    ap.add_argument("--num-threads", type=int, default=1)
    ap.add_argument("--no-kttv", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    configure_threads(args.num_threads)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    paths = default_paths(Path(args.data_root) if args.data_root else None)
    log("[baselines] doc du lieu G5 ...")
    df = load_g5(paths=paths)
    astro = load_astro_tide(paths=paths)
    log(f"[baselines] G5: {len(df)} dong; astro: {len(astro)} dong")

    # Triều thiên văn THEO TRẠM (ưu tiên) — triều Vũng Tàu không dùng chung được
    # với các trạm khác (mực tham chiếu khác nhau ~2 m).
    astro_by_st = _load_astro_by_station(REPO)
    if astro_by_st:
        log(f"[baselines] dung trieu thien van theo tram cho: "
            f"{', '.join(sorted(astro_by_st))}")
    else:
        log("[baselines] KHONG co bang trieu thien van theo tram -> dung Vung Tau")

    lo, hi = SPLITS["test"]
    test_days = sorted(d for d in df["day"].unique().tolist() if lo <= d <= hi)
    log(f"[baselines] tap test: {len(test_days)} ngay phat ban tin ({lo} → {hi})")

    # --- B0 ---------------------------------------------------------------
    log("[baselines] B0 (persistence theo chu ky) ...")
    b0 = persistence_forecast_many(df, test_days, astro=astro, t_out=5)

    # --- B_harm -----------------------------------------------------------
    log("[baselines] B_harm (dieu hoa thien van) ...")
    if astro_by_st:
        bharm: list[dict] = []
        for st, a_st in astro_by_st.items():
            bharm.extend(harmonic_forecast_many(a_st, test_days, t_out=5,
                                                stations=[st]))
    else:
        bharm = harmonic_forecast_many(astro, test_days, t_out=5)

    # --- B1 ---------------------------------------------------------------
    log("[baselines] B1 (dieu hoa + hoi quy phan du) ...")
    b1: list[dict] = []
    b1_info: list[dict] = []
    for st in sorted(df["station"].unique().tolist()):
        a_st = astro_by_st.get(st, astro)
        try:
            model = fit_residual_regression(df, st, SPLITS["train"], a_st)
        except ValueError as exc:
            log(f"[B1] bo qua tram {st}: {exc}")
            continue
        b1_info.append({"station": st, "intercept": model.intercept,
                        "coefficients": dict(zip(model.feature_names,
                                                [float(c) for c in model.coefficients])),
                        "lags": model.lags})
        sub = df[df["station"] == st]
        days = sorted(d for d in sub["day"].unique().tolist() if lo <= d <= hi)
        b1.extend(b1_forecast_many(df, a_st, st, days, model, t_out=5))
    with open(out_dir / "b1_coefficients.json", "w", encoding="utf-8") as fh:
        json.dump(b1_info, fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")

    # --- B2 ---------------------------------------------------------------
    log("[baselines] B2 (LSTM tung tram) ...")
    datasets = build_datasets(df, t_in=14, t_out=5)
    b2_stations = args.b2_stations or ["PHU_AN", "NHA_BE", "THU_DAU_MOT"]
    b2 = train_b2(datasets, args.seeds, epochs=args.b2_epochs,
                  hidden=args.b2_hidden, stations=b2_stations)

    # --- B_KTTV -----------------------------------------------------------
    bktv: list[dict] = []
    if not args.no_kttv:
        log("[baselines] B_KTTV (du bao chinh thuc tu MongoDB) ...")
        try:
            from pymongo import MongoClient
            client = MongoClient("mongodb://localhost:27017")
            coll = client["vietnam_gis"]["official_forecasts"]
            bktv = kttv_forecast_many_from_collection(coll, test_days, t_out=5)
            client.close()
        except Exception as exc:  # noqa: BLE001
            log(f"[baselines] BO QUA B_KTTV: {exc}")
            bktv = []

    # --- ghi du bao -------------------------------------------------------
    groups = {"B0": b0, "B_harm": bharm, "B1": b1, "B2": b2, "B_KTTV": bktv}
    all_records: list[dict] = []
    summary_rows: list[dict] = []
    obs = observed_daily(df)

    for name, recs in groups.items():
        d_dir = out_dir / name
        d_dir.mkdir(parents=True, exist_ok=True)
        if not recs:
            log(f"[baselines] {name}: khong co ban ghi")
            continue
        errs = validate_prediction_records(recs)
        path = write_predictions(recs, d_dir, filename="predictions_test.csv")
        met = evaluate_records(recs, df, model=name, split="test")
        row = {"model": name, "n_predictions": len(recs),
               "n_schema_errors": len(errs), **met.to_row()}
        summary_rows.append(row)
        all_records.extend(recs)
        log(f"[baselines] {name}: {len(recs)} ban ghi -> {path}; "
            f"MAE={met.mae:.4f} m  RMSE={met.rmse:.4f}  NSE={met.nse:.4f}  "
            f"KGE={met.kge:.4f}  MAE_dt={met.mae_dt:.3f} h  ({len(errs)} loi schema)")

    # --- bang tong hop ----------------------------------------------------
    if summary_rows:
        cols = ["model", "n_predictions", "n_schema_errors", "n", "mae", "rmse", "bias",
                "nse", "kge", "mae_dt", "pearson_r", "coverage_10_90"]
        df_sum = pd.DataFrame(summary_rows)
        for c in cols:
            if c not in df_sum.columns:
                df_sum[c] = np.nan
        df_sum = df_sum[cols]
        df_sum.to_csv(out_dir / "summary.csv", index=False, encoding="utf-8")
        log(f"[baselines] bang tong hop -> {out_dir / 'summary.csv'}")

    # --- ghep mau theo chan troi / tram ------------------------------------
    if all_records:
        merged = match_predictions(all_records, obs)
        merged.to_csv(out_dir / "matched_samples_test.csv", index=False,
                       encoding="utf-8")
        lead_rows, st_rows = [], []
        for (model, lead), sub in merged.groupby(["model", "lead_day"]):
            met = evaluate_records(
                [r for r in all_records if r["model"] == model
                 and r["lead_day"] == lead], df, model=model, split="test")
            lead_rows.append({"model": model, "lead_day": int(lead), **met.to_row()})
        for (model, st), sub in merged.groupby(["model", "station"]):
            met = evaluate_records(
                [r for r in all_records if r["model"] == model
                 and r["station"] == st], df, model=model, split="test")
            st_rows.append({"model": model, "station": str(st), **met.to_row()})
        pd.DataFrame(lead_rows).to_csv(out_dir / "summary_by_lead.csv", index=False,
                                       encoding="utf-8")
        pd.DataFrame(st_rows).to_csv(out_dir / "summary_by_station.csv", index=False,
                                     encoding="utf-8")

    # --- file du bao gop (tien cho evaluation.R) ---------------------------
    if all_records:
        write_predictions(all_records, out_dir, filename="predictions_all_test.csv")

    print("\n=== KET QUA GĐ5 (tap test) ===")
    if summary_rows:
        print(pd.DataFrame(summary_rows)[
            ["model", "n_predictions", "mae", "rmse", "nse", "kge", "mae_dt"]
        ].to_string(index=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
