"""Worker dừng có trật tự (SIGTERM) → việc ``train`` về ``queued`` (không tính lượt), không ``failed`` mã -9;
việc ``train`` cũ trong hàng đợi có tham số ngoài danh sách trắng hiện hành (``epochs`` > 500) → ``failed`` có lý do
rõ và `models.train_state="failed"` (giao lại được)."""
from __future__ import annotations

import subprocess
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from worker import jobs as worker_jobs
from worker.scheduler import WORKER_SHUTDOWN_REASON, Scheduler, WorkerShutdown
from worker.train import abandon_training, run_training

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)


def _seed(db, ver: int = 7, params: dict | None = None) -> str:
    db["models"].insert_one({"_id": f"phasegnn-{ver}", "version": ver, "name": "phasegnn", "status": "staging",
                             "train_state": "queued", "job_id": f"train:{ver}"})
    db["jobs"].insert_one({"_id": f"train:{ver}", "type": "train", "state": "queued", "priority": 0,
                           "params": params or {"epochs": 2, "model_version": ver},
                           "requested_by": "user:acc_t", "fence": None, "created_at": NOW, "attempts": 0})
    return f"phasegnn-{ver}"


@pytest.fixture()
def sched(fake_db, fake_lock):
    s = Scheduler(fake_db, fake_lock, instance="worker-1", resources={"train": "train"})
    s.abandon_hooks["train"] = abandon_training
    yield s
    s.shutdown()


def _handler(root: Path, runner):
    def train(db, cache=None, *, model_version=None, epochs=50, learning_rate=1e-3, guard=None, job_id=None):
        return run_training(db, root=root, model_version=model_version, epochs=epochs, learning_rate=learning_rate,
                            guard=guard, job_id=job_id, runner=runner, available=lambda: (True, ""),
                            heartbeat_s=0.01)
    return train


def _shutdown_runner(cmd, *, cwd, env, timeout_s, guard=None, on_tick=None, tick_s=30.0):
    (Path(cwd) / cmd[cmd.index("--out-dir") + 1]).mkdir(parents=True, exist_ok=True)
    raise WorkerShutdown("tiến trình con bị dừng khi worker thoát (mã -9)")


def test_dung_co_trat_tu_tra_viec_ve_queued_khong_tinh_luot(sched, fake_db, tmp_path):
    mid = _seed(fake_db)
    out = sched.run_queue_once({"train": _handler(tmp_path, _shutdown_runner)}, types=["train"], db=fake_db,
                               cache=None)
    assert out["state"] == "interrupted" and out["requeue"] == "queued", out
    job = fake_db["jobs"].find_one({"_id": "train:7"})
    assert job["state"] == "queued" and job["attempts"] == 0, "không tăng attempts"
    assert job["worker"] is None and job["reclaims"][-1]["reason"] == WORKER_SHUTDOWN_REASON
    assert job["last_interrupt"]["reason"] == WORKER_SHUTDOWN_REASON
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_state"] == "queued" and m["train_reason"] == WORKER_SHUTDOWN_REASON, m
    assert m.get("train_error") is None, "không ghi lỗi mã -9"
    assert m["train_fence"] == out["fence"], "fence của lượt bị ngắt vẫn ghi lại ⇒ lượt sau (fence lớn hơn) ghi đè"
    assert not list((tmp_path / "results" / "models" / mid).glob("attempt-*")), "thư mục lượt dở dang bị xoá"


def test_sau_khi_dung_luot_moi_chay_lai_thanh_cong(sched, fake_db, tmp_path):
    import json

    mid = _seed(fake_db)
    sched.run_queue_once({"train": _handler(tmp_path, _shutdown_runner)}, types=["train"], db=fake_db, cache=None)

    def ok_runner(cmd, *, cwd, env, timeout_s, guard=None, on_tick=None, tick_s=30.0):
        out = Path(cwd) / cmd[cmd.index("--out-dir") + 1]
        out.mkdir(parents=True, exist_ok=True)
        (out / "checkpoint_seed0.pt").write_bytes(b"x")
        (out / "model_record_seed0.json").write_text(json.dumps({"metrics": {"val_mae_peak": 0.2}}), encoding="utf-8")
        return subprocess.CompletedProcess(cmd, 0, "", "")

    out = sched.run_queue_once({"train": _handler(tmp_path, ok_runner)}, types=["train"], db=fake_db, cache=None)
    assert out["state"] == "succeeded", out
    assert fake_db["models"].find_one({"_id": mid})["train_state"] == "succeeded"
    assert fake_db["jobs"].find_one({"_id": "train:7"})["attempts"] == 0


def test_kill_children_lam_run_proc_nem_worker_shutdown(tmp_path):
    """`_run_proc` thật: tiến trình con bị `kill_children` dừng ⇒ `WorkerShutdown`, không trả mã -9."""
    box: dict = {}

    def target():
        try:
            box["res"] = worker_jobs._run_proc([sys.executable, "-c", "import time; time.sleep(60)"],
                                               cwd=tmp_path, env=None, timeout_s=120, tick_s=0.2)
        except BaseException as e:  # noqa: BLE001
            box["exc"] = e

    t = threading.Thread(target=target)
    t.start()
    deadline = time.monotonic() + 10
    while not worker_jobs._LIVE_PROCS and time.monotonic() < deadline:
        time.sleep(0.05)
    assert worker_jobs.kill_children() == 1
    t.join(timeout=15)
    assert isinstance(box.get("exc"), WorkerShutdown), box


def test_viec_train_cu_epochs_qua_tran_failed_ro_ly_do(sched, fake_db, tmp_path):
    """Việc giao trước khi siết ``epochs`` ≤ 500 (params ``epochs=1500``) → ``failed`` + `models` ``failed``."""
    mid = _seed(fake_db, params={"epochs": 1500, "model_version": 7})
    calls: list = []
    out = sched.run_queue_once({"train": _handler(tmp_path, lambda *a, **k: calls.append(1))}, types=["train"],
                               db=fake_db, cache=None)
    assert out["state"] == "failed" and out["reason"] == "invalid_params" and calls == []
    job = fake_db["jobs"].find_one({"_id": "train:7"})
    assert "epochs" in job["error"] and "500" in job["error"], job["error"]
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_state"] == "failed" and "epochs" in m["train_error"], "không kẹt `queued`"
