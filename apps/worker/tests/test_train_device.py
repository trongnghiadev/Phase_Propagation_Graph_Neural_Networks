"""Worker truyền ``--device`` cho `phasegnn.train` (tham số việc → `PGNN_TRAIN_DEVICE` → ``auto``); không cần torch."""
from __future__ import annotations

import inspect
import json
import subprocess
from pathlib import Path

import pytest

from worker.train import build_command, run_training, train_device

REPO_ROOT = Path(__file__).resolve().parents[3]


def _seed(db, ver: int = 5) -> None:
    db["models"].insert_one({"_id": f"phasegnn-{ver}", "version": ver, "name": "phasegnn", "status": "staging",
                             "train_state": "queued", "job_id": f"train:{ver}"})


class _Runner:
    def __init__(self):
        self.cmds: list[list[str]] = []

    def __call__(self, cmd, *, cwd, env, timeout_s, guard=None, on_tick=None, tick_s=30.0):
        self.cmds.append(cmd)
        out = Path(cwd) / cmd[cmd.index("--out-dir") + 1]
        out.mkdir(parents=True, exist_ok=True)
        (out / "checkpoint_seed0.pt").write_bytes(b"fake")
        (out / "model_record_seed0.json").write_text(json.dumps({"metrics": {"val_mae_peak": 0.2}}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _train(db, root, **kw):
    r = _Runner()
    out = run_training(db, root=root, model_version=5, epochs=1, runner=r, available=lambda: (True, ""),
                       heartbeat_s=0.01, **kw)
    return out, r.cmds[0]


@pytest.fixture(autouse=True)
def _no_env(monkeypatch):
    monkeypatch.delenv("PGNN_TRAIN_DEVICE", raising=False)


def test_train_device_thu_tu_uu_tien(monkeypatch):
    assert train_device() == "auto"
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "CUDA")
    assert train_device() == "cuda"
    assert train_device("cpu") == "cpu", "tham số việc thắng biến môi trường"
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "gpu")
    with pytest.raises(ValueError):
        train_device()


def test_build_command_device():
    cmd = build_command(model_version=1, epochs=1, learning_rate=0.001, out_dir="x", fence=None, device="cuda")
    assert cmd[cmd.index("--device") + 1] == "cuda"
    assert "--device" not in build_command(model_version=1, epochs=1, learning_rate=0.001, out_dir="x", fence=None)
    with pytest.raises(ValueError):
        build_command(model_version=1, epochs=1, learning_rate=0.001, out_dir="x", fence=None, device="cuda; ls")


def test_run_training_mac_dinh_auto(fake_db, tmp_path):
    _seed(fake_db)
    out, cmd = _train(fake_db, tmp_path)
    assert out["status"] == "ok" and cmd[cmd.index("--device") + 1] == "auto"
    assert fake_db["models"].find_one({"_id": "phasegnn-5"})["train_params"]["device"] == "auto"


def test_run_training_theo_bien_moi_truong_va_tham_so(fake_db, tmp_path, monkeypatch):
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "cuda")
    _seed(fake_db)
    _, cmd = _train(fake_db, tmp_path)
    assert cmd[cmd.index("--device") + 1] == "cuda"
    _seed(fake_db, ver=6)
    r = _Runner()
    run_training(fake_db, root=tmp_path, model_version=6, epochs=1, runner=r, available=lambda: (True, ""),
                 heartbeat_s=0.01, device="cpu")
    assert r.cmds[0][r.cmds[0].index("--device") + 1] == "cpu"


def test_run_training_env_sai_loi_truoc_khi_ghi(fake_db, tmp_path, monkeypatch):
    monkeypatch.setenv("PGNN_TRAIN_DEVICE", "tpu")
    _seed(fake_db)
    with pytest.raises(ValueError):
        _train(fake_db, tmp_path)
    assert fake_db["models"].find_one({"_id": "phasegnn-5"})["train_state"] == "queued", "không ghi gì"


def test_train_run_nhan_device():
    from worker.jobs import train_run

    assert "device" in inspect.signature(train_run).parameters


def test_cli_phasegnn_co_co_device():
    src = REPO_ROOT / "packages" / "phasegnn" / "src" / "phasegnn" / "train.py"
    if not src.is_file():
        pytest.skip("không có mã nguồn phasegnn")
    text = src.read_text(encoding="utf-8")
    assert '"--device"' in text and '"--deterministic"' in text
