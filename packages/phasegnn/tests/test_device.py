"""Chọn thiết bị (`phasegnn.device`) và cờ ``--device``/``--deterministic`` của `phasegnn.train` — không cần GPU."""
from __future__ import annotations

import pytest

torch = pytest.importorskip("torch")

from phasegnn import train as train_mod  # noqa: E402
from phasegnn.device import (DeviceUnavailable, device_info, parse_device_spec, resolve_device,  # noqa: E402
                             set_deterministic)


def test_cpu_luon_la_cpu():
    assert resolve_device("cpu", cuda_available=False) == "cpu"
    assert resolve_device("CPU ", cuda_available=True) == "cpu"


def test_auto_khong_gpu_la_cpu_co_gpu_la_cuda():
    assert resolve_device("auto", cuda_available=False) == "cpu"
    assert resolve_device("auto", cuda_available=True) == "cuda"


@pytest.mark.parametrize("spec", ["cuda", "cuda:0", "cuda:3"])
def test_yeu_cau_cuda_khi_khong_co_thi_loi_ro(spec):
    with pytest.raises(DeviceUnavailable, match="--device auto"):
        resolve_device(spec, cuda_available=False)


def test_cuda_n_vuot_so_gpu():
    assert resolve_device("cuda:0", cuda_available=True, device_count=1) == "cuda:0"
    with pytest.raises(DeviceUnavailable, match="chỉ có 1 GPU"):
        resolve_device("cuda:1", cuda_available=True, device_count=1)


@pytest.mark.parametrize("spec", ["gpu", "cuda:x", "mps", "cuda:-1", ""])
def test_gia_tri_la_bi_tu_choi(spec):
    if spec == "":
        assert parse_device_spec(spec) == "cpu", "rỗng → mặc định cpu"
        return
    with pytest.raises(ValueError):
        parse_device_spec(spec)


def test_auto_tren_may_that():
    """Trên image CPU (test chạy không cần GPU): auto ⇒ cpu; cuda ⇒ lỗi."""
    if torch.cuda.is_available():
        assert resolve_device("auto") == "cuda"
    else:
        assert resolve_device("auto") == "cpu"
        with pytest.raises(DeviceUnavailable):
            resolve_device("cuda")


def test_device_info_cpu():
    info = device_info("cpu")
    assert info["device"] == "cpu" and info["torch"] == torch.__version__ and "gpu_name" not in info


def test_set_deterministic_bat_tat():
    before = torch.are_deterministic_algorithms_enabled()
    try:
        set_deterministic(True)
        assert torch.are_deterministic_algorithms_enabled() and torch.backends.cudnn.deterministic
        set_deterministic(False)
        assert not torch.are_deterministic_algorithms_enabled()
    finally:
        set_deterministic(before)


class _Stop(Exception):
    pass


def _stop_after_parse(monkeypatch):
    """Dừng `train.main` ngay sau khi phân tích cờ (không nạp dữ liệu thật)."""
    seen: dict = {}

    def fake_load(*_a, **_k):
        raise _Stop()

    monkeypatch.setattr(train_mod, "load_g5", fake_load)
    real_info = train_mod.device_info

    def spy_info(dev):
        seen["device"] = dev
        return real_info(dev)

    monkeypatch.setattr(train_mod, "device_info", spy_info)
    return seen


def test_cli_mac_dinh_cpu(monkeypatch):
    seen = _stop_after_parse(monkeypatch)
    with pytest.raises(_Stop):
        train_mod.main(["--no-mlflow"])
    assert seen["device"] == "cpu", "mặc định giữ cpu để tái lập số thực nghiệm cũ"


def test_cli_nhan_co_device_auto(monkeypatch):
    seen = _stop_after_parse(monkeypatch)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(_Stop):
        train_mod.main(["--device", "auto", "--no-mlflow"])
    assert seen["device"] == "cpu"


def test_cli_cuda_khong_co_gpu_thoat_ma_2(monkeypatch, capsys):
    _stop_after_parse(monkeypatch)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    with pytest.raises(SystemExit) as ei:
        train_mod.main(["--device", "cuda"])
    assert ei.value.code == 2 and "torch.cuda.is_available" in capsys.readouterr().err


def test_cli_device_sai_cu_phap(monkeypatch):
    _stop_after_parse(monkeypatch)
    with pytest.raises(SystemExit):
        train_mod.main(["--device", "gpu"])


def test_cli_deterministic(monkeypatch):
    _stop_after_parse(monkeypatch)
    before = torch.are_deterministic_algorithms_enabled()
    try:
        with pytest.raises(_Stop):
            train_mod.main(["--deterministic"])
        assert torch.are_deterministic_algorithms_enabled()
    finally:
        set_deterministic(before)


def test_trainconfig_co_truong_device_deterministic():
    tc = train_mod.TrainConfig()
    assert tc.device == "cpu" and tc.deterministic is False
