#!/usr/bin/env python
"""Nạp checkpoint và kiểm tra phân phối đầu ra mô hình (tạm, dùng để điều tra)."""
from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))

from phasegnn.data import build_datasets, default_paths, load_g5, load_graph  # noqa: E402
from phasegnn.forecast import load_checkpoint  # noqa: E402
from phasegnn.train import build_station_subgraph, prepare_sample, _select_daily  # noqa: E402

CKPT = ROOT / "results" / "E2" / "phasegnn_long" / "checkpoint_seed0.pt"

model, tcfg, res = load_checkpoint(CKPT)
print("model cfg:", model.cfg)
print("train cfg delta_h:", tcfg.delta_h, "t_in:", tcfg.t_in, "t_out:", tcfg.t_out)

df = load_g5(paths=default_paths(ROOT))
graph = load_graph(paths=default_paths(ROOT))
ds = build_datasets(df, t_in=tcfg.t_in, t_out=tcfg.t_out)["val"]
print("val samples:", len(ds))

sub = build_station_subgraph(graph, "PHU_AN")
samples = [s for s in ds.samples if s.station == "PHU_AN"][:16]
sts = [prepare_sample(s, sub, model.cfg, tcfg.t_in, tcfg.t_out) for s in samples]
node_seq = torch.as_tensor(np.stack([x.node_seq for x in sts]), dtype=torch.float32)
print("node_seq shape:", tuple(node_seq.shape))
print("node_seq stats: mean=%.3f std=%.3f min=%.2f max=%.2f" % (
    node_seq.mean(), node_seq.std(), node_seq.min(), node_seq.max()))
# kiểm tra node_seq của node trạm
st_node = sub.station_node
print("station node seq stats: mean=%.3f std=%.3f min=%.2f max=%.2f" % (
    node_seq[:, st_node].mean(), node_seq[:, st_node].std(),
    node_seq[:, st_node].min(), node_seq[:, st_node].max()))

ei = torch.as_tensor(sub.edge_index, dtype=torch.long)
ea = torch.as_tensor(sub.edge_attr, dtype=torch.float32)
ed = torch.as_tensor(sub.edge_dir, dtype=torch.long)
tau = torch.as_tensor(sub.tau_phys, dtype=torch.float32)
static = torch.as_tensor(sub.static, dtype=torch.float32)

with torch.no_grad():
    out = model.forward_batch(node_seq, static, ei, ea, ed, tau,
                               target_nodes=torch.as_tensor([int(st_node)], dtype=torch.long))
    peak = out.peak_height.squeeze(1)
    hours = out.peak_hour.squeeze(1)
    print("raw peak_height (all steps) mean=%.3f std=%.3f min=%.2f max=%.2f" % (
        peak.mean(), peak.std(), peak.min(), peak.max()))
    print("raw peak_hour mean=%.3f std=%.3f min=%.2f max=%.2f" % (
        hours.mean(), hours.std(), hours.min(), hours.max()))
    pk, hr, pd_, hd_ = _select_daily(peak, hours, tcfg.t_out, model.cfg.delta_h)
    print("selected daily peak q50: mean=%.3f std=%.3f" % (pd_.mean(), pd_.std()))
    print("selected daily hour (max): mean=%.3f" % hd_.mean())
    # hour of the TALLER predicted peak (đúng cách)
    step_idx = pk[..., 1].argmax(dim=2)
    hr_tall = hr.gather(2, step_idx.unsqueeze(-1)).squeeze(-1)
    print("selected daily hour (của đỉnh cao nhất): mean=%.3f std=%.3f" % (
        hr_tall.mean(), hr_tall.std()))

    # so với thực đo
    obs_h = np.array([s.peak_heights for s in samples])
    obs_t = np.array([s.peak_hours for s in samples])
    print("\nobs peak_heights mean=%.3f std=%.3f" % (np.nanmean(obs_h), np.nanstd(obs_h)))
    print("obs peak_hours    mean=%.3f std=%.3f" % (np.nanmean(obs_t), np.nanstd(obs_t)))
    print("pred pd_ vs obs MAE = %.4f" % np.nanmean(np.abs(pd_.numpy() - obs_h)))
