"""Hàm mất mát của PhaseGNN (đề cương 5.2.5).

Tổng hàm mất mát:

    L = L_seq + λ₁·L_peak + λ₂·L_timing + λ₃·L_τ + λ₄·L_mono

* `L_seq` — **pinball loss** (quantile loss) trên 3 phân vị (q10/q50/q90) của mọi đỉnh và chân.
* `L_peak` — sai số độ cao đỉnh ngày, tính qua **soft-max** trên 2 đỉnh (khác việc hard-argmax
  để gradient không bị đứt).
* `L_timing` — sai số thời điểm đỉnh (giờ), tính theo **khoảng cách vòng 24 giờ** để 23:30 và
  00:15 là gần nhau.
* `L_τ` — `mean(δ²)`: giữ độ trễ học được gần vật lý.
* `L_mono` — phạt khi phân vị đảo thứ tự (q10 > q50 hoặc q50 > q90).

Quy ước tensor trong module:
* `pred`: ``[B, 2, 3]`` — 2 đỉnh (hoặc chân) × 3 phân vị.
* `target`: ``[B, 2]`` — giá trị thực đo của từng đỉnh (NaN = không quan trắc).
* `pred_hour`: ``[B, 2]`` — giờ dự báo (0..24), là **độ lệch** so với giờ đỉnh thiên văn
  (theo đề cương) — dùng để tính `L_timing` với khoảng cách vòng.
"""
from __future__ import annotations

import math

import torch
from torch import Tensor

#: Các mức phân vị mà mô hình dự báo (khớp với `schemas/prediction/v1.schema.json`).
QUANTILES: tuple[float, ...] = (0.1, 0.5, 0.9)

#: Trần nhỏ cho thang chuẩn hoá trong soft-max đỉnh (m) — tránh chia 0 khi hai đỉnh bằng nhau.
MIN_HEIGHT_SCALE = 0.1

#: Thang vật lý tham chiếu cho soft-max **chọn đỉnh** (m) — biên độ triều đặc trưng của
#: lưu vực Đồng Nai (đỉnh ≈ 1,0–1,6 m; chân ≈ −0,3–0,6 m). Dùng thang cố định thay vì
#: chuẩn hoá theo chính dữ liệu để trọng số soft-max có ý nghĩa vật lý: chênh lệch
#: 0,5 m giữa hai đỉnh là khác biệt quyết định ⇒ soft-argmax nghiêng hẳn về đỉnh cao.
#: Nếu chuẩn hoá theo hiệu số giữa hai đỉnh thì trọng số luôn gần 50/50 ⇒ mất ý nghĩa.
PEAK_SCALE_M = 0.5

#: Nhiệt độ mặc định của soft-max **chọn đỉnh** (đơn vị mét). Với thang `PEAK_SCALE_M`
#: = 0,5 m, nhiệt độ 0,25 ⇒ chênh lệch 1 m giữa hai đỉnh cho logits ±4 ⇒ trọng số
#: ~[0,018; 0,982] — đỉnh cao thống trị, nhưng vẫn khả vi (gradient không bị đứt).
DEFAULT_TEMPERATURE = 0.25


# ---------------------------------------------------------------------------
# Pinball / quantile loss
# ---------------------------------------------------------------------------
def pinball_loss(pred: Tensor, target: Tensor, quantiles: tuple[float, ...] = QUANTILES) -> Tensor:
    """Pinball loss (quantile loss) trên nhiều phân vị.

    Tham số
    -------
    pred : ``[B, ..., Q]`` — giá trị dự báo theo phân vị ở trục cuối.
    target : ``[B, ...]`` — giá trị thực đo (NaN được bỏ qua).
    quantiles : các mức phân vị (mặc định 0,1 / 0,5 / 0,9).

    Công thức chuẩn: ``L_τ(u) = u·(τ − 1{u < 0})`` với `u = y − ŷ`.
    """
    if pred.shape[-1] != len(quantiles):
        raise ValueError(f"pred có {pred.shape[-1]} phân vị, cần {len(quantiles)}")
    q = torch.as_tensor(quantiles, dtype=pred.dtype, device=pred.device)
    diff = target.unsqueeze(-1) - pred                       # [B, ..., Q]
    mask = torch.isfinite(target).unsqueeze(-1).expand_as(diff)
    # NaN ở target cho diff = NaN ⇒ phải thay trước khi nhân (NaN·0 vẫn là NaN)
    diff = torch.where(mask, diff, torch.zeros_like(diff))
    loss = torch.maximum(q * diff, (q - 1.0) * diff)
    if not bool(mask.any()):
        return pred.sum() * 0.0
    return loss.sum() / mask.sum().clamp(min=1).to(pred.dtype)


# ---------------------------------------------------------------------------
# Đỉnh ngày khả vi: soft-max (độ cao) và soft-argmax (giờ)
# ---------------------------------------------------------------------------
def soft_peak_height(pred: Tensor, temperature: float = DEFAULT_TEMPERATURE) -> Tensor:
    """Độ cao đỉnh ngày khả vi: soft-max trên 2 đỉnh.

    Tham số
    -------
    pred : ``[B, 2, Q]`` — dự báo độ cao của 2 đỉnh (3 phân vị ở trục cuối).
    temperature : nhiệt độ của soft-max (nhỏ → gần hard-max).

    Trả về ``[B, Q]`` — độ cao đỉnh ngày dưới dạng kỳ vọng theo trọng số soft-max.

    **Chuẩn hoá.** Để nhiệt độ không phụ thuộc đơn vị (mét) và không bị "chết" khi hai đỉnh
    có cùng bộ phân vị (std = 0), ta chuẩn hoá theo **độ lệch giữa hai đỉnh ở phân vị 0,5**
    với trần nhỏ `MIN_HEIGHT_SCALE` (m):
    `scale = max(|h₁ − h₂|, MIN_HEIGHT_SCALE)`.
    """
    if pred.dim() != 3 or pred.shape[1] != 2:
        raise ValueError(f"pred phải có dạng [B, 2, Q], nhận {tuple(pred.shape)}")
    t = max(float(temperature), 1e-6)
    mid = pred[:, :, 1]                                    # [B, 2] — phân vị trung vị
    # Thang chuẩn hoá **cố định theo vật lý** (xem `PEAK_SCALE_M`).
    scale = torch.as_tensor(PEAK_SCALE_M, dtype=mid.dtype, device=mid.device)
    logits = (mid - mid.mean(dim=1, keepdim=True)) / scale / t
    w = torch.softmax(logits, dim=1)                       # [B, 2]
    return (w.unsqueeze(-1) * pred).sum(dim=1)             # [B, Q]


def soft_peak_hour(pred: Tensor, pred_hour: Tensor, temperature: float = DEFAULT_TEMPERATURE) -> Tensor:
    """Giờ đỉnh ngày khả vi (soft-argmax theo trọng số soft-max của độ cao).

    Tham số
    -------
    pred : ``[B, 2, Q]`` — độ cao 2 đỉnh.
    pred_hour : ``[B, 2]`` — giờ dự báo của 2 đỉnh (giờ trong ngày, 0..24).
    temperature : nhiệt độ soft-max.

    Trả về ``[B]`` — giờ đỉnh dự báo (giờ trong ngày, 0..24).

    **Chuẩn hoá nhiệt độ.** Trọng số soft-max được chuẩn hoá theo **độ lớn tuyệt đối**
    của độ cao đỉnh (thang mét) chứ không phải theo hiệu số giữa hai đỉnh. Lý do: nếu
    chuẩn hoá theo hiệu số, hai đỉnh luôn cho trọng số gần 50/50 (vì hiệu số chính là
    thang chuẩn hoá) ⇒ soft-argmax không "nghiêng" về đỉnh cao hơn, trái với ý nghĩa
    vật lý. Với thang mét và `MIN_HEIGHT_SCALE` làm trần dưới, chênh lệch 1 m giữa hai
    đỉnh là khác biệt quyết định ⇒ trọng số nghiêng hẳn về đỉnh cao.

    **Nội suy vòng 24 h.** Vì giờ là đại lượng vòng (23:30 và 00:15 là gần nhau), ta
    trung bình **theo pha** (`atan2` của tổng vector đơn vị) thay vì trung bình số học —
    nếu không, trọng số mềm sẽ "kéo" giờ qua đêm và cho ra giá trị vô nghĩa (ví dụ
    11,8 h thay vì ~0 h).
    """
    if pred_hour.shape != pred.shape[:2]:
        raise ValueError(f"pred_hour {tuple(pred_hour.shape)} không khớp pred {tuple(pred.shape)}")
    mid = pred[:, :, 1]                                   # [B, 2]
    # Thang chuẩn hoá **cố định theo vật lý** (xem `PEAK_SCALE_M`): chênh lệch độ cao
    # giữa hai đỉnh được chia cho thang này ⇒ trọng số soft-max nghiêng về đỉnh cao
    # đúng như ý nghĩa "đỉnh ngày".
    scale = torch.as_tensor(PEAK_SCALE_M, dtype=mid.dtype, device=mid.device)
    t = max(float(temperature), 1e-6)
    logits = (mid - mid.mean(dim=1, keepdim=True)) / scale / t
    w = torch.softmax(logits, dim=1)                    # [B, 2]

    # --- mở vòng (unwrap) theo đỉnh trội ---------------------------------
    # chỉ số đỉnh có trọng số lớn nhất
    base_idx = torch.argmax(w, dim=1)                   # [B]
    base_h = torch.gather(pred_hour, 1, base_idx.unsqueeze(1)).squeeze(1)     # [B]
    # lệch có dấu của từng đỉnh so với đỉnh gốc, trong khoảng (-12, 12]
    delta = torch.remainder(pred_hour - base_h.unsqueeze(1) + 12.0, 24.0) - 12.0
    soft_delta = (w * delta).sum(dim=1)                 # [B]
    return (base_h + soft_delta) % 24.0                 # [B]


def circular_distance_h(a: Tensor, b: Tensor, period: float = 24.0) -> Tensor:
    """Khoảng cách vòng giữa hai mốc giờ (mặc định chu kỳ 24 h).

    23:30 và 00:15 cách nhau 0,75 h, không phải 23,25 h.
    """
    p = float(period)
    diff = torch.remainder(a - b + p / 2.0, p) - p / 2.0
    return diff.abs()


# ---------------------------------------------------------------------------
# Các thành phần hàm mất mát
# ---------------------------------------------------------------------------
def seq_loss(pred: Tensor, target: Tensor, quantiles: tuple[float, ...] = QUANTILES) -> Tensor:
    """`L_seq`: pinball loss trên các đỉnh/chân."""
    return pinball_loss(pred, target, quantiles)


def peak_loss(pred: Tensor, target_peak: Tensor, temperature: float = DEFAULT_TEMPERATURE) -> Tensor:
    """`L_peak`: |Ĥ_peak − H_peak| với Ĥ_peak tính bằng soft-max.

    Tham số
    -------
    pred : ``[B, 2, Q]``.
    target_peak : ``[B]`` — độ cao đỉnh ngày thực đo (NaN = bỏ qua).
    """
    hp = soft_peak_height(pred, temperature)             # [B, Q]
    # so phân vị 0,5 (trung vị) với thực đo, và trung bình các phân vị cho độ chắc chắn
    err_med = (hp[:, 1] - target_peak).abs()
    err_mean = (hp.mean(dim=-1) - target_peak).abs()
    mask = torch.isfinite(target_peak)
    if not bool(mask.any()):
        return pred.sum() * 0.0
    loss = 0.5 * (err_med + err_mean)
    return (loss * mask).sum() / mask.sum().clamp(min=1).to(pred.dtype)


def timing_loss(pred: Tensor, pred_hour: Tensor, target_peak: Tensor, target_hour: Tensor,
                temperature: float = DEFAULT_TEMPERATURE) -> Tensor:
    """`L_timing`: sai số thời điểm đỉnh theo **khoảng cách vòng 24 h**.

    Tham số
    -------
    pred : ``[B, 2, Q]`` — dùng cho trọng số soft-max.
    pred_hour : ``[B, 2]`` — giờ dự báo của 2 đỉnh.
    target_peak : ``[B]`` — độ cao đỉnh thực đo (để phân biệt đỉnh nào cần so).
    target_hour : ``[B]`` — giờ đỉnh thực đo (NaN = bỏ qua).

    Ghi chú: khi mô hình dự báo **độ lệch** so với giờ đỉnh thiên văn, `target_hour` cũng
    nên là độ lệch tương ứng — hàm này hoàn toàn không quan tâm mốc tham chiếu, chỉ so
    hai giá trị trên cùng một vòng 24 h.
    """
    hp = soft_peak_height(pred, temperature)             # [B, Q]
    hour_hat = soft_peak_hour(pred, pred_hour, temperature)   # [B]
    mask = torch.isfinite(target_peak) & torch.isfinite(target_hour)
    if not bool(mask.any()):
        return pred.sum() * 0.0
    dist = circular_distance_h(hour_hat, target_hour)    # [B]
    # trọng số theo độ cao: ngày có đỉnh cao hơn thì sai số giờ quan trọng hơn
    weight = torch.nan_to_num(hp[:, 1], nan=0.0).abs()
    w = torch.where(mask, weight.clamp(min=0.0) + 1e-3, torch.zeros_like(weight))
    return (dist * w).sum() / w.sum().clamp(min=1e-6)


def mono_loss(pred: Tensor) -> Tensor:
    """`L_mono`: phạt khi phân vị đảo thứ tự (q10 > q50 hoặc q50 > q90)."""
    if pred.shape[-1] < 3:
        return pred.sum() * 0.0
    q10, q50, q90 = pred[..., 0], pred[..., 1], pred[..., 2]
    pen = torch.relu(q10 - q50) + torch.relu(q50 - q90)
    return pen.mean()


def tau_loss(delta: Tensor) -> Tensor:
    """`L_τ = mean(δ²)`: giữ độ trễ học được gần vật lý."""
    if delta.numel() == 0:
        return torch.zeros((), dtype=torch.float32)
    return (delta ** 2).mean()


def total_loss(pred: Tensor, target: Tensor, pred_hour: Tensor | None = None,
               target_peak: Tensor | None = None, target_hour: Tensor | None = None,
               delta: Tensor | None = None,
               lam1: float = 1.0, lam2: float = 0.5, lam3: float = 0.1, lam4: float = 0.1,
               temperature: float = DEFAULT_TEMPERATURE) -> tuple[Tensor, dict[str, float]]:
    """Tổng hàm mất mát và các thành phần (để theo dõi).

    Tham số
    -------
    pred : ``[B, 2, Q]`` — dự báo độ cao (đỉnh hoặc chân).
    target : ``[B, 2]`` — thực đo (NaN = bỏ qua).
    pred_hour : ``[B, 2]`` — giờ dự báo (tuỳ chọn; cần cho `L_timing`).
    target_peak : ``[B]`` — độ cao đỉnh thực đo (tuỳ chọn).
    target_hour : ``[B]`` — giờ đỉnh thực đo (tuỳ chọn).
    delta : ``[E, T]`` hoặc ``[E]`` — `δ` của các cạnh (tuỳ chọn; cần cho `L_τ`).

    Trả về ``(loss, parts)`` với `parts` là dict các giá trị vô hướng để log.
    """
    l_seq = seq_loss(pred, target)
    loss = l_seq
    parts = {"L_seq": float(l_seq.detach())}

    if target_peak is not None:
        l_peak = peak_loss(pred, target_peak, temperature)
        loss = loss + lam1 * l_peak
        parts["L_peak"] = float(l_peak.detach())

    if pred_hour is not None and target_peak is not None and target_hour is not None:
        l_time = timing_loss(pred, pred_hour, target_peak, target_hour, temperature)
        loss = loss + lam2 * l_time
        parts["L_timing"] = float(l_time.detach())

    if delta is not None:
        l_tau = tau_loss(delta)
        loss = loss + lam3 * l_tau
        parts["L_tau"] = float(l_tau.detach())

    l_mono = mono_loss(pred)
    loss = loss + lam4 * l_mono
    parts["L_mono"] = float(l_mono.detach())
    parts["total"] = float(loss.detach())
    return loss, parts


# ---------------------------------------------------------------------------
# Chỉ số đánh giá (để dừng sớm và báo cáo)
# ---------------------------------------------------------------------------
def peak_mae(pred: Tensor, target: Tensor) -> float:
    """MAE độ cao đỉnh ngày (phân vị 0,5) — chỉ số dùng để dừng sớm.

    Khác với hàm mất mát (dùng soft-max để giữ gradient), ở đây ta dùng **max cứng**
    giữa 2 đỉnh: đánh giá phải phản ánh đúng giá trị dự báo, không phải trộn mềm.
    """
    mid = pred[..., 1]
    hp = mid.max(dim=-1).values if mid.dim() >= 2 else mid
    mask = torch.isfinite(target)
    if not bool(mask.any()):
        return float("nan")
    return float((hp[mask] - target[mask]).abs().mean())


def peak_hour_mae(pred: Tensor, pred_hour: Tensor, target_hour: Tensor,
                  temperature: float = DEFAULT_TEMPERATURE) -> float:
    """MAE giờ đỉnh (khoảng cách vòng 24 h), tính trên những ngày có thực đo."""
    hour_hat = soft_peak_hour(pred, pred_hour, temperature)
    mask = torch.isfinite(target_hour)
    if not bool(mask.any()):
        return float("nan")
    d = circular_distance_h(hour_hat[mask], target_hour[mask])
    return float(d.mean())


def nse(pred: Tensor, target: Tensor) -> float:
    """Nash–Sutcliffe efficiency (theo chu kỳ thiên văn, dùng để so sánh với R)."""
    mask = torch.isfinite(target) & torch.isfinite(pred)
    if int(mask.sum()) < 2:
        return float("nan")
    y = target[mask]
    p = pred[mask]
    denom = ((y - y.mean()) ** 2).sum()
    if float(denom) == 0.0:
        return float("nan")
    return float(1.0 - ((y - p) ** 2).sum() / denom)


def mae(pred: Tensor, target: Tensor) -> float:
    """MAE bỏ qua NaN."""
    mask = torch.isfinite(target) & torch.isfinite(pred)
    if not bool(mask.any()):
        return float("nan")
    return float((pred[mask] - target[mask]).abs().mean())


def quantile_coverage(pred: Tensor, target: Tensor) -> dict[str, float]:
    """Tỉ lệ quan sát nằm trong khoảng [q10, q90] (kiểm tra hiệu chuẩn phân vị).

    `pred` có dạng ``[N, ..., Q]``, `target` có dạng ``[N, ...]`` ⇒ `target` được mở rộng
    theo trục cuối để so với `pred[..., 0]` và `pred[..., 2]`.

    **Trường hợp `target` chỉ có số phần tử bằng `N`** (ví dụ `pred` là ``[N, P, Q]`` còn
    `target` là độ cao đỉnh ngày ``[N]``): ta dùng **đỉnh cứng** của dự báo (max theo
    trục đỉnh) rồi so với khoảng [q10, q90] tương ứng — đây là cách kiểm tra hiệu
    chuẩn phân vị đúng nghĩa cho bài toán dự báo đỉnh.
    """
    if pred.shape[:-1] == target.shape:
        mask = torch.isfinite(target)
        if not bool(mask.any()):
            return {"coverage_10_90": float("nan")}
        y = target[mask]
        p10 = pred[..., 0][mask]
        p90 = pred[..., 2][mask]
        return {"coverage_10_90": float(((y >= p10) & (y <= p90)).float().mean())}

    # target theo batch: dùng đỉnh cứng
    if target.numel() == pred.shape[0]:
        mid = pred[..., 1]
        q10 = pred[..., 0]
        q90 = pred[..., 2]
        if mid.dim() >= 2:
            # max theo trục đỉnh (trục -2)
            sel = torch.argmax(mid, dim=-1, keepdim=True)
            q10 = torch.gather(q10, -1, sel).squeeze(-1)
            q90 = torch.gather(q90, -1, sel).squeeze(-1)
        else:
            q50 = mid
        mask = torch.isfinite(target)
        if not bool(mask.any()):
            return {"coverage_10_90": float("nan")}
        y = target[mask]
        return {"coverage_10_90": float(
            ((y >= q10[mask]) & (y <= q90[mask])).float().mean())}

    raise ValueError(f"shape không khớp: pred {tuple(pred.shape)} vs target "
                     f"{tuple(target.shape)}")


def quantile_summary(values: list[float]) -> dict[str, float]:
    """Tóm tắt một danh sách số (trung vị, trung bình, min, max)."""
    finite = [v for v in values if math.isfinite(v)]
    if not finite:
        return {"n": 0, "mean": float("nan"), "median": float("nan"),
                "min": float("nan"), "max": float("nan")}
    finite_sorted = sorted(finite)
    n = len(finite_sorted)
    median = (finite_sorted[n // 2] if n % 2
              else 0.5 * (finite_sorted[n // 2 - 1] + finite_sorted[n // 2]))
    return {"n": n, "mean": sum(finite) / n, "median": median,
            "min": finite_sorted[0], "max": finite_sorted[-1]}
