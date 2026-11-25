"""Việc `train` từ hàng đợi (`worker.train`): tiến trình huấn luyện giả, không cần torch/phasegnn."""
from __future__ import annotations

import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from worker.scheduler import Scheduler
from worker.train import build_command, run_training

NOW = datetime(2026, 11, 25, tzinfo=timezone.utc)
REPO_ROOT = Path(__file__).resolve().parents[3]


def _seed(db, ver: int = 3, **extra) -> str:
    """Bản ghi như API `POST /models/train` tạo + việc `train:<n>`."""
    db["models"].insert_one({"_id": f"phasegnn-{ver}", "version": ver, "name": "phasegnn", "status": "staging",
                             "train_state": "queued", "job_id": f"train:{ver}", **extra})
    db["jobs"].insert_one({"_id": f"train:{ver}", "type": "train", "state": "queued", "priority": 0,
                           "params": {"epochs": 2, "learning_rate": 0.01, "model_version": ver},
                           "requested_by": "user:acc_t", "fence": None, "created_at": NOW})
    return f"phasegnn-{ver}"


class FakeTrainer:
    """Thay `_run_proc`: ghi checkpoint + model_record như `phasegnn.train`, gọi `on_tick` (heartbeat)."""

    def __init__(self, root: Path, *, rc: int = 0, write_ckpt: bool = True, raise_exc: Exception | None = None):
        self.root, self.rc, self.write_ckpt, self.raise_exc = root, rc, write_ckpt, raise_exc
        self.calls: list[dict] = []

    def __call__(self, cmd, *, cwd, env, timeout_s, guard=None, on_tick=None, tick_s=30.0):
        self.calls.append({"cmd": cmd, "cwd": cwd, "env": env, "guard": guard})
        if on_tick is not None:
            on_tick()
        if self.raise_exc is not None:
            raise self.raise_exc
        out = Path(cwd) / cmd[cmd.index("--out-dir") + 1]
        if self.rc == 0 and self.write_ckpt:
            out.mkdir(parents=True, exist_ok=True)
            (out / "checkpoint_seed0.pt").write_bytes(b"fake")
            (out / "model_record_seed0.json").write_text(json.dumps(
                {"metrics": {"val_mae_peak": 0.21, "val_mae_hour": float("nan"), "best_epoch": 1, "x": "y"}}),
                encoding="utf-8")
        return subprocess.CompletedProcess(cmd, self.rc, "train log", "" if self.rc == 0 else "Traceback: boom")


def _handler(root: Path, trainer, available=lambda: (True, "")):
    def train(db, cache=None, *, model_version=None, epochs=50, learning_rate=1e-3, station=None, basin=None,
              guard=None, job_id=None, max_train_samples=None, max_val_samples=None, subsample="spread"):
        return run_training(db, root=root, model_version=model_version, epochs=epochs,
                            learning_rate=learning_rate, station=station, basin=basin, guard=guard,
                            job_id=job_id, runner=trainer, available=available, heartbeat_s=0.01,
                            max_train_samples=max_train_samples, max_val_samples=max_val_samples,
                            subsample=subsample)
    return train


@pytest.fixture()
def sched(fake_db, fake_lock):
    s = Scheduler(fake_db, fake_lock, instance="worker-1", resources={"train": "train"})
    yield s
    s.shutdown()


def _run(sched, fake_db, root, trainer, **kw):
    return sched.run_queue_once({"train": _handler(root, trainer, **kw)}, types=["train"], db=fake_db, cache=None)


def test_train_thanh_cong_cap_nhat_models(sched, fake_db, tmp_path):
    mid = _seed(fake_db)
    tr = FakeTrainer(tmp_path)
    out = _run(sched, fake_db, tmp_path, tr)
    assert out["state"] == "succeeded", out
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_state"] == "succeeded" and m["status"] == "staging", "promote là thao tác riêng"
    att = f"results/models/phasegnn-3/attempt-{out['fence']}"
    assert m["checkpoint_path"] == f"{att}/checkpoint_seed0.pt", "mỗi lượt (fence) một thư mục riêng"
    assert m["metrics"] == {"val_mae_peak": 0.21, "val_mae_hour": None, "best_epoch": 1}
    assert m["train_fence"] == out["fence"] and m["train_job_id"] == "train:3" and m.get("train_heartbeat")
    job = fake_db["jobs"].find_one({"_id": "train:3"})
    assert job["state"] == "succeeded" and job["result"]["checkpoint_path"] == m["checkpoint_path"]
    cmd = tr.calls[0]["cmd"]
    assert cmd[cmd.index("-m") + 1] == "phasegnn.train"
    assert cmd[cmd.index("--epochs") + 1] == "2" and float(cmd[cmd.index("--lr") + 1]) == 0.01
    assert cmd[cmd.index("--model-version") + 1] == "3"
    assert cmd[cmd.index("--train-fence") + 1] == str(out["fence"])
    assert cmd[cmd.index("--out-dir") + 1] == att
    assert "--max-train-samples" not in cmd and "--subsample" not in cmd, "không giới hạn mẫu → không truyền cờ"
    assert tr.calls[0]["env"].get("MONGO_DB") == fake_db.name, "tiến trình con dùng đúng DB của worker"
    # chạy lại việc đã xong → bỏ qua (idempotent)
    out2 = run_training(fake_db, root=tmp_path, model_version=3, runner=tr, available=lambda: (True, ""))
    assert out2["status"] == "skipped" and len(tr.calls) == 1


def test_thieu_phasegnn_torch_skipped(sched, fake_db, tmp_path):
    mid = _seed(fake_db)
    tr = FakeTrainer(tmp_path)
    out = _run(sched, fake_db, tmp_path, tr, available=lambda: (False, "không có module 'torch'"))
    assert out["state"] == "skipped" and out["reason"] == "job_skipped"
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_state"] == "skipped" and "torch" in m["train_reason"]
    job = fake_db["jobs"].find_one({"_id": "train:3"})
    assert job["state"] == "skipped" and "phasegnn/torch" in job["reason"] and tr.calls == []


def test_tien_trinh_loi_failed(sched, fake_db, tmp_path):
    mid = _seed(fake_db)
    out = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path, rc=2))
    assert out["state"] == "failed"
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_state"] == "failed" and "boom" in m["train_error"]
    assert fake_db["jobs"].find_one({"_id": "train:3"})["state"] == "failed"


def test_thieu_checkpoint_failed(sched, fake_db, tmp_path):
    mid = _seed(fake_db)
    out = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path, write_ckpt=False))
    assert out["state"] == "failed"
    assert fake_db["models"].find_one({"_id": mid})["train_state"] == "failed"


def test_mat_khoa_dung_va_khong_ghi_models(sched, fake_db, tmp_path):
    from vngis.locks import LockLost

    mid = _seed(fake_db)
    out = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path, raise_exc=LockLost("mất khoá")))
    assert out["state"] == "lost"
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_state"] == "running", "không ghi gì sau khi mất khoá; lượt sau (fence lớn hơn) nhận lại"
    # scheduler tự trả việc về hàng đợi (attempts += 1) → lượt sau chạy được với fence lớn hơn
    assert out["requeue"] == "queued"
    job = fake_db["jobs"].find_one({"_id": "train:3"})
    assert job["state"] == "queued" and job["attempts"] == 1 and job["worker"] is None
    out2 = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path))
    assert out2["state"] == "succeeded" and out2["fence"] > out["fence"]
    assert fake_db["models"].find_one({"_id": mid})["train_fence"] == out2["fence"]


def test_fence_cu_khong_ghi_de(fake_db, tmp_path):
    """Bản ghi đã mang `train_fence` lớn hơn (lượt mới) → lượt cũ không chuyển trạng thái, không chạy."""
    mid = _seed(fake_db, train_fence=99)

    class G:
        fence, lost = 5, False

        def check(self):
            pass

    tr = FakeTrainer(tmp_path)
    out = run_training(fake_db, root=tmp_path, model_version=3, guard=G(), runner=tr, available=lambda: (True, ""))
    assert out["status"] == "skipped" and tr.calls == []
    assert fake_db["models"].find_one({"_id": mid})["train_state"] == "queued"


def test_khoa_train_mot_luot_moi_luc(sched, fake_db, fake_lock, tmp_path):
    """Khoá tài nguyên `train` đang bị giữ → việc trả về `queued`, không chạy."""
    from vngis.locks import LockGuard

    _seed(fake_db)
    g = LockGuard(fake_lock, db=fake_db, resource="train", ttl_ms=60_000)
    g.acquire()
    try:
        tr = FakeTrainer(tmp_path)
        out = _run(sched, fake_db, tmp_path, tr)
        assert out["state"] == "skipped" and out["reason"] == "lock_busy" and tr.calls == []
        assert fake_db["jobs"].find_one({"_id": "train:3"})["state"] == "queued"
    finally:
        g.release()


def test_tham_so_la_bi_tu_choi(sched, fake_db, tmp_path):
    _seed(fake_db)
    fake_db["jobs"].update_one({"_id": "train:3"}, {"$set": {"params": {"model_version": 3, "script": "x.py"}}})
    out = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path))
    assert out["state"] == "failed" and out["reason"] == "invalid_params"


def test_bo_quet_chung_khong_nhan_viec_train(sched, fake_db):
    _seed(fake_db)
    assert sched.run_queue_once({"cache_warm": lambda db, cache=None: {}}, exclude_types=["train"],
                                db=fake_db, cache=None) is None
    assert fake_db["jobs"].find_one({"_id": "train:3"})["state"] == "queued"


def test_build_command_chi_so_da_kiem_tra():
    cmd = build_command(model_version=4, epochs=7, learning_rate=0.002, out_dir="results/models/phasegnn-4",
                        fence=11)
    assert cmd[0] == sys.executable and "--no-mlflow" in cmd
    assert cmd[cmd.index("--epochs") + 1] == "7" and cmd[cmd.index("--train-fence") + 1] == "11"


def test_run_proc_mat_khoa_dung_tien_trinh_con(tmp_path):
    """`_run_proc` thật: `on_tick` được gọi; mất khoá → dừng tiến trình con, ném LockLost."""
    from vngis.locks import LockLost

    from worker.jobs import _run_proc

    class G:
        lost = False

    g, ticks = G(), []

    def tick():
        ticks.append(1)
        g.lost = True

    t0 = datetime.now()
    with pytest.raises(LockLost):
        _run_proc([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path, env=None,
                  timeout_s=120, guard=g, on_tick=tick, tick_s=0.1)
    assert ticks and (datetime.now() - t0).total_seconds() < 20


def test_forecast_chon_ban_promote_moi_nhat(fake_db, tmp_path, monkeypatch):
    """Tạm thời có 2 bản production (giữa hai lệnh ghi promote) → worker chọn lượt promote mới nhất."""
    from worker.forecast import resolve_checkpoint

    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    monkeypatch.delenv("FORECAST_CHECKPOINT_DIR", raising=False)
    for name in ("v5.pt", "v2.pt"):
        (tmp_path / "results").mkdir(exist_ok=True)
        (tmp_path / "results" / name).write_bytes(b"x")
    fake_db["models"].insert_many([
        {"version": 5, "status": "production", "promote_seq": 1, "checkpoint_path": "results/v5.pt"},
        {"version": 2, "status": "production", "promote_seq": 2, "checkpoint_path": "results/v2.pt"},
    ])
    p, info = resolve_checkpoint(fake_db, tmp_path)
    assert p is not None and p.name == "v2.pt" and info["model_version"] == "2"


def test_mat_khoa_qua_max_attempts_danh_failed_ca_models(fake_db, fake_lock, tmp_path):
    """Mất khoá ở mọi lượt → sau `max_attempts` lượt: việc `failed`, `models.train_state="failed"`."""
    from vngis.locks import LockLost

    from worker.train import abandon_training

    s = Scheduler(fake_db, fake_lock, instance="worker-1", resources={"train": "train"}, max_attempts=2)
    s.abandon_hooks["train"] = abandon_training
    try:
        mid = _seed(fake_db)
        states = []
        for _ in range(3):
            out = _run(s, fake_db, tmp_path, FakeTrainer(tmp_path, raise_exc=LockLost("mất khoá")))
            if out is None:
                break
            states.append(out.get("requeue"))
        assert states == ["queued", "failed"]
        job = fake_db["jobs"].find_one({"_id": "train:3"})
        assert job["state"] == "failed" and "mất khoá" in job["error"]
        m = fake_db["models"].find_one({"_id": mid})
        assert m["train_state"] == "failed" and "train:3" in m["train_error"]
    finally:
        s.shutdown()


def test_reclaim_nhan_lai_viec_train_lost_sot(sched, fake_db):
    """Bản ghi `train` `lost` sót lại (worker chết trước khi trả về hàng đợi) → reclaim_stale trả `queued`;
    việc loại khác `lost` cũ không bị chạy lại."""
    from datetime import timedelta

    old = NOW - timedelta(days=1)
    _seed(fake_db)
    fake_db["jobs"].update_one({"_id": "train:3"}, {"$set": {"state": "lost", "fence": 4, "updated_at": old}})
    fake_db["jobs"].insert_one({"_id": "reload:x", "type": "reload_bulletins", "state": "lost",
                                "requested_by": "user:acc_t", "updated_at": old})
    out = sched.reclaim_stale()
    assert out["requeued"] == 1
    assert fake_db["jobs"].find_one({"_id": "train:3"})["state"] == "queued"
    assert fake_db["jobs"].find_one({"_id": "reload:x"})["state"] == "lost"


def test_viec_khac_mat_khoa_cung_tra_ve_hang_doi(sched, fake_db):
    from vngis.locks import LockLost

    fake_db["jobs"].insert_one({"_id": "fc:1", "type": "forecast_run", "state": "queued", "priority": 0,
                                "params": {}, "requested_by": "user:acc_t", "fence": None, "created_at": NOW})

    def fc(db, cache=None):
        raise LockLost("mất khoá")

    out = sched.run_queue_once({"forecast_run": fc}, db=fake_db, cache=None)
    assert out["state"] == "lost" and out["requeue"] == "queued"
    assert fake_db["jobs"].find_one({"_id": "fc:1"})["state"] == "queued"


def test_model_writer_fence_0_khong_coi_la_none(fake_db):
    from worker.train import _ModelWriter

    class G:
        fence, lost = 0, False

        def check(self):
            pass

    fake_db["models"].insert_one({"_id": "phasegnn-1", "version": 1, "train_fence": 5})
    w = _ModelWriter(fake_db, "phasegnn-1", G())
    assert w.fence == 0
    assert w.set({"train_state": "running"}) is False, "fence 0 < train_fence 5 → bị chặn"


def test_abandon_khong_dung_luot_giao_lai_moi(fake_db):
    from worker.train import abandon_training

    fake_db["models"].insert_one({"_id": "phasegnn-3", "version": 3, "train_state": "queued", "job_id": "train:3:2"})
    assert abandon_training(fake_db, {"_id": "train:3", "model_version": 3}, "x") is False
    assert fake_db["models"].find_one({"_id": "phasegnn-3"})["train_state"] == "queued"


# ---------------------------------------------------------------- giới hạn mẫu ---
def test_build_command_gioi_han_mau():
    cmd = build_command(model_version=4, epochs=3, learning_rate=0.001, out_dir="results/models/phasegnn-4/attempt-2",
                        fence=2, max_train_samples=2000, max_val_samples=400, subsample="spread")
    assert cmd[cmd.index("--max-train-samples") + 1] == "2000"
    assert cmd[cmd.index("--max-val-samples") + 1] == "400"
    assert cmd[cmd.index("--subsample") + 1] == "spread"
    only_val = build_command(model_version=4, epochs=3, learning_rate=0.001, out_dir="x", fence=None,
                             max_val_samples=100, subsample="head")
    assert "--max-train-samples" not in only_val and only_val[only_val.index("--subsample") + 1] == "head"
    with pytest.raises(ValueError):
        build_command(model_version=4, epochs=3, learning_rate=0.001, out_dir="x", fence=None,
                      max_train_samples=100, subsample="all; rm -rf")


def test_co_tren_cli_cua_phasegnn_train():
    """Tên cờ khớp argparse của `phasegnn.train` (đọc mã nguồn, không import torch)."""
    src = REPO_ROOT / "packages" / "phasegnn" / "src" / "phasegnn" / "train.py"
    if not src.is_file():
        pytest.skip("không có mã nguồn phasegnn")
    text = src.read_text(encoding="utf-8")
    for flag in ("--max-train-samples", "--max-val-samples", "--subsample", "--train-fence", "--model-version",
                 "--out-dir", "--epochs", "--lr", "--no-mlflow"):
        assert f'"{flag}"' in text, flag
    assert 'SUBSAMPLE_MODES = ("head", "spread")' in text


def test_train_truyen_gioi_han_mau_tu_hang_doi(sched, fake_db, tmp_path):
    mid = _seed(fake_db)
    fake_db["jobs"].update_one({"_id": "train:3"}, {"$set": {"params": {
        "model_version": 3, "epochs": 2, "max_train_samples": 500, "max_val_samples": 100}}})
    tr = FakeTrainer(tmp_path)
    out = _run(sched, fake_db, tmp_path, tr)
    assert out["state"] == "succeeded", out
    cmd = tr.calls[0]["cmd"]
    assert cmd[cmd.index("--max-train-samples") + 1] == "500"
    assert cmd[cmd.index("--max-val-samples") + 1] == "100"
    assert cmd[cmd.index("--subsample") + 1] == "spread", "mặc định spread"
    m = fake_db["models"].find_one({"_id": mid})
    assert m["train_params"]["max_train_samples"] == 500 and m["train_params"]["subsample"] == "spread"
    job = fake_db["jobs"].find_one({"_id": "train:3"})
    assert job["result"]["estimate"]["train_samples"] == 500 and job["result"]["estimate"]["verdict"] == "ok"


def test_tham_so_mau_ngoai_mien_bi_tu_choi(sched, fake_db, tmp_path):
    _seed(fake_db)
    fake_db["jobs"].update_one({"_id": "train:3"}, {"$set": {"params": {"model_version": 3,
                                                                         "max_train_samples": 5}}})
    tr = FakeTrainer(tmp_path)
    out = _run(sched, fake_db, tmp_path, tr)
    assert out["state"] == "failed" and out["reason"] == "invalid_params" and tr.calls == []


# ---------------------------------------------------------------- thư mục theo lượt ---
def test_luot_thang_don_thu_muc_luot_cu(sched, fake_db, tmp_path):
    """Lượt cũ (fence nhỏ) còn để lại checkpoint (tiến trình mồ côi) → lượt thắng ghi thư mục riêng,
    `checkpoint_path` trỏ vào thư mục của nó, rồi xoá `attempt-<k>` cũ; thư mục khác không bị đụng."""
    mid = _seed(fake_db)
    base = tmp_path / "results" / "models" / mid
    old = base / "attempt-0"
    old.mkdir(parents=True)
    (old / "checkpoint_seed0.pt").write_bytes(b"orphan")
    keep = base / "notes"
    keep.mkdir()
    out = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path))
    assert out["state"] == "succeeded" and out["fence"] >= 1
    m = fake_db["models"].find_one({"_id": mid})
    own = base / f"attempt-{out['fence']}"
    assert m["checkpoint_path"] == f"results/models/{mid}/attempt-{out['fence']}/checkpoint_seed0.pt"
    assert (own / "checkpoint_seed0.pt").read_bytes() == b"fake"
    assert not old.exists() and keep.exists()
    assert fake_db["jobs"].find_one({"_id": "train:3"})["result"]["removed_attempts"] == ["attempt-0"]


def test_tien_trinh_mo_coi_khong_ghi_de_luot_moi(sched, fake_db, tmp_path):
    """Lượt 1 mất khoá (tiến trình con coi như mồ côi, vẫn ghi vào thư mục của nó) → lượt 2 thắng; tệp lượt 1
    ghi sau đó không đụng checkpoint của lượt 2."""
    from vngis.locks import LockLost

    mid = _seed(fake_db)
    out1 = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path, raise_exc=LockLost("mất khoá")))
    assert out1["state"] == "lost"
    out2 = _run(sched, fake_db, tmp_path, FakeTrainer(tmp_path))
    assert out2["state"] == "succeeded" and out2["fence"] > out1["fence"]
    # tiến trình mồ côi của lượt 1 ghi muộn
    orphan = tmp_path / "results" / "models" / mid / f"attempt-{out1['fence']}"
    orphan.mkdir(parents=True, exist_ok=True)
    (orphan / "checkpoint_seed0.pt").write_bytes(b"orphan")
    m = fake_db["models"].find_one({"_id": mid})
    assert (tmp_path / m["checkpoint_path"]).read_bytes() == b"fake"
    assert f"attempt-{out2['fence']}" in m["checkpoint_path"]


def test_luot_thua_xoa_thu_muc_cua_minh(fake_db, tmp_path):
    """Ghi kết quả bị fence từ chối (lượt mới hơn đã chiếm) → không đổi `checkpoint_path`, xoá thư mục mình."""
    mid = _seed(fake_db)

    class G:
        fence, lost = 5, False

        def check(self):
            pass

    class Overtaken(FakeTrainer):
        def __call__(self, cmd, **kw):
            res = super().__call__(cmd, **kw)
            fake_db["models"].update_one({"_id": mid}, {"$set": {"train_fence": 9}})
            return res

    tr = Overtaken(tmp_path)
    with pytest.raises(RuntimeError, match="fence"):
        run_training(fake_db, root=tmp_path, model_version=3, guard=G(), runner=tr, available=lambda: (True, ""))
    assert not (tmp_path / "results" / "models" / mid / "attempt-5").exists()
    assert fake_db["models"].find_one({"_id": mid}).get("checkpoint_path") is None


def test_kill_children_dung_tien_trinh_con(tmp_path):
    """`kill_children` (atexit/tín hiệu) dừng tiến trình con đang chạy trong nhóm riêng."""
    import threading

    from worker import jobs

    res: dict = {}

    def run():
        try:
            res["p"] = jobs._run_proc([sys.executable, "-c", "import time; time.sleep(60)"], cwd=tmp_path,
                                      env=None, timeout_s=120)
        except Exception as e:  # noqa: BLE001 - dừng do worker thoát ⇒ WorkerShutdown (không trả mã -9)
            res["exc"] = e

    t = threading.Thread(target=run)
    t0 = datetime.now()
    t.start()
    for _ in range(100):
        if jobs._LIVE_PROCS:
            break
        threading.Event().wait(0.05)
    assert jobs.kill_children() == 1
    t.join(20)
    from worker.scheduler import WorkerShutdown

    assert not t.is_alive() and isinstance(res.get("exc"), WorkerShutdown), res
    assert (datetime.now() - t0).total_seconds() < 20 and not jobs._LIVE_PROCS
