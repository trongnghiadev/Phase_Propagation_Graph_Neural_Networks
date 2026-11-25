"""Kiểm thử `phasegnn.train.evaluate` theo lô (B ≥ 2).

Hồi quy cho lỗi `IndexError` khi tính pinball theo lô: `pinball_loss` trả về **vô hướng** nhưng
code cũ đánh chỉ số `[bi]` cho từng mẫu ⇒ lỗi ngay khi lô có từ 2 mẫu (các test cũ chỉ dùng B = 1).
Mô hình giả cho dự báo xác định theo từng mẫu để so kết quả theo lô với kết quả từng mẫu.
"""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from phasegnn import train as T  # noqa: E402
from phasegnn.losses import pinball_loss  # noqa: E402
from phasegnn.model import ModelConfig  # noqa: E402

T_OUT, DELTA_H = 2, 12.0
STEPS = int(T_OUT * 24 / DELTA_H)


class _FakeModel:
    """Dự báo phụ thuộc mẫu (qua `node_seq[b, 0, 0, 0]`), không phụ thuộc mẫu khác trong lô."""

    def eval(self):
        return self

    def forward_batch(self, node_seq, static, ei, ea, ed, tau, target_nodes=None):
        b = node_seq.shape[0]
        base = node_seq[:, 0, 0, 0].reshape(b, 1, 1, 1, 1)
        t = torch.arange(STEPS, dtype=torch.float32).reshape(1, 1, STEPS, 1, 1)
        k = torch.arange(2, dtype=torch.float32).reshape(1, 1, 1, 2, 1)
        q = torch.tensor([-0.2, 0.0, 0.2]).reshape(1, 1, 1, 1, 3)
        peak = base + 0.1 * t + 0.05 * k + q                              # [B, 1, T, 2, Q]
        hours = (torch.arange(STEPS) % 2 * 12.0 + 3.0).reshape(1, 1, STEPS, 1) \
            + torch.tensor([0.0, 5.0]).reshape(1, 1, 1, 2)
        return SimpleNamespace(peak_height=peak, peak_hour=hours.expand(b, 1, STEPS, 2))


def _sample(i: int, nan: bool = False):
    tgt = np.full((T_OUT, 2), np.nan) if nan else np.array([[1.0 + i, 1.2 + i], [0.9 + i, 1.4 + i]])
    hrs = np.array([[4.0, 9.0], [4.0, 9.0]])
    return SimpleNamespace(station="S", base=float(i), targets=tgt, target_hours=hrs,
                           peak_heights=np.nanmax(tgt, axis=1) if not nan else np.full(T_OUT, np.nan),
                           peak_hours=np.array([9.0, 9.0]))


def _prepare(sample, sub, cfg, t_in, t_out):
    node_seq = np.zeros((1, 2, 1), dtype=np.float32)
    node_seq[0, 0, 0] = sample.base
    return T.SampleTensors(node_seq=node_seq, peak_target=sample.targets,
                           peak_hour_target=sample.target_hours, low_target=sample.targets,
                           peak_height_target=sample.peak_heights, peak_hour_target_day=sample.peak_hours)


@pytest.fixture()
def setup(monkeypatch):
    monkeypatch.setattr(T, "prepare_sample", _prepare)
    sub = SimpleNamespace(edge_index=np.zeros((2, 0)), edge_attr=np.zeros((0, 1)), edge_dir=np.zeros(0),
                          tau_phys=np.zeros(0), static=np.zeros((1, 1)), station_node=0)
    tcfg = T.TrainConfig(t_out=T_OUT, delta_h=DELTA_H)
    return {"S": sub}, ModelConfig(delta_h=DELTA_H), tcfg


def _eval(samples, setup, bs):
    subs, cfg, tcfg = setup
    return T.evaluate(_FakeModel(), samples, subs, cfg, tcfg, "cpu", batch_size=bs)


@pytest.mark.parametrize("bs", [2, 3, 32])
def test_evaluate_theo_lo_bang_tung_mau(setup, bs):
    """Lô B ≥ 2 không lỗi và cho **cùng** chỉ số với chạy từng mẫu (B = 1)."""
    samples = [_sample(i) for i in range(5)]
    ref = _eval(samples, setup, 1)
    got = _eval(samples, setup, bs)
    assert got["n_samples"] == ref["n_samples"] == 5
    for key in ("mae_peak", "mae_slot", "mae_hour", "pinball"):
        assert np.isfinite(got[key])
        assert got[key] == pytest.approx(ref[key], rel=1e-6, abs=1e-9), key


def test_pinball_la_trung_binh_theo_mau(setup):
    """`pinball` = trung bình pinball **của từng mẫu** (trên thang của mô hình)."""
    samples = [_sample(i) for i in range(3)]
    per = [_eval([s], setup, 1)["pinball"] for s in samples]
    assert _eval(samples, setup, 3)["pinball"] == pytest.approx(float(np.mean(per)))
    # kiểm tra độc lập với `pinball_loss` cho mẫu đầu
    assert per[0] > 0 and np.isfinite(per[0])
    assert pinball_loss(torch.zeros(1, 1, 3), torch.zeros(1, 1)).item() == 0.0


def test_mau_toan_nan_khong_tinh_vao_pinball(setup):
    """Mẫu không có nhãn nào (toàn NaN) không kéo trung bình pinball xuống 0."""
    samples = [_sample(i) for i in range(3)]
    base = _eval(samples, setup, 4)
    with_nan = _eval(samples + [_sample(9, nan=True)], setup, 4)
    assert with_nan["pinball"] == pytest.approx(base["pinball"])
    assert with_nan["mae_slot"] == pytest.approx(base["mae_slot"])
    assert with_nan["n_samples"] == base["n_samples"] + 1
    only_nan = _eval([_sample(0, nan=True), _sample(1, nan=True)], setup, 2)
    assert np.isnan(only_nan["pinball"])
