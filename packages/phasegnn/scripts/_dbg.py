#!/usr/bin/env python
import sys
from pathlib import Path
import torch

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "packages" / "phasegnn" / "src"))
from phasegnn.train import _select_daily  # noqa: E402

delta_h, steps_per_day, n_days = 12.0, 2, 3
t = steps_per_day * n_days
peak = torch.zeros(t, 2, 3)
hours = torch.zeros(t, 2)
for day, step in enumerate([1, 3, 4]):
    peak[step, :, 1] = 1.0 + day
    hours[step, :] = 6.0 + day
pk, hr, pd_, hd_ = _select_daily(peak, hours, t_out=n_days, delta_h=delta_h)
print("single:", pk.shape, hr.shape, pd_.shape, hd_.shape)
print("pk[:,0,1]=", pk[:, 0, 1])
print("hr[:,0]=", hr[:, 0])
print("pd_=", pd_, "hd_=", hd_)

torch.manual_seed(7)
peak_b = torch.randn(3, t, 2, 3)
hours_b = torch.rand(3, t, 2) * 24.0
try:
    out = _select_daily(peak_b, hours_b, t_out=n_days, delta_h=delta_h)
    print("batch:", [o.shape for o in out])
except Exception as e:
    print("batch ERROR:", type(e).__name__, e)
