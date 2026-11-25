"""Đồ thị con v3 (rút gọn đường đi biển/hồ → trạm), bố cục đặc trưng v3 và `tau_mode="phase"`."""
from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pytest

torch = pytest.importorskip("torch")

from phasegnn import train as T  # noqa: E402
from phasegnn.data import DYNAMIC_FEATURES, STATIC_FEATURE_NAMES, GraphData  # noqa: E402
from phasegnn.layers import PhaseMessage  # noqa: E402
from phasegnn.model import ModelConfig, PhaseGNN  # noqa: E402

SEA = "SEA_VUNGTAU"


def _graph() -> GraphData:
    """Biển → A → ST → B → RES (cạnh `up`, τ_up = 1, 2, 3, 4 h), mỗi cạnh có `down` ngược
    (τ_down = 10, 20, 30, 40 h) trừ cạnh rời biển; thêm nhánh A → X không có hồ."""
    nodes = [SEA, "A", "ST", "B", "RES", "X"]
    idx = {n: i for i, n in enumerate(nodes)}
    up = [(SEA, "A", 1.0, None), ("A", "ST", 2.0, 20.0), ("ST", "B", 3.0, 30.0),
          ("B", "RES", 4.0, 40.0), ("A", "X", 5.0, 50.0)]
    src, dst, ed, tau, length = [], [], [], [], []
    for a, b, tu, td in up:
        src.append(idx[a]); dst.append(idx[b]); ed.append(1); tau.append(tu); length.append(1.0)
        if td is not None:
            src.append(idx[b]); dst.append(idx[a]); ed.append(0); tau.append(td); length.append(2.0)
    e = len(src)
    attr = np.zeros((e, len(T.EDGE_ATTR_NAMES)), dtype=np.float32)
    col = {k: i for i, k in enumerate(T.EDGE_ATTR_NAMES)}
    attr[:, col["length_km"]] = length
    attr[:, col["log_tau"]] = np.log(tau)
    static = np.zeros((len(nodes), len(STATIC_FEATURE_NAMES) + 1), dtype=np.float32)
    static[idx["RES"], STATIC_FEATURE_NAMES.index("is_reservoir")] = 1.0
    return GraphData(node_ids=nodes, node_index=idx,
                     edge_index=np.asarray([src, dst], dtype=np.int64),
                     edge_dir=np.asarray(ed, dtype=np.int64),
                     tau_phys=np.asarray(tau, dtype=np.float32), edge_attr=attr,
                     static_attr=static, station_node={"ST": idx["ST"]})


def _edges(sub):
    return {(sub.node_ids[a], sub.node_ids[b], int(d)): float(t)
            for (a, b), d, t in zip(np.asarray(sub.edge_index).T, sub.edge_dir, sub.tau_phys)}


def test_v3_noi_bien_va_ho_vao_tram_voi_tau_tich_luy():
    sub = T.build_station_subgraph(_graph(), "ST", version=3)
    assert sub.version == 3
    assert sub.node_ids[sub.station_node] == "ST"
    assert set(sub.node_ids) == {"ST", SEA, "RES"}          # không có nút trung gian / nhánh X
    e = _edges(sub)
    assert e[(SEA, "ST", 1)] == pytest.approx(1.0 + 2.0)    # Σ τ_up biển → trạm
    assert e[("ST", SEA, 0)] == pytest.approx(20.0)         # cạnh rời biển không có `down`
    assert e[("RES", "ST", 0)] == pytest.approx(30.0 + 40.0)  # xả hồ xuôi dòng vào trạm
    assert e[("ST", "RES", 1)] == pytest.approx(3.0 + 4.0)
    down_in = [k for k in e if k[1] == "ST" and k[2] == 0]
    assert len(down_in) == 1
    assert [sub.node_ids[r] for r in sub.reservoir_nodes] == ["RES"]
    col = {k: i for i, k in enumerate(T.EDGE_ATTR_NAMES)}
    k = list(_edges(sub)).index(("RES", "ST", 0))
    assert sub.edge_attr[k, col["is_down"]] == 1.0
    assert sub.edge_attr[k, col["length_km"]] == pytest.approx(4.0)
    assert sub.edge_attr[k, col["log_tau"]] == pytest.approx(np.log(70.0))


def test_v1_v2_khong_doi():
    g = _graph()
    for v in (1, 2):
        sub = T.build_station_subgraph(g, "ST", version=v)
        assert sub.version == v and "RES" not in sub.node_ids


def _sample(t_in: int = 3):
    f = len(DYNAMIC_FEATURES)
    feat = np.zeros((t_in, f), dtype=np.float32)
    ix = {k: i for i, k in enumerate(DYNAMIC_FEATURES)}
    feat[:, ix["h_peak1"]] = 0.5
    feat[:, ix["astro_h1"]] = 7.0
    feat[:, ix["astro_h2"]] = 7.0
    feat[:, ix["res_spill_m3s"]] = np.arange(t_in) + 1.0
    feat[:, ix["res_turbine_m3s"]] = 3.0
    z = np.zeros((2, 2))
    return SimpleNamespace(features=feat, targets=z, target_hours=z, target_lows=z,
                           peak_heights=np.zeros(2), peak_hours=np.zeros(2),
                           targets_scaled=None, peak_heights_scaled=None), ix


def test_prepare_sample_v3_dat_trieu_vao_bien_va_xa_ho_vao_nut_ho():
    sub = T.build_station_subgraph(_graph(), "ST", version=3)
    cfg = ModelConfig(delta_h=12.0)
    smp, ix = _sample()
    st = T.prepare_sample(smp, sub, cfg, t_in=3, t_out=2)
    x = st.node_seq
    sea = sub.sea_node
    res = sub.reservoir_nodes[0]
    assert np.allclose(x[sea, :, 0:2], 7.0)                       # astro_h → độ cao nút biển
    assert np.allclose(x[sub.station_node, :, 0], 0.5)            # quan trắc trạm giữ nguyên
    assert np.allclose(x[res, :, ix["res_turbine_m3s"]], 3.0)
    assert set(np.unique(x[res, :, ix["res_spill_m3s"]])) <= {1.0, 2.0, 3.0}
    assert np.allclose(x[res, :, 0:4], 0.0)                       # nút hồ không có mực nước


def test_tau_mode_phase_cho_gradient_theo_delta():
    torch.manual_seed(0)
    h = torch.randn(2, 3, 6, 4)
    ei = torch.tensor([[1, 2], [0, 0]])
    ea = torch.randn(2, 2)
    ed = torch.tensor([1, 0])
    tau = torch.tensor([0.9, 30.0])
    grads = {}
    for mode in ("grid", "phase"):
        torch.manual_seed(1)
        m = PhaseMessage(node_dim=4, edge_dim=2, delta_h=12.0, tau_mode=mode)
        nn_init = m.delta_mlp[-1]
        torch.nn.init.normal_(nn_init.weight, std=0.01)           # δ ≠ 0 để có đường gradient
        msg, _, _ = m.forward_batch(h, tau, ei, ea, ed)
        msg.pow(2).sum().backward()
        grads[mode] = float(nn_init.weight.grad.abs().sum())
        assert ("phase_proj.weight" in m.state_dict()) == (mode == "phase")
    assert grads["phase"] > 0
    assert grads["phase"] != pytest.approx(grads["grid"])


def test_phase_embedding_tuan_hoan_theo_m2():
    m = PhaseMessage(node_dim=3, edge_dim=1, tau_mode="phase")
    t = torch.tensor([1.0, 1.0 + 12.4206012 * 25.0])
    emb = m.phase_embedding(t)
    # chu kỳ M2 nhưng không chu kỳ 24 h ⇒ khác nhau, song thành phần M2 trùng
    a = 2 * np.pi * t.numpy() / 12.4206012
    assert np.allclose(np.sin(a[0]), np.sin(a[1]), atol=1e-4)
    assert emb.shape == (2, 3)


def test_mac_dinh_luot_moi_va_checkpoint_cu(tmp_path):
    from phasegnn.forecast import load_checkpoint

    assert T.SUBGRAPH_VERSION == 2
    tc = T.TrainConfig(hidden_dim=8, delta_h=12.0)
    assert tc.subgraph_version == 2 and tc.tau_mode == "phase"
    assert T._make_model_cfg(tc).tau_mode == "phase"
    assert ModelConfig().tau_mode == "grid"
    # checkpoint cũ: không có `tau_mode` ở cả config lẫn train_config ⇒ grid, v1
    mcfg = ModelConfig(hidden_dim=8, delta_h=12.0)
    model = PhaseGNN(mcfg, in_dim=4, static_dim=3, edge_dim=2)
    d = mcfg.to_dict()
    d.pop("tau_mode")
    tcd = dict(tc.__dict__)
    tcd.pop("tau_mode")
    tcd.pop("subgraph_version")
    path = tmp_path / "old.pt"
    torch.save({"model_state": model.state_dict(), "config": d, "train_config": tcd,
                "dims": {"in_dim": 4, "static_dim": 3, "edge_dim": 2}, "result": {}}, path)
    m2, tcfg, _ = load_checkpoint(path)
    assert m2.cfg.tau_mode == "grid" and tcfg.tau_mode == "grid" and tcfg.subgraph_version == 1


def test_device_info_nap_duoc_voi_weights_only(tmp_path):
    """`device_info` ghi vào checkpoint phải chỉ gồm kiểu cơ bản (trước đây `TorchVersion`
    làm `torch.load(weights_only=True)` từ chối mọi checkpoint mới)."""
    from phasegnn.device import device_info

    info = device_info("cpu")
    assert all(isinstance(v, (str, bool, int, float)) for v in info.values())
    path = tmp_path / "d.pt"
    torch.save({"result": {"config": {"device_info": info}}}, path)
    assert torch.load(path, weights_only=True)["result"]["config"]["device_info"] == info
