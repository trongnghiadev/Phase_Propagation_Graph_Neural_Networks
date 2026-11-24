"""Kiểm thử mô hình (checklist 6.5 — mục 3 và 4; và kiểm tra forward shape).

* **Dữ liệu tổng hợp (Muskingum, τ biết trước): học lại τ với sai số tương đối < 20%.**
* **Overfit 1 batch nhỏ (loss về gần 0)** để chắc mô hình học được.
* **Forward shape** đúng cho PhaseGNN và các biến thể B3/B4/B5a/B5b.
"""
from __future__ import annotations

import math

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from phasegnn.layers import DIR_DOWN, DIR_UP, PhaseMessage, PhaseShift
from phasegnn.model import ModelConfig, PhaseGNN, daily_grid, interpolate_to_grid
from phasegnn.train import _select_daily


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------
def _toy_graph(n_nodes: int = 6, n_edges: int = 10, edge_dim: int = 4, node_dim: int = 3):
    """Đồ thị ngẫu nhiên nhỏ với cả hai chiều."""
    g = torch.Generator().manual_seed(42)
    src = torch.randint(0, n_nodes, (n_edges,), generator=g)
    dst = torch.randint(0, n_nodes, (n_edges,), generator=g)
    edge_index = torch.stack([src, dst], dim=0)
    edge_dir = torch.randint(0, 2, (n_edges,), generator=g)
    edge_attr = torch.randn(n_edges, edge_dim, generator=g)
    tau = torch.rand(n_edges, generator=g) * 4 + 0.5
    static = torch.randn(n_nodes, 5, generator=g)
    return edge_index, edge_attr, edge_dir, tau, static


def _model(kind: str = "phasegnn", learn_tau: bool = True, directions=(True, True),
           in_dim: int = 3, static_dim: int = 5, edge_dim: int = 4,
           delta_h: float = 1.0, hidden: int = 16) -> PhaseGNN:
    cfg = ModelConfig(hidden_dim=hidden, num_gnn_layers=1, delta_h=delta_h,
                      learn_tau=learn_tau, directions=directions, model_kind=kind)
    return PhaseGNN(cfg, in_dim=in_dim, static_dim=static_dim, edge_dim=edge_dim)


# ---------------------------------------------------------------------------
# Forward shape
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind,learn_tau,directions", [
    ("phasegnn", True, (True, True)),
    ("phasegnn", False, (True, True)),      # B4
    ("phasegnn", True, (False, True)),      # B5a
    ("phasegnn", True, (True, False)),      # B5b
    ("graphsage", False, (True, True)),     # B3
])
def test_forward_shapes(kind, learn_tau, directions):
    """Đầu ra có đúng hình dạng: đỉnh/chân × phân vị + giờ."""
    n, t = 6, 12
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(kind=kind, learn_tau=learn_tau, directions=directions)
    node_seq = torch.randn(n, t, 3)
    out = model(node_seq, static, edge_index, edge_attr, edge_dir, tau,
                target_nodes=torch.tensor([0, 3]))
    b = 2
    assert out.peak_height.shape == (b, t, 2, 3)
    assert out.peak_hour.shape == (b, t, 2)
    assert out.low_height.shape == (b, t, 2, 3)
    assert out.low_hour.shape == (b, t, 2)
    assert torch.isfinite(out.peak_height).all()
    assert torch.isfinite(out.peak_hour).all()


def test_forward_all_nodes_when_no_target():
    n, t = 5, 8
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model()
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    assert out.peak_height.shape[0] == n


def test_phasegnn_reports_delta_and_tau():
    """PhaseGNN phải trả δ và τ để tính `L_τ`."""
    n, t = 6, 10
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(learn_tau=True)
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    assert out.delta is not None
    assert out.tau_eff is not None
    assert out.delta.shape[0] == edge_index.shape[1]
    assert out.tau_eff.shape[0] == edge_index.shape[1]


def test_b4_has_no_learned_delta():
    """B4 (τ cố định) không có δ học được."""
    n, t = 6, 10
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(learn_tau=False)
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    assert out.delta is None


# ---------------------------------------------------------------------------
# Chọn đỉnh theo ngày (`_select_daily`)
# ---------------------------------------------------------------------------
def test_select_daily_single_sample():
    """Mẫu đơn: chọn bước có q50 lớn nhất trong mỗi ngày."""
    delta_h, steps_per_day, n_days = 12.0, 2, 3
    t = steps_per_day * n_days
    peak = torch.zeros(t, 2, 3)
    hours = torch.zeros(t, 2)
    # ngày 0: đỉnh ở bước 1; ngày 1: đỉnh ở bước 3; ngày 2: đỉnh ở bước 4
    for day, step in enumerate([1, 3, 4]):
        peak[step, :, 1] = 1.0 + day
        hours[step, :] = 6.0 + day
    pk, hr, pd_, hd_ = _select_daily(peak, hours, t_out=n_days, delta_h=delta_h)
    assert pk.shape == (n_days, 2, 3)
    assert hr.shape == (n_days, 2)
    assert torch.allclose(pk[:, 0, 1], torch.tensor([1.0, 2.0, 3.0]))
    assert torch.allclose(hr[:, 0], torch.tensor([6.0, 7.0, 8.0]))


def test_select_daily_batched_matches_single():
    """Lô phải cho **cùng kết quả** với gọi từng mẫu (ranh giới ngày không bị lẫn).

    Đây là hồi quy cho lỗi: khi reshape `[B, T, ...]` thành `[B·T, ...]`, ngày của mẫu
    thứ hai bị nối tiếp ngày của mẫu thứ nhất.
    """
    delta_h, steps_per_day, n_days = 12.0, 2, 3
    t = steps_per_day * n_days
    torch.manual_seed(7)
    peak = torch.randn(3, t, 2, 3)
    hours = torch.rand(3, t, 2) * 24.0
    pk_b, hr_b, pd_b, hd_b = _select_daily(peak, hours, t_out=n_days, delta_h=delta_h)
    assert pk_b.shape == (3, n_days, 2, 3)
    assert hr_b.shape == (3, n_days, 2)
    for i in range(3):
        pk, hr, pd_, hd_ = _select_daily(peak[i], hours[i], t_out=n_days, delta_h=delta_h)
        assert torch.allclose(pk_b[i], pk, atol=1e-6)
        assert torch.allclose(hr_b[i], hr, atol=1e-6)
        assert torch.allclose(pd_b[i], pd_, atol=1e-6)


# ---------------------------------------------------------------------------
# Dự báo theo lô (`forward_batch`)
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("kind,learn_tau,directions", [
    ("phasegnn", True, (True, True)),
    ("phasegnn", False, (True, True)),      # B4
    ("phasegnn", True, (False, True)),      # B5a
    ("phasegnn", True, (True, False)),      # B5b
    ("graphsage", False, (True, True)),     # B3
])
def test_forward_batch_matches_single(kind, learn_tau, directions):
    """Dự báo theo lô phải cho **cùng kết quả** với dự báo từng mẫu.

    Đây là điều kiện để dùng `forward_batch` (nhanh hơn 2–4× trên CPU) trong dự báo và
    đánh giá mà không đổi kết quả số học.
    """
    n, t, b = 6, 12, 4
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(kind=kind, learn_tau=learn_tau, directions=directions)
    model.eval()
    node_seq = torch.randn(b, n, t, 3)
    with torch.no_grad():
        out_b = model.forward_batch(node_seq, static, edge_index, edge_attr, edge_dir, tau)
        for i in range(b):
            out_s = model(node_seq[i], static, edge_index, edge_attr, edge_dir, tau)
            assert torch.allclose(out_b.peak_height[i], out_s.peak_height, atol=1e-5)
            assert torch.allclose(out_b.peak_hour[i], out_s.peak_hour, atol=1e-5)
            assert torch.allclose(out_b.low_height[i], out_s.low_height, atol=1e-5)


def test_forward_batch_target_nodes():
    """`forward_batch` hỗ trợ chọn node đích (node trạm).

    Dạng đầu ra: ``[B, K, T, 2, Q]`` — B = số mẫu trong lô, K = số node được chọn.
    """
    n, t, b = 5, 8, 3
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model()
    model.eval()
    node_seq = torch.randn(b, n, t, 3)
    with torch.no_grad():
        out = model.forward_batch(node_seq, static, edge_index, edge_attr, edge_dir, tau,
                                  target_nodes=torch.tensor([2]))
    assert out.peak_height.shape == (b, 1, t, 2, 3)
    assert out.peak_hour.shape == (b, 1, t, 2)


def test_forward_batch_gradients_flow():
    """Gradient phải chảy qua `forward_batch` (dùng cho huấn luyện theo lô)."""
    n, t, b = 6, 10, 3
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(learn_tau=True)
    out = model.forward_batch(torch.randn(b, n, t, 3), static, edge_index, edge_attr,
                              edge_dir, tau)
    out.peak_height.square().mean().backward()
    assert model.message.delta_mlp[-1].weight.grad is not None
    assert float(model.message.delta_mlp[-1].weight.grad.abs().sum()) > 0


def test_model_gradients_flow_to_all_parameters():
    """Gradient phải tới được tham số của `PhaseMessage` (τ học được) và các đầu ra."""
    n, t = 6, 10
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(learn_tau=True)
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    loss = out.peak_height.square().mean() + out.peak_hour.square().mean()
    loss.backward()
    assert model.message.delta_mlp[-1].weight.grad is not None
    assert float(model.message.delta_mlp[-1].weight.grad.abs().sum()) > 0
    assert model.peak_head[0].weight.grad is not None


def test_interpolate_to_grid_linear():
    """Nội suy giữa hai mốc đỉnh phải tuyến tính."""
    values = np.array([[0.0, 0.0], [2.0, 4.0]], dtype=np.float32)
    hours = np.array([[0.0, 0.0], [12.0, 12.0]], dtype=np.float32)
    grid = np.array([3.0, 6.0], dtype=np.float32)
    out = interpolate_to_grid(values, hours, grid)
    assert out[0, 0] == pytest.approx(0.5, rel=1e-4)
    assert out[1, 0] == pytest.approx(1.0, rel=1e-4)
    assert out[0, 1] == pytest.approx(1.0, rel=1e-4)


def test_daily_grid_shape():
    grid_hours, day_index = daily_grid(3, delta_h=1.0)
    assert len(grid_hours) == 72
    assert len(day_index) == 72
    assert day_index[0] == 0 and day_index[-1] == 2
    grid12, day12 = daily_grid(2, delta_h=12.42)
    assert len(grid12) == 2 * max(int(round(24 / 12.42)), 1)


# ---------------------------------------------------------------------------
# 6.5 mục 3 — học lại τ trên dữ liệu tổng hợp (Muskingum)
# ---------------------------------------------------------------------------
def _muskingum_series(n: int = 400, k: float = 0.2, n_res: int = 3):
    """Chuỗi lưu lượng tổng hợp theo Muskingum (dạng **bảo toàn khối lượng**).

    Mô hình: một dãy `n_res` hồ chứa nối tiếp, mỗi hồ có thời gian trễ `tau_res` bước
    và hệ số tiêu tán `x`:

        S[t]  = x·I[t] + (1−x)·S[t−1]
        O[t]  = S[t − tau_res]

    Với `x = 0,2`, `n_res = 3`, `tau_res = 2` ⇒ lưu lượng ra trễ tổng cộng
    `Σ (tau_res + 1) ≈ 9` bước so với lưu lượng vào — và **bảo toàn khối lượng**
    (`Σ I = Σ O`), nên chuỗi ổn định (không phân kỳ).

    **Vì sao không dùng dạng cổ điển `C₀·I[t] + C₁·I[t−1] + C₂·O[t−1] + C₃·O[t−2]`?**
    Với `C₁ = C₃ = (1−k)/2`, `C₂ = (1+k)/2`, `C₀ = C₂ − k` thì **tổng hệ số bằng
    `2 − k` ≠ 1** ⇒ phương trình hồi quy phân kỳ (thử k = 0,2: chuỗi ra tăng không
    dừng, đạt ~114 và tiếp tục lớn). Khi đó hàm mất mát không có cực tiểu tại τ thật
    và gradient descent không học được τ. Dạng dãy hồ ở trên giữ được tính chất
    "trễ rõ ràng + tiêu tán" mà cơ chế `PhaseShift` cần kiểm tra, đồng thời ổn định.
    """
    tau_res = 2
    x = 0.2
    inflow = np.zeros(n)
    mid = n // 2
    inflow[mid:mid + 20] = 10.0

    # dãy hồ chứa: mỗi hồ giữ nước `x` bước và truyền (1−x) sang hồ kế tiếp
    storage = np.zeros(n)
    out = np.zeros(n)
    buf = np.zeros((n_res, max(tau_res, 1)))
    for t in range(n):
        storage[t] = x * inflow[t] + (1.0 - x) * (storage[t - 1] if t > 0 else 0.0)
        # đưa qua dãy hồ (mỗi hồ trễ `tau_res` bước)
        v = storage[t]
        for j in range(n_res):
            buf[j, t % buf.shape[1]] = v
            v = buf[j, (t - tau_res) % buf.shape[1]] if t >= tau_res else 0.0
        out[t] = v
    return inflow, out


def test_learn_tau_on_synthetic_muskingum_within_20pct():
    """Học lại τ từ dữ liệu tổng hợp có τ biết trước — sai số tương đối < 20%.

    Cách làm: dùng `PhaseShift` (cơ chế dịch pha của PhaseGNN) với τ là tham số tự do,
    tối ưu bằng gradient để khớp chuỗi **quan sát** (đã trễ đúng τ) với chuỗi
    **lưu lượng ra** đưa vào hàm dịch pha.

    Vì `PhaseShift` thực hiện ``y[t] = x[t − τ]``, ta đưa vào chuỗi **chưa trễ** (`out`)
    và so với chuỗi **đã trễ** (`obs`): khi τ đúng, `PhaseShift(out, τ) ≡ obs`.
    """
    tau_true = 6.0
    delta_h = 1.0
    inflow, out = _muskingum_series(n=400)

    # Chuỗi "quan sát" = chuỗi ra trễ đúng tau_true (lấy mẫu tại các bước nguyên).
    shift = int(round(tau_true / delta_h))
    obs = np.zeros_like(out)
    obs[shift:] = out[:len(out) - shift]

    x = torch.as_tensor(out, dtype=torch.float32).reshape(1, -1, 1)
    target = torch.as_tensor(obs, dtype=torch.float32).reshape(1, -1, 1)

    tau = torch.tensor([3.0], requires_grad=True)     # khởi tạo lệch τ thật
    opt = torch.optim.Adam([tau], lr=0.1)
    for _ in range(400):
        y = PhaseShift(delta_h=delta_h)(x, tau)
        loss = ((y - target) ** 2).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    tau_learned = float(tau.detach())
    rel_err = abs(tau_learned - tau_true) / tau_true
    assert rel_err < 0.20, f"τ học được {tau_learned:.3f} h, τ thật {tau_true} h, " \
                           f"sai số tương đối {rel_err:.1%}"


def test_tau_loss_landscape_has_minimum_at_true_delay():
    """Kiểm tra chất lượng cơ chế: hàm mất mát phải có cực tiểu tại τ thật."""
    tau_true = 6.0
    delta_h = 1.0
    _, out = _muskingum_series(n=400)
    shift = int(round(tau_true / delta_h))
    obs = np.zeros_like(out)
    obs[shift:] = out[:len(out) - shift]
    x = torch.as_tensor(out, dtype=torch.float32).reshape(1, -1, 1)
    target = torch.as_tensor(obs, dtype=torch.float32).reshape(1, -1, 1)

    losses = {}
    for tv in (4.0, 5.0, 6.0, 7.0, 8.0):
        y = PhaseShift(delta_h=delta_h)(x, torch.tensor([tv]))
        losses[tv] = float(((y - target) ** 2).mean())
    assert losses[6.0] < losses[5.0] < losses[4.0]
    assert losses[6.0] < losses[7.0] < losses[8.0]
    assert losses[6.0] == pytest.approx(0.0, abs=1e-4)


def test_learn_tau_with_phase_message_gradient():
    """Cơ chế `τ = τ_phys·exp(δ)` của `PhaseMessage` cũng phải học được τ."""
    torch.manual_seed(0)
    n_edges = 3
    edge_index = torch.tensor([[0, 1, 2], [1, 2, 3]], dtype=torch.long)
    edge_dir = torch.zeros(n_edges, dtype=torch.long)
    edge_attr = torch.randn(n_edges, 3)
    tau_phys = torch.full((n_edges,), 2.0)
    msg = PhaseMessage(node_dim=4, edge_dim=3, learn_tau=True, smooth_steps=1)
    h = torch.randn(4, 6, 4)
    with torch.no_grad():
        msg.delta_mlp[-1].bias.fill_(0.0)
    bias = msg.delta_mlp[-1].bias
    opt = torch.optim.Adam([bias], lr=0.1)
    target_tau = 4.0
    tau_eff = None
    for _ in range(200):
        _, tau_eff, _ = msg(h, tau_phys, edge_index, edge_attr, edge_dir)
        loss = ((tau_eff - target_tau) ** 2).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
    assert abs(float(tau_eff.detach().mean()) - target_tau) < 0.2


# ---------------------------------------------------------------------------
# 6.5 mục 4 — overfit 1 batch nhỏ
# ---------------------------------------------------------------------------
def test_overfit_single_small_batch():
    """Mô hình phải overfit được một batch nhỏ (loss → gần 0)."""
    torch.manual_seed(0)
    n, t = 6, 12
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model(hidden=32)
    node_seq = torch.randn(n, t, 3)
    # đích phải khớp hình dạng đầu ra `[B, T, 2, Q]`
    target = torch.randn(2, t, 2, 3)

    opt = torch.optim.AdamW(model.parameters(), lr=5e-3)
    first = None
    for step in range(250):
        out = model(node_seq, static, edge_index, edge_attr, edge_dir, tau,
                    target_nodes=torch.tensor([0, 1]))
        loss = ((out.peak_height - target) ** 2).mean()
        opt.zero_grad()
        loss.backward()
        opt.step()
        if first is None:
            first = float(loss)
    assert float(loss) < 0.05 * first, \
        f"loss không giảm đủ: {first:.5f} → {float(loss):.5f}"
    assert float(loss) < 0.05
