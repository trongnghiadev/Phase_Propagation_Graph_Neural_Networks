"""Huấn luyện PhaseGNN và các biến thể (checklist 6.6).

**Cách chạy (CPU, trong Docker).**

    docker build -f packages/phasegnn/Dockerfile -t phasegnn:local .
    docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
        python -m phasegnn.train --variant phasegnn --epochs 20 --out-dir results/E2/phasegnn

**Mô hình.** Xem `model.py`. Ở đây ta huấn luyện trên **tập cửa sổ ngày** của G5: mỗi mẫu là
(station, ngày phát bản tin) với `T_in` ngày quan trọc và `T_out` ngày dự báo.

**Bản đồ nút → trạm.** Mỗi nút reach có thể mang 0 hoặc nhiều mã trạm. Ta dựng một đồ
thị con gồm: nút biển, các nút trạm, và **đường đi** nối chúng (đường đi dài nhất theo
chiều `up` từ biển tới mỗi trạm, và đường đi xuôi từ mỗi trạm về biển) — nhờ vậy mỗi
mẫu chỉ cần ~30–60 nút thay vì 647 nút, nhưng vẫn giữ đúng cấu trúc lan truyền pha hai
chiều (đề cương 5.2.4).

**Dừng sớm** theo MAE đỉnh ngày trên tập val (đề cương 5.2.6), `patience` mặc định 10.

**Cố định seed** (`--seed`) để chạy lại được; checklist yêu cầu 5 seed cho mỗi biến thể
(xem `scripts/run_variants.py`).
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Sequence

import numpy as np

from .data import (DEFAULT_T_IN, DEFAULT_T_OUT, DYNAMIC_FEATURES, SPLITS, FeatureScaler,
                   GraphData, SampleSet, WindowSample, build_datasets, check_leakage,
                   default_paths, load_g5, load_g5_meta, load_graph)
from .losses import (QUANTILES, circular_distance_h, mae, mono_loss, peak_mae,
                     peak_hour_mae, pinball_loss, soft_peak_height, tau_loss)
from .model import (ModelConfig, PhaseGNN, configure_threads, daily_grid,
                    interpolate_to_grid, seed_everything)
from .registry import MlflowTracker, ModelRecord, optuna_search, register_model, save_registry_json

#: Biến thể mô hình → cấu hình (đề cương 5.2.7).
VARIANTS: dict[str, dict] = {
    "phasegnn": {"model_kind": "phasegnn", "learn_tau": True, "directions": (True, True)},
    "B3": {"model_kind": "graphsage", "learn_tau": False, "directions": (True, True)},
    "B4": {"model_kind": "phasegnn", "learn_tau": False, "directions": (True, True)},
    "B5a": {"model_kind": "phasegnn", "learn_tau": True, "directions": (False, True)},
    "B5b": {"model_kind": "phasegnn", "learn_tau": True, "directions": (True, False)},
}


# ---------------------------------------------------------------------------
# Đồ thị con theo trạm
# ---------------------------------------------------------------------------
@dataclass
class StationSubgraph:
    """Đồ thị con chứa đường đi pha hai chiều của một trạm."""

    node_ids: list[str]
    node_index: dict[str, int]
    edge_index: np.ndarray          # [2, E] (chỉ số trong đồ thị con)
    edge_dir: np.ndarray            # [E]
    tau_phys: np.ndarray            # [E]
    edge_attr: np.ndarray           # [E, D]
    static: np.ndarray              # [N, S]
    station_node: int               # chỉ số node của trạm trong đồ thị con
    sea_node: int                   # chỉ số node biển


def build_station_subgraph(graph: GraphData, station: str,
                           sea_id: str = "SEA_VUNGTAU") -> StationSubgraph:
    """Dựng đồ thị con: biển ↔ trạm, theo cả hai chiều.

    **Cấu trúc đồ thị.** Cạnh `up` đi **từ biển vào thượng nguồn** (hướng sóng triều
    đi vào) và cạnh `down` đi **xuôi dòng chảy** (từ thượng nguồn ra biển). Mỗi cặp
    nút liền nhau trên dòng chảy có **hai cạnh** (một `up`, một `down`) với hai τ khác
    nhau — đó là cơ chế lan truyền pha hai chiều của PhaseGNN.

    Ta giữ lại:
    * **đường đi dài nhất theo chiều `up`** từ nút biển tới nút trạm (đường đi của sóng
      triều — sóng đi vào theo nhánh bị trễ nhất), và
    * **đường đi xuôi dòng chảy** từ nút trạm về biển.

    Cả hai chiều của mỗi cặp cạnh trên đường đi `up` được giữ ⇒ đồ thị con có đúng
    cấu trúc hai chiều nhưng chỉ gồm ~10–30 nút thay vì 647 nút.

    **Lưu ý:** mã trạm (`"PHU_AN"`) và **mã nút** (`"44043114"`) là hai thứ khác nhau —
    `graph.station_node` ánh xạ mã trạm → *chỉ số* node. BFS dưới đây làm việc với
    *mã nút*.
    """
    if station not in graph.station_node:
        raise KeyError(f"trạm {station!r} không có nút trong đồ thị")
    if sea_id not in graph.node_index:
        raise KeyError(f"nút biển {sea_id!r} không có trong đồ thị")

    st_node = graph.node_ids[graph.station_node[station]]
    keep: set[str] = {sea_id, st_node}
    edge_keep: set[int] = set()

    # --- đường đi theo chiều `up` từ biển tới trạm --------------------------
    # `up` đi biển → thượng nguồn, nên ta dò BFS **xuôi** theo cạnh `up` từ biển và
    # ghi lại đường đi (predecessor) để truy ngược về trạm.
    adj_up: dict[str, list[tuple[str, int]]] = {}
    for e in range(graph.num_edges):
        if graph.edge_dir[e] != 1:
            continue
        s = graph.node_ids[graph.edge_index[0, e]]
        d = graph.node_ids[graph.edge_index[1, e]]
        adj_up.setdefault(s, []).append((d, e))

    # BFS từ nút trạm theo cạnh `up` **ngược** (tức đi từ dst → src) để tìm nút biển:
    # nút biển là nguồn (`src`) của cạnh `up` đầu tiên.
    rev_up: dict[str, list[tuple[str, int]]] = {}
    for e in range(graph.num_edges):
        if graph.edge_dir[e] != 1:
            continue
        s = graph.node_ids[graph.edge_index[0, e]]
        d = graph.node_ids[graph.edge_index[1, e]]
        rev_up.setdefault(d, []).append((s, e))

    prev: dict[str, tuple[str, int]] = {}      # nút → (nút cha, chỉ số cạnh)
    queue = [st_node]
    seen = {st_node}
    found = False
    while queue and not found:
        cur = queue.pop(0)
        for par, e in rev_up.get(cur, ()):
            if par in seen:
                continue
            seen.add(par)
            prev[par] = (cur, e)
            if par == sea_id:
                found = True
                break
            queue.append(par)

    # dựng đường đi từ nút biển ngược lại trạm
    if found:
        cur = sea_id
        while cur in prev:
            par, e = prev[cur]           # cạnh `up`: par (thượng nguồn) → cur (hạ nguồn)
            keep.add(cur)
            keep.add(par)
            edge_keep.add(e)
            # cạnh `down` của cùng cặp nút (chiều ngược lại)
            for e2 in range(graph.num_edges):
                if graph.edge_dir[e2] == 0 \
                        and graph.node_ids[graph.edge_index[0, e2]] == cur \
                        and graph.node_ids[graph.edge_index[1, e2]] == par:
                    edge_keep.add(e2)
                    break
            cur = par

    node_ids = sorted(keep)
    node_index = {v: i for i, v in enumerate(node_ids)}
    edges = sorted(edge_keep)
    src = np.asarray([node_index[graph.node_ids[graph.edge_index[0, e]]] for e in edges],
                     dtype=np.int64)
    dst = np.asarray([node_index[graph.node_ids[graph.edge_index[1, e]]] for e in edges],
                     dtype=np.int64)
    edge_index = np.stack([src, dst], axis=0)
    edge_dir = graph.edge_dir[edges]
    tau = graph.tau_phys[edges]
    eattr = graph.edge_attr[edges]
    static = graph.static_attr[[node_index[v] for v in node_ids]]
    return StationSubgraph(
        node_ids=node_ids,
        node_index=node_index,
        edge_index=edge_index,
        edge_dir=edge_dir,
        tau_phys=tau,
        edge_attr=eattr,
        static=static,
        station_node=node_index[st_node],
        sea_node=node_index[sea_id],
    )


# ---------------------------------------------------------------------------
# Chuẩn bị tensor cho một mẫu
# ---------------------------------------------------------------------------
@dataclass
class SampleTensors:
    """Tensor đầu vào/nhãn của một mẫu cho mô hình trên đồ thị con của trạm."""

    node_seq: np.ndarray        # [N, T, F + GRID_CLOCK_CHANNELS]
    peak_target: np.ndarray     # [T_out, 2]
    peak_hour_target: np.ndarray  # [T_out, 2]
    low_target: np.ndarray      # [T_out, 2]
    peak_height_target: np.ndarray   # [T_out]
    peak_hour_target_day: np.ndarray  # [T_out]
    #: Nhãn độ cao **ở thang chuẩn hoá** (cùng thang với `node_seq`) — xem `HeightScale`.
    #: `None` ⇒ dùng nhãn tính bằng mét (hành vi cũ).
    peak_target_scaled: np.ndarray | None = None
    peak_height_target_scaled: np.ndarray | None = None


#: Số kênh **đồng hồ lưới** bổ sung vào `node_seq`.
#:
#: **Sai lệch đã sửa (2026-11-24).** Trước đây `prepare_sample` ghi đè cột giờ quan
#: sát (`t_peak1_h`, `t_peak2_h`) của node trạm bằng `grid_hours` (0, 12, 24, …) — tức
#: **xoá mất thông tin giờ đỉnh quan trọc** và đồng thời chèn một đặc trưng **chưa
#: chuẩn hoá** (0..24) vào giữa đặc trưng đã z-score. Hệ quả: mô hình không biết đỉnh
#: quan trọc xảy ra lúc mấy giờ ⇒ không thể học pha, và `L_timing` không có tín hiệu.
#:
#: Nay đồng hồ lưới được **bổ sung** thành 2 kênh riêng (chuẩn hoá về [-1, 1]) mà không
#: đụng tới các cột quan sát.
GRID_CLOCK_CHANNELS = 2

#: Chuẩn hoá đồng hồ lưới: `(h − 12) / 12` ⇒ khoảng [-1, 1].
def _normalize_grid_clock(hours: np.ndarray) -> np.ndarray:
    return ((hours.astype(np.float32) - 12.0) / 12.0)


def prepare_sample(sample: WindowSample, sub: StationSubgraph, cfg: ModelConfig,
                   t_in: int, t_out: int) -> SampleTensors:
    """Dựng tensor cho một mẫu: nội suy đặc trưng động lên lưới giờ Δ.

    **Cấu trúc đồ thị con.** Ba loại nút:

    * nút **biển** (`SEA_VUNGTAU`): mang triều thiên văn (không có quan trọc thực đo).
    * nút **trạm**: mang quan trọc thực đo của trạm đó (đỉnh/chân + giờ).
    * nút reach trung gian: mang **trạng thái sóng** do PhaseGNN lan truyền (không có
      quan trọc trực tiếp).

    Vì dữ liệu quan trọc chỉ có ở trạm, ta đặt chuỗi quan trọc của trạm vào node trạm; các
    node trung gian bắt đầu từ trạng thái 0 và được cập nhật bởi cơ chế lan truyền pha
    (đúng vai trò của node reach trong bản đầy đủ).

    **Bố cục `node_seq`** (``[N, G, F + 2]``):

    * ``0:2``   — độ cao đỉnh 1/2 nội suy lên lưới Δ (đơn vị chuẩn hoá của G5).
    * ``2:4``   — **giờ đỉnh quan sát** 1/2 nội suy lên lưới Δ (giữ nguyên ý nghĩa;
      trước đây chỗ này bị ghi đè bằng đồng hồ lưới — xem `GRID_CLOCK_CHANNELS`).
    * ``4:F``   — các đặc trưng động còn lại theo ngày (`h_low*`, `t_low*`,
      `missing_*`, `res_*`, `rain_mm`, `astro_*`).
    * ``F:F+2`` — **đồng hồ lưới** (2 kênh, chuẩn hoá về [-1, 1]): `sin(2πh/24)` và
      `cos(2πh/24)`. Dùng sin/cos để biểu diễn giờ **vòng 24 h** mà không gián đoạn
      tại 0 h/24 h (nếu dùng giờ thô, mô hình phải học cả tính tuần hoàn).
    """
    grid_hours, day_index = daily_grid(t_in, cfg.delta_h)
    feat = sample.features                             # [T_in, F]
    n_nodes = len(sub.node_ids)
    n_feat = feat.shape[1]

    # đặc trưng động: độ cao đỉnh 1/2 + giờ đỉnh 1/2 (4 cột đầu của DYNAMIC_FEATURES)
    heights = feat[:, 0:2]
    hours = feat[:, 2:4]
    hourly_h = interpolate_to_grid(heights, hours, grid_hours)       # [G, 2]
    # **giờ quan sát** nội suy lên lưới Δ (giữ nguyên; không ghi đè)
    hourly_t = interpolate_to_grid(hours, hours, grid_hours)         # [G, 2]
    rest = feat[:, 4:]                                              # [T_in, F-4]
    rest_grid = rest[day_index]                                     # [G, F-4]

    # đặc trưng tĩnh của node biển: thêm giờ triều thiên văn để node biển "biết" sóng
    node_seq = np.zeros((n_nodes, len(grid_hours), n_feat + GRID_CLOCK_CHANNELS),
                        dtype=np.float32)

    # node trạm: toàn bộ đặc trưng động của trạm
    st = sub.station_node
    node_seq[st, :, 0:2] = hourly_h
    node_seq[st, :, 2:4] = hourly_t
    node_seq[st, :, 4:n_feat] = rest_grid

    # node biển: đặc trưng triều thiên văn (nếu có trong mẫu) — dùng `astro_h1/astro_h2`
    # (cột 12, 13 của DYNAMIC_FEATURES) và giờ tương ứng (14, 15).
    if n_feat >= 16:
        astro_h = feat[:, 12:14]
        astro_t = feat[:, 14:16]
        a_hourly = interpolate_to_grid(astro_h, astro_t, grid_hours)
        a_t_hourly = interpolate_to_grid(astro_t, astro_t, grid_hours)
        sea = sub.sea_node
        node_seq[sea, :, 0:2] = a_hourly
        node_seq[sea, :, 2:4] = a_t_hourly

    # đồng hồ lưới (2 kênh sin/cos) cho **mọi** node
    ang = 2.0 * np.pi * (grid_hours.astype(np.float32) / 24.0)
    node_seq[:, :, n_feat] = np.sin(ang)[None, :]
    node_seq[:, :, n_feat + 1] = np.cos(ang)[None, :]

    return SampleTensors(
        node_seq=node_seq,
        peak_target=sample.targets,
        peak_hour_target=sample.target_hours,
        low_target=sample.target_lows,
        peak_height_target=sample.peak_heights,
        peak_hour_target_day=sample.peak_hours,
        peak_target_scaled=sample.targets_scaled,
        peak_height_target_scaled=sample.peak_heights_scaled,
    )


# ---------------------------------------------------------------------------
# Vòng huấn luyện
# ---------------------------------------------------------------------------
@dataclass
class TrainConfig:
    variant: str = "phasegnn"
    epochs: int = 30
    batch_size: int = 16
    lr: float = 1e-3
    weight_decay: float = 1e-4
    seed: int = 0
    t_in: int = DEFAULT_T_IN
    t_out: int = DEFAULT_T_OUT
    hidden_dim: int = 64
    num_gnn_layers: int = 2
    delta_h: float = 1.0
    lam1: float = 1.0
    lam2: float = 0.5
    lam3: float = 0.1
    lam4: float = 0.1
    patience: int = 10
    max_train_samples: int | None = None
    max_val_samples: int | None = None
    aux_weight: float = 0.3
    device: str = "cpu"
    #: So luong CPU cho torch. Mac dinh 1: khoi luong nho (do thi con) bi da luong
    #: lam chay cham hon ~20 lan (do lai 24/11/2026).
    num_threads: int = 1
    #: Ghi theo doi MLflow (co the tat khi khong co MLflow server).
    use_mlflow: bool = True
    #: Ghi ban ghi mo hinh vao collection `models` (MongoDB).
    register_to_db: bool = True
    #: Ten bai toan MLflow.
    mlflow_experiment: str = "phasegnn"


@dataclass
class TrainResult:
    variant: str
    best_val_mae: float
    best_epoch: int
    history: list[dict] = field(default_factory=list)
    data_snapshot: dict = field(default_factory=dict)
    config: dict = field(default_factory=dict)
    dims: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "variant": self.variant,
            "best_val_mae": self.best_val_mae,
            "best_epoch": self.best_epoch,
            "history": self.history,
            "data_snapshot": self.data_snapshot,
            "config": self.config,
            "dims": self.dims,
        }


def _make_model_cfg(tcfg: TrainConfig) -> ModelConfig:
    v = VARIANTS.get(tcfg.variant)
    if v is None:
        raise ValueError(f"variant không hợp lệ: {tcfg.variant!r} "
                         f"(chọn trong {list(VARIANTS)})")
    return ModelConfig(
        hidden_dim=tcfg.hidden_dim,
        num_gnn_layers=tcfg.num_gnn_layers,
        delta_h=tcfg.delta_h,
        learn_tau=v["learn_tau"],
        directions=v["directions"],
        model_kind=v["model_kind"],
    )


def train_model(datasets: dict[str, SampleSet], graph: GraphData, tcfg: TrainConfig,
                log=print) -> tuple[PhaseGNN, TrainResult]:
    """Huấn luyện một biến thể trên `train`, dừng sớm theo MAE đỉnh ngày trên `val`."""
    import torch
    from torch import nn

    seed_everything(tcfg.seed)
    configure_threads(tcfg.num_threads)
    cfg = _make_model_cfg(tcfg)
    device = torch.device(tcfg.device)

    train_set = datasets["train"]
    val_set = datasets.get("val")
    if not train_set.samples:
        raise ValueError("tập huấn luyện rỗng")

    # --- đồ thị con cho từng trạm ------------------------------------------
    subgraphs: dict[str, StationSubgraph] = {}
    for st in sorted({s.station for s in train_set.samples}):
        try:
            subgraphs[st] = build_station_subgraph(graph, st)
        except KeyError:
            log(f"[CẢNH BÁO] bỏ qua trạm {st}: không tìm thấy node trong đồ thị")

    # kích thước đặc trưng
    # (`node_seq` có thêm `GRID_CLOCK_CHANNELS` kênh đồng hồ lưới — xem `prepare_sample`)
    in_dim = len(train_set.feature_names) + GRID_CLOCK_CHANNELS
    static_dim = graph.static_attr.shape[1]
    edge_dim = graph.edge_attr.shape[1]
    model = PhaseGNN(cfg, in_dim=in_dim, static_dim=static_dim, edge_dim=edge_dim).to(device)

    train_samples = [s for s in train_set.samples if s.station in subgraphs]
    if tcfg.max_train_samples:
        train_samples = train_samples[:tcfg.max_train_samples]
    val_samples = [s for s in (val_set.samples if val_set else []) if s.station in subgraphs]
    if tcfg.max_val_samples:
        val_samples = val_samples[:tcfg.max_val_samples]

    opt = torch.optim.AdamW(model.parameters(), lr=tcfg.lr, weight_decay=tcfg.weight_decay)
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(opt, T_max=max(tcfg.epochs, 1))

    best_mae = float("inf")
    best_state: dict | None = None
    best_epoch = -1
    history: list[dict] = []
    since_best = 0

    for ep in range(tcfg.epochs):
        model.train()
        order = np.random.permutation(len(train_samples))
        tot_loss = 0.0
        n_batch = 0
        t0 = time.time()
        for i in range(0, len(order), tcfg.batch_size):
            batch = [train_samples[j] for j in order[i:i + tcfg.batch_size]]
            tensors = _batch_tensors(batch, subgraphs, cfg, tcfg, device)
            loss, parts = _loss_for_batch(model, tensors, cfg, tcfg, device)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            tot_loss += float(loss.detach())
            n_batch += 1
        train_loss = tot_loss / max(n_batch, 1)

        val_metrics = evaluate(model, val_samples, subgraphs, cfg, tcfg, device,
                               height_scale=train_set.height_scale) \
            if val_samples else {"mae_peak": float("nan")}
        val_mae = val_metrics.get("mae_peak", float("nan"))
        history.append({"epoch": ep, "train_loss": train_loss, "val": val_metrics,
                        "seconds": time.time() - t0})
        log(f"[{tcfg.variant}] epoch {ep}: train_loss = {train_loss:.5f} | "
            f"val MAE đỉnh = {val_mae:.4f} m ({time.time() - t0:.1f}s)")

        if not math.isnan(val_mae) and val_mae < best_mae - 1e-6:
            best_mae = val_mae
            best_epoch = ep
            best_state = {k: v.detach().clone() for k, v in model.state_dict().items()}
            since_best = 0
        else:
            since_best += 1
            if since_best >= tcfg.patience:
                log(f"[{tcfg.variant}] dừng sớm sau {since_best} epoch không cải thiện.")
                break
        scheduler.step()

    if best_state is not None:
        model.load_state_dict(best_state)
    return model, TrainResult(variant=tcfg.variant, best_val_mae=best_mae,
                               best_epoch=best_epoch, history=history,
                               config=tcfg.__dict__.copy(),
                               dims={"in_dim": in_dim, "static_dim": static_dim,
                                     "edge_dim": edge_dim})


def _batch_tensors(batch, subgraphs, cfg, tcfg, device) -> list[tuple]:
    """Chuẩn bị (tensor, target, weight) cho từng mẫu trong lô.

    **Gom theo trạm.** Các mẫu cùng trạm dùng chung đồ thị con ⇒ ta gom chúng thành
    một lô gọi `PhaseGNN.forward_batch` (nhanh hơn 2–4× trên CPU, kết quả số học giống
    hệt). Lô trả về có dạng:

        `(sub, node_seq[B,N,T,F], static[N,S], ei, ea, ed, tau, st_idx, tgt, tgt_h, ph, ph_t, w[B])`
    """
    import torch

    groups: dict[str, list] = {}
    for s in batch:
        groups.setdefault(s.station, []).append(s)

    out = []
    for st_name, samples in groups.items():
        sub = subgraphs[st_name]
        sts = [prepare_sample(s, sub, cfg, tcfg.t_in, tcfg.t_out) for s in samples]
        node_seq = torch.as_tensor(np.stack([x.node_seq for x in sts]),
                                   dtype=torch.float32, device=device)
        ei = torch.as_tensor(sub.edge_index, dtype=torch.long, device=device)
        ea = torch.as_tensor(sub.edge_attr, dtype=torch.float32, device=device)
        ed = torch.as_tensor(sub.edge_dir, dtype=torch.long, device=device)
        tau = torch.as_tensor(sub.tau_phys, dtype=torch.float32, device=device)
        static = torch.as_tensor(sub.static, dtype=torch.float32, device=device)
        tgt = torch.as_tensor(np.stack([x.peak_target for x in sts]),
                              dtype=torch.float32, device=device)
        tgt_h = torch.as_tensor(np.stack([x.peak_hour_target for x in sts]),
                                dtype=torch.float32, device=device)
        ph = torch.as_tensor(np.stack([x.peak_height_target for x in sts]),
                             dtype=torch.float32, device=device)
        ph_t = torch.as_tensor(np.stack([x.peak_hour_target_day for x in sts]),
                               dtype=torch.float32, device=device)
        # **Nhãn ở thang chuẩn hoá** (cùng thang với `node_seq`) — xem `HeightScale`.
        # Nếu không có (mẫu cũ / không có thang), dùng nhãn tính bằng mét.
        if sts[0].peak_target_scaled is not None:
            tgt_s = torch.as_tensor(np.stack([x.peak_target_scaled for x in sts]),
                                    dtype=torch.float32, device=device)
            ph_s = torch.as_tensor(np.stack([x.peak_height_target_scaled for x in sts]),
                                   dtype=torch.float32, device=device)
        else:
            tgt_s, ph_s = None, None
        w = 1.0 if st_name in ("PHU_AN", "NHA_BE", "THU_DAU_MOT") else tcfg.aux_weight
        out.append((sub, node_seq, static, ei, ea, ed, tau, int(sub.station_node),
                    tgt, tgt_h, ph, ph_t, float(w), tgt_s, ph_s))
    return out


def _sort_slots_by_hour(values: "torch.Tensor", hours: "torch.Tensor") -> "torch.Tensor":
    """Sắp xếp các **slot đỉnh** theo giờ tăng dần (trục slot = -2).

    Tham số
    -------
    values : tensor có trục slot ở vị trí ``-2`` (ví dụ ``[B, T, 2, Q]`` hoặc ``[B, 2]``).
    hours : tensor có trục slot ở vị trí ``-1`` (ví dụ ``[B, T, 2]`` hoặc ``[B, 2]``).

    Trả về `values` với 2 slot đã sắp xếp theo giờ.

    **Vì sao cần?** Quy ước chung (`schemas/prediction/v1.schema.json`,
    `pipelines/r/R/evaluation.R`) là `peak_idx = 0` là **đỉnh thứ nhất trong ngày**
    (đỉnh sớm hơn). Cả nhãn (`targets = [h_peak1, h_peak2]`, đã theo giờ) lẫn dự báo
    đều phải tuân theo quy ước đó, nếu không hai đỉnh có thể bị hoán đổi ⇒ sai số độ
    cao rất lớn (đo được: MAE test 0,70 m trước khi sửa).
    """
    import torch

    if values.dim() < 2:
        return values
    # Trục slot: với tensor ≥ 3 chiều thì slot nằm ở ``-2`` ( ví dụ ``[B, T, 2, Q]``);
    # với tensor 2 chiều ``[B, 2]`` thì slot nằm ở chiều cuối.
    slot_dim = values.dim() - 2 if values.dim() >= 3 else values.dim() - 1
    # chỉ số theo giờ: dùng giờ đã quy về vòng 24 h để sắp xếp ổn định
    h = torch.remainder(hours, 24.0)
    h = h.reshape(*h.shape[:h.dim() - 1], h.shape[-1])
    idx = torch.argsort(h, dim=slot_dim, stable=True)
    # `idx` phải có **cùng số chiều** với `values` để `gather` lấy đúng giá trị ở
    # từng phân vị (trục cuối của `values` là trục phân vị, không phải trục slot).
    if idx.dim() < values.dim():
        idx = idx.unsqueeze(-1).expand(*idx.shape, values.shape[-1])
    elif idx.dim() > values.dim():
        idx = idx.squeeze(-1)
    return torch.gather(values, slot_dim, idx)


def _loss_for_batch(model, tensors: Sequence[tuple], cfg: ModelConfig,
                    tcfg: TrainConfig, device) -> tuple:
    """Tính tổng hàm mất mát trên một lô (trả về `(loss, parts)`)."""
    import torch

    total = None
    parts: dict[str, float] = {}
    n_samples = 0
    for (_sub, node_seq, static, ei, ea, ed, tau, st_idx,
         tgt, tgt_h, ph, ph_t, w, tgt_s, ph_s) in tensors:
        out = model.forward_batch(node_seq, static, ei, ea, ed, tau,
                                  target_nodes=torch.as_tensor([st_idx], dtype=torch.long))
        b = node_seq.shape[0]
        # đầu ra [B, T, 2, Q]: lấy node trạm
        peak = out.peak_height[:, 0]           # [B, T, 2, Q]
        hours = out.peak_hour[:, 0]            # [B, T, 2]
        p_peak, p_hours, p_peak_day, p_hour_day = _select_daily(peak, hours, tcfg.t_out,
                                                                tcfg.delta_h)
        # **Dùng nhãn ở thang chuẩn hoá** nếu có (cùng thang với đầu vào) — xem
        # `data.HeightScale`. Nhờ vậy mô hình chỉ học *độ lệch* so với mức trung bình
        # của trạm ⇒ gradient cân bằng giữa các trạm (trước đây nhãn tính bằng mét
        # khiến mô hình chỉ học được một hằng số toàn cục ≈ 0,88 m ⇒ MAE test 0,70 m).
        if tgt_s is not None:
            tgt_used, ph_used = tgt_s, ph_s
        else:
            tgt_used, ph_used = tgt, ph
        # Sắp xếp **cả** dự báo và nhãn theo giờ để 2 slot tương ứng với nhau
        # (peak_idx = 0 ⇔ đỉnh sớm hơn) — xem `_sort_slots_by_hour`.
        p_peak = _sort_slots_by_hour(p_peak, p_hours)         # [B, t_out, 2, Q]
        tgt_used = _sort_slots_by_hour(tgt_used, tgt_h)       # [B, t_out, 2]
        tgt_h = _sort_slots_by_hour(tgt_h, tgt_h)              # [B, t_out, 2]
        # `pinball_loss` và `mono_loss` hoạt động trên trục cuối ⇒ gọi **một lần cho cả
        # lô** (`p_peak` = [B, t_out, 2, Q], `tgt_used` = [B, t_out, 2]) thay vì lặp Python.
        l_seq = pinball_loss(p_peak, tgt_used)
        valid_p = torch.isfinite(ph_used)
        l_peak = (torch.nn.functional.smooth_l1_loss(p_peak_day[valid_p], ph_used[valid_p])
                  if bool(valid_p.any()) else l_seq * 0.0)
        mask_h = torch.isfinite(ph_t) & torch.isfinite(p_hour_day)
        l_time = (circular_distance_h(p_hour_day[mask_h], ph_t[mask_h]).mean()
                  if bool(mask_h.any()) else l_seq * 0.0)
        l_tau = tau_loss(out.delta) if out.delta is not None else l_seq * 0.0
        l_mono = mono_loss(p_peak)
        loss = (l_seq + tcfg.lam1 * l_peak + tcfg.lam2 * l_time
                + tcfg.lam3 * l_tau + tcfg.lam4 * l_mono) * w
        total = loss if total is None else total + loss
        n_samples += b
        for k, v in (("L_seq", l_seq), ("L_peak", l_peak), ("L_timing", l_time),
                     ("L_tau", l_tau), ("L_mono", l_mono)):
            parts[k] = parts.get(k, 0.0) + float(v.detach()) * b
    n = max(n_samples, 1)
    return total / max(len(tensors), 1), {k: v / n for k, v in parts.items()}


def _select_daily(peak: "torch.Tensor", hours: "torch.Tensor", t_out: int,
                  delta_h: float) -> tuple:
    """Chọn giá trị đại diện cho mỗi ngày từ chuỗi giờ.

    Mỗi ngày có `S = 24/Δ` bước. Ta chọn **đỉnh dự báo** trong ngày (bước có độ cao q50
    lớn nhất) → độ cao đỉnh ngày và giờ tương ứng.

    Trả về ``(peak_by_day, hours_by_day, peak_day, hour_day)``.

    **Hỗ trợ cả lô (vector hoá).** Với `peak` dạng ``[T, 2, Q]`` (mẫu đơn) trả về
    ``[t_out, 2, Q]``; với `peak` dạng ``[B, T, 2, Q]`` (lô) trả về ``[B, t_out, 2, Q]``.

    **Cách chọn trong lô.** Ta reshape ``[B, T, 2, Q]`` → ``[B, t_out, S, 2, Q]`` (nhóm theo
    ngày) rồi `argmax` trên trục `S` ⇒ **một lệnh tensor cho cả lô**, thay vì lặp Python
    theo từng mẫu (lặp này làm chi phí huấn luyện tăng ~5× trên CPU).

    **Sai lệch đã sửa (2026-11-24).** Trước đây `hour_day` lấy `hr.max(dim=2)` — tức
    **giờ lớn nhất trong 2 slot đỉnh** — trong khi nhãn `peak_hours` (xem
    `data.build_samples`) là giờ của **đỉnh cao hơn**. Hai định nghĩa không cùng ý nghĩa
    ⇒ `L_timing` so sánh hai đại lượng không tương ứng và mô hình không thể học được
    giờ đỉnh. Ở đây `hour_day` = giờ **của đỉnh có q50 cao hơn** (đúng nhãn), còn
    `peak_day` = q50 của đỉnh đó.

    **Ý nghĩa từng giá trị trả về.**

    * ``peak_by_day`` ``[B, days, 2, Q]`` — với mỗi **slot đỉnh** (0/1), bộ phân vị tại
      **bước có q50 lớn nhất trong ngày**.
    * ``hours_by_day`` ``[B, days, 2]`` — giờ (0..24) của bước đó, theo slot.
    * ``peak_day`` ``[B, days]`` — **đỉnh cao nhất trong ngày** = max trên 2 slot của q50
      tại bước đã chọn (so được với nhãn `peak_heights` = max(h_peak1, h_peak2)).
    * ``hour_day`` ``[B, days]`` — giờ của **đỉnh cao nhất** (khớp nhãn `peak_hours`).
    """
    import torch

    steps_per_day = max(int(round(24.0 / delta_h)), 1)

    if peak.dim() == 4:
        b = peak.shape[0]
        T = peak.shape[1]
        days = min(t_out, T // steps_per_day)
        if days <= 0:
            return (peak[:, :0], hours[:, :0], peak[:, :0, :, 1], hours[:, :0])
        # [B, T, 2, Q] → [B, days, S, 2, Q]
        p_grp = peak[:, :days * steps_per_day].reshape(b, days, steps_per_day,
                                                       peak.shape[2], peak.shape[3])
        h_grp = hours[:, :days * steps_per_day].reshape(b, days, steps_per_day,
                                                       hours.shape[2])
        q50 = p_grp[:, :, :, :, 1]                     # [B, days, S, 2]
        # max theo **bước** (trục S = 2) ⇒ `best` = [B, days, 2]
        best = q50.max(dim=2).values                  # [B, days, 2] — q50 lớn nhất theo bước
        step_idx = q50.argmax(dim=2)                  # [B, days, 2] — bước của từng slot
        b_idx = torch.arange(b, device=peak.device).view(b, 1, 1).expand(b, days, 2)
        d_idx = torch.arange(days, device=peak.device).view(1, days, 1).expand(b, days, 2)
        s_idx = torch.arange(2, device=peak.device).view(1, 1, 2).expand(b, days, 2)
        pk = p_grp[b_idx, d_idx, step_idx, s_idx]     # [B, days, 2, Q]
        hr = h_grp[b_idx, d_idx, step_idx, s_idx]     # [B, days, 2] — giờ của từng slot
        # **đỉnh cao nhất trong ngày**: slot nào có q50 lớn hơn
        slot_idx = best.argmax(dim=2)                 # [B, days]
        pd_ = best.gather(2, slot_idx.unsqueeze(-1)).squeeze(-1)      # [B, days]
        hd_ = hr.gather(2, slot_idx.unsqueeze(-1)).squeeze(-1)        # [B, days]
        return (pk, hr, pd_, hd_)

    # --- mẫu đơn: [T, 2, Q] → [days, 2, Q] ---------------------------------
    T = peak.shape[0]
    days = min(t_out, T // steps_per_day)
    if days <= 0:
        return (peak[:0], hours[:0], peak[:0, :, 0], hours[:0, 0])
    p_grp = peak[:days * steps_per_day].reshape(days, steps_per_day, peak.shape[1],
                                                peak.shape[2])
    h_grp = hours[:days * steps_per_day].reshape(days, steps_per_day, hours.shape[1])
    q50 = p_grp[:, :, :, 1]                          # [days, S, 2]
    best = q50.max(dim=1).values                    # [days, 2]
    step_idx = q50.argmax(dim=1)                    # [days, 2]
    d_idx = torch.arange(days, device=peak.device).view(-1, 1).expand(days, 2)
    s_idx = torch.arange(2, device=peak.device).view(1, -1).expand(days, 2)
    pk = p_grp[d_idx, step_idx, s_idx]               # [days, 2, Q]
    hr = h_grp[d_idx, step_idx, s_idx]              # [days, 2]
    slot_idx = best.argmax(dim=1)                   # [days]
    pd_ = best.gather(1, slot_idx.unsqueeze(-1)).squeeze(-1)          # [days]
    hd_ = hr.gather(1, slot_idx.unsqueeze(-1)).squeeze(-1)            # [days]
    return (pk, hr, pd_, hd_)


def evaluate(model, samples: Sequence[WindowSample], subgraphs, cfg: ModelConfig,
             tcfg: TrainConfig, device, batch_size: int = 32,
             height_scale=None) -> dict:
    """Đánh giá trên tập val: MAE đỉnh, MAE giờ đỉnh, pinball.

    **Theo lô.** Các mẫu cùng trạm được gom lại và gọi `forward_batch` một lần — cùng
    cơ chế với `forecast.predict_split` (kết quả số học giống hệt, nhanh hơn 2–4×).

    **Hai định nghĩa MAE (sai lệch đã sửa 2026-11-24).**

    * `mae_peak` — MAE của **đỉnh cao nhất trong ngày**: `max_q50(dự báo)` so với
      `max(h_peak1, h_peak2)` (thực đo). Đây là chỉ số dừng sớm (đề cương 5.2.6).
      Vì chỉ so đỉnh cao nhất nên chỉ số này **nhỏ hơn** MAE theo từng đỉnh.
    * `mae_slot` — MAE **theo từng đỉnh** sau khi sắp xếp 2 slot theo giờ (đỉnh sớm ↔
      đỉnh sớm). Đây là định nghĩa **giống hệt** chỉ số mà `pipelines/r/R/evaluation.R`
      tính trên `predictions_test.csv` ⇒ dùng để so sánh với baseline một cách công
      bằng. Trước đây chỉ có `mae_peak` (best-of-2) nên nhìn "val MAE ≈ 0,18 m" nhưng
      MAE chính thức trên tập test là 0,70 m ⇒ **so sánh không cùng định nghĩa**.

    **Đơn vị.** Nếu mô hình dự báo trên thang chuẩn hoá (`data.HeightScale`), ta chuyển
    **cả** dự báo và nhãn về **mét** trước khi tính sai số ⇒ chỉ số luôn có đơn vị m
    và **so sánh được** với baseline và với `evaluation.R`.
    """
    import torch

    model.eval()
    if not samples:
        return {"mae_peak": float("nan"), "mae_slot": float("nan")}
    errs_h: list[float] = []
    errs_slot: list[float] = []
    errs_t: list[float] = []
    pin: list[float] = []
    n = 0

    groups: dict[str, list] = {}
    for s in samples:
        groups.setdefault(s.station, []).append(s)

    bs = max(int(batch_size), 1)
    with torch.no_grad():
        for st_name, st_samples in groups.items():
            sub = subgraphs[st_name]
            ei = torch.as_tensor(sub.edge_index, dtype=torch.long, device=device)
            ea = torch.as_tensor(sub.edge_attr, dtype=torch.float32, device=device)
            ed = torch.as_tensor(sub.edge_dir, dtype=torch.long, device=device)
            tau = torch.as_tensor(sub.tau_phys, dtype=torch.float32, device=device)
            static = torch.as_tensor(sub.static, dtype=torch.float32, device=device)
            st_idx = int(sub.station_node)

            for start in range(0, len(st_samples), bs):
                chunk = st_samples[start:start + bs]
                sts = [prepare_sample(s, sub, cfg, tcfg.t_in, tcfg.t_out) for s in chunk]
                node_seq = torch.as_tensor(np.stack([x.node_seq for x in sts]),
                                           dtype=torch.float32, device=device)
                out = model.forward_batch(
                    node_seq, static, ei, ea, ed, tau,
                    target_nodes=torch.as_tensor([st_idx], dtype=torch.long))
                peak = out.peak_height[:, 0]
                hours = out.peak_hour[:, 0]
                p_peak, p_hours, p_pd, p_hd = _select_daily(peak, hours, tcfg.t_out,
                                                            tcfg.delta_h)
                # sắp xếp slot theo giờ (đồng bộ với cách xuất CSV)
                # **Dùng nhãn ở thang chuẩn hoá nếu có** (cùng thang với dự báo).
                if sts[0].peak_target_scaled is not None:
                    tgt_all = torch.as_tensor(
                        np.stack([x.peak_target_scaled for x in sts]),
                        dtype=torch.float32, device=device)
                    ph_all = torch.as_tensor(
                        np.stack([x.peak_height_target_scaled for x in sts]),
                        dtype=torch.float32, device=device)
                else:
                    tgt_all = torch.as_tensor(np.stack([x.peak_target for x in sts]),
                                              dtype=torch.float32, device=device)
                    ph_all = torch.as_tensor(np.stack([x.peak_height_target for x in sts]),
                                             dtype=torch.float32, device=device)
                tgt_h_all = torch.as_tensor(np.stack([x.peak_hour_target for x in sts]),
                                            dtype=torch.float32, device=device)
                p_peak_s = _sort_slots_by_hour(p_peak, p_hours)      # [B, t_out, 2, Q]
                tgt_s = _sort_slots_by_hour(tgt_all, tgt_h_all)       # [B, t_out, 2]
                # pinball cho cả lô một lệnh (`p_peak` = [B, t_out, 2, Q])
                pin_all = pinball_loss(p_peak_s, tgt_s)
                # **Chuyển về mét** để chỉ số có đơn vị m (so sánh được với baseline).
                if height_scale is not None and sts[0].peak_target_scaled is not None:
                    sd = float(height_scale.std.get(f"{st_name}::both", 1.0))
                    mu = float(height_scale.mean.get(f"{st_name}::both", 0.0))
                    p_peak_m = p_peak_s * sd + mu
                    tgt_m = tgt_s * sd + mu
                    p_pd_m = p_pd * sd + mu
                    ph_m = ph_all * sd + mu
                else:
                    p_peak_m, tgt_m, p_pd_m, ph_m = p_peak_s, tgt_s, p_pd, ph_all
                for bi, s in enumerate(chunk):
                    pin.append(float(pin_all[bi]))
                    for d in range(p_pd.shape[1]):
                        if (np.isfinite(s.peak_heights[d])
                                and np.isfinite(float(p_pd_m[bi, d]))):
                            errs_h.append(abs(float(p_pd_m[bi, d])
                                              - float(s.peak_heights[d])))
                        if (np.isfinite(s.peak_hours[d])
                                and np.isfinite(float(p_hd[bi, d]))):
                            d_h = abs((float(p_hd[bi, d])
                                       - float(s.peak_hours[d]) + 12) % 24 - 12)
                            errs_t.append(d_h)
                        # MAE theo từng đỉnh (sau khi sắp xếp theo giờ) — đơn vị mét
                        for k in range(p_peak_m.shape[2]):
                            obs = float(tgt_m[bi, d, k])
                            prd = float(p_peak_m[bi, d, k, 1])
                            if np.isfinite(obs) and np.isfinite(prd):
                                errs_slot.append(abs(prd - obs))
                    n += 1
    return {
        "mae_peak": float(np.mean(errs_h)) if errs_h else float("nan"),
        "mae_slot": float(np.mean(errs_slot)) if errs_slot else float("nan"),
        "mae_hour": float(np.mean(errs_t)) if errs_t else float("nan"),
        "pinball": float(np.mean(pin)) if pin else float("nan"),
        "n_samples": n,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Huấn luyện PhaseGNN / biến thể")
    ap.add_argument("--variant", default="phasegnn", choices=list(VARIANTS))
    ap.add_argument("--epochs", type=int, default=30)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--lr", type=float, default=1e-3)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--t-in", type=int, default=DEFAULT_T_IN)
    ap.add_argument("--t-out", type=int, default=DEFAULT_T_OUT)
    ap.add_argument("--hidden-dim", type=int, default=64)
    ap.add_argument("--num-gnn-layers", type=int, default=2)
    ap.add_argument("--delta-h", type=float, default=1.0)
    ap.add_argument("--patience", type=int, default=10)
    ap.add_argument("--max-train-samples", type=int, default=None)
    ap.add_argument("--max-val-samples", type=int, default=None)
    ap.add_argument("--out-dir", default=None, help="thư mục ghi checkpoint và log")
    ap.add_argument("--data-root", default=None, help="gốc repo (mặc định suy ra tự động)")
    ap.add_argument("--no-mlflow", action="store_true", help="không ghi MLflow")
    ap.add_argument("--no-register", action="store_true",
                    help="không ghi bản ghi mô hình vào collection `models`")
    ap.add_argument("--mlflow-tracking-uri", default=None, help="URI theo dõi MLflow")
    ap.add_argument("--model-name", default=None, help="tên mô hình trong registry")
    ap.add_argument("--num-threads", type=int, default=1,
                    help="số luồng CPU cho torch (mặc định 1 — nhanh nhất với đồ thị con)")
    ap.add_argument("--optuna-trials", type=int, default=0,
                    help="số lần thử Optuna (0 = không tìm siêu tham số)")
    ap.add_argument("--optuna-time-budget", type=float, default=1800.0,
                    help="ngân sách thời gian Optuna (giây)")
    args = ap.parse_args(argv)

    root = Path(args.data_root) if args.data_root else None
    paths = default_paths(root)
    print(f"[train] đọc dữ liệu từ {paths['g5']}", flush=True)

    df = load_g5(paths=paths)
    meta = load_g5_meta(paths=paths)
    graph = load_graph(paths=paths)
    print(f"[train] G5: {len(df)} dòng; đồ thị: {graph.num_nodes} nút, {graph.num_edges} cạnh",
          flush=True)

    datasets = build_datasets(df, t_in=args.t_in, t_out=args.t_out)
    for name, ds in datasets.items():
        leaks = check_leakage(ds.samples)
        print(f"[train] tập {name}: {len(ds)} mẫu; rò rỉ: {len(leaks)}", flush=True)
        if leaks:
            print(f"[train]   ví dụ rò rỉ: {leaks[0]}", flush=True)
            return 1

    # --- Tìm siêu tham số bằng Optuna (tùy chọn) --------------------------
    search_info: dict = {}
    if args.optuna_trials and args.optuna_trials > 0:
        def _objective(params: dict) -> float:
            cfg_trial = TrainConfig(
                variant=args.variant, epochs=args.epochs,
                batch_size=int(params.get("batch_size", args.batch_size)),
                lr=float(params.get("lr", args.lr)),
                weight_decay=float(params.get("weight_decay", 1e-4)),
                seed=args.seed, t_in=args.t_in, t_out=args.t_out,
                hidden_dim=int(params.get("hidden_dim", args.hidden_dim)),
                num_gnn_layers=int(params.get("num_gnn_layers", args.num_gnn_layers)),
                delta_h=args.delta_h, patience=args.patience,
                lam1=float(params.get("lam1", 1.0)), lam2=float(params.get("lam2", 0.5)),
                lam3=float(params.get("lam3", 0.1)), lam4=float(params.get("lam4", 0.1)),
                max_train_samples=args.max_train_samples,
                max_val_samples=args.max_val_samples,
                use_mlflow=False, register_to_db=False,
            )
            _, res = train_model(datasets, graph, cfg_trial)
            return float(res.best_val_mae)

        search_info = optuna_search(_objective, n_trials=args.optuna_trials,
                                    time_budget_s=args.optuna_time_budget,
                                    seed=args.seed)
        bp = search_info.get("best_params") or {}
        if bp:
            args.lr = float(bp.get("lr", args.lr))
            args.batch_size = int(bp.get("batch_size", args.batch_size))
            args.hidden_dim = int(bp.get("hidden_dim", args.hidden_dim))
            args.num_gnn_layers = int(bp.get("num_gnn_layers", args.num_gnn_layers))
            print(f"[train] Optuna: best val MAE = {search_info.get('best_value')}; "
                  f"dung tham so {bp}", flush=True)

    tcfg = TrainConfig(
        variant=args.variant, epochs=args.epochs, batch_size=args.batch_size,
        lr=args.lr, seed=args.seed, t_in=args.t_in, t_out=args.t_out,
        hidden_dim=args.hidden_dim, num_gnn_layers=args.num_gnn_layers,
        delta_h=args.delta_h, patience=args.patience,
        max_train_samples=args.max_train_samples, max_val_samples=args.max_val_samples,
        num_threads=args.num_threads,
        use_mlflow=not args.no_mlflow,
        register_to_db=not args.no_register,
        mlflow_experiment="phasegnn",
    )
    model, result = train_model(datasets, graph, tcfg)
    result.data_snapshot = meta.get("data_snapshot", {})

    out_dir = Path(args.out_dir) if args.out_dir else Path("results") / "E2" / args.variant
    out_dir.mkdir(parents=True, exist_ok=True)
    import torch

    ckpt_path = out_dir / f"checkpoint_seed{args.seed}.pt"
    torch.save({"model_state": model.state_dict(),
                "config": _make_model_cfg(tcfg).to_dict(),
                "train_config": tcfg.__dict__,
                "dims": result.dims,
                "result": result.to_dict()},
               ckpt_path)
    log_path = out_dir / f"train_log_seed{args.seed}.json"
    with open(log_path, "w", encoding="utf-8") as fh:
        json.dump(result.to_dict(), fh, ensure_ascii=False, indent=1, default=str)
        fh.write("\n")
    if search_info:
        with open(out_dir / f"optuna_seed{args.seed}.json", "w", encoding="utf-8") as fh:
            json.dump(search_info, fh, ensure_ascii=False, indent=1, default=str)
            fh.write("\n")

    # --- MLflow: tham số, chỉ số, artifact -------------------------------
    run_name = f"{args.variant}-seed{args.seed}"
    with MlflowTracker(run_name, params=tcfg.__dict__,
                       tracking_uri=args.mlflow_tracking_uri,
                       experiment=tcfg.mlflow_experiment,
                       enabled=tcfg.use_mlflow) as tracker:
        tracker.log_params({k: v for k, v in tcfg.__dict__.items()})
        tracker.log_metrics({"val_mae_peak": result.best_val_mae,
                             "best_epoch": float(result.best_epoch)})
        for entry in result.history:
            tracker.log_metrics(
                {"train_loss": float(entry.get("train_loss", float("nan"))),
                 "val_mae_peak": float((entry.get("val") or {}).get("mae_peak", float("nan"))),
                 "val_mae_hour": float((entry.get("val") or {}).get("mae_hour", float("nan")))},
                step=int(entry.get("epoch", 0)))
        tracker.log_artifact(log_path)
        tracker.log_artifact(ckpt_path)

    # --- Bản ghi mô hình (`status: staging`) -------------------------------
    model_id = f"{args.variant}-seed{args.seed}"
    record = ModelRecord(
        model_id=model_id,
        name=args.model_name or f"{args.variant}-v1",
        variant=args.variant,
        status="staging",
        version=1,
        hyperparams=_make_model_cfg(tcfg).to_dict() | {"train": tcfg.__dict__},
        metrics={"val_mae_peak": result.best_val_mae,
                 "val_mae_hour": (result.history[-1].get("val") or {}).get("mae_hour")
                 if result.history else None,
                 "best_epoch": result.best_epoch},
        data_snapshot=result.data_snapshot,
        artifact_path=str(ckpt_path),
        train_config=tcfg.__dict__.copy(),
        seed=args.seed,
    )
    doc = register_model(record, log=print) if tcfg.register_to_db else record.to_mongo()
    save_registry_json(doc, out_dir, filename=f"model_record_seed{args.seed}.json")

    print(f"[train] xong. val MAE đỉnh = {result.best_val_mae:.4f} m; "
          f"lưu tại {out_dir}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
