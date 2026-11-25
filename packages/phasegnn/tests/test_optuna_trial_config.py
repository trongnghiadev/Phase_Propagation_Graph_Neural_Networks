"""Lần thử Optuna (`train.trial_config`) giữ đúng mọi cờ CLI ngoài siêu tham số được tìm."""
from __future__ import annotations

import argparse

from phasegnn.train import trial_config


def _args(**kw) -> argparse.Namespace:
    base = dict(variant="phasegnn", epochs=3, batch_size=16, lr=1e-3, seed=1, t_in=14, t_out=5, hidden_dim=64,
                num_gnn_layers=2, delta_h=12.0, patience=4, max_train_samples=1200, max_val_samples=300,
                subsample="spread", subgraph_version=3, tau_mode="grid", num_threads=2, device="cpu",
                deterministic=True)
    base.update(kw)
    return argparse.Namespace(**base)


def test_trial_config_truyen_du_co_cli():
    cfg = trial_config(_args(), {"lr": 5e-4, "hidden_dim": 32}, ("BIEN_HOA", "NHA_BE"))
    assert cfg.tau_mode == "grid" and cfg.subgraph_version == 3
    assert cfg.subsample == "spread" and cfg.holdout_stations == ("BIEN_HOA", "NHA_BE")
    assert cfg.device == "cpu" and cfg.deterministic is True and cfg.num_threads == 2
    assert cfg.max_train_samples == 1200 and cfg.max_val_samples == 300 and cfg.delta_h == 12.0
    assert cfg.lr == 5e-4 and cfg.hidden_dim == 32, "siêu tham số lấy từ lần thử"
    assert cfg.use_mlflow is False and cfg.register_to_db is False


def test_trial_config_khong_lay_mac_dinh_cua_trainconfig():
    cfg = trial_config(_args(subgraph_version=1, tau_mode="phase", subsample="head"), {})
    assert (cfg.subgraph_version, cfg.tau_mode, cfg.subsample, cfg.holdout_stations) == (1, "phase", "head", ())
