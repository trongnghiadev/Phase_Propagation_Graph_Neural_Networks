#!/usr/bin/env python
"""Thống kê độ trễ pha học được (τ_eff = τ_phys · exp(δ)) của checkpoint PhaseGNN.

Chạy forward trên một tập con mẫu test (cách đều theo trạm) và gom `δ` của mọi cạnh
(`ModelOutput.delta`, ``[B, E]``: δ trung bình theo bước thời gian của từng cạnh, đã kẹp trong
`[DELTA_MIN, DELTA_MAX]` = [−1.5, 1.5]). Báo theo hướng cạnh (`down` = xuôi dòng, `up` =
ngược dòng — triều đi từ biển vào):

* `delta_mean`, `delta_abs_mean`, `delta_sd`: thống kê δ;
* `ratio_median` = trung vị exp(δ) (τ_eff / τ_phys); `ratio_p10`, `ratio_p90`;
* `frac_clamped`: tỉ lệ cạnh có |δ| ≥ 1.49 (chạm biên kẹp);
* `tau_phys_mean_h`, `tau_eff_mean_h`: τ trung bình (giờ) trên các cạnh có τ_phys > 0.

Biến thể không học τ (B4) hoặc không có cơ chế lan truyền pha (B3) bị bỏ qua.

**Cách chạy (trong Docker).**

    docker run --rm -v "$PWD:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \\
        python -X utf8 scripts/tau_summary.py --out results/E2/tau_spread.csv \\
            results/E2/phasegnn_spread/checkpoint_seed0.pt results/E2/phasegnn_abl/checkpoint_seed0.pt
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))

from phasegnn.data import build_datasets, default_paths, load_g5, load_graph  # noqa: E402
from phasegnn.forecast import load_checkpoint  # noqa: E402
from phasegnn.train import build_station_subgraph, prepare_sample, select_samples  # noqa: E402


def summarize_checkpoint(path: Path, df: pd.DataFrame, graph, n_samples: int) -> list[dict]:
    import torch

    model, tcfg, _ = load_checkpoint(path)
    cfg = model.cfg
    base = {"checkpoint": path.as_posix(), "variant": tcfg.variant, "seed": tcfg.seed,
            "subsample": getattr(tcfg, "subsample", "head"),
            "subgraph_version": int(getattr(tcfg, "subgraph_version", 1))}
    if cfg.model_kind != "phasegnn" or not cfg.learn_tau:
        return [{**base, "direction": "-", "note": "không học τ"}]
    ds = build_datasets(df, t_in=tcfg.t_in, t_out=tcfg.t_out,
                        holdout_stations=tuple(tcfg.holdout_stations or ()))["test"]
    samples = select_samples(ds.samples, n_samples, mode="spread")
    acc: dict[str, list[np.ndarray]] = {"down": [], "up": []}
    tau_acc: dict[str, list[np.ndarray]] = {"down": [], "up": []}
    by_st: dict[str, list] = {}
    for s in samples:
        by_st.setdefault(s.station, []).append(s)
    with torch.no_grad():
        for st, chunk in by_st.items():
            try:
                sub = build_station_subgraph(graph, st,
                                             version=int(getattr(tcfg, "subgraph_version", 1)))
            except KeyError:
                continue
            ed_np = np.asarray(sub.edge_dir)
            tau_np = np.asarray(sub.tau_phys, dtype=float)
            # thứ tự cạnh trong `delta` = [các cạnh down, các cạnh up] (model._forward_phase_batch)
            idx_down = np.where(ed_np == 0)[0]
            idx_up = np.where(ed_np != 0)[0]
            sts = [prepare_sample(s, sub, cfg, tcfg.t_in, tcfg.t_out) for s in chunk]
            node_seq = torch.as_tensor(np.stack([x.node_seq for x in sts]), dtype=torch.float32)
            out = model.forward_batch(
                node_seq, torch.as_tensor(sub.static, dtype=torch.float32),
                torch.as_tensor(sub.edge_index, dtype=torch.long),
                torch.as_tensor(sub.edge_attr, dtype=torch.float32),
                torch.as_tensor(sub.edge_dir, dtype=torch.long),
                torch.as_tensor(sub.tau_phys, dtype=torch.float32),
                target_nodes=torch.as_tensor([int(sub.station_node)], dtype=torch.long))
            if out.delta is None:
                continue
            d = out.delta.detach().cpu().numpy()            # [B, E]
            nd = len(idx_down)
            acc["down"].append(d[:, :nd].ravel())
            acc["up"].append(d[:, nd:].ravel())
            tau_acc["down"].append(np.tile(tau_np[idx_down], d.shape[0]))
            tau_acc["up"].append(np.tile(tau_np[idx_up], d.shape[0]))
    rows = []
    for k in ("down", "up"):
        if not acc[k]:
            continue
        d = np.concatenate(acc[k])
        tp = np.concatenate(tau_acc[k])
        if d.size == 0:
            rows.append({**base, "direction": k, "n_edge_samples": 0, "note": "không có cạnh"})
            continue
        r = np.exp(d)
        pos = tp > 0
        rows.append({**base, "direction": k, "n_edge_samples": int(d.size),
                     "delta_mean": float(d.mean()), "delta_abs_mean": float(np.abs(d).mean()),
                     "delta_sd": float(d.std()), "ratio_median": float(np.median(r)),
                     "ratio_p10": float(np.quantile(r, 0.1)),
                     "ratio_p90": float(np.quantile(r, 0.9)),
                     "frac_clamped": float((np.abs(d) >= 1.49).mean()),
                     "tau_phys_mean_h": float(tp[pos].mean()) if pos.any() else float("nan"),
                     "tau_eff_mean_h": float((tp * r)[pos].mean()) if pos.any() else float("nan")})
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Thống kê τ học được của PhaseGNN")
    ap.add_argument("checkpoints", nargs="+")
    ap.add_argument("--n-samples", type=int, default=450, help="số mẫu test (cách đều)")
    ap.add_argument("--out", default="results/E2/tau_summary.csv")
    args = ap.parse_args(argv)
    paths = default_paths()
    df = load_g5(paths=paths)
    graph = load_graph(paths=paths)
    rows = []
    for c in args.checkpoints:
        rr = summarize_checkpoint(Path(c), df, graph, args.n_samples)
        for r in rr:
            print(f"[tau] {c} {r.get('direction')}: |δ|={r.get('delta_abs_mean', float('nan')):.3f}"
                  f" ratio_med={r.get('ratio_median', float('nan')):.3f}", flush=True)
        rows.extend(rr)
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(out, index=False, encoding="utf-8")
    print(f"[tau] ghi {out}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
