"""Chọn thiết bị tính toán (CPU/GPU) và chế độ tái lập cho huấn luyện PhaseGNN.

* ``resolve_device(spec)``: ``"auto"`` → ``"cuda"`` nếu ``torch.cuda.is_available()``, không thì ``"cpu"``;
  ``"cpu"``; ``"cuda"``; ``"cuda:N"``. Yêu cầu CUDA mà máy/image không có ⇒ `DeviceUnavailable` (thông báo
  rõ nguyên nhân thường gặp: image torch bản CPU, thiếu ``--gpus all``, driver NVIDIA cũ).
* ``set_deterministic(True)``: bật ``torch.use_deterministic_algorithms`` + ``cudnn.deterministic`` (tắt
  ``cudnn.benchmark``) và đặt ``CUBLAS_WORKSPACE_CONFIG`` (bắt buộc để cuBLAS tất định trên CUDA ≥ 10.2).
  Chậm hơn; chỉ dùng khi cần tái lập từng bit trên GPU. Trên CPU kết quả với cùng seed vốn đã lặp lại.
* ``device_info(device)``: dict mô tả thiết bị thực dùng (ghi vào ``train_log``/``model_record``).

Mặc định của ``python -m phasegnn.train`` vẫn là ``--device cpu`` để chạy lại đúng số liệu thực nghiệm cũ
(mọi lượt trong ``results/E2``, kể cả ``*_v3``/``phasegnn_v3grid``/``phasegnn_v2phase`` ngày 25/11/2026, đều huấn luyện
trên CPU — ``train_config.device = "cpu"``). Việc ``train`` của worker truyền ``--device`` theo tham số việc →
``PGNN_TRAIN_DEVICE`` → ``auto``.
"""
from __future__ import annotations

import os
import re

#: Giá trị hợp lệ của ``--device`` (ngoài dạng ``cuda:N``).
DEVICE_CHOICES = ("auto", "cpu", "cuda")
_CUDA_N = re.compile(r"^cuda:(\d+)$")


class DeviceUnavailable(RuntimeError):
    """Yêu cầu một thiết bị không có trên máy/image hiện tại."""


def _normalize(spec: str | None) -> str:
    s = (spec or "cpu").strip().lower()
    if s in DEVICE_CHOICES or _CUDA_N.match(s):
        return s
    raise ValueError(f"thiết bị không hợp lệ: {spec!r} (chọn auto|cpu|cuda|cuda:N)")


def parse_device_spec(spec: str | None) -> str:
    """Kiểm tra cú pháp (không import torch); dùng làm ``type=`` cho argparse."""
    return _normalize(spec)


def resolve_device(spec: str | None = "auto", *, cuda_available=None, device_count=None) -> str:
    """Trả tên thiết bị cụ thể (``"cpu"``, ``"cuda"`` hoặc ``"cuda:N"``).

    `cuda_available`/`device_count` chỉ để test (giả lập máy có/không có GPU); mặc định hỏi torch.
    """
    s = _normalize(spec)
    if s == "cpu":
        return "cpu"
    if cuda_available is None or (device_count is None and s.startswith("cuda:")):
        import torch

        if cuda_available is None:
            cuda_available = torch.cuda.is_available
        if device_count is None:
            device_count = torch.cuda.device_count
    has_cuda = bool(cuda_available() if callable(cuda_available) else cuda_available)
    if s == "auto":
        return "cuda" if has_cuda else "cpu"
    if not has_cuda:
        raise DeviceUnavailable(
            f"yêu cầu --device {s} nhưng torch không thấy GPU CUDA (torch.cuda.is_available() = False). "
            "Kiểm tra: image dùng torch bản CUDA (phasegnn:cuda, không phải phasegnn:local bản CPU), "
            "container chạy với --gpus all, driver NVIDIA trên Windows đủ mới cho CUDA trên WSL2. "
            "Dùng --device auto để tự lùi về CPU.")
    m = _CUDA_N.match(s)
    if m:
        n = int(m.group(1))
        count = int(device_count() if callable(device_count) else (device_count or 0))
        if n >= count:
            raise DeviceUnavailable(f"yêu cầu {s} nhưng chỉ có {count} GPU (chỉ số 0..{count - 1})")
    return s


def set_deterministic(enabled: bool = True) -> None:
    """Bật/tắt chế độ tất định của torch (xem docstring module)."""
    import torch

    if enabled:
        # Phải đặt trước khi cuBLAS khởi tạo handle (tức trước phép nhân ma trận đầu tiên trên GPU).
        os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    torch.use_deterministic_algorithms(bool(enabled))
    torch.backends.cudnn.deterministic = bool(enabled)
    torch.backends.cudnn.benchmark = False


def device_info(device: str) -> dict:
    """Mô tả thiết bị thực dùng: ``{"device", "torch", "cuda"?, "gpu_name"?, "deterministic"}``."""
    import torch

    # `str(...)`: `torch.__version__` là `TorchVersion` — nếu ghi nguyên vào checkpoint thì
    # `torch.load(weights_only=True)` (`forecast.load_checkpoint`) từ chối nạp (sửa 25/11/2026).
    info: dict = {"device": str(device), "torch": str(torch.__version__),
                  "deterministic": bool(torch.are_deterministic_algorithms_enabled())}
    if str(device).startswith("cuda"):
        dev = torch.device(device)
        idx = dev.index if dev.index is not None else torch.cuda.current_device()
        info["cuda"] = str(torch.version.cuda)
        info["gpu_name"] = torch.cuda.get_device_name(idx)
        cap = torch.cuda.get_device_capability(idx)
        info["capability"] = f"sm_{cap[0]}{cap[1]}"
    return info
