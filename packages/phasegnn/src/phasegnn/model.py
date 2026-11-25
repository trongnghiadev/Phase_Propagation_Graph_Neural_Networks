"""Mô hình PhaseGNN và các biến thể so sánh (đề cương 5.2.4, 5.2.7).

**PhaseGNN** (mô hình đề xuất) gồm ba phần:

1. `PhaseShift` + `PhaseMessage` — dịch pha và truyền thông điệp theo độ trễ học được
   (xem `layers.py`), theo hai chiều `down` (dòng chảy) và `up` (triều).
2. `TopoGRU` — cập nhật trạng thái ẩn theo thứ tự topo, hai lượt (down rồi up), lặp `K` lần.
3. Đầu ra: 2 đỉnh/ngày × 3 phân vị (q10/q50/q90) + giờ đỉnh (độ lệch so với giờ đỉnh
   thiên văn, theo đề cương 5.2.5).

**Biến thể** (đề cương 5.2.7).

| Mã | Mô hình | Cấu hình |
|---|---|---|
| `phasegnn` | mô hình đề xuất | mặc định |
| `B3` | GraphSAGE + GRU, **không** trễ pha | `model_kind="graphsage"` |
| `B4` | PhaseGNN với `τ = τ_phys` **cố định** | `learn_tau=False` |
| `B5a` | chỉ chiều **triều** (`up`) | `directions=(False, True)` |
| `B5b` | chỉ chiều **dòng chảy** (`down`) | `directions=(True, False)` |

**Cấu trúc lô (batch).** Mô hình chạy trên **toàn bộ đồ thị** (hoặc đồ thị con của một
trạm — xem `train.build_station_subgraph`). Với mỗi lô:

* `node_seq` : ``[N, T, F]`` — chuỗi đặc trưng động của mọi nút theo bước nội bộ Δ.
* `static` : ``[N, S]`` — đặc trưng tĩnh của nút.
* `edge_index`, `edge_attr`, `edge_dir`, `tau_phys` — cạnh của đồ thị.
* `target_nodes` : chỉ số các node cần dự báo (các trạm của lô).

Kết quả: `peak_height [B, T, 2, Q]`, `peak_hour [B, T, 2]`.

**Bước thời gian nội bộ.** Mô hình chạy ở bước Δ (mặc định 1 h). Dữ liệu quan trọc là
đỉnh/chân theo **ngày** nên được nội suy lên lưới giờ Δ (nội tuyến giữa hai mốc đỉnh kế
tiếp, nội suy vòng 24 h) — xem `interpolate_to_grid`.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Sequence

import numpy as np
import torch
from torch import Tensor, nn

from .layers import PhaseMessage, TopoGRU, split_directions, topo_order
from .losses import QUANTILES

#: Số giờ trong một ngày.
HOURS_PER_DAY = 24

#: Tham số hoá đầu ra **giờ**: `hour = HOUR_CENTER + HOUR_SCALE · (2σ(raw) − 1)`.
#:
#: **Vì sao cần?** Trước đây `peak_hour_head` là `Linear → SiLU → Linear` **không có**
#: hàm kích hoạt đầu ra ⇒ mô hình phải tự học thang 0..24 chỉ bằng gradient, và với
#: hàm mất mát khoảng cách vòng (gradient nhỏ khi sai số lớn) đầu ra bị **co lại về 0**
#: (thực tế đo được: giờ dự báo ≈ 0,10 h thay vì 6,4 h / 17,8 h). Với tham số hoá
#: `sigmoid`, đầu ra luôn nằm trong khoảng hợp lệ ngay từ bước đầu ⇒ gradient của
#: `L_timing` luôn có tín hiệu ⇒ mô hình học được giờ thật.
HOUR_CENTER = 12.0
HOUR_SCALE = 12.0

#: Neo của thang **độ cao dự báo**.
#:
#: **Sai lệch đã sửa (2026-11-24).** Trước đây `peak_head` được neo về `HEIGHT_CENTER`
#: = 1,0 **mét** — nhưng mô hình dự báo trên **thang chuẩn hoá** (`data.HeightScale`),
#: nơi giá trị trung bình là **0** (không phải 1,0). Neo sai thang ⇒ dự báo lệch trung
#: bình một cách hệ thống (~0,9 m thay vì ~1,3 m) và không thể học được biến động.
#:
#: Nay thang đầu ra là **chuẩn hoá** ⇒ bias khởi tạo = 0 tương ứng "trung bình của trạm".
#: Giá trị tuyệt đối (mét) được khôi phục khi xuất kết quả — xem `forecast.predict_split`.
HEIGHT_CENTER = 0.0

#: Neo thang độ cao **tính bằng mét** (chỉ dùng cho tài liệu/tham chiếu).
#:
#: Median độ cao đỉnh ngày của lưu vực Đồng Nai ≈ 1,0–1,3 m (thấp hơn ở các trạm
#: thượng nguồn, cao hơn ở Vũng Tàu ≈ 3,7 m). Trước đây giá trị này bị dùng làm bias của
#: tầng đầu ra trên thang mét; nay chỉ còn ý nghĩa tham chiếu.
HEIGHT_CENTER_M = 1.0


@dataclass
class ModelConfig:
    """Cấu hình mô hình (siêu tham số theo đề cương 5.2.6)."""

    hidden_dim: int = 64
    num_gnn_layers: int = 2          # K — số lần lặp hai lượt
    delta_h: float = 1.0             # bước nội bộ Δ (giờ); 12.42 = nửa chu kỳ M2
    message_hidden: int = 32
    smooth_steps: int = 2
    learn_tau: bool = True
    directions: tuple[bool, bool] = (True, True)
    model_kind: str = "phasegnn"     # "phasegnn" | "graphsage"
    out_quantiles: tuple[float, ...] = QUANTILES
    #: Cách τ tác động lên thông điệp (`layers.PhaseMessage`): "grid" (mặc định — cấu hình
    #: của mọi checkpoint trước 25/11/2026, không có trường này) hoặc "phase".
    tau_mode: str = "grid"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["directions"] = list(self.directions)
        d["out_quantiles"] = list(self.out_quantiles)
        return d

    @classmethod
    def from_dict(cls, d: dict) -> "ModelConfig":
        d = dict(d)
        if "directions" in d:
            d["directions"] = tuple(bool(x) for x in d["directions"])
        if "out_quantiles" in d:
            d["out_quantiles"] = tuple(float(x) for x in d["out_quantiles"])
        known = {k: v for k, v in d.items() if k in cls.__dataclass_fields__}
        return cls(**known)


@dataclass
class ModelOutput:
    """Kết quả một lần forward."""

    peak_height: Tensor            # [B, T, 2, Q] — độ cao dự báo của 2 đỉnh
    peak_hour: Tensor              # [B, T, 2] — giờ dự báo (độ lệch so với thiên văn)
    low_height: Tensor             # [B, T, 2, Q] — độ cao chân
    low_hour: Tensor               # [B, T, 2]
    delta: Tensor | None = None    # [E] — δ trung bình của các cạnh (cho L_τ)
    tau_eff: Tensor | None = None  # [E] — τ thực dùng (giờ)


# ---------------------------------------------------------------------------
# Nội suy chuỗi động theo giờ
# ---------------------------------------------------------------------------
def interpolate_to_grid(values: np.ndarray, hours: np.ndarray, grid_hours: np.ndarray,
                        period_h: float = 24.0) -> np.ndarray:
    """Nội suy chuỗi đỉnh/chân (theo giờ trong ngày) lên lưới giờ của bước Δ.

    Tham số
    -------
    values : ``[T, K]`` — giá trị (NaN = không quan trắc).
    hours : ``[T, K]`` — giờ tương ứng trong ngày (0..24).
    grid_hours : ``[G]`` — các mốc giờ cần lấy giá trị.

    Trả về ``[G, K]``. Với mỗi cột `k`, ta dựng đường thẳng giữa các mốc
    ``(hours[t, k], values[t, k])`` rồi nội suy tuyến tính theo giờ (nội suy vòng 24 h).
    """
    g = len(grid_hours)
    k = values.shape[1]
    out = np.full((g, k), np.nan, dtype=np.float32)
    for col in range(k):
        xs = hours[:, col]
        ys = values[:, col]
        mask = np.isfinite(xs) & np.isfinite(ys)
        if not mask.any():
            continue
        x = xs[mask]
        y = ys[mask]
        order = np.argsort(x, kind="stable")
        x = x[order]
        y = y[order]
        keep = np.concatenate(([True], np.diff(x) > 1e-9))
        x = x[keep]
        y = y[keep]
        if len(x) == 1:
            out[:, col] = y[0]
            continue
        # nội suy vòng: mở rộng điểm đầu/cuối theo chu kỳ
        x_ext = np.concatenate((x - period_h, x, x + period_h))
        y_ext = np.concatenate((y, y, y))
        out[:, col] = np.interp(grid_hours, x_ext, y_ext).astype(np.float32)
    return out


def daily_grid(t_in: int, delta_h: float) -> tuple[np.ndarray, np.ndarray]:
    """Lưới giờ của bước Δ cho `t_in` ngày.

    Trả về ``(grid_hours, day_index)``: `grid_hours` là giờ trong ngày (0..24) của từng mốc
    lưới (lặp lại theo từng ngày), `day_index` là chỉ số ngày (0..t_in−1) tương ứng.

    Với Δ = 1 h: mỗi ngày có 24 mốc ⇒ tổng `24·t_in` mốc. Với Δ = 12,42 h (nửa chu kỳ
    M2): mỗi ngày có `round(24/Δ) = 2` mốc.
    """
    steps_per_day = max(int(round(HOURS_PER_DAY / delta_h)), 1)
    hours_per_day = (np.arange(steps_per_day, dtype=np.float32) * delta_h) % HOURS_PER_DAY
    # lặp lại lưới giờ qua `t_in` ngày
    grid_hours = np.tile(hours_per_day, t_in)
    day_index = np.repeat(np.arange(t_in), steps_per_day)
    return grid_hours, day_index


# ---------------------------------------------------------------------------
# Tham số hoá đầu ra (giờ, độ cao)
# ---------------------------------------------------------------------------
def decode_hours(raw: Tensor) -> Tensor:
    """Ánh xạ đầu ra thô của đầu ra giờ → giờ trong ngày (0..24).

    ``hour = (HOUR_CENTER + HOUR_SCALE · (2·sigmoid(raw) − 1)) mod 24``.

    Vì `sigmoid ∈ (0,1)` ⇒ giờ luôn nằm trong `(HOUR_CENTER − HOUR_SCALE,
    HOUR_CENTER + HOUR_SCALE)` = (0, 24) ⇒ **luôn là giờ hợp lệ trong ngày**, kể cả ở
    bước huấn luyện đầu tiên. Đây là nguyên nhân gốc khiến giờ dự báo trước đây co về
    ~0: đầu ra thô không có ràng buộc nào và gradient của khoảng cách vòng 24 h rất nhỏ
    khi sai số lớn.
    """
    s = torch.sigmoid(raw)
    return torch.remainder(HOUR_CENTER + HOUR_SCALE * (2.0 * s - 1.0), HOURS_PER_DAY)


def decode_heights(raw: Tensor, n_quantiles: int) -> Tensor:
    """Giải mã đầu ra thô của đầu ra độ cao → ``[..., n_quantiles]`` (m).

    Hiện tại đây là **phép nhận dạng** (giữ nguyên tính biểu đạt của mô hình: đầu ra
    tuyến tính không bị chặn trên/dưới), vì thứ tự phân vị đã được đảm bảo ở tầng xuất
    (`forecast._monotonic_quantiles`) và bằng `L_mono` trong lúc huấn luyện.

    Hàm được giữ lại (thay vì bỏ hẳn) để có **một điểm ánh xạ duy nhất** giữa đầu ra
    mô hình và đơn vị vật lý — nếu sau này cần thêm tham số hoá (ví dụ `softplus` cho
    độ cao âm) thì chỉ sửa ở đây.
    """
    return raw


# ---------------------------------------------------------------------------
# Mô hình
# ---------------------------------------------------------------------------
class PhaseGNN(nn.Module):
    """Mô hình lan truyền pha hai chiều trên đồ thị lưu vực.

    Tham số
    -------
    cfg : `ModelConfig`.
    in_dim : số kênh đặc trưng động (theo bước nội bộ, mỗi nút).
    static_dim : số kênh đặc trưng tĩnh của nút.
    edge_dim : số kênh đặc trưng cạnh.
    """

    def __init__(self, cfg: ModelConfig, in_dim: int, static_dim: int, edge_dim: int) -> None:
        super().__init__()
        self.cfg = cfg
        self.in_dim = int(in_dim)
        self.static_dim = int(static_dim)
        self.edge_dim = int(edge_dim)
        hidden = int(cfg.hidden_dim)
        nq = len(cfg.out_quantiles)

        # Nhúng đặc trưng động + tĩnh
        self.encoder = nn.Sequential(
            nn.Linear(self.in_dim + self.static_dim, hidden), nn.SiLU())

        if cfg.model_kind == "graphsage":
            # B3: GraphSAGE + GRU — không dịch pha, chỉ truyền thông điệp tức thời.
            self.sage = nn.ModuleList([
                nn.Linear(hidden * 2, hidden) for _ in range(cfg.num_gnn_layers)])
        else:
            self.message = PhaseMessage(
                node_dim=hidden, edge_dim=edge_dim, q_dim=0,
                hidden=cfg.message_hidden, smooth_steps=cfg.smooth_steps,
                delta_h=cfg.delta_h, learn_tau=cfg.learn_tau, tau_mode=cfg.tau_mode)
            self.topo = TopoGRU(node_dim=hidden, in_dim=self.in_dim + self.static_dim,
                                message_dim=hidden, num_layers=1,
                                directions=cfg.directions)

        # GRU cập nhật trạng thái theo thời gian (đầu vào: trạng thái GNN + đặc trưng)
        self.gru = nn.GRU(hidden + self.in_dim + self.static_dim, hidden,
                          num_layers=1, batch_first=True)

        # Đầu ra.
        #
        # **Tham số hoá đầu ra.** `peak_head` xuất **giá trị thô**; phần giải mã sang
        # độ cao (m) / giờ (0..24) nằm ở `decode_heights` / `decode_hours` ⇒ đầu ra luôn
        # hợp lệ về mặt vật lý, không phụ thuộc quá trình huấn luyện.
        #
        # Với `nq` phân vị, `peak_head` xuất `2 · nq` số (2 đỉnh × nq phân vị); sau khi
        # reshape thành `[..., 2, nq]`, `decode_heights` giải mã **riêng từng đỉnh**:
        # chỉ số `nq // 2` là **trung vị** (giá trị tuyệt đối), `nq // 2` số còn lại là
        # **nửa độ rộng** (m) của các phân vị hai bên.
        half_q = nq // 2
        self.peak_head = nn.Sequential(
            nn.Linear(hidden, hidden), nn.SiLU(), nn.Linear(hidden, 2 * nq))
        self.peak_hour_head = nn.Sequential(
            nn.Linear(hidden, hidden), nn.SiLU(), nn.Linear(hidden, 2))
        self.low_head = nn.Sequential(
            nn.Linear(hidden, hidden), nn.SiLU(), nn.Linear(hidden, 2 * nq))
        self.low_hour_head = nn.Sequential(
            nn.Linear(hidden, hidden), nn.SiLU(), nn.Linear(hidden, 2))
        # Khởi tạo đầu ra **độ cao**: bias của tầng cuối được neo về `HEIGHT_CENTER`
        # (≈ 1,0 m — median quan trọc của lưu vực Đồng Nai) ⇒ dự báo khởi điểm đã ở
        # đúng vùng giá trị thay vì quanh 0. Trọng số khởi tạo **nhỏ** (không phải 0) để
        # gradient truyền về tầng ẩn ngay từ bước đầu tiên.
        nn.init.normal_(self.peak_head[-1].weight, std=0.01)
        nn.init.constant_(self.peak_head[-1].bias, HEIGHT_CENTER)
        nn.init.normal_(self.low_head[-1].weight, std=0.01)
        nn.init.constant_(self.low_head[-1].bias, 0.0)
        # Khởi tạo đầu ra **giờ**: bias = 0 ⇒ `sigmoid(0)=0.5` ⇒ `decode_hours(0) = 12`
        # (nửa đêm); mô hình phải học để dịch sang ~6 h / ~18 h.
        nn.init.normal_(self.peak_hour_head[-1].weight, std=0.01)
        nn.init.zeros_(self.peak_hour_head[-1].bias)
        nn.init.normal_(self.low_hour_head[-1].weight, std=0.01)
        nn.init.zeros_(self.low_hour_head[-1].bias)

        self._last_delta: Tensor | None = None
        self._last_tau: Tensor | None = None

    # -- forward -------------------------------------------------------------
    def forward(self, node_seq: Tensor, static: Tensor, edge_index: Tensor, edge_attr: Tensor,
                edge_dir: Tensor, tau_phys: Tensor,
                target_nodes: Tensor | None = None) -> ModelOutput:
        """Chạy mô hình.

        Tham số
        --------
        node_seq : ``[N, T, in_dim]`` — đặc trưng động theo bước nội bộ Δ.
        static : ``[N, static_dim]``.
        edge_index : ``[2, E]``; `edge_attr`: ``[E, edge_dim]``; `edge_dir`: ``[E]``;
        `tau_phys`: ``[E]`` (giờ).
        target_nodes : ``[B]`` — chỉ số node cần dự báo. Nếu `None`, lấy toàn bộ node.

        Trả về `ModelOutput` với `peak_height` có dạng ``[B, T, 2, Q]`` (T = số bước lưới).
        """
        cfg = self.cfg
        n, t, _ = node_seq.shape
        hidden = int(cfg.hidden_dim)
        device = node_seq.device

        inp = torch.cat([node_seq, static.unsqueeze(1).expand(-1, t, -1)], dim=-1)
        h_seq = self.encoder(inp)                        # [N, T, H]

        if cfg.model_kind == "graphsage":
            out = self._forward_graphsage(h_seq, node_seq, static, edge_index)
        else:
            out = self._forward_phase(h_seq, node_seq, static, edge_index, edge_attr,
                                      edge_dir, tau_phys)

        peak_raw = self.peak_head(out).view(n, t, 2, len(cfg.out_quantiles))
        peak_q = decode_heights(peak_raw, len(cfg.out_quantiles))   # [N, T, 2, nq]
        peak_hour = decode_hours(self.peak_hour_head(out))          # [N, T, 2]
        low_raw = self.low_head(out).view(n, t, 2, len(cfg.out_quantiles))
        low_q = decode_heights(low_raw, len(cfg.out_quantiles))      # [N, T, 2, nq]
        low_hour = decode_hours(self.low_hour_head(out))             # [N, T, 2]

        idx = target_nodes if target_nodes is not None else torch.arange(n, device=device)
        return ModelOutput(
            peak_height=peak_q[idx],
            peak_hour=peak_hour[idx],
            low_height=low_q[idx],
            low_hour=low_hour[idx],
            delta=self._last_delta,
            tau_eff=self._last_tau,
        )

    # -- forward theo lô ----------------------------------------------------
    def forward_batch(self, node_seq: Tensor, static: Tensor, edge_index: Tensor,
                      edge_attr: Tensor, edge_dir: Tensor, tau_phys: Tensor,
                      target_nodes: Tensor | None = None) -> ModelOutput:
        """Chạy mô hình cho **nhiều mẫu cùng trạm** trong một lô.

        Tham số
        --------
        node_seq : ``[B, N, T, in_dim]`` — đặc trưng động của `B` mẫu (cùng đồ thị con).
        static : ``[N, static_dim]`` — đặc trưng tĩnh (dùng chung cho cả lô).
        edge_index / edge_attr / edge_dir / tau_phys : dùng chung cho cả lô.

        Trả về `ModelOutput` với `peak_height` dạng ``[B, T, 2, Q]``.

        **Vì sao cần?** Trên CPU, `nn.GRU` tốn phần lớn thời gian do chi phí gọi kernel
        theo từng bước thời gian, gần như độc lập với số nút. Gom `B` mẫu vào một lô biến
        trục batch của GRU từ `N` thành `B·N` ⇒ giảm số lần gọi GRU đi `B` lần mà kết quả
        số học **giống hệt** (mỗi mẫu vẫn là một chuỗi độc lập trong GRU).
        """
        cfg = self.cfg
        b, n, t, _ = node_seq.shape
        hidden = int(cfg.hidden_dim)
        device = node_seq.device

        inp = torch.cat([node_seq, static.view(1, n, 1, -1).expand(b, n, t, -1)], dim=-1)
        h_seq = self.encoder(inp)                        # [B, N, T, H]

        if cfg.model_kind == "graphsage":
            out = self._forward_graphsage_batch(h_seq, node_seq, static, edge_index)
        else:
            out = self._forward_phase_batch(h_seq, node_seq, static, edge_index, edge_attr,
                                            edge_dir, tau_phys)

        peak_raw = self.peak_head(out).view(b, n, t, 2, len(cfg.out_quantiles))
        peak_q = decode_heights(peak_raw, len(cfg.out_quantiles))   # [B, N, T, 2, nq]
        peak_hour = decode_hours(self.peak_hour_head(out))          # [B, N, T, 2]
        low_raw = self.low_head(out).view(b, n, t, 2, len(cfg.out_quantiles))
        low_q = decode_heights(low_raw, len(cfg.out_quantiles))      # [B, N, T, 2, nq]
        low_hour = decode_hours(self.low_hour_head(out))             # [B, N, T, 2]

        idx = target_nodes if target_nodes is not None else torch.arange(n, device=device)
        return ModelOutput(
            peak_height=peak_q[:, idx],
            peak_hour=peak_hour[:, idx],
            low_height=low_q[:, idx],
            low_hour=low_hour[:, idx],
            delta=self._last_delta,
            tau_eff=self._last_tau,
        )

    def _forward_graphsage_batch(self, h_seq: Tensor, node_seq: Tensor, static: Tensor,
                                 edge_index: Tensor) -> Tensor:
        """B3 (GraphSAGE + GRU) theo lô — GRU chạy trên trục batch `B·N`."""
        b, n, t, hidden = h_seq.shape
        out = torch.zeros_like(h_seq)
        src, dst = edge_index[0], edge_index[1]
        h_prev = h_seq.new_zeros((1, b * n, hidden))
        for step in range(t):
            cur = h_seq[:, :, step]                       # [B, N, H]
            agg = cur
            for layer in self.sage:
                if edge_index.shape[1]:
                    m = layer(torch.cat([cur[:, src], cur[:, dst]], dim=-1))   # [B, E, H]
                    msg = TopoGRU.scatter_sum_batch(m, dst, n)                 # [B, N, H]
                else:
                    msg = torch.zeros_like(cur)
                agg = layer(torch.cat([cur, msg], dim=-1))
            x = torch.cat([node_seq[:, :, step], static.view(1, n, -1).expand(b, n, -1)], dim=-1)
            gru_in = torch.cat([agg, x], dim=-1).reshape(b * n, 1, -1)
            o, h_prev = self.gru(gru_in, h_prev.contiguous())
            out[:, :, step] = o.view(b, n, hidden)
        return out

    def _forward_phase_batch(self, h_seq: Tensor, node_seq: Tensor, static: Tensor,
                             edge_index: Tensor, edge_attr: Tensor, edge_dir: Tensor,
                             tau_phys: Tensor) -> Tensor:
        """PhaseGNN theo lô — xem `forward_batch`."""
        b, n, t, hidden = h_seq.shape
        idx_down, idx_up = split_directions(edge_dir, edge_dir)
        ei_down = edge_index[:, idx_down]
        ei_up = edge_index[:, idx_up]
        ea_down = edge_attr[idx_down]
        ea_up = edge_attr[idx_up]
        tau_down = tau_phys[idx_down]
        tau_up = tau_phys[idx_up]
        dir_down = torch.zeros(len(idx_down), dtype=torch.long, device=h_seq.device)
        dir_up = torch.ones(len(idx_up), dtype=torch.long, device=h_seq.device)

        msg_down, tau_d, delta_d = self.message.forward_batch(
            h_seq, tau_down, ei_down, ea_down, dir_down)
        msg_up, tau_u, delta_u = self.message.forward_batch(
            h_seq, tau_up, ei_up, ea_up, dir_up)
        if self.cfg.learn_tau:
            self._last_delta = torch.cat([delta_d, delta_u], dim=1)     # [B, E]
            self._last_tau = torch.cat([tau_d, tau_u], dim=1)
        else:
            self._last_delta = None
            self._last_tau = None

        m_down = self.topo.aggregate_batch(msg_down, ei_down, n)        # [B, N, T, H]
        m_up = self.topo.aggregate_batch(msg_up, ei_up, n)

        x_seq = torch.cat([node_seq, static.view(1, n, 1, -1).expand(b, n, t, -1)], dim=-1)
        if self.cfg.directions[0]:
            m1 = m_down
        else:
            m1 = torch.zeros_like(m_down)
        if self.cfg.directions[1]:
            m2 = m_up
        else:
            m2 = torch.zeros_like(m_up)

        h = None
        for _ in range(int(self.cfg.num_gnn_layers)):
            if self.cfg.directions[0]:
                h = self.topo.forward_sequence_batch(x_seq, m1, (True, False))
            if self.cfg.directions[1]:
                init = h[:, :, -1, :] if h is not None else None
                h = self.topo.forward_sequence_batch(x_seq, m2, (False, True), init_state=init)
            if not (self.cfg.directions[0] or self.cfg.directions[1]):
                h = self.topo.forward_sequence_batch(x_seq, m1, (False, False))
        if h is None:
            h = torch.zeros(b, n, t, hidden, device=h_seq.device, dtype=h_seq.dtype)
        return h

    def _forward_graphsage(self, h_seq: Tensor, node_seq: Tensor, static: Tensor,
                           edge_index: Tensor) -> Tensor:
        """B3 — GraphSAGE + GRU, không trễ pha."""
        n, t, hidden = h_seq.shape
        out = torch.zeros_like(h_seq)
        # Trạng thái ẩn của GRU: `[num_layers, N, hidden]` (mỗi nút là một chuỗi riêng).
        h_prev = h_seq.new_zeros((1, n, hidden))
        for step in range(t):
            cur = h_seq[:, step]
            agg = cur
            for layer in self.sage:
                msg = torch.zeros_like(cur)
                if edge_index.shape[1]:
                    m = layer(torch.cat([cur[edge_index[0]], cur[edge_index[1]]], dim=-1))
                    msg = msg.index_add(0, edge_index[1], m)
                agg = layer(torch.cat([cur, msg], dim=-1))
            x = torch.cat([node_seq[:, step], static], dim=-1)
            # GRU nhận đầu vào `[N, 1, F]` (batch = N nút, chuỗi = 1 bước thời gian)
            gru_in = torch.cat([agg, x], dim=-1).unsqueeze(1)
            o, h_prev = self.gru(gru_in, h_prev.contiguous())
            out[:, step] = o.squeeze(1)
        return out

    def _forward_phase(self, h_seq: Tensor, node_seq: Tensor, static: Tensor,
                       edge_index: Tensor, edge_attr: Tensor, edge_dir: Tensor,
                       tau_phys: Tensor) -> Tensor:
        """PhaseGNN — dịch pha + truyền thông điệp hai chiều theo thứ tự topo.

        **Cơ chế dịch pha theo thời gian.** Thông điệp từ nút `u` sang nút `v` ở bước `s`
        mang theo trạng thái của `u` tại bước `s − τ_uv` (τ tính bằng **giờ**, bước nội
        bộ Δ). Vì vậy phép dịch pha được áp dụng trên **toàn bộ chuỗi thời gian** một
        lượt (`[N, T, H]` → `[E, T, H]`), cho phép gradient của τ truyền ngược đúng.

        **Chi phí.** Phép dịch + làm trơn được tính **một lần** cho toàn chuỗi (O(E·T)),
        sau đó vòng lặp thời gian chỉ gom thông điệp và cập nhật trạng thái theo thứ tự
        topo. (Nếu tính thông điệp lại ở từng bước với cửa sổ `0..s`, tổng chi phí là
        O(T²) — với T = 336 bước (14 ngày × 24 h) thì quá chậm cho CPU.)

        **Suy ra trạng thái.** Thông điệp đã dịch pha sẵn được dùng làm đầu vào của
        `TopoGRU` ở **mỗi** bước; trạng thái ẩn được cập nhật tuần tự (đúng thứ tự
        topo hai lượt) và đầu ra cuối cùng được đọc bởi các đầu ra dự báo.
        """
        n, t, hidden = h_seq.shape
        idx_down, idx_up = split_directions(edge_dir, edge_dir)
        ei_down = edge_index[:, idx_down]
        ei_up = edge_index[:, idx_up]
        ea_down = edge_attr[idx_down]
        ea_up = edge_attr[idx_up]
        tau_down = tau_phys[idx_down]
        tau_up = tau_phys[idx_up]
        dir_down = torch.zeros(len(idx_down), dtype=torch.long, device=h_seq.device)
        dir_up = torch.ones(len(idx_up), dtype=torch.long, device=h_seq.device)

        order_down = topo_order(n, ei_down, "down")
        order_up = topo_order(n, ei_up, "up")

        # --- thông điệp đã dịch pha: tính MỘT LẦN cho toàn chuỗi -----------
        # `h_seq` là trạng thái ẩn mã hoá từ đặc trưng động (sóng triều + xả hồ);
        # dịch pha theo τ của từng cạnh ⇒ mỗi nút nhận thông điệp đã trễ đúng.
        msg_down, tau_d, delta_d = self.message(
            h_seq, tau_down, ei_down, ea_down, dir_down)
        msg_up, tau_u, delta_u = self.message(
            h_seq, tau_up, ei_up, ea_up, dir_up)
        if self.cfg.learn_tau:
            self._last_delta = torch.cat([delta_d, delta_u], dim=0)      # [E]
            self._last_tau = torch.cat([tau_d, tau_u], dim=0)
        else:
            self._last_delta = None
            self._last_tau = None

        # gom thông điệp theo nút đích (mỗi bước thời gian riêng)
        m_down = self.topo.aggregate(msg_down, ei_down, n)              # [N, T, H]
        m_up = self.topo.aggregate(msg_up, ei_up, n)                    # [N, T, H]

        # --- truyền theo thứ tự topo, hai lượt (vector hoá theo thời gian) --
        # Lượt 1: chiều xuôi (dòng chảy). Lượt 2: chiều ngược (triều), dùng trạng
        # thái **sau** lượt 1 — đúng công thức đề cương 5.2.4.
        x_seq = torch.cat([node_seq, static.unsqueeze(1).expand(-1, t, -1)], dim=-1)
        if self.cfg.directions[0]:
            m1 = m_down
        else:
            m1 = torch.zeros_like(m_down)
        if self.cfg.directions[1]:
            m2 = m_up
        else:
            m2 = torch.zeros_like(m_up)

        h = None
        for _ in range(int(self.cfg.num_gnn_layers)):
            if self.cfg.directions[0]:
                h1 = self.topo.forward_sequence(x_seq, m1, (True, False))
                h = h1
            if self.cfg.directions[1]:
                # lượt 2 dùng trạng thái **sau lượt 1** (mốc cuối của chuỗi lượt 1 làm
                # trạng thái ẩn ban đầu của GRU lượt 2) — đúng công thức đề cương 5.2.4.
                init = h[:, -1, :] if h is not None else None
                h = self.topo.forward_sequence(x_seq, m2, (False, True), init_state=init)
            if not (self.cfg.directions[0] or self.cfg.directions[1]):
                h = self.topo.forward_sequence(x_seq, m1, (False, False))
        if h is None:
            h = torch.zeros(n, t, hidden, device=h_seq.device, dtype=h_seq.dtype)
        return h


# ---------------------------------------------------------------------------
# Tiện ích
# ---------------------------------------------------------------------------
def build_edge_tensors(graph, device=None) -> dict[str, Tensor]:
    """Chuyển `GraphData` sang tensor torch."""
    dev = device or torch.device("cpu")
    return {
        "edge_index": torch.as_tensor(graph.edge_index, dtype=torch.long, device=dev),
        "edge_attr": torch.as_tensor(graph.edge_attr, dtype=torch.float32, device=dev),
        "edge_dir": torch.as_tensor(graph.edge_dir, dtype=torch.long, device=dev),
        "tau_phys": torch.as_tensor(graph.tau_phys, dtype=torch.float32, device=dev),
        "static": torch.as_tensor(graph.static_attr, dtype=torch.float32, device=dev),
    }


def seed_everything(seed: int) -> None:
    """Cố định seed cho torch/numpy (tính lặp lại được)."""
    import random

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def configure_threads(num_threads: int = 1) -> None:
    """Gioi han so luong CPU dung cho torch.

    **Voi khoi luong nho (mot do thi con vai chuc nut, chuoi 336 buoc) thi da luong
    cua PyTorch gay "thrashing"**: do lai 1 luot GRU 12x336x91 mat 1186 ms voi 2
    luong nhung chi 60 ms voi 1 luong (do lai tren may nay, 24/11/2026). Vi vay mac
    dinh la **1 luong** cho CPU; tren GPU dien hinh khong ap dung.
    """
    import torch

    try:
        torch.set_num_threads(int(num_threads))
    except RuntimeError:
        pass
