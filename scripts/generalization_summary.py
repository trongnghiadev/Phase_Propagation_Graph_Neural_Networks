#!/usr/bin/env python
"""E5 – Tổng quát hóa (đề cương mục 7.2): chấm trên trạm giữ riêng và trên năm triều cao nhất.

Chấm bằng `phasegnn.evaluate` (ghép đỉnh ngày, như `results/baselines/summary.csv`).

* **Giữ riêng trạm** (`--stations`): chỉ số của từng mô hình trên các trạm giữ riêng (gộp và
  theo từng trạm) + kiểm định ghép cặp (Wilcoxon, DM Newey–West trên chuỗi theo ngày — cùng
  hàm với `scripts/ablation_summary.py`) giữa mô hình giữ riêng (`--ref`) và từng mô hình khác.
* **Giữ riêng năm** (`--year`): chỉ số trên các ngày đích thuộc năm đó (mọi trạm).

**Cách chạy (trong Docker).**

    docker run --rm -v "$PWD:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
        python -X utf8 scripts/generalization_summary.py --out-dir results/E5 \
            --stations PHU_AN,TAN_AN --year 2025 --ref holdout \
            --pred holdout=results/E5/phasegnn_spread_holdout/test_full/predictions_test.csv \
            --pred B0=results/baselines/B0/predictions_test.csv ...

**Nhiều seed.** `--pred nhãn=a.csv,b.csv,c.csv` (nhiều đường dẫn cách nhau dấu phẩy, mỗi seed
một file): mô hình `nhãn` là **dự báo trung bình qua seed** (`ablation_summary.seed_mean`), dùng
cho bảng chính và kiểm định; chỉ số từng seed ghi thêm vào `holdout_station_by_seed.csv`
(nhãn `nhãn-s<i>`) và trung bình ± độ lệch chuẩn vào `holdout_station_mean_sd.csv`. Nếu mô hình
tham chiếu và mô hình khác cùng có ≥ 2 seed, kiểm định lặp lại theo từng cặp seed cùng chỉ số
(`pairing = seed<i>`; bảng chính `pairing = seedmean`).
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "packages" / "phasegnn" / "src"))
sys.path.insert(0, str(HERE))

from ablation_summary import holm, paired, seed_mean  # noqa: E402
from phasegnn.data import default_paths, load_g5  # noqa: E402
from phasegnn.evaluate import compute_metrics, match_predictions, observed_daily  # noqa: E402


def _metrics(df: pd.DataFrame, obs: pd.DataFrame, name: str) -> dict:
    if df.empty:  # mô hình không có dự báo ở trạm/năm này (vd. B_KTTV ở Tân An)
        return {"n": 0}
    m = match_predictions(df.where(df.notna(), None).to_dict("records"), obs)
    return compute_metrics(m, name).to_row()


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E5 – tổng quát hóa")
    ap.add_argument("--pred", action="append", required=True,
                    help="nhãn=đường_dẫn_CSV (lặp lại cho mỗi mô hình)")
    ap.add_argument("--stations", default="", help="trạm giữ riêng, cách nhau dấu phẩy")
    ap.add_argument("--year", default="", help="năm giữ riêng (YYYY)")
    ap.add_argument("--ref", default=None, help="nhãn mô hình tham chiếu cho kiểm định")
    ap.add_argument("--out-dir", default="results/E5")
    ap.add_argument("--dm-lag", type=int, default=4)
    args = ap.parse_args(argv)

    obs = observed_daily(load_g5(paths=default_paths()))
    preds: dict[str, pd.DataFrame] = {}
    per_seed: dict[str, list[pd.DataFrame]] = {}
    for item in args.pred:
        label, path = item.split("=", 1)
        paths = [p for p in path.split(",") if p]
        frames = [pd.read_csv(p) for p in paths]
        if len(frames) > 1:
            per_seed[label] = frames
            df = seed_mean(frames, label)
        else:
            df = frames[0]
        df["model"] = label
        preds[label] = df
    out = Path(args.out_dir)
    out.mkdir(parents=True, exist_ok=True)

    stations = [s.strip() for s in args.stations.split(",") if s.strip()]
    if stations and per_seed:
        srows = []
        for label, frames in per_seed.items():
            for i, df in enumerate(frames):
                sub = df[df["station"].isin(stations)]
                for st, part in [("+".join(stations), sub)] + [
                        (st, df[df["station"] == st]) for st in stations]:
                    srows.append({**_metrics(part, obs, f"{label}-s{i}"),
                                  "run": f"{label}-s{i}", "model": label, "seed": i,
                                  "station": st})
        bs = pd.DataFrame(srows)
        bs.to_csv(out / "holdout_station_by_seed.csv", index=False, encoding="utf-8")
        num = [c for c in bs.columns if c not in ("model", "run", "seed", "station", "split")
               and pd.api.types.is_numeric_dtype(bs[c])]
        agg = bs.groupby(["model", "station"], sort=False)[num].agg(["mean", "std"])
        agg.columns = [f"{a}_{b}" for a, b in agg.columns]
        agg = agg.reset_index()
        agg.insert(2, "n_seeds", agg["model"].map(lambda m: len(per_seed[m])))
        agg.to_csv(out / "holdout_station_mean_sd.csv", index=False, encoding="utf-8")
    if stations:
        rows, tests = [], []
        for label, df in preds.items():
            sub = df[df["station"].isin(stations)]
            rows.append({"model": label, "station": "+".join(stations),
                         **_metrics(sub, obs, label)})
            for st in stations:
                rows.append({"model": label, "station": st,
                             **_metrics(df[df["station"] == st], obs, label)})
        pd.DataFrame(rows).to_csv(out / "holdout_station_metrics.csv", index=False,
                                  encoding="utf-8")
        if args.ref:
            ref = preds[args.ref][preds[args.ref]["station"].isin(stations)]
            for label, df in preds.items():
                if label == args.ref:
                    continue
                other = df[df["station"].isin(stations)]
                if other.empty:
                    continue
                r = paired(ref, other, obs, args.dm_lag)
                tests.append({"pairing": "seedmean", "ref": args.ref, "other": label,
                              "stations": "+".join(sorted(other["station"].unique())), **r})
            for key in ("wilcoxon_p", "dm_p"):
                for t, a in zip(tests, holm([t[key] for t in tests])):
                    t[f"{key}_holm"] = a
            # lặp lại theo từng seed (chỉ cho cặp cùng có nhiều seed); Holm trong từng seed
            nref = len(per_seed.get(args.ref, []))
            for i in range(nref):
                block = []
                for label, frames in per_seed.items():
                    if label == args.ref or len(frames) <= i:
                        continue
                    a = per_seed[args.ref][i]
                    b = frames[i]
                    r = paired(a[a["station"].isin(stations)], b[b["station"].isin(stations)],
                               obs, args.dm_lag)
                    block.append({"pairing": f"seed{i}", "ref": args.ref, "other": label,
                                  "stations": "+".join(stations), **r})
                for key in ("wilcoxon_p", "dm_p"):
                    for t, a in zip(block, holm([t[key] for t in block])):
                        t[f"{key}_holm"] = a
                tests.extend(block)
            pd.DataFrame(tests).to_csv(out / "holdout_station_tests.csv", index=False,
                                       encoding="utf-8")
            for t in tests:
                print(f"[E5] {t['pairing']} {args.ref} vs {t['other']}: MAE {t['mae_ref']:.4f} / "
                      f"{t['mae_var']:.4f} DM p_holm={t['dm_p_holm']:.3g}", flush=True)

    if args.year:
        rows = []
        for label, df in preds.items():
            sub = df[df["target_date"].astype(str).str[:4] == args.year]
            rows.append({"model": label, "year": args.year, **_metrics(sub, obs, label)})
        pd.DataFrame(rows).to_csv(out / f"holdout_year_{args.year}_metrics.csv", index=False,
                                  encoding="utf-8")
    print(f"[E5] ghi {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
