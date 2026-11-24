#!/usr/bin/env python
"""Kiểm tra thứ tự phân vị đầu ra + giá trị scaler (tạm)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))

from phasegnn.data import (DYNAMIC_FEATURES, build_datasets, default_paths,  # noqa: E402
                           load_g5, load_graph)
from phasegnn.forecast import load_checkpoint  # noqa: E402
from phasegnn.train import build_station_subgraph, prepare_sample  # noqa: E402

model, tcfg, res = load_checkpoint(ROOT / "results" / "E2" / "phasegnn_long" / "checkpoint_seed0.pt")
df = load_g5(paths=default_paths(ROOT))
graph = load_graph(paths=default_paths(ROOT))
ds = build_datasets(df, t_in=tcfg.t_in, t_out=tcfg.t_out)

print("DYNAMIC_FEATURES =", DYNAMIC_FEATURES)
sc = ds["train"].scaler
print("\nscaler mean:")
for i, f in enumerate(ds["train"].feature_names):
    print(f"  {i:2d} {f:16s} mean={sc.mean[i]:10.4f} std={sc.std[i]:10.4f}")

# kiểm tra thứ tự phân vị ở đầu ra mô hình
sub = build_station_subgraph(graph, "PHU_AN")
samples = [s for s in ds["val"].samples if s.station == "PHU_AN"][:8]
sts = [prepare_sample(s, sub, model.cfg, tcfg.t_in, tcfg.t_out) for s in samples]
node_seq = torch.as_tensor(np.stack([x.node_seq for x in sts]), dtype=torch.float32)
ei = torch.as_tensor(sub.edge_index, dtype=torch.long)
ea = torch.as_tensor(sub.edge_attr, dtype=torch.float32)
ed = torch.as_tensor(sub.edge_dir, dtype=torch.long)
tau = torch.as_tensor(sub.tau_phys, dtype=torch.float32)
static = torch.as_tensor(sub.static, dtype=torch.float32)
with torch.no_grad():
    out = model.forward_batch(node_seq, static, ei, ea, ed, tau,
                               target_nodes=torch.as_tensor([int(sub.station_node)], dtype=torch.long))
    pk = out.peak_height.squeeze(1)     # [B, T, 2, Q]
    print("\npeak_height shape:", tuple(pk.shape))
    for q in range(pk.shape[-1]):
        print(f"  q-index {q}: mean={pk[..., q].mean():.4f} std={pk[..., q].std():.4f}")
    # so với ground truth cho ngày đầu
    print("\ntarget h_peak1/h_peak2 cho 3 mẫu đầu:")
    for i, s in enumerate(samples[:3]):
        print("  ", np.round(s.targets, 3).tolist())
    print("dự báo q(0),q(1),q(2) tại bước 0, đỉnh 0:")
    for i in range(3):
        print("  ", np.round(pk[i, 0, 0, :].numpy(), 4).tolist())
