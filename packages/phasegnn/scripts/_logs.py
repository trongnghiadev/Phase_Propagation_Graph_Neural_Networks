#!/usr/bin/env python
"""Tóm tắt train_log_seed0.json của các lần chạy trong results/E2."""
from __future__ import annotations

import glob
import json
import os

for d in sorted(glob.glob("results/E2/*/train_log_seed0.json")):
    log = json.load(open(d, encoding="utf-8"))
    cfg = log.get("config", {})
    name = os.path.dirname(d)
    print(f"{name:30s} variant={log.get('variant'):10s} "
          f"best_val_mae={log.get('best_val_mae')!r} best_epoch={log.get('best_epoch')}")
    print(f"    cfg: epochs={cfg.get('epochs')} seed={cfg.get('seed')} "
          f"max_train={cfg.get('max_train_samples')} max_val={cfg.get('max_val_samples')} "
          f"delta_h={cfg.get('delta_h')} t_in={cfg.get('t_in')} t_out={cfg.get('t_out')}")
    h = log.get("history", [])
    if h:
        v = h[-1]["val"]
        print("    last val: " + ", ".join(
            f"{k}={round(x, 4) if isinstance(x, float) else x}" for k, x in v.items()))
    print()
