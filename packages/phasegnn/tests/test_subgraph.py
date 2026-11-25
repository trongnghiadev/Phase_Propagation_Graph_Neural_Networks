"""Kiểm thử `train.build_station_subgraph` theo phiên bản và `forecast.load_checkpoint`.

Hồi quy cho lỗi trước 25/11/2026: điều kiện tìm cạnh `down` ngược chiều bị đảo nên đồ thị con
của mọi trạm **không có cạnh `down`** (v1). v2 giữ đúng một cạnh `down` cho mỗi cạnh `up` trên
đường biển → trạm. Checkpoint cũ (không có `subgraph_version`) phải được nạp với v1.
"""
from __future__ import annotations

import numpy as np
import pytest

from phasegnn import train as T
from phasegnn.data import GraphData

SEA = "SEA_VUNGTAU"


def _chain_graph(with_branch: bool = True) -> GraphData:
    """Biển → A → B → ST (cạnh `up`), mỗi cạnh có cạnh `down` ngược chiều; thêm nhánh A → X."""
    nodes = [SEA, "A", "B", "ST"] + (["X"] if with_branch else [])
    idx = {n: i for i, n in enumerate(nodes)}
    up_pairs = [(SEA, "A"), ("A", "B"), ("B", "ST")] + ([("A", "X")] if with_branch else [])
    src, dst, ed = [], [], []
    for a, b in up_pairs:
        src += [idx[a], idx[b]]
        dst += [idx[b], idx[a]]
        ed += [1, 0]                       # up: a → b; down: b → a
    e = len(src)
    return GraphData(node_ids=nodes, node_index=idx,
                     edge_index=np.asarray([src, dst], dtype=np.int64),
                     edge_dir=np.asarray(ed, dtype=np.int64),
                     tau_phys=np.arange(1, e + 1, dtype=np.float32) * 0.1,
                     edge_attr=np.zeros((e, 2), dtype=np.float32),
                     static_attr=np.zeros((len(nodes), 3), dtype=np.float32),
                     station_node={"ST": idx["ST"]})


def _pairs(sub: T.StationSubgraph, direction: int) -> set[tuple[str, str]]:
    m = np.asarray(sub.edge_dir) == direction
    ei = np.asarray(sub.edge_index)[:, m]
    return {(sub.node_ids[a], sub.node_ids[b]) for a, b in ei.T}


def test_v2_moi_canh_up_co_canh_down_nguoc_chieu():
    sub = T.build_station_subgraph(_chain_graph(), "ST", version=2)
    up, down = _pairs(sub, 1), _pairs(sub, 0)
    assert up == {(SEA, "A"), ("A", "B"), ("B", "ST")}
    assert len(down) == len(up)
    assert down == {(b, a) for a, b in up}
    assert "X" not in sub.node_ids          # nhánh ngoài đường đi không được thêm


def test_v1_giu_hanh_vi_cu_khong_co_canh_down():
    sub = T.build_station_subgraph(_chain_graph(), "ST", version=1)
    assert len(_pairs(sub, 1)) == 3
    assert _pairs(sub, 0) == set()


def test_v2_tau_khop_canh():
    g = _chain_graph()
    sub = T.build_station_subgraph(g, "ST", version=2)
    # mặc định lượt mới là v2 (+ tau_mode phase) theo ablation 25/11/2026
    assert T.SUBGRAPH_VERSION == 2 and T.TrainConfig().subgraph_version == 2
    assert len(_pairs(sub, 0)) == 3
    # τ_phys của đồ thị con lấy đúng từ cạnh tương ứng trong đồ thị toàn cục
    full = {(g.node_ids[a], g.node_ids[b], int(d)): float(t) for (a, b), d, t in
            zip(g.edge_index.T, g.edge_dir, g.tau_phys)}
    for (a, b), d, t in zip(np.asarray(sub.edge_index).T, sub.edge_dir, sub.tau_phys):
        assert full[(sub.node_ids[a], sub.node_ids[b], int(d))] == pytest.approx(float(t))


def test_phien_ban_khong_hop_le():
    with pytest.raises(ValueError):
        T.build_station_subgraph(_chain_graph(), "ST", version=3)


@pytest.mark.parametrize("stored, expected", [(None, 1), (1, 1), (2, 2)])
def test_load_checkpoint_doc_subgraph_version(tmp_path, stored, expected):
    torch = pytest.importorskip("torch")
    from phasegnn.forecast import load_checkpoint
    from phasegnn.model import ModelConfig, PhaseGNN

    mcfg = ModelConfig(hidden_dim=8, delta_h=12.0)
    model = PhaseGNN(mcfg, in_dim=4, static_dim=3, edge_dim=2)
    tc = dict(T.TrainConfig(hidden_dim=8, delta_h=12.0).__dict__)
    if stored is None:
        tc.pop("subgraph_version")               # checkpoint trước 25/11/2026
    else:
        tc["subgraph_version"] = stored
    path = tmp_path / "ckpt.pt"
    torch.save({"model_state": model.state_dict(), "config": mcfg.to_dict(), "train_config": tc,
                "dims": {"in_dim": 4, "static_dim": 3, "edge_dim": 2}, "result": {}}, path)
    _m, tcfg, _r = load_checkpoint(path)
    assert tcfg.subgraph_version == expected
