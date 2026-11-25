"""Các lớp kiến trúc của PhaseGNN (đề cương 5.2.2 – 5.2.4).

Module này KHÔNG phụ thuộc `torch_geometric`: mọi phép gom đệm (`scatter_add`) được viết
bằng `index_add_` của PyTorch thuần, nhờ vậy mô hình chạy được trên image CPU tối giản
(`python:3.12-slim` + `torch` CPU) mà không cần cài `torch-geometric` (vốn kéo theo
`pyg-lib`/`torch-scatter` phải biên dịch).

**Ba khối chính.**

1. `PhaseShift` — tầng dịch pha khả vi: dịch một chuỗi theo độ trễ τ (giờ) với bước nội bộ Δ
   (mặc định 1 h) bằng nội suy tuyến tính, khả vi theo τ:
   ``k = ⌊τ/Δ⌋``, ``β = τ/Δ − k``, ``h(t−τ) ≈ (1−β)·h(t−kΔ) + β·h(t−(k+1)Δ)``.

2. `PhaseMessage` — thông điệp lan truyền pha hai chiều (đề cương 5.2.3):
   ``τ_uv(t) = τ_phys,uv · exp(δ_ψ^dir(e_uv, h_u(t), q(t)))``,
   ``γ_uv = softplus(a_ψ^dir(e_uv))`` (hệ số biên độ), làm trơn J bước quanh thời điểm trễ.

3. `TopoGRU` — cập nhật trạng thái ẩn theo thứ tự topo hai lượt (down rồi up), lặp K lần.

**Quy ước tensor.** Ở mọi nơi trong module: `x` có dạng ``[N, T, F]`` (N nút, T bước thời gian,
F đặc trưng), `edge_index` có dạng ``[2, E]`` (hàng 0 = nút nguồn `src`, hàng 1 = nút đích `dst`),
`edge_attr` có dạng ``[E, D]``, `edge_dir` là tensor ``[E]`` với giá trị 0 = `down`, 1 = `up`.
"""
from __future__ import annotations

import math

import torch
from torch import Tensor, nn

# ---------------------------------------------------------------------------
# Hằng số
# ---------------------------------------------------------------------------
DIR_DOWN = 0
DIR_UP = 1
NUM_DIRS = 2

#: Giới hạn của `δ` (log-hệ số hiệu chỉnh độ trễ) — đề cương 5.2.3: kẹp `δ ∈ [−1,5; 1,5]`.
#: Chu kỳ sóng triều M2 (giờ) — dùng cho nhúng pha của τ (`PhaseMessage`, `tau_mode="phase"`).
T_M2_H = 12.4206012
#: Chế độ tác động của τ lên thông điệp (xem `PhaseMessage`).
TAU_MODES = ("grid", "phase")

DELTA_MIN = -1.5
DELTA_MAX = 1.5

#: Số bước làm trơn quanh thời điểm trễ (đề cương 5.2.3: "làm trơn J bước").
DEFAULT_SMOOTH_STEPS = 2

#: Bước nội bộ mặc định (giờ). `12.42` là nửa chu kỳ M2 (đề cương 5.2.2).
DEFAULT_DELTA_H = 1.0
TIDAL_HALF_CYCLE_H = 12.42


def _as_float(value: float | Tensor, like: Tensor) -> Tensor:
    """Ép giá trị về tensor cùng kiểu với `like`."""
    if isinstance(value, Tensor):
        return value.to(dtype=like.dtype, device=like.device)
    return torch.as_tensor(value, dtype=like.dtype, device=like.device)


# ---------------------------------------------------------------------------
# 1. PhaseShift — dịch pha khả vi
# ---------------------------------------------------------------------------
class PhaseShift(nn.Module):
    """Dịch chuỗi theo độ trễ τ (giờ) theo bước nội bộ Δ, khả vi theo τ.

    Cho chuỗi ``x`` dạng ``[N, T, F]`` và độ trễ ``tau`` dạng ``[N]`` (giờ) cho từng nút,
    hàm trả về chuỗi đã dịch: ``y[:, t] = x[:, t − τ]`` (nội suy tuyến tính).

    Vì `k = ⌊τ/Δ⌋` là bước nguyên, phép nội suy được viết dạng **khả vi liên tục** theo β
    (phần thập phân của τ/Δ); gradient truyền ngược qua `β` nên τ học được bằng gradient thuần.

    Tham số
    -------
    delta_h : bước nội bộ Δ (giờ). Mặc định 1 h.
    max_steps : trần số bước trễ tối đa được xem xét (để giữ chi phí O(T·F) thay vì O(T²)).
    """

    def __init__(self, delta_h: float = DEFAULT_DELTA_H, max_steps: int | None = None) -> None:
        super().__init__()
        if delta_h <= 0:
            raise ValueError(f"delta_h phải > 0, nhận {delta_h}")
        self.delta_h = float(delta_h)
        self.max_steps = int(max_steps) if max_steps is not None else None

    def forward(self, x: Tensor, tau: Tensor) -> Tensor:
        """Dịch `x` (``[N, T, F]``) theo `tau` (``[N]``, giờ) → ``[N, T, F]``."""
        if x.dim() != 3:
            raise ValueError(f"x phải có dạng [N, T, F], nhận {tuple(x.shape)}")
        n, t, f = x.shape
        tau = _as_float(tau, x).reshape(-1)
        if tau.numel() != n:
            raise ValueError(f"tau phải có {n} phần tử, nhận {tau.numel()}")

        # số bước trễ (nguyên) và phần thập phân
        ratio = tau / self.delta_h
        k = torch.floor(ratio)
        beta = ratio - k                       # ∈ [0, 1)
        k = k.clamp(min=0.0)
        if self.max_steps is not None:
            k = k.clamp(max=float(self.max_steps))

        # Với bước trễ nguyên `k` và phần thập phân `β`:
        #   y[t] = (1−β)·x[t−k] + β·x[t−k−1]
        # Nội suy này tương đương nội suy giữa hai mốc lịch cách Δ, nên sai số
        # so với dịch pha liên tục là O(Δ) — với Δ = 1 h và τ vài giờ là chấp nhận được.
        y = self._gather_shift(x, k, beta)
        return y

    def _gather_shift(self, x: Tensor, k: Tensor, beta: Tensor) -> Tensor:
        """Lấy `x[:, t−k]` và `x[:, t−k−1]` rồi trộn theo `β`."""
        n, t, f = x.shape
        dev = x.device
        # chỉ số thời gian cho từng nút: t − k (và t − k − 1)
        times = torch.arange(t, device=dev, dtype=torch.long).unsqueeze(0)      # [1, T]
        kk = k.round().to(torch.long).unsqueeze(1)                              # [N, 1]
        i0 = (times - kk).clamp(min=0)
        i1 = (times - kk - 1).clamp(min=0)
        # ngoài vùng quan sát (t < k) → giữ giá trị đầu tiên (tương đương "trước khi có dữ liệu")
        idx0 = i0.unsqueeze(-1).expand(n, t, f)
        idx1 = i1.unsqueeze(-1).expand(n, t, f)
        v0 = torch.gather(x, 1, idx0)
        v1 = torch.gather(x, 1, idx1)
        b = beta.to(x.dtype).reshape(n, 1).unsqueeze(-1)     # [N, 1, 1]
        return (1.0 - b) * v0 + b * v1

    def extra_repr(self) -> str:
        return f"delta_h={self.delta_h}, max_steps={self.max_steps}"


# ---------------------------------------------------------------------------
# 2. PhaseMessage — thông điệp lan truyền pha
# ---------------------------------------------------------------------------
class PhaseMessage(nn.Module):
    """Tính thông điệp truyền theo độ trễ học được, theo từng cạnh và từng bước thời gian.

    **Công thức (đề cương 5.2.3).**

    * Độ trễ: ``τ_uv(t) = τ_phys,uv · exp(δ_ψ^dir(e_uv, h_u(t), q(t)))``,
      với `δ` xuất phát từ một MLP (đầu vào: đặc trưng cạnh + trạng thái nguồn + lưu lượng xả
      hồ) và bị kẹp trong `[DELTA_MIN, DELTA_MAX]`. Tầng cuối khởi tạo bằng 0 ⇒ ban đầu
      ``τ = τ_phys``.
    * Biên độ: ``γ_uv = softplus(a_ψ^dir(e_uv))`` — cho phép cả suy giảm (<1) và khuếch đại (>1).
    * Làm trơn: ``h̃_u(t) = Σ_j w_j · h_u(t − τ_uv − jΔ)`` với `w_j` là trọng số chuẩn hoá
      (nhân bố nhịp theo mô hình trượt, cũng khả vi theo τ).

    Tham số
    -------
    node_dim : số kênh trạng thái ẩn của nút.
    edge_dim : số kênh đặc trưng cạnh.
    q_dim : số kênh đặc trưng lưu lượng xả hồ (mặc định 0 = không dùng).
    hidden : kích thước ẩn của MLP `δ`.
    smooth_steps : số bước làm trơn J.
    delta_h : bước nội bộ Δ (giờ).
    learn_tau : nếu `False` → `τ = τ_phys` cố định (biến thể B4).
    tau_mode : cách τ tác động lên thông điệp (thêm 25/11/2026).

        * ``"grid"`` (mặc định, hành vi cũ): chỉ dịch chuỗi trạng thái nguồn trên lưới Δ
          (`PhaseShift`: nội suy giữa hai mốc lưới). Khi τ ≪ Δ (vd. τ ≈ 0.9 h, Δ = 12 h)
          phép dịch gần như không đổi gì ⇒ gradient theo δ rất yếu (δ ≈ 0 sau huấn luyện).
        * ``"phase"``: như ``"grid"`` **cộng** một nhúng pha liên tục của τ:
          ``P · [sin, cos](2π τ / T_M2), [sin, cos](2π τ / 24 h)`` (`P`: tuyến tính 4 → node_dim).
          Với sóng bán nhật triều, trễ τ tương đương quay pha M2 một góc 2πτ/T_M2; nhúng
          này cho trạm đích biết độ lệch pha tích luỹ dọc cạnh, liên tục và khả vi theo τ ở
          mọi độ lớn của τ (không phụ thuộc Δ).
    """

    def __init__(self, node_dim: int, edge_dim: int, q_dim: int = 0, hidden: int = 32,
                 smooth_steps: int = DEFAULT_SMOOTH_STEPS, delta_h: float = DEFAULT_DELTA_H,
                 learn_tau: bool = True, tau_mode: str = "grid") -> None:
        super().__init__()
        if tau_mode not in TAU_MODES:
            raise ValueError(f"tau_mode phải là một trong {TAU_MODES}, nhận {tau_mode!r}")
        self.tau_mode = tau_mode
        self.node_dim = int(node_dim)
        self.edge_dim = int(edge_dim)
        self.q_dim = int(q_dim)
        self.smooth_steps = int(smooth_steps)
        self.delta_h = float(delta_h)
        self.learn_tau = bool(learn_tau)

        in_dim = self.edge_dim + self.node_dim + self.q_dim
        self.delta_mlp = nn.Sequential(
            nn.Linear(in_dim, hidden),
            nn.SiLU(),
            nn.Linear(hidden, NUM_DIRS),
        )
        # khởi tạo tầng cuối bằng 0 ⇒ δ = 0 ⇒ τ = τ_phys (đề cương 5.2.3)
        nn.init.zeros_(self.delta_mlp[-1].weight)
        nn.init.zeros_(self.delta_mlp[-1].bias)

        # hệ số biên độ γ theo chiều: γ = softplus(a)
        self.gamma_raw = nn.Parameter(torch.zeros(NUM_DIRS))

        # trọng số làm trơn J bước (mềm, khởi tạo đồng đều)
        self.smooth_logits = nn.Parameter(torch.zeros(max(self.smooth_steps, 1)))

        self.shifter = PhaseShift(delta_h=self.delta_h)

        if self.tau_mode == "phase":
            # nhúng pha của τ (4 kênh → node_dim); khởi tạo nhỏ, khác 0 để δ có gradient ngay
            self.phase_proj = nn.Linear(4, self.node_dim, bias=False)
            nn.init.normal_(self.phase_proj.weight, std=0.1)

    def phase_embedding(self, tau: Tensor) -> Tensor:
        """``tau`` (giờ, dạng bất kỳ ``[...]``) → nhúng pha ``[..., node_dim]`` (chế độ ``phase``)."""
        a = 2.0 * math.pi * tau / T_M2_H
        b = 2.0 * math.pi * tau / 24.0
        feats = torch.stack([torch.sin(a), torch.cos(a), torch.sin(b), torch.cos(b)], dim=-1)
        return self.phase_proj(feats)

    # -- trạng thái nội bộ -------------------------------------------------
    def effective_tau(self, tau_phys: Tensor, edge_dir: Tensor, delta: Tensor) -> Tensor:
        """τ thực dùng trên cạnh: ``τ_phys · exp(δ)``.

        `tau_phys` có dạng ``[E]``, `delta` có dạng ``[E, T]`` ⇒ kết quả ``[E, T]``.
        """
        return tau_phys.unsqueeze(-1) * torch.exp(delta)

    def forward(self, h: Tensor, tau_phys: Tensor, edge_index: Tensor, edge_attr: Tensor,
                edge_dir: Tensor, node_q: Tensor | None = None) -> tuple[Tensor, Tensor, Tensor]:
        """Tính thông điệp theo cạnh.

        Tham số
        -------
        h : ``[N, T, node_dim]`` — trạng thái ẩn của các nút.
        tau_phys : ``[E]`` — độ trễ vật lý của từng cạnh (giờ).
        edge_index : ``[2, E]`` — (`src`, `dst`).
        edge_attr : ``[E, edge_dim]``.
        edge_dir : ``[E]`` — 0 = `down`, 1 = `up`.
        node_q : ``[N, T, q_dim]`` — lưu lượng xả hồ theo nút/bước (tuỳ chọn).

        Trả về
        -------
        msg : ``[E, T, node_dim]`` — thông điệp đã nhân γ và đã làm trơn.
        tau_eff : ``[E]`` — độ trễ thực dùng (để tính `L_τ`).
        delta : ``[E]`` — δ hiệu chỉnh độ trễ (để tính `L_τ = mean(δ²)`).
        """
        n, t, _ = h.shape
        e = edge_index.shape[1]
        device, dtype = h.device, h.dtype

        # --- δ_ψ theo chiều ------------------------------------------------
        src, dst = edge_index[0], edge_index[1]
        h_src = h[src]                                            # [E, T, node_dim]
        parts = [edge_attr.unsqueeze(1).expand(e, t, self.edge_dim)]
        if self.node_dim:
            parts.append(h_src)
        if self.q_dim and node_q is not None:
            parts.append(node_q[src])
        mlp_in = torch.cat(parts, dim=-1)
        delta_all = self.delta_mlp(mlp_in)                        # [E, T, NUM_DIRS]
        # chọn theo chiều của cạnh: index [E, T, 1] → kết quả [E, T, 1] → squeeze → [E, T]
        dir_idx = edge_dir.clamp(0, NUM_DIRS - 1).view(e, 1, 1).expand(e, t, 1)
        delta = torch.gather(delta_all, 2, dir_idx).squeeze(-1)  # [E, T]
        delta = torch.clamp(delta, DELTA_MIN, DELTA_MAX)

        if self.learn_tau:
            tau = self.effective_tau(tau_phys, edge_dir, delta)    # [E, T]
        else:
            tau = tau_phys.unsqueeze(1).expand(e, t)

        # --- làm trơn quanh thời điểm trễ -----------------------------------
        # h̃(t) = Σ_j w_j · h(t − τ − jΔ)
        w = torch.softmax(self.smooth_logits, dim=0)              # [J]
        j_steps = torch.arange(self.smooth_steps, device=device, dtype=dtype)
        acc = torch.zeros(e, t, self.node_dim, device=device, dtype=dtype)
        for j in range(self.smooth_steps):
            tau_j = tau + float(j) * self.delta_h
            shifted = self._shift_per_edge(h[src], tau_j)          # [E, T, node_dim]
            acc = acc + w[j] * shifted
        h_tilde = acc
        if self.tau_mode == "phase":
            h_tilde = h_tilde + self.phase_embedding(tau)          # [E, T, node_dim]

        # --- biên độ γ ------------------------------------------------------
        gamma = torch.nn.functional.softplus(self.gamma_raw)[edge_dir.clamp(0, NUM_DIRS - 1)]
        gamma = gamma.view(e, 1, 1)

        msg = gamma * h_tilde
        tau_eff = tau.mean(dim=1) if self.learn_tau else tau_phys
        delta_eff = delta.mean(dim=1) if self.learn_tau else torch.zeros_like(tau_phys)
        return msg, tau_eff, delta_eff

    def forward_batch(self, h: Tensor, tau_phys: Tensor, edge_index: Tensor, edge_attr: Tensor,
                      edge_dir: Tensor) -> tuple[Tensor, Tensor, Tensor]:
        """Bản **theo lô** của `forward`: `h` có dạng ``[B, N, T, node_dim]``.

        Toàn bộ mẫu trong lô dùng **chung** một đồ thị con (cùng `edge_index`, `edge_attr`,
        `edge_dir`, `tau_phys`) — đúng như khi dự báo nhiều ngày cho cùng một trạm.

        Trả về `msg` dạng ``[B, E, T, node_dim]`` (thông điệp đã nhân γ và làm trơn).
        """
        b, n, t, _ = h.shape
        e = edge_index.shape[1]
        device, dtype = h.device, h.dtype

        src, dst = edge_index[0], edge_index[1]
        h_src = h[:, src]                                       # [B, E, T, node_dim]
        parts = [edge_attr.unsqueeze(0).unsqueeze(2).expand(b, e, t, self.edge_dim)]
        if self.node_dim:
            parts.append(h_src)
        mlp_in = torch.cat(parts, dim=-1)
        delta_all = self.delta_mlp(mlp_in)                       # [B, E, T, NUM_DIRS]
        dir_idx = edge_dir.clamp(0, NUM_DIRS - 1).view(1, e, 1, 1).expand(b, e, t, 1)
        delta = torch.gather(delta_all, 3, dir_idx).squeeze(-1)  # [B, E, T]
        delta = torch.clamp(delta, DELTA_MIN, DELTA_MAX)

        if self.learn_tau:
            tau = tau_phys.view(1, e, 1) * torch.exp(delta)       # [B, E, T]
        else:
            tau = tau_phys.view(1, e, 1).expand(b, e, t)

        w = torch.softmax(self.smooth_logits, dim=0)             # [J]
        j_steps = torch.arange(self.smooth_steps, device=device, dtype=dtype)
        acc = torch.zeros(b, e, t, self.node_dim, device=device, dtype=dtype)
        for j in range(self.smooth_steps):
            tau_j = tau + float(j) * self.delta_h
            shifted = self._shift_per_edge_batch(h_src, tau_j)    # [B, E, T, node_dim]
            acc = acc + w[j] * shifted
        h_tilde = acc
        if self.tau_mode == "phase":
            h_tilde = h_tilde + self.phase_embedding(tau)          # [B, E, T, node_dim]

        gamma = torch.nn.functional.softplus(self.gamma_raw)[edge_dir.clamp(0, NUM_DIRS - 1)]
        gamma = gamma.view(1, e, 1, 1)

        msg = gamma * h_tilde
        tau_eff = tau.mean(dim=2) if self.learn_tau else tau_phys.view(1, e).expand(b, e)
        delta_eff = delta.mean(dim=2) if self.learn_tau else torch.zeros_like(tau_eff)
        return msg, tau_eff, delta_eff

    def _shift_per_edge_batch(self, x: Tensor, tau: Tensor) -> Tensor:
        """Dịch theo lô: `x` = ``[B, E, T, F]``, `tau` = ``[B, E, T]`` → ``[B, E, T, F]``."""
        b, e, t, f = x.shape
        ratio = tau / self.delta_h
        k = torch.floor(ratio).clamp(min=0.0)
        beta = ratio - k
        times = torch.arange(t, device=x.device, dtype=torch.long).view(1, 1, t)
        kl = k.to(torch.long)                                    # [B, E, T]
        i0 = (times - kl).clamp(min=0)
        i1 = (times - kl - 1).clamp(min=0)
        # `gather` theo trục thời gian (dim=2) với chỉ số `[B, E, T, F]`
        idx0 = i0.unsqueeze(-1).expand(b, e, t, f)
        idx1 = i1.unsqueeze(-1).expand(b, e, t, f)
        v0 = torch.gather(x, 2, idx0)
        v1 = torch.gather(x, 2, idx1)
        bt = beta.to(x.dtype).unsqueeze(-1)
        return (1.0 - bt) * v0 + bt * v1

    def _shift_per_edge(self, x: Tensor, tau: Tensor) -> Tensor:
        """Dịch từng chuỗi theo τ riêng (x: ``[E, T, F]``, τ: ``[E, T]``).

        Vì mỗi cạnh có τ riêng, ta dùng trực tiếp công thức nội suy tuyến tính thay vì
        `PhaseShift` (vốn giả định τ chung theo nút).
        """
        e, t, f = x.shape
        ratio = tau / self.delta_h
        k = torch.floor(ratio).clamp(min=0.0)
        beta = ratio - k
        times = torch.arange(t, device=x.device, dtype=torch.long).unsqueeze(0)   # [1, T]
        kl = k.to(torch.long)
        i0 = (times - kl).clamp(min=0)
        i1 = (times - kl - 1).clamp(min=0)
        v0 = torch.gather(x, 1, i0.unsqueeze(-1).expand(e, t, f))
        v1 = torch.gather(x, 1, i1.unsqueeze(-1).expand(e, t, f))
        b = beta.to(x.dtype).unsqueeze(-1)
        return (1.0 - b) * v0 + b * v1

    def delta_penalty(self, delta: Tensor) -> Tensor:
        """`L_τ = mean(δ²)` — kéo độ trễ học được về gần vật lý."""
        return (delta ** 2).mean()


# ---------------------------------------------------------------------------
# 3. TopoGRU — lan truyền theo thứ tự topo hai lượt
# ---------------------------------------------------------------------------
class TopoGRU(nn.Module):
    """Cập nhật trạng thái ẩn theo thứ tự topo, hai lượt (down rồi up), lặp K lần.

    **Công thức (đề cương 5.2.4).**

    Lượt 1 (dòng chảy, từ thượng nguồn/hồ ra cửa sông): xử lý các nút theo thứ tự topo
    chiều `down`; mỗi nút nhận thông điệp từ nút thượng nguồn đã có trạng thái.

    Lượt 2 (triều, từ nút biển vào thượng lguồn): xử lý theo thứ tự topo chiều `up`; mỗi nút
    nhận thông điệp từ nút phía biển (đã có trạng thái **sau** lượt 1).

    Hai lượt trong cùng một bước thời gian cho phép thông tin xả hồ và triều cùng tới mọi
    nút trong một bước. Lặp `K` lần để mô phỏng tương tác hai sóng.

    Tham số
    -------
    node_dim : số kênh trạng thái ẩn.
    in_dim : số kênh đầu vào của GRU (đặc trưng quan trọc + đặc trưng tĩnh).
    message_dim : số kênh thông điệp gộp.
    num_layers : số lớp GRU.
    directions : (`True`, `True`) = hai chiều; chỉ `down` ⇒ B5b (bỏ chiều triều);
        chỉ `up` ⇒ B5a (bỏ chiều dòng chảy).
    """

    def __init__(self, node_dim: int, in_dim: int, message_dim: int, num_layers: int = 1,
                 directions: tuple[bool, bool] = (True, True)) -> None:
        super().__init__()
        self.node_dim = int(node_dim)
        self.message_dim = int(message_dim)
        self.use_down, self.use_up = bool(directions[0]), bool(directions[1])
        if not (self.use_down or self.use_up):
            raise ValueError("phải bật ít nhất một chiều")
        self.gru = nn.GRU(input_size=in_dim + message_dim, hidden_size=node_dim,
                          num_layers=num_layers, batch_first=False)
        self.proj = nn.Linear(node_dim, node_dim)
        self.gate = nn.Linear(node_dim, node_dim)

    # -- gom tin nhắn --------------------------------------------------------
    @staticmethod
    def scatter_sum(src: Tensor, index: Tensor, dim_size: int) -> Tensor:
        """Cộng dồn theo `index` (`scatter_add` thuần PyTorch)."""
        out = src.new_zeros((dim_size,) + src.shape[1:])
        return out.index_add_(0, index, src)

    @staticmethod
    def scatter_sum_batch(src: Tensor, index: Tensor, dim_size: int) -> Tensor:
        """Cộng dồn theo nút đích cho lô: `src` = ``[B, E, ...]`` → ``[B, N, ...]``."""
        b = src.shape[0]
        flat_dst = (index.view(1, -1) + torch.arange(b, device=src.device).view(-1, 1) * dim_size)
        flat_src = src.reshape(b * src.shape[1], *src.shape[2:])
        out_flat = src.new_zeros((b * dim_size,) + tuple(src.shape[2:]))
        out_flat.index_add_(0, flat_dst.reshape(-1), flat_src)
        return out_flat.view(b, dim_size, *src.shape[2:])

    def aggregate(self, msg: Tensor, edge_index: Tensor, num_nodes: int) -> Tensor:
        """Gom thông điệp theo nút đích: ``M_dst = Σ_{u→dst} msg``."""
        dst = edge_index[1]
        return self.scatter_sum(msg, dst, num_nodes)

    def forward(self, h: Tensor, x: Tensor, msg_down: Tensor, msg_up: Tensor,
                edge_index_down: Tensor, edge_index_up: Tensor,
                topo_order_down: Tensor, topo_order_up: Tensor,
                num_nodes: int, pre_aggregated: bool = False) -> Tensor:
        """Một bước thời gian nội bộ.

        Tham số
        -------
        h : ``[N, node_dim]`` — trạng thái ẩn hiện tại.
        x : ``[N, in_dim]`` — đặc trưng quan trọc + tĩnh.
        msg_down : ``[E_down, message_dim]`` — thông điệp chiều xuôi (đã dịch pha);
            nếu `pre_aggregated=True` thì đây là thông điệp **đã gom theo nút**,
            dạng ``[N, message_dim]``.
        msg_up : ``[E_up, message_dim]`` — thông điệp chiều ngược (đã dịch pha).
        edge_index_down / edge_index_up : ``[2, E]``.
        topo_order_down / topo_order_up : thứ tự topo (tensor chỉ số nút).
        pre_aggregated : nếu `True`, bỏ qua bước gom thông điệp (đã gom sẵn).

        **Cơ chế hai lượt.** Thông điệp của cả hai chiều được gom trước (mỗi thông điệp đã
        được dịch pha theo τ của cạnh tương ứng), rồi:

        * lượt 1 (`down`): trạng thái của các nút được cập nhật theo thứ tự topo chiều
          xuôi — nhận thông điệp từ nút thượng nguồn;
        * lượt 2 (`up`): trạng thái sau lượt 1 được cập nhật theo thứ tự topo chiều ngược
          — nhận thông điệp từ nút phía biển.

        Vì `nn.GRU` của PyTorch tính cổng `reset`/`update` trên cùng trạng thái trước đó
        (song song), ta gọi GRU một lần cho toàn bộ nút ở mỗi lượt; thứ tự topo quyết định
        THỨ TỰ cập nhật giữa hai lượt, tức là trạng thái lượt 2 đã phản ánh lượt 1.
        """
        if pre_aggregated:
            m_down = msg_down if self.use_down \
                else h.new_zeros((num_nodes, self.message_dim))
            m_up = msg_up if self.use_up \
                else h.new_zeros((num_nodes, self.message_dim))
        else:
            m_down = self.aggregate(msg_down, edge_index_down, num_nodes) if self.use_down \
                else h.new_zeros((num_nodes, self.message_dim))
            m_up = self.aggregate(msg_up, edge_index_up, num_nodes) if self.use_up \
                else h.new_zeros((num_nodes, self.message_dim))
        m = m_down + m_up

        # cổng (gate) điều khiển lượng thông điệp nhập vào trạng thái
        g = torch.sigmoid(self.gate(h))
        inp = torch.cat([x, m], dim=-1)

        # Lượt 1: chiều xuôi (dòng chảy)
        h_down = self._gru_step(h, inp) if self.use_down else h
        # Lượt 2: chiều ngược (triều) — dùng trạng thái sau lượt 1
        h_up = self._gru_step(h_down, inp) if self.use_up else h_down

        return g * self.proj(h_up) + (1.0 - g) * h_up

    def _gru_step(self, h: Tensor, inp: Tensor) -> Tensor:
        """Một bước GRU cho toàn bộ nút (song song theo trục nút)."""
        out, _ = self.gru(inp.unsqueeze(0), h.unsqueeze(0).contiguous())
        return out.squeeze(0)

    def forward_sequence(self, x_seq: Tensor, m_seq: Tensor,
                         directions: tuple[bool, bool] | None = None,
                         init_state: Tensor | None = None) -> Tensor:
        """Cập nhật trạng thái ẩn trên **toàn bộ chuỗi thời gian** (vector hoá).

        Tham số
        -------
        x_seq : ``[N, T, in_dim]`` — đặc trưng động + tĩnh theo thời gian.
        m_seq : ``[N, T, message_dim]`` — thông điệp đã gom theo nút (đã dịch pha).
        init_state : ``[N, node_dim]`` hoặc `None` — trạng thái ẩn ban đầu (dùng cho
            lượt 2 để nhận trạng thái **sau lượt 1**).

        Trả về ``[N, T, node_dim]``.

        **Vì sao cần cách này?** Vòng lặp Python theo từng bước thời gian (T = 336 cho
        cửa sổ 14 ngày × 24 h) tốn ~33 s trên CPU do chi phí gọi `nn.GRU` cho ma trận rất
        nhỏ. Ở đây ta truyền **cả chuỗi** vào GRU một lượt ⇒ PyTorch xử lý song song
        trên trục thời gian, nhanh hơn ~30 lần.

        **Cơ chế hai lượt được giữ:** lượt 1 dùng thông điệp chiều `down`, lượt 2 dùng
        thông điệp chiều `up` và **trạng thái sau lượt 1** — trạng thái sau lượt 1 được
        truyền sang lượt 2 qua `init_state` (đặt làm trạng thái ẩn ban đầu của GRU lượt 2).
        """
        if directions is not None:
            use_down, use_up = bool(directions[0]), bool(directions[1])
        else:
            use_down, use_up = self.use_down, self.use_up

        n, t, _ = x_seq.shape
        if not (use_down or use_up):
            # không dùng chiều nào ⇒ trạng thái chỉ là phép chiếu của đặc trưng
            base = torch.zeros(n, t, self.node_dim, dtype=x_seq.dtype, device=x_seq.device)
            g = torch.sigmoid(self.gate(base))
            return g * self.proj(base) + (1.0 - g) * base

        # GRU trên trục thời gian: đổi thứ tự thành `[T, N, F]` (seq_len, batch, feature)
        inp = torch.cat([x_seq, m_seq], dim=-1).permute(1, 0, 2).contiguous()
        h0 = None
        if init_state is not None:
            # trạng thái khởi tạo cho GRU: `[num_layers, N, H]`
            h0 = init_state.unsqueeze(0).contiguous()
        out, _ = self.gru(inp, h0)                                # [T, N, node_dim]
        out = out.permute(1, 0, 2)                                # [N, T, node_dim]
        # cổng (gate) điều khiển lượng thông điệp nhập vào trạng thái
        g = torch.sigmoid(self.gate(out))
        return g * self.proj(out) + (1.0 - g) * out

    def extra_repr(self) -> str:
        return (f"node_dim={self.node_dim}, message_dim={self.message_dim}, "
                f"down={self.use_down}, up={self.use_up}")

    # ------------------------------------------------------------------ lô -----
    def aggregate_batch(self, msg: Tensor, edge_index: Tensor, num_nodes: int) -> Tensor:
        """Gom thông điệp theo nút đích cho lô: `msg` = ``[B, E, T, H]`` → ``[B, N, T, H]``."""
        dst = edge_index[1]
        out = msg.new_zeros((msg.shape[0], num_nodes) + msg.shape[2:])
        # `index_add_` với chỉ số lặp lại theo trục batch:
        # ta gom theo (batch, dst) bằng cách dùng chỉ số phẳng rồi reshape.
        b = msg.shape[0]
        flat_dst = (dst.view(1, -1) + torch.arange(b, device=msg.device).view(-1, 1) * num_nodes)
        flat_src = msg.reshape(b * msg.shape[1], *msg.shape[2:])
        out_flat = out.new_zeros((b * num_nodes,) + tuple(msg.shape[2:]))
        out_flat.index_add_(0, flat_dst.reshape(-1), flat_src)
        return out_flat.view(b, num_nodes, *msg.shape[2:])

    def forward_sequence_batch(self, x_seq: Tensor, m_seq: Tensor,
                               directions: tuple[bool, bool] | None = None,
                               init_state: Tensor | None = None) -> Tensor:
        """Bản **theo lô** của `forward_sequence`.

        `x_seq` = ``[B, N, T, in_dim]``, `m_seq` = ``[B, N, T, message_dim]`` →
        trả về ``[B, N, T, node_dim]``.

        **Vì sao cần?** `nn.GRU` trên CPU có chi phí gọi kernel cho mỗi bước thời gian, nên
        tốc độ gần như không đổi khi tăng số nút trong một chuỗi. Gom nhiều mẫu (cùng trạm)
        vào một lô biến trục batch của GRU từ `N` thành `B·N` ⇒ giảm số lần gọi GRU đi
        `B` lần, giữ nguyên kết quả số học.

        **Bố cục bộ nhớ.** Ta chuyển sang bố cục **theo thời gian trước** `[T, B·N, F]`
        ngay từ đầu để đầu vào GRU là *contiguous* — không cần `permute`/`contiguous`
        sao chép lại (chi phí này chiếm phần lớn thời gian khi huấn luyện trên CPU).
        """
        if directions is not None:
            use_down, use_up = bool(directions[0]), bool(directions[1])
        else:
            use_down, use_up = self.use_down, self.use_up

        b, n, t, _ = x_seq.shape
        if not (use_down or use_up):
            base = torch.zeros(b, n, t, self.node_dim, dtype=x_seq.dtype, device=x_seq.device)
            g = torch.sigmoid(self.gate(base))
            return g * self.proj(base) + (1.0 - g) * base

        # `[B, N, T, F]` → `[T, B·N, F]` (seq_len, batch, feature) — 1 lần copy.
        inp = torch.cat([x_seq, m_seq], dim=-1).permute(2, 0, 1, 3).reshape(t, b * n, -1)
        inp = inp.contiguous()
        h0 = None
        if init_state is not None:
            # `init_state` = `[B, N, H]` → trạng thái khởi tạo GRU `[1, B·N, H]`
            h0 = init_state.reshape(b * n, -1).unsqueeze(0).contiguous()
        out, _ = self.gru(inp, h0)                                # [T, B·N, node_dim]
        # trả về `[B, N, T, H]` (khớp với `x_seq` để lớp sau dùng tiếp)
        out = out.view(t, b, n, -1).permute(1, 2, 0, 3).contiguous()
        g = torch.sigmoid(self.gate(out))
        return g * self.proj(out) + (1.0 - g) * out


# ---------------------------------------------------------------------------
# Tiện ích topo
# ---------------------------------------------------------------------------
def topo_order(num_nodes: int, edge_index: Tensor, direction: str) -> Tensor:
    """Thứ tự topo của các nút theo một chiều.

    * `direction="down"`: thứ tự để lan truyền TỪ thượng nguồn XUÔNG hạ nguồn, tức là thứ tự
      topo của đồ thị có cạnh đảo chiều (`dst → src` trong `edge_index` là chiều dòng chảy).
    * `direction="up"`: thứ tự để lan truyền TỪ biển NGƯỢC dòng chảy, tức thứ tự topo của
      đồ thị có cạnh `src → dst` theo chiều `up`.

    Trả về tensor chỉ số nút, sắp xếp sao cho mọi nút trong một cạnh đều đứng trước nút kia
    theo chiều lan truyền (Kahn).
    """
    device = edge_index.device
    n = int(num_nodes)
    if edge_index.numel() == 0:
        return torch.arange(n, device=device)
    src, dst = edge_index[0], edge_index[1]
    if direction == "down":
        # lan truyền xuôi: thượng nguồn (src trong `down` edges) → hạ nguồn (dst)
        succ: dict[int, list[int]] = {}
        indeg = [0] * n
        for s, d in zip(src.tolist(), dst.tolist()):
            succ.setdefault(s, []).append(d)
            indeg[d] += 1
    elif direction == "up":
        # lan truyền ngược: hạ nguồn (src trong `up` edges) → thượng nguồn (dst)
        succ = {}
        indeg = [0] * n
        for s, d in zip(src.tolist(), dst.tolist()):
            succ.setdefault(s, []).append(d)
            indeg[d] += 1
    else:
        raise ValueError(f"direction phải là 'down' hoặc 'up', nhận {direction!r}")

    queue = [i for i in range(n) if indeg[i] == 0]
    order: list[int] = []
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        order.append(node)
        for nxt in succ.get(node, ()):
            indeg[nxt] -= 1
            if indeg[nxt] == 0:
                queue.append(nxt)
    # nút còn lại (chu trình) → thêm vào cuối để không mất nút
    seen = set(order)
    for i in range(n):
        if i not in seen:
            order.append(i)
    return torch.tensor(order, dtype=torch.long, device=device)


def split_directions(edge_index: Tensor, edge_dir: Tensor) -> tuple[Tensor, Tensor]:
    """Tách chỉ số cạnh theo chiều (`down` / `up`) → (`idx_down`, `idx_up`)."""
    is_down = edge_dir == DIR_DOWN
    return torch.nonzero(is_down, as_tuple=False).flatten(), \
        torch.nonzero(~is_down, as_tuple=False).flatten()


def softplus_inv(y: float) -> float:
    """Giá trị thô `a` để `softplus(a) = y` (tiện cho test)."""
    if y <= 0:
        raise ValueError("y phải > 0")
    return math.log(math.expm1(y))
