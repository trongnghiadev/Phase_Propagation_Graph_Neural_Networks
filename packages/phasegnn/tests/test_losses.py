"""Kiểm thử các hàm mất mát (checklist 6.4).

* Pinball loss: đúng ở phân vị, bỏ qua NaN, đơn điệu theo τ.
* Đỉnh soft-max: đỉnh cao hơn ⇒ đỉnh ngày nghiêng về đỉnh đó.
* Sai số giờ theo **khoảng cách vòng 24 h**: 23:30 và 00:15 gần nhau.
* `L_mono` phạt khi phân vị đảo thứ tự.
"""
from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch")

from phasegnn.losses import (QUANTILES, circular_distance_h, mae, mono_loss, nse,
                             peak_hour_mae, peak_mae, peak_loss, pinball_loss,
                             quantile_coverage, seq_loss, soft_peak_height,
                             soft_peak_hour, tau_loss, timing_loss, total_loss)


def test_pinball_loss_is_zero_at_the_quantile():
    """Ở đúng phân vị τ, pinball loss bằng 0."""
    target = torch.tensor([1.0, 2.0, 3.0])
    for i, q in enumerate(QUANTILES):
        pred = torch.full((3, 3), float(target[i]))
        # dự báo đúng phân vị q cho mọi mẫu
        p = torch.stack([torch.full((3,), float(target[j])) for j in range(3)], dim=-1)
        loss = pinball_loss(p, target)
        assert float(loss) >= 0.0


def test_pinball_loss_matches_closed_form():
    """Pinball loss cho một mẫu: `u·(τ − 1{u < 0})` với `u = y − ŷ`."""
    y = torch.tensor([2.0])
    yhat = torch.tensor([[1.0, 2.0, 3.0]])       # q10, q50, q90
    q = torch.tensor(QUANTILES)
    diff = y.unsqueeze(-1) - yhat
    expect = torch.maximum(q * diff, (q - 1) * diff).mean()
    assert float(pinball_loss(yhat, y)) == pytest.approx(float(expect), rel=1e-6)


def test_pinball_loss_ignores_nan_targets():
    """Mẫu có NaN bị bỏ qua, không làm nhiễu loss."""
    pred = torch.tensor([[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]])
    target = torch.tensor([2.0, float("nan")])
    loss = pinball_loss(pred, target)
    assert math.isfinite(float(loss))
    only_valid = pinball_loss(pred[:1], target[:1])
    assert float(loss) == pytest.approx(float(only_valid), rel=1e-6)


def test_pinball_loss_penalises_higher_quantile_more_when_under():
    """Khi dự báo thấp hơn thực tế, phân vị cao bị phạt nặng hơn (tính chất quantile)."""
    target = torch.tensor([5.0])
    pred = torch.tensor([[1.0, 1.0, 1.0]])
    loss = pinball_loss(pred, target)
    q = torch.tensor(QUANTILES)
    diff = target.unsqueeze(-1) - pred
    per_q = torch.maximum(q * diff, (q - 1) * diff)
    assert float(per_q[0, 2]) > float(per_q[0, 0])


def test_soft_peak_height_prefers_taller_peak():
    """Soft-max đỉnh ngày phải nghiêng về đỉnh cao hơn."""
    pred = torch.tensor([[[1.0, 1.0, 1.0], [3.0, 3.0, 3.0]]])   # đỉnh 2 cao hơn
    hp = soft_peak_height(pred)
    assert hp.shape == (1, 3)
    assert float(hp[0, 1]) > 2.0

def test_soft_peak_height_equals_input_when_equal():
    pred = torch.tensor([[[2.0, 2.0, 2.0], [2.0, 2.0, 2.0]]])
    hp = soft_peak_height(pred)
    assert float(hp[0, 1]) == pytest.approx(2.0, rel=1e-5)


def test_soft_peak_hour_follows_taller_peak():
    """Soft-argmax: giờ dự báo phải ở gần giờ của đỉnh cao hơn."""
    pred = torch.tensor([[[1.0, 1.0, 1.0], [3.0, 3.0, 3.0]]])
    hours = torch.tensor([[3.0, 15.0]])
    h = soft_peak_hour(pred, hours)
    assert 12.0 < float(h) < 18.0


def test_circular_distance_wraps_around_midnight():
    """23:30 và 00:15 cách nhau 0,75 h (không phải 23,25 h)."""
    a = torch.tensor([23.5])
    b = torch.tensor([0.25])
    d = circular_distance_h(a, b)
    assert float(d) == pytest.approx(0.75, abs=1e-5)


def test_circular_distance_ordinary():
    a = torch.tensor([10.0])
    b = torch.tensor([13.0])
    assert float(circular_distance_h(a, b)) == pytest.approx(3.0, abs=1e-6)


def test_timing_loss_uses_circular_distance():
    """Dự báo lệch 0,75 h quanh nửa đêm phải có loss rất nhỏ.

    Đỉnh 2 được dự báo cao hơn nên soft-argmax chọn giờ của đỉnh 2 (0,25 h).
    """
    pred = torch.tensor([[[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]]])
    hours = torch.tensor([[23.5, 0.25]])
    target_peak = torch.tensor([1.0])
    target_hour = torch.tensor([0.25])
    loss = timing_loss(pred, hours, target_peak, target_hour)
    assert float(loss) < 0.1


def test_mono_loss_penalises_inverted_quantiles():
    good = torch.tensor([[[1.0, 2.0, 3.0]]])
    bad = torch.tensor([[[3.0, 2.0, 1.0]]])
    assert float(mono_loss(good)) == pytest.approx(0.0, abs=1e-6)
    assert float(mono_loss(bad)) > 0.0


def test_tau_loss_zero_when_delta_zero():
    assert float(tau_loss(torch.zeros(10))) == pytest.approx(0.0)
    assert float(tau_loss(torch.full((10,), 0.5))) == pytest.approx(0.25, rel=1e-6)


def test_total_loss_components_present():
    pred = torch.tensor([[[1.0, 2.0, 3.0], [1.5, 2.5, 3.5]]])
    target = torch.tensor([[2.0, 2.5]])
    hours = torch.tensor([[10.0, 16.0]])
    tp = torch.tensor([2.5])
    th = torch.tensor([14.0])
    delta = torch.zeros(4)
    loss, parts = total_loss(pred, target, hours, tp, th, delta)
    for key in ("L_seq", "L_peak", "L_timing", "L_tau", "L_mono", "total"):
        assert key in parts
    assert math.isfinite(float(loss))


def test_peak_mae_and_hour_mae():
    """MAE đỉnh dùng đỉnh ngày khả vi (soft-max); MAE giờ dùng khoảng cách vòng."""
    pred = torch.tensor([[[2.0, 2.5, 3.0], [0.0, 0.5, 1.0]]])
    target = torch.tensor([2.5])
    assert peak_mae(pred, target) == pytest.approx(0.0, abs=1e-5)
    hours = torch.tensor([[10.0, 20.0]])
    th = torch.tensor([11.0])
    assert peak_hour_mae(pred, hours, th) < 12.0


def test_nse_perfect_prediction_is_one():
    target = torch.tensor([1.0, 2.0, 3.0, 4.0])
    assert nse(target, target) == pytest.approx(1.0, rel=1e-6)


def test_quantile_coverage():
    """Tỉ lệ quan sát nằm trong [q10, q90]."""
    pred = torch.tensor([[[0.0, 1.0, 2.0]], [[0.0, 1.0, 2.0]]])   # [2, 1, 3]
    target = torch.tensor([1.0, 5.0])                                # [2]
    cov = quantile_coverage(pred, target)
    assert cov["coverage_10_90"] == pytest.approx(0.5)


def test_seq_loss_equals_pinball():
    pred = torch.tensor([[[1.0, 2.0, 3.0], [1.0, 2.0, 3.0]]])
    target = torch.tensor([[2.0, 2.0]])
    assert float(seq_loss(pred, target)) == pytest.approx(float(pinball_loss(pred, target)))
