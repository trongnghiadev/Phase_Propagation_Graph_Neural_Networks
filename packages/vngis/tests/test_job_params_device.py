"""`TrainParams.device` (thiết bị huấn luyện) trong danh sách trắng tham số hàng đợi."""
from __future__ import annotations

import pytest

from vngis.job_params import JobParamsError, validate_job_params


@pytest.mark.parametrize("dev", ["auto", "cpu", "cuda"])
def test_train_nhan_device_hop_le(dev):
    assert validate_job_params("train", {"device": dev}) == {"device": dev}


def test_train_device_bo_trong_de_worker_quyet():
    assert "device" not in validate_job_params("train", {"epochs": 3}), "mặc định do worker (PGNN_TRAIN_DEVICE)"


@pytest.mark.parametrize("dev", ["gpu", "cuda:0", "CUDA", "cpu; rm -rf /", 1, ""])
def test_train_device_la_bi_tu_choi(dev):
    with pytest.raises(JobParamsError):
        validate_job_params("train", {"device": dev})


def test_forecast_run_khong_nhan_device():
    """Thiết bị dự báo chỉ theo biến môi trường của worker (`PGNN_FORECAST_DEVICE`)."""
    with pytest.raises(JobParamsError):
        validate_job_params("forecast_run", {"device": "cuda"})


# ------------------------------------------------ ước lượng thời gian theo thiết bị ---
def _clean_env(monkeypatch):
    for k in ("PGNN_TRAIN_SEC_PER_SAMPLE", "PGNN_TRAIN_SEC_PER_SAMPLE_CUDA", "PGNN_TRAIN_DEVICE"):
        monkeypatch.delenv(k, raising=False)


def test_uoc_luong_cuda_bang_cpu_chia_4(monkeypatch):
    from vngis.job_params import estimate_train_seconds

    _clean_env(monkeypatch)
    cpu = estimate_train_seconds({"epochs": 20, "device": "cpu"}, timeout_s=10**9)
    cuda = estimate_train_seconds({"epochs": 20, "device": "cuda"}, timeout_s=10**9)
    assert cpu["device"] == "cpu" and cuda["device"] == "cuda"
    assert abs(cpu["per_epoch_s"] / cuda["per_epoch_s"] - 4.0) < 0.01
    monkeypatch.setenv("PGNN_TRAIN_SEC_PER_SAMPLE_CUDA", "0.001")
    assert estimate_train_seconds({"epochs": 1, "device": "cuda", "max_train_samples": 1000,
                                   "max_val_samples": 300}, timeout_s=10**9)["per_epoch_s"] == 1.1


def test_uoc_luong_device_hieu_luc_tu_bien_moi_truong(monkeypatch):
    from vngis.job_params import effective_train_device, estimate_train_seconds

    _clean_env(monkeypatch)
    assert effective_train_device(None) == "auto"
    auto = estimate_train_seconds({"epochs": 5}, timeout_s=10**9)
    assert auto["device"] == "auto"
    assert auto["per_epoch_s"] == estimate_train_seconds({"epochs": 5, "device": "cpu"}, timeout_s=10**9)["per_epoch_s"], \
        "auto ước lượng thận trọng theo CPU"
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "cuda")
    assert effective_train_device(None) == "cuda" and effective_train_device("cpu") == "cpu", "tham số thắng env"
    assert estimate_train_seconds({"epochs": 5}, timeout_s=10**9)["device"] == "cuda"
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "tpu")
    assert effective_train_device(None) == "auto"
