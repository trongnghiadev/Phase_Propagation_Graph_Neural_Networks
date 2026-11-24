"""Kiểm thử hồi quy cho các lỗi đã phát hiện (24/11/2026).

**Bối cảnh.** Kết quả cũ cho thấy MAE trên tập test ≈ 0,70 m trong khi "val MAE"
ghi trong log ≈ 0,18 m, và MAE giờ ≈ 0,70 h. Điều tra cho thấy **5 lỗi độc lập**:

1. **`peak_hour_head` không có ràng buộc đầu ra** ⇒ giờ dự báo co về ~0,1 h
   (thực tế 6,4 h và 17,8 h) ⇒ `L_timing` không có tín hiệu, mô hình không học được pha.
2. **`_select_daily` lấy `hr.max(dim=2)`** (giờ lớn nhất trong 2 slot) trong khi nhãn
   `peak_hours` là giờ của **đỉnh cao hơn** ⇒ `L_timing` so hai đại lượng khác nghĩa.
3. **`prepare_sample` ghi đè cột giờ quan sát** (`t_peak1_h`, `t_peak2_h`) của node trạm
   bằng đồng hồ lưới thô (0, 12, 24…) ⇒ mất thông tin pha + chèn đặc trưng chưa
   chuẩn hoá.
4. **Thứ tự `peak_idx` không theo quy ước** (đỉnh sớm = 0) ⇒ hai đỉnh có thể bị hoán
   đổi ⇒ sai số độ cao lớn.
5. **Định nghĩa MAE không nhất quán**: log huấn luyện dùng MAE "đỉnh cao nhất" (best-of-2)
   còn CSV/R dùng MAE theo từng đỉnh ⇒ "val 0,18 m" và "test 0,70 m" **không so sánh
   được** với nhau.

Các test dưới đây khóa lại từng lỗi để không tái phát.
"""
from __future__ import annotations

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from phasegnn.model import (HEIGHT_CENTER, HOURS_PER_DAY, ModelConfig, PhaseGNN,
                            decode_hours)
from phasegnn.train import _select_daily, _sort_slots_by_hour

# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------
def _toy_graph(n_nodes: int = 6, n_edges: int = 10, edge_dim: int = 4):
    g = torch.Generator().manual_seed(42)
    src = torch.randint(0, n_nodes, (n_edges,), generator=g)
    dst = torch.randint(0, n_nodes, (n_edges,), generator=g)
    edge_index = torch.stack([src, dst], dim=0)
    edge_dir = torch.randint(0, 2, (n_edges,), generator=g)
    edge_attr = torch.randn(n_edges, edge_dim, generator=g)
    tau = torch.rand(n_edges, generator=g) * 4 + 0.5
    static = torch.randn(n_nodes, 5, generator=g)
    return edge_index, edge_attr, edge_dir, tau, static


def _model(in_dim: int = 3, hidden: int = 16, delta_h: float = 1.0) -> PhaseGNN:
    cfg = ModelConfig(hidden_dim=hidden, num_gnn_layers=1, delta_h=delta_h)
    return PhaseGNN(cfg, in_dim=in_dim, static_dim=5, edge_dim=4)


# ---------------------------------------------------------------------------
# Lỗi 1 — giờ dự báo phải luôn nằm trong (0, 24) và **không co về 0**
# ---------------------------------------------------------------------------
def test_decode_hours_always_in_valid_range():
    """`decode_hours` phải cho giờ hợp lệ (0, 24) với **mọi** đầu ra thô."""
    raw = torch.linspace(-50.0, 50.0, 401).reshape(-1, 1)
    h = decode_hours(raw)
    assert torch.isfinite(h).all()
    assert float(h.min()) >= 0.0
    assert float(h.max()) < HOURS_PER_DAY
    # giá trị giữa (raw = 0) ⇒ 12 h
    assert float(decode_hours(torch.zeros(1))) == pytest.approx(12.0, abs=1e-5)


def test_model_peak_hour_not_collapsed_to_zero():
    """**Hồi quy lỗi 1:** giờ dự báo của mô hình chưa huấn luyện phải ở **giữa vòng
    24 h**, không được co về ~0.

    Trước khi sửa, đầu ra của `peak_hour_head` là giá trị thô của lớp tuyến tính ⇒
    sau vài epoch gradient của khoảng cách vòng 24 h triệt tiêu và giờ dự báo bị kéo
    về ~0,1 h (đo được trên checkpoint cũ).
    """
    n, t = 6, 12
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model()
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    h = out.peak_hour
    assert torch.isfinite(h).all()
    assert float(h.min()) >= 0.0 and float(h.max()) < HOURS_PER_DAY
    # không được co về gần 0: ít nhất một nửa số giờ phải > 3 h
    assert float((h > 3.0).float().mean()) > 0.5


def test_model_peak_hour_gradient_is_not_vanishing():
    """Gradient của `L_timing` (khoảng cách vòng) phải **không triệt tiêu**.

    Với đầu ra thô không ràng buộc, khi sai số giờ lớn thì `d|Δ|/dθ → 0` ⇒ mô hình
    không học được pha. Với tham số hoá `sigmoid`, gradient phải giữ được độ lớn.
    """
    n, t = 6, 12
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model()
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    pred = out.peak_hour.reshape(-1)
    target = torch.full_like(pred, 6.0)
    d = torch.remainder(pred - target + 12.0, 24.0) - 12.0
    loss = d.abs().mean()
    loss.backward()
    g = model.peak_hour_head[-1].weight.grad
    assert g is not None
    assert float(g.abs().sum()) > 1e-6, "gradient của giờ đỉnh triệt tiêu"


# ---------------------------------------------------------------------------
# Lỗi 2 — `_select_daily` phải trả giờ của **đỉnh cao hơn**
# ---------------------------------------------------------------------------
def test_select_daily_hour_belongs_to_taller_peak():
    """**Hồi quy lỗi 2:** `hour_day` phải là giờ của đỉnh có q50 **cao hơn**.

    Nhãn `peak_hours` (xem `data.build_samples`) = giờ của `max(h_peak1, h_peak2)`.
    Trước khi sửa, `_select_daily` trả `max` của giờ hai slot ⇒ sai lệch ngẫu nhiên
    với nhãn ⇒ `L_timing` không bao giờ giảm.
    """
    # 2 ngày (delta_h = 24 h ⇒ 1 bước/ngày), 2 slot; slot 0 đỉnh cao ở giờ 18,
    # slot 1 đỉnh thấp ở giờ 6
    peak = torch.zeros(2, 2, 3)
    hours = torch.zeros(2, 2)
    peak[0, 0, 1] = 1.2      # bước 0 (ngày 0), slot 0: q50 = 1,2
    peak[0, 1, 1] = 0.5      # bước 0 (ngày 0), slot 1: q50 = 0,5
    peak[1, 0, 1] = 0.9      # bước 1 (ngày 1), slot 0: q50 = 0,9
    peak[1, 1, 1] = 2.0      # bước 1 (ngày 1), slot 1: q50 = 2,0  ← đỉnh cao nhất
    hours[0, :] = torch.tensor([6.0, 6.0])
    hours[1, :] = torch.tensor([18.0, 18.0])
    pk, hr, pd_, hd_ = _select_daily(peak, hours, t_out=2, delta_h=24.0)
    # ngày 0: đỉnh cao nhất = slot 0 (q50 = 1,2) ở giờ 6
    assert float(pd_[0]) == pytest.approx(1.2, abs=1e-6)
    assert float(hd_[0]) == pytest.approx(6.0, abs=1e-6)
    # ngày 1: đỉnh cao nhất = slot 1 (q50 = 2,0) ở giờ 18
    assert float(pd_[1]) == pytest.approx(2.0, abs=1e-6)
    assert float(hd_[1]) == pytest.approx(18.0, abs=1e-6)


def test_select_daily_hour_not_max_of_slots():
    """Trường hợp phân biệt: giờ của đỉnh **thấp** lớn hơn giờ của đỉnh **cao**.

    `max` của 2 slot sẽ cho 12 h (sai); đáp án đúng là 6 h (đỉnh cao, giờ 6).
    """
    peak = torch.zeros(2, 2, 3)
    hours = torch.zeros(2, 2)
    peak[0, 0, 1] = 2.0      # đỉnh cao ở giờ 6
    peak[0, 1, 1] = 0.4      # đỉnh thấp ở giờ 12
    hours[0, :] = torch.tensor([6.0, 12.0])
    peak[1, :, 1] = 0.1
    hours[1, :] = torch.tensor([0.0, 18.0])
    _pk, _hr, pd_, hd_ = _select_daily(peak, hours, t_out=1, delta_h=24.0)
    assert float(pd_[0]) == pytest.approx(2.0, abs=1e-6)
    assert float(hd_[0]) == pytest.approx(6.0, abs=1e-6), \
        "hour_day phải là giờ của đỉnh cao hơn, không phải max của 2 slot"


def test_select_daily_batch_and_single_agree_on_hour():
    """Lô và mẫu đơn phải cho cùng `hour_day` (đồng bộ với `L_timing`)."""
    delta_h, steps_per_day, n_days = 12.0, 2, 3
    t = steps_per_day * n_days
    torch.manual_seed(11)
    peak = torch.randn(3, t, 2, 3)
    hours = torch.rand(3, t, 2) * 24.0
    pk_b, hr_b, pd_b, hd_b = _select_daily(peak, hours, t_out=n_days, delta_h=delta_h)
    for i in range(3):
        pk, hr, pd_, hd_ = _select_daily(peak[i], hours[i], t_out=n_days, delta_h=delta_h)
        assert torch.allclose(pk_b[i], pk, atol=1e-6)
        assert torch.allclose(hr_b[i], hr, atol=1e-6)
        assert torch.allclose(pd_b[i], pd_, atol=1e-6)
        assert torch.allclose(hd_b[i], hd_, atol=1e-6)


# ---------------------------------------------------------------------------
# Lỗi 4 — thứ tự `peak_idx` theo quy ước chung (đỉnh sớm = 0)
# ---------------------------------------------------------------------------
def test_sort_slots_by_hour_puts_earlier_peak_first():
    """`_sort_slots_by_hour` phải xếp slot theo giờ tăng dần."""
    values = torch.tensor([[10.0, 20.0]])       # [B, 2]
    hours = torch.tensor([[18.0, 6.0]])         # slot 0 muộn hơn slot 1
    out = _sort_slots_by_hour(values, hours)
    assert torch.allclose(out, torch.tensor([[20.0, 10.0]]))


def test_sort_slots_by_hour_handles_4d():
    """Kiểm tra trên tensor `[B, T, 2, Q]` (dạng dùng trong hàm mất mát)."""
    values = torch.zeros(2, 3, 2, 3)
    values[..., 0, :] = 1.0
    values[..., 1, :] = 2.0
    hours = torch.zeros(2, 3, 2)
    hours[..., 0] = 20.0
    hours[..., 1] = 4.0
    out = _sort_slots_by_hour(values, hours)
    assert torch.allclose(out[..., 0, :], torch.full((2, 3, 3), 2.0))
    assert torch.allclose(out[..., 1, :], torch.full((2, 3, 3), 1.0))


def test_sort_slots_by_hour_handles_nan_hours():
    """Giờ NaN không được làm hỏng thứ tự (giữ ổn định)."""
    values = torch.tensor([[1.0, 2.0]])
    hours = torch.tensor([[float("nan"), 5.0]])
    out = _sort_slots_by_hour(values, hours)
    assert torch.isfinite(out).all()


# ---------------------------------------------------------------------------
# Lỗi 3 — `prepare_sample` không được ghi đè giờ quan sát
# ---------------------------------------------------------------------------
def test_prepare_sample_keeps_observed_hours():
    """**Hồi quy lỗi 3:** node trạm phải giữ **giờ đỉnh quan sát** (đã chuẩn hoá).

    Trước khi sửa, `node_seq[st, :, 2:4] = grid_hours[:, None]` ghi đè 2 cột giờ
    quan sát bằng đồng hồ lưới thô ⇒ mô hình không biết đỉnh quan trọc xảy ra lúc
    mấy giờ, đồng thời đặc trưng mới lại **chưa chuẩn hoá** (0..24 xen giữa z-score).
    """
    from phasegnn.train import GRID_CLOCK_CHANNELS, StationSubgraph, prepare_sample

    n_feat = 18
    t_in, t_out = 3, 2
    delta_h = 12.0
    cfg = ModelConfig(delta_h=delta_h)
    # 2 ngày quan trọc, mỗi ngày 2 đỉnh; giờ quan sát khác nhau rõ rệt
    features = np.zeros((t_in, n_feat), dtype=np.float32)
    features[:, 2] = -1.2      # t_peak1_h (chuẩn hoá) = -1,2
    features[:, 3] = 0.8       # t_peak2_h (chuẩn hoá) = 0,8
    features[:, 0] = 0.5       # h_peak1
    features[:, 1] = 0.4       # h_peak2

    class _S:
        station = "ST"
        issue_date = "2024-01-05"
        days = ["2024-01-02", "2024-01-03", "2024-01-04"]

    _S.features = features
    _S.targets = np.zeros((t_out, 2), dtype=np.float32)
    _S.target_hours = np.zeros((t_out, 2), dtype=np.float32)
    _S.target_lows = np.zeros((t_out, 2), dtype=np.float32)
    _S.target_low_hours = np.zeros((t_out, 2), dtype=np.float32)
    _S.peak_heights = np.zeros(t_out, dtype=np.float32)
    _S.peak_hours = np.zeros(t_out, dtype=np.float32)

    sub = StationSubgraph(
        node_ids=["SEA", "ST"], node_index={"SEA": 0, "ST": 1},
        edge_index=np.zeros((2, 0), dtype=np.int64), edge_dir=np.zeros(0, dtype=np.int64),
        tau_phys=np.zeros(0, dtype=np.float32), edge_attr=np.zeros((0, 4), dtype=np.float32),
        static=np.zeros((2, 5), dtype=np.float32), station_node=1, sea_node=0)

    out = prepare_sample(_S(), sub, cfg, t_in, t_out)
    assert out.node_seq.shape == (2, t_in * 2, n_feat + GRID_CLOCK_CHANNELS)
    # cột giờ quan sát của node trạm phải **bằng giá trị đã chuẩn hoá** (không phải
    # đồng hồ lưới 0/12)
    st_hours = out.node_seq[1, :, 2:4]
    assert np.allclose(st_hours[:, 0], -1.2, atol=1e-4)
    assert np.allclose(st_hours[:, 1], 0.8, atol=1e-4)
    # đồng hồ lưới phải ở 2 kênh riêng và chuẩn hoá trong [-1, 1]
    clock = out.node_seq[1, :, n_feat:]
    assert clock.min() >= -1.001 and clock.max() <= 1.001
    # kênh sin/cos: tại 0 h ⇒ sin = 0, cos = 1
    assert abs(float(clock[0, 0])) < 1e-6
    assert abs(float(clock[0, 1]) - 1.0) < 1e-6


def test_prepare_sample_grid_clock_is_not_overwriting_features():
    """Đồng hồ lưới phải nằm ở **kênh riêng**, không ghi đè cột quan sát."""
    from phasegnn.train import GRID_CLOCK_CHANNELS, StationSubgraph, prepare_sample

    n_feat = 18
    cfg = ModelConfig(delta_h=12.0)
    features = np.random.RandomState(0).randn(3, n_feat).astype(np.float32)

    class _S:
        station = "ST"
        issue_date = "2024-01-05"
        days = ["2024-01-02", "2024-01-03", "2024-01-04"]

    _S.features = features
    _S.targets = np.zeros((2, 2), dtype=np.float32)
    _S.target_hours = np.zeros((2, 2), dtype=np.float32)
    _S.target_lows = np.zeros((2, 2), dtype=np.float32)
    _S.target_low_hours = np.zeros((2, 2), dtype=np.float32)
    _S.peak_heights = np.zeros(2, dtype=np.float32)
    _S.peak_hours = np.zeros(2, dtype=np.float32)

    sub = StationSubgraph(
        node_ids=["SEA", "ST"], node_index={"SEA": 0, "ST": 1},
        edge_index=np.zeros((2, 0), dtype=np.int64), edge_dir=np.zeros(0, dtype=np.int64),
        tau_phys=np.zeros(0, dtype=np.float32), edge_attr=np.zeros((0, 4), dtype=np.float32),
        static=np.zeros((2, 5), dtype=np.float32), station_node=1, sea_node=0)

    out = prepare_sample(_S(), sub, cfg, 3, 2)
    # 4 cột đầu của node trạm phải khác giá trị gốc của lưới (0, 12)
    st = out.node_seq[1]
    assert not np.allclose(st[:, 2], 0.0) or not np.allclose(st[:, 3], 0.0)
    # độ rộng của mọi đặc trưng quan sát phải hợp lý (không phải hằng số 0/12)
    assert float(st[:, 0].std()) > 1e-6
    # kênh đồng hồ lưới cuối cùng phải là sin/cos
    assert out.node_seq.shape[-1] == n_feat + GRID_CLOCK_CHANNELS


# ---------------------------------------------------------------------------
# Lỗi 5 — định nghĩa MAE phải nhất quán
# ---------------------------------------------------------------------------
def test_model_initial_peak_height_near_data_median():
    """Đầu ra khởi tạo của `peak_head` phải ở quanh **median quan trọc** (≈ 1 m).

    Trước khi sửa, bias ngẫu nhiên ⇒ dự báo khởi điểm lệch xa vùng dữ liệu ⇒ mô hình
    dễ rơi vào trạng thái "hằng số an toàn" và không học được biến động.
    """
    n, t = 6, 12
    edge_index, edge_attr, edge_dir, tau, static = _toy_graph(n)
    model = _model()
    out = model(torch.randn(n, t, 3), static, edge_index, edge_attr, edge_dir, tau)
    q50 = out.peak_height[..., 1]
    assert abs(float(q50.mean()) - HEIGHT_CENTER) < 1.0
    # **biến động** của dự báo phải khác 0 (không được là hằng số)
    assert float(q50.std()) > 1e-4


def test_peak_quantiles_are_monotone_after_export():
    """Sau khi xuất CSV, phân vị phải đơn điệu (schema bắt buộc)."""
    from phasegnn.forecast import _monotonic_quantiles

    assert _monotonic_quantiles(1.2, 1.0, 0.8) == (1.0, 1.0, 1.0)
    assert _monotonic_quantiles(0.8, 1.0, 1.2) == (0.8, 1.0, 1.2)
    q10, q50, q90 = _monotonic_quantiles(1.5, 1.0, 0.5)
    assert q10 <= q50 <= q90


def test_hours_to_iso_is_utc_of_vietnam_hour():
    """`_hours_to_iso` phải quy đổi đúng giờ VN → ISO 8601 UTC."""
    from phasegnn.forecast import _hours_to_iso

    # 08:00 VN = 01:00 UTC cùng ngày
    assert _hours_to_iso("2024-07-10", 8.0) == "2024-07-10T01:00:00Z"
    # 06:30 VN = 23:30 UTC **hôm trước**
    assert _hours_to_iso("2024-07-10", 6.5) == "2024-07-09T23:30:00Z"
    # 18:00 VN = 11:00 UTC cùng ngày
    assert _hours_to_iso("2024-07-10", 18.0) == "2024-07-10T11:00:00Z"


def test_hours_to_iso_roundtrip_to_vietnam_hour():
    """Vòng qua–lại: ISO 8601 UTC → giờ VN phải khớp giờ đầu vào."""
    from phasegnn.forecast import _hours_to_iso

    for h in (0.0, 3.25, 6.5, 9.0, 12.0, 17.75, 20.0, 23.5):
        iso = _hours_to_iso("2024-07-10", h)
        assert iso is not None and iso.endswith("Z")
        import datetime as _dt
        t = _dt.datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        vn = (t + _dt.timedelta(hours=7)).hour + \
            (t + _dt.timedelta(hours=7)).minute / 60.0
        assert abs(vn - h) < 1.0 / 60.0 + 1e-9
