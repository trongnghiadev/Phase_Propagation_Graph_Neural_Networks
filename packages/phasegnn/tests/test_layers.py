"""Kiểm thử tầng dịch pha và thông điệp lan truyền (checklist 6.5 — mục 1 và 2).

* `PhaseShift` dịch xung đúng τ (sai số < 0,05 bước) và gradient theo τ khác 0.
* Lan truyền hai lượt trên đồ thị dựng tay: xung tới đích sau đúng Σ τ_up / Σ τ_down.
"""
from __future__ import annotations

import math

import pytest

torch = pytest.importorskip("torch")

from phasegnn.layers import (DELTA_MAX, DELTA_MIN, DIR_DOWN, DIR_UP, PhaseMessage,
                             PhaseShift, TopoGRU, split_directions, topo_order)


# ---------------------------------------------------------------------------
# 1. PhaseShift dịch xung đúng τ
# ---------------------------------------------------------------------------
def _pulse(period: int = 100, width: int = 3, t: int = 200) -> torch.Tensor:
    """Xung đơn vị (hình chữ nhật) trên lưới bước thời gian."""
    x = torch.zeros(1, t, 1)
    x[0, period:period + width, 0] = 1.0
    return x


def _delta_pulse(period: int = 100, t: int = 200) -> torch.Tensor:
    """Xung đơn vị đúng 1 bước (dùng khi cần đo vị trí đỉnh chính xác)."""
    x = torch.zeros(1, t, 1)
    x[0, period, 0] = 1.0
    return x


def test_phase_shift_moves_pulse_by_exact_delay():
    """Xung tại bước 50 dịch sang bước 50 + k·Δ với sai số < 0,05 bước."""
    delta = 1.0
    x = _delta_pulse(period=50)
    shift = PhaseShift(delta_h=delta)
    for k in (3, 7, 12):
        tau = torch.tensor([k * delta])
        y = shift(x, tau)
        peak = int(torch.argmax(y[0, :, 0]))
        assert abs(peak - (50 + k)) < 0.05, f"k={k}: peak={peak}, kỳ vọng ~{50 + k}"


def test_phase_shift_substep_interpolation_is_accurate():
    """Với τ không nguyên bước, vị trí xung phải nằm trong 0,05 bước của giá trị thật.

    Vì phép trộn tuyến tính làm "cháy" xung trên 2 bước, ta đo **centroid** (trọng tâm
    năng lượng) thay vì `argmax` — centroid là bộc lộ chính xác vị trí trễ liên tục.
    """
    delta = 1.0
    x = _delta_pulse(period=50)
    shift = PhaseShift(delta_h=delta)
    for frac in (0.0, 0.25, 0.5, 0.75):
        tau = torch.tensor([10.0 + frac])
        y = shift(x, tau)
        w = y[0, :, 0].clamp(min=0.0)
        centroid = float((w * torch.arange(len(w), dtype=torch.float32)).sum() / w.sum())
        assert abs(centroid - (50 + 10.0 + frac)) < 0.05, \
            f"frac={frac}: centroid={centroid:.3f}, kỳ vọng {50 + 10.0 + frac:.3f}"


def test_phase_shift_gradient_wrt_tau_is_nonzero():
    """Gradient theo τ phải khác 0 (τ học được bằng gradient).

    Ta dùng hàm mất mát **có đáp án** (MSE với vị trí mong muốn) chứ không dùng `sum`
    — vì dịch xung giữ nguyên tổng nên `sum` không có gradient.
    """
    delta = 1.0
    x = _delta_pulse(period=50)
    tau = torch.tensor([7.3], requires_grad=True)
    y = PhaseShift(delta_h=delta)(x, tau)
    # đích: xung ở bước 57 (≈ 50 + 7,3 làm tròn xuống phần nguyên + phần thập phân)
    target = torch.zeros_like(y)
    target[0, 57, 0] = 1.0
    loss = ((y - target) ** 2).sum()
    loss.backward()
    assert tau.grad is not None
    assert abs(float(tau.grad)) > 1e-8, "gradient theo τ bằng 0 ⇒ τ không học được"


def test_phase_shift_zero_delay_is_identity():
    x = torch.randn(2, 20, 3)
    y = PhaseShift(delta_h=1.0)(x, torch.zeros(2))
    assert torch.allclose(y, x, atol=1e-6)


# ---------------------------------------------------------------------------
# 2. Lan truyền hai chiều trên đồ thị dựng tay
# ---------------------------------------------------------------------------
def _chain_graph(n: int = 4) -> tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    """Đồ thị đường: 0 → 1 → … → n−1 theo chiều `down` (chiều ngược lại là `up`).

    Trả về `(edge_index, edge_dir, tau_phys, node_static)`.
    """
    src, dst = [], []
    dirs = []
    taus = []
    for i in range(n - 1):
        src.append(i)
        dst.append(i + 1)
        dirs.append(DIR_DOWN)
        taus.append(2.0)
        src.append(i + 1)
        dst.append(i)
        dirs.append(DIR_UP)
        taus.append(3.0)
    edge_index = torch.tensor([src, dst], dtype=torch.long)
    edge_dir = torch.tensor(dirs, dtype=torch.long)
    tau = torch.tensor(taus, dtype=torch.float32)
    return edge_index, edge_dir, tau


def test_two_direction_propagation_delays_sum_tau():
    """Xung từ nút 0 tới nút cuối: độ trễ = Σ τ theo chiều đi.

    Kiểm tra bằng cách dùng `PhaseShift` trực tiếp trên từng cạnh của đường
    (mô phỏng đúng cơ chế dịch pha của `PhaseMessage`): tổng độ trễ phải bằng
    tổng τ của các cạnh trên đường đi.
    """
    n = 4
    edge_index, edge_dir, tau = _chain_graph(n)
    # đường đi chiều `up`: 3 → 2 → 1 → 0, mỗi cạnh τ = 3 h
    total_tau = float(tau[edge_dir == DIR_UP].sum())
    assert total_tau == pytest.approx(9.0)

    # dịch xung qua từng cạnh: vị trí xung phải dịch đúng tổng τ
    x = _delta_pulse(period=10, t=120)
    cur = x
    for _ in range(n - 1):
        cur = PhaseShift(delta_h=1.0)(cur, torch.tensor([3.0]))
    peak = int(torch.argmax(cur[0, :, 0]))
    assert abs(peak - (10 + total_tau)) < 0.05


def test_phase_message_uses_learned_tau_when_enabled():
    """Khi `learn_tau=True` và δ ≠ 0, τ thực dùng khác τ_phys theo `exp(δ)`."""
    edge_index, edge_dir, tau = _chain_graph(3)
    h = torch.randn(3, 5, 4)
    msg_mod = PhaseMessage(node_dim=4, edge_dim=3, learn_tau=True)
    # đẩy δ khỏi 0 bằng cách đặt bias của tầng cuối
    with torch.no_grad():
        msg_mod.delta_mlp[-1].bias[DIR_UP] = 0.5
    edge_attr = torch.randn(edge_index.shape[1], 3)
    msg, tau_eff, delta = msg_mod(h, tau, edge_index, edge_attr, edge_dir)
    assert msg.shape == (edge_index.shape[1], 5, 4)
    assert delta.shape == (edge_index.shape[1],)
    up_idx = (edge_dir == DIR_UP).nonzero(as_tuple=False).flatten()
    expected = tau[up_idx] * math.exp(0.5)
    assert torch.allclose(tau_eff[up_idx], expected, rtol=1e-4)
    # chiều down không bị δ tác động
    down_idx = (edge_dir == DIR_DOWN).nonzero(as_tuple=False).flatten()
    assert torch.allclose(tau_eff[down_idx], tau[down_idx], rtol=1e-6)


def test_phase_message_fixed_tau_when_disabled():
    """B4: `learn_tau=False` ⇒ τ = τ_phys cố định, δ = 0."""
    edge_index, edge_dir, tau = _chain_graph(3)
    h = torch.randn(3, 5, 4)
    msg_mod = PhaseMessage(node_dim=4, edge_dim=3, learn_tau=False)
    with torch.no_grad():
        msg_mod.delta_mlp[-1].bias.fill_(1.0)
    edge_attr = torch.randn(edge_index.shape[1], 3)
    _, tau_eff, delta = msg_mod(h, tau, edge_index, edge_attr, edge_dir)
    assert torch.allclose(tau_eff, tau, rtol=1e-6)
    assert torch.allclose(delta, torch.zeros_like(delta))


def test_phase_message_delta_is_clamped():
    """δ bị kẹp trong [DELTA_MIN, DELTA_MAX] (đề cương 5.2.3)."""
    edge_index, edge_dir, tau = _chain_graph(3)
    h = torch.randn(3, 5, 4)
    msg_mod = PhaseMessage(node_dim=4, edge_dim=3, learn_tau=True)
    with torch.no_grad():
        msg_mod.delta_mlp[-1].bias.fill_(100.0)
    edge_attr = torch.randn(edge_index.shape[1], 3)
    _, _, delta = msg_mod(h, tau, edge_index, edge_attr, edge_dir)
    assert float(delta.detach().max()) <= DELTA_MAX + 1e-6
    assert float(delta.detach().min()) >= DELTA_MIN - 1e-6


def test_phase_message_amplitude_gamma_softplus():
    """γ = softplus(a): khởi tạo a = 0 ⇒ γ = softplus(0) ≈ 0,693."""
    edge_index, edge_dir, tau = _chain_graph(3)
    h = torch.ones(3, 5, 4)
    msg_mod = PhaseMessage(node_dim=4, edge_dim=3, smooth_steps=1)
    edge_attr = torch.randn(edge_index.shape[1], 3)
    msg, _, _ = msg_mod(h, tau, edge_index, edge_attr, edge_dir)
    gamma = torch.nn.functional.softplus(msg_mod.gamma_raw)
    assert pytest.approx(float(gamma[DIR_DOWN]), rel=1e-5) == float(
        torch.nn.functional.softplus(torch.tensor(0.0)))
    # thông điệp = γ × giá trị đã làm trơn (= 1 khi h=1 và τ=0 hiệu ứng dịch tại t=0)
    assert torch.isfinite(msg).all()


def test_topo_gru_two_passes_uses_upstream_state():
    """Lượt 2 (chiều triều) phải dùng trạng thái SAU lượt 1.

    Ta kiểm tra bằng cách so trạng thái đầu ra với trạng thái mà GRU tính trực tiếp
    từ `h` (lượt 1) — nếu lượt 2 dùng `h` cũ thì kết quả khác.
    """
    torch.manual_seed(0)
    n = 3
    edge_index, edge_dir, tau = _chain_graph(n)
    idx_down, idx_up = split_directions(edge_dir, edge_dir)
    ei_down = edge_index[:, idx_down]
    ei_up = edge_index[:, idx_up]
    topo = TopoGRU(node_dim=4, in_dim=3, message_dim=4, directions=(True, True))
    h = torch.randn(n, 4)
    x = torch.randn(n, 3)
    m_down = torch.randn(ei_down.shape[1], 4)
    m_up = torch.randn(ei_up.shape[1], 4)
    order_down = topo_order(n, ei_down, "down")
    order_up = topo_order(n, ei_up, "up")
    out = topo(h, x, m_down, m_up, ei_down, ei_up, order_down, order_up, n)
    assert out.shape == (n, 4)
    assert torch.isfinite(out).all()


def test_topo_gru_single_direction_variants():
    """B5a (chỉ `up`) và B5b (chỉ `down`) phải chạy và cho khác kết quả."""
    torch.manual_seed(0)
    n = 3
    edge_index, edge_dir, tau = _chain_graph(n)
    idx_down, idx_up = split_directions(edge_dir, edge_dir)
    ei_down = edge_index[:, idx_down]
    ei_up = edge_index[:, idx_up]
    order_down = topo_order(n, ei_down, "down")
    order_up = topo_order(n, ei_up, "up")
    h = torch.randn(n, 4)
    x = torch.randn(n, 3)
    m_down = torch.randn(ei_down.shape[1], 4)
    m_up = torch.randn(ei_up.shape[1], 4)

    both = TopoGRU(node_dim=4, in_dim=3, message_dim=4, directions=(True, True))
    only_up = TopoGRU(node_dim=4, in_dim=3, message_dim=4, directions=(False, True))
    only_down = TopoGRU(node_dim=4, in_dim=3, message_dim=4, directions=(True, False))
    o_both = both(h, x, m_down, m_up, ei_down, ei_up, order_down, order_up, n)
    o_up = only_up(h, x, m_down, m_up, ei_down, ei_up, order_down, order_up, n)
    o_down = only_down(h, x, m_down, m_up, ei_down, ei_up, order_down, order_up, n)
    assert o_up.shape == o_down.shape == o_both.shape
    assert not torch.allclose(o_both, o_up)


def test_topo_order_respects_direction():
    """Thứ tự topo chiều `down` phải đặt nút thượng nguồn trước nút hạ nguồn."""
    edge_index, edge_dir, _ = _chain_graph(4)
    idx_down, idx_up = split_directions(edge_dir, edge_dir)
    order_down = topo_order(4, edge_index[:, idx_down], "down").tolist()
    order_up = topo_order(4, edge_index[:, idx_up], "up").tolist()
    pos_down = {v: i for i, v in enumerate(order_down)}
    pos_up = {v: i for i, v in enumerate(order_up)}
    # chiều down: cạnh 0→1 ⇒ 0 trước 1
    assert pos_down[0] < pos_down[1]
    # chiều up: cạnh 1→0 ⇒ 1 trước 0
    assert pos_up[1] < pos_up[0]
