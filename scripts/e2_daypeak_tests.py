#!/usr/bin/env python
"""E2 – kiểm định ghép đỉnh ngày (9 trạm) cho mẫu `spread`: tạo lại các bảng mà `results/E2/README.md` trích.

Chỉ đọc dự báo đã có trong `results/` (không huấn luyện lại) và chuỗi quan trắc G5 (`phasegnn.data`). Chấm bằng
cùng hàm với `scripts/generalization_summary.py` / `scripts/ablation_summary.py` (`paired`: MAE ghép cặp,
Wilcoxon, DM Newey–West trên chuỗi theo ngày; Holm trong từng bảng). Mô hình nhiều seed → dự báo trung bình
qua seed (`seed_mean`).

* ``--set spread`` → `results/E2/daypeak_tests_spread.csv`: tham chiếu `phasegnn-spread` (v1, TB 3 seed) vs
  `phasegnn-abl` (TB 3 seed), `phasegnn-long` (seed 0), B0, B_harm, B_KTTV — Holm trên 5.
* ``--set v2`` → `results/E2/daypeak_tests_spread_v2.csv`: tham chiếu `phasegnn-spread-v2` (TB 3 seed) vs
  `phasegnn-spread` (v1), B0, B_harm, B_KTTV — Holm trên 4.
* ``--set v3`` → `results/E2/daypeak_tests_v3.csv`: tham chiếu `phasegnn-v3` (đồ thị con v3 + `tau_mode=phase`,
  TB 3 seed) vs `phasegnn-spread-v2`, `B5a-v3`, `B5b-v3`, B0, B_harm, B_KTTV — Holm trên 6.
* ``--set diag`` → `results/E2/daypeak_tests_diag.csv`: bảng chẩn đoán đồ thị × τ — tham chiếu
  `phasegnn-spread-v2` (v2 + grid, TB 3 seed) vs `phasegnn-v2phase`, `phasegnn-v3grid`, `phasegnn-v3` — **Holm trên 3**
  (họ = 3 cấu hình phasegnn so với v2 + grid; không gồm mô hình cơ sở). Cặp v3 vs v2 cũng có trong ``--set v3`` nhưng
  ở đó Holm trên 6 ⇒ cùng p thô, p_Holm khác.

Chạy (Docker, gốc repo):

    docker run --rm -v "$PWD:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
        python -X utf8 scripts/e2_daypeak_tests.py --set all
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))
sys.path.insert(0, str(HERE))

from ablation_summary import holm, paired, seed_mean  # noqa: E402

E2 = "results/E2"
BASE = "results/baselines"


def _seeds(run: str) -> list[str]:
    return [f"{E2}/{run}/test_full_seed{i}/predictions_test.csv" for i in range(3)]


SETS: dict[str, dict] = {
    "spread": {"out": f"{E2}/daypeak_tests_spread.csv", "ref": "phasegnn-spread",
               "preds": {"phasegnn-spread": _seeds("phasegnn_spread"),
                         "phasegnn-abl": _seeds("phasegnn_abl"),
                         "phasegnn-long": [f"{E2}/phasegnn_long/test_full/predictions_test.csv"],
                         "B0": [f"{BASE}/B0/predictions_test.csv"],
                         "B_harm": [f"{BASE}/B_harm/predictions_test.csv"],
                         "B_KTTV": [f"{BASE}/B_KTTV/predictions_test.csv"]}},
    "v2": {"out": f"{E2}/daypeak_tests_spread_v2.csv", "ref": "phasegnn-spread-v2",
           "preds": {"phasegnn-spread-v2": _seeds("phasegnn_spread_v2"),
                     "phasegnn-spread": _seeds("phasegnn_spread"),
                     "B0": [f"{BASE}/B0/predictions_test.csv"],
                     "B_harm": [f"{BASE}/B_harm/predictions_test.csv"],
                     "B_KTTV": [f"{BASE}/B_KTTV/predictions_test.csv"]}},
    "v3": {"out": f"{E2}/daypeak_tests_v3.csv", "ref": "phasegnn-v3",
           "preds": {"phasegnn-v3": _seeds("phasegnn_v3"),
                     "phasegnn-spread-v2": _seeds("phasegnn_spread_v2"),
                     "B5a-v3": _seeds("B5a_v3"),
                     "B5b-v3": _seeds("B5b_v3"),
                     "B0": [f"{BASE}/B0/predictions_test.csv"],
                     "B_harm": [f"{BASE}/B_harm/predictions_test.csv"],
                     "B_KTTV": [f"{BASE}/B_KTTV/predictions_test.csv"]}},
    "diag": {"out": f"{E2}/daypeak_tests_diag.csv", "ref": "phasegnn-spread-v2",
             "preds": {"phasegnn-spread-v2": _seeds("phasegnn_spread_v2"),
                       "phasegnn-v2phase": _seeds("phasegnn_v2phase"),
                       "phasegnn-v3grid": _seeds("phasegnn_v3grid"),
                       "phasegnn-v3": _seeds("phasegnn_v3")}},
}


def _load(label: str, paths: list[str]) -> pd.DataFrame:
    frames = [pd.read_csv(ROOT / p) for p in paths]
    df = seed_mean(frames, label) if len(frames) > 1 else frames[0]
    df["model"] = label
    return df


def run_set(name: str, obs: pd.DataFrame, dm_lag: int = 4) -> pd.DataFrame:
    cfg = SETS[name]
    preds = {k: _load(k, v) for k, v in cfg["preds"].items()}
    ref = preds[cfg["ref"]]
    stations = sorted(ref["station"].unique())
    ref = ref[ref["station"].isin(stations)]
    rows = []
    for label, df in preds.items():
        if label == cfg["ref"]:
            continue
        other = df[df["station"].isin(stations)]
        r = paired(ref, other, obs, dm_lag)
        rows.append({"set": name, "ref": cfg["ref"], "other": label, "n_seeds_ref": len(cfg["preds"][cfg["ref"]]),
                     "n_seeds_other": len(cfg["preds"][label]),
                     "stations": "+".join(sorted(other["station"].unique())), **r})
    for key in ("wilcoxon_p", "dm_p"):
        for t, a in zip(rows, holm([t[key] for t in rows])):
            t[f"{key}_holm"] = a
    out = pd.DataFrame(rows)
    out.to_csv(ROOT / cfg["out"], index=False, encoding="utf-8")
    for t in rows:
        print(f"[E2 {name}] {cfg['ref']} vs {t['other']}: n={t['n_pairs']} MAE {t['mae_ref']:.4f} / "
              f"{t['mae_var']:.4f} DM p_holm={t['dm_p_holm']:.3g}", flush=True)
    print(f"[E2] ghi {cfg['out']}", flush=True)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="E2 – kiểm định ghép đỉnh ngày (spread, v2, v3, diag)")
    ap.add_argument("--set", choices=["spread", "v2", "v3", "diag", "all"], default="all")
    ap.add_argument("--dm-lag", type=int, default=4)
    args = ap.parse_args(argv)
    from phasegnn.data import default_paths, load_g5  # noqa: PLC0415
    from phasegnn.evaluate import observed_daily  # noqa: PLC0415

    obs = observed_daily(load_g5(paths=default_paths()))
    for name in (["spread", "v2", "v3", "diag"] if args.set == "all" else [args.set]):
        run_set(name, obs, args.dm_lag)
    return 0


if __name__ == "__main__":
    sys.exit(main())
