"""Mô hình PhaseGNN — dự báo đỉnh mực nước trên mạng sông bằng lan truyền pha hai chiều.

Các module chính:

* `data` — nạp Parquet G5 + đồ thị G1/G3, dựng cửa sổ huấn luyện, chia tập train/val/test.
* `layers` — `PhaseShift` (dịch pha khả vi), `PhaseMessage` (thông điệp hai chiều), `TopoGRU`.
* `losses` — pinball, đỉnh soft-max, sai số giờ theo khoảng cách vòng.
* `model` — `PhaseGNN` và các biến thể B3/B4/B5a/B5b.
* `baselines` — B0 persistence, B_KTTV, B_harm, B1 hồi quy phần dư, B2 LSTM.
* `train` — vòng huấn luyện, dừng sớm, checkpoint.
* `forecast` — sinh dự báo theo `schemas/prediction/v1.schema.json`.

Phụ thuộc: `vngis`, `torch`, `numpy`, `pandas` (không cần `torch-geometric`).
"""
from .layers import PhaseMessage, PhaseShift, TopoGRU
from .losses import (QUANTILES, circular_distance_h, mono_loss, peak_loss, pinball_loss,
                     seq_loss, soft_peak_height, soft_peak_hour, tau_loss, timing_loss,
                     total_loss)
from .model import ModelConfig, ModelOutput, PhaseGNN

__version__ = "0.1.0"

__all__ = [
    "PhaseShift", "PhaseMessage", "TopoGRU",
    "QUANTILES", "pinball_loss", "seq_loss", "peak_loss", "timing_loss", "tau_loss",
    "mono_loss", "total_loss", "soft_peak_height", "soft_peak_hour", "circular_distance_h",
    "ModelConfig", "ModelOutput", "PhaseGNN",
]
