"""Kiểm thử scheduler của worker (GĐ7.7).

Bao gồm:
- `JobRunner`: chạy job dưới khoá phân tán, ghi trạng thái vào `jobs`
  (running / succeeded / failed / skipped / lost), kèm `worker`, `fence`, `heartbeat`;
- **một instance duy nhất**: instance thứ hai phải nhận `state="skipped"`;
- **fencing token**: `last_fence` trong `jobs` tăng đơn điệu, và bản ghi có
  `last_fence >= fence` bị từ chối khi ghi;
- `Scheduler` của APScheduler: đăng ký job theo lịch và chạy đúng một lần.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from worker.jobs import bulletin_checksums, warm_cache
from worker.scheduler import JobRunner, Scheduler


# --------------------------------------------------------------- JobRunner ----
def test_job_thanh_cong_ghi_trang_thai(fake_db, fake_lock):
    """Job chạy thành công → `jobs` có `state="succeeded"`, `worker`, `fence`, `heartbeat`."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")
    out = runner.run("job:ok", lambda: {"số": 1}, job_id="J1")
    assert out["state"] == "succeeded"
    assert out["fence"] >= 1

    doc = fake_db["jobs"].find_one({"_id": "J1"})
    assert doc is not None
    assert doc["state"] == "succeeded"
    assert doc["worker"] == "worker-1"
    assert doc["fence"] == out["fence"]
    assert doc["heartbeat"] is not None
    assert doc["result"] == {"số": 1}


def test_job_that_bai_ghi_state_failed(fake_db, fake_lock):
    """Job ném exception → `state="failed"`, scheduler không chết."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")

    def boom():
        raise RuntimeError("lỗi có chủ đích")

    out = runner.run("job:fail", boom, job_id="J2")
    assert out["state"] == "failed"
    assert "lỗi có chủ đích" in out["error"]
    assert fake_db["jobs"].find_one({"_id": "J2"})["state"] == "failed"


def test_chi_mot_instance_chay_job(fake_db, fake_lock):
    """Instance thứ hai không được chạy job (khoá bận) → `state="skipped"`."""
    runner1 = JobRunner(fake_db, fake_lock, instance="worker-1")
    runner2 = JobRunner(fake_db, fake_lock, instance="worker-2")

    started = threading.Event()
    release = threading.Event()
    ran = []

    def slow():
        ran.append("worker-1")
        started.set()
        release.wait(timeout=10)
        return "xong"

    t = threading.Thread(target=lambda: runner1.run("job:lock", slow, job_id="J3"))
    t.start()
    assert started.wait(timeout=10), "job phải bắt đầu"

    out2 = runner2.run("job:lock", lambda: ran.append("worker-2"), job_id="J3")
    assert out2["state"] == "skipped"
    assert out2["reason"] == "lock_busy"
    assert "worker-2" not in ran, "instance thứ hai không được chạy job"

    release.set()
    t.join(timeout=10)
    assert ran == ["worker-1"]
    assert fake_db["jobs"].find_one({"_id": "J3"})["state"] == "succeeded"


def test_fencing_token_tang_don_dieu(fake_db, fake_lock):
    """Mỗi lượt chạy nhận `fence` lớn hơn lượt trước (bộ đếm trong `lock_fences`)."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")
    fences = []
    for i in range(3):
        out = runner.run("job:fence", lambda: None, job_id=f"JF{i}")
        fences.append(out["fence"])
    assert fences == sorted(fences)
    assert fences[0] < fences[-1], "fence phải tăng theo thời gian"
    # Bộ đếm fencing lưu theo resource của khoá (`lock:{job_key}`).
    assert fake_db["lock_fences"].find_one({"_id": "lock:job:fence"})["fence"] == fences[-1]

def test_job_id_theo_ngay_va_ghi_dung_ten(fake_db, fake_lock):
    """Job không truyền `job_id` → tự sinh theo `job_key` và ghi vào `jobs`."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")
    out = runner.run("cache_warm:2026-01-01", lambda: None)
    assert out["state"] == "succeeded"
    assert out["job_id"].startswith("cache_warm:2026-01-01")
    assert fake_db["jobs"].find_one({"_id": out["job_id"]}) is not None


def test_ghi_voi_fence_cu_bi_tu_choi(fake_db, fake_lock):
    """`LockGuard.write` từ chối khi `last_fence` đã lớn hơn (fencing token)."""
    from vngis.locks import FenceRejected, LockGuard

    guard = LockGuard(fake_lock, db=fake_db, resource="res:fence-test", ttl_ms=3000)
    guard.acquire()
    fence = guard.fence
    fake_db["job_state"].insert_one({"_id": "res:fence-test", "last_fence": fence})
    try:
        with pytest.raises(FenceRejected):
            guard.write({}, {"state": "done"}, collection="job_state")
    finally:
        guard.release()


# ---------------------------------------------------------------- Scheduler ----
def test_scheduler_dang_ky_job_theo_lich(fake_db, fake_lock):
    """`Scheduler.cron` / `interval` đăng ký job và `jobs()` liệt kê được."""
    sched = Scheduler(fake_db, fake_lock, instance="worker-1")
    try:
        sched.cron("ingest_bulletins", "0 6 * * *", lambda **kw: None)
        sched.interval("cache_warm", 600, lambda **kw: None)
        ids = {j["id"] for j in sched.jobs()}
        assert {"ingest_bulletins", "cache_warm"} <= ids
    finally:
        sched.shutdown()


def test_scheduler_job_chay_va_ghi_trang_thai(fake_db, fake_lock):
    """Job được scheduler gọi → chạy đúng một lần và ghi `state="succeeded"`."""
    import datetime
    import time

    sched = Scheduler(fake_db, fake_lock, instance="worker-1")
    calls = []

    def job():
        calls.append(1)
        return "ok"

    try:
        # APScheduler chỉ nhận job sau khi scheduler đã start.
        sched.start()
        sched.date("now", datetime.datetime.now() + datetime.timedelta(milliseconds=50), job)
        for _ in range(100):
            if calls:
                break
            time.sleep(0.05)
        assert calls, "job phải được gọi"
        docs = list(fake_db["jobs"].find({}))
        assert docs
        assert any(d.get("state") == "succeeded" for d in docs)
    finally:
        sched.shutdown()


# --------------------------------------------------------------------- jobs ----
def test_warm_cache_lam_nong_tat_ca_collection(fake_db, fake_redis):
    """`warm_cache` làm nóng cache cho các collection mặc định."""
    from vngis.cache import Cache

    cache = Cache(fake_redis, ttl_s={"default": 60}, jitter=0.0)
    out = warm_cache(fake_db, cache)
    assert out["status"] == "ok"
    assert set(out["warmed"]) == {"stations", "tide_extremes", "reservoir_daily"}


def test_warm_cache_khong_co_cache(fake_db):
    """Không có cache → `status="skipped"`, không ném lỗi."""
    out = warm_cache(fake_db, None)
    assert out["status"] == "skipped"


def test_bulletin_checksums(fake_db):
    """Đối chiếu số bản tin / số bản chính / SHA-256."""
    fake_db["hydromet_bulletins"].insert_many([{"_id": "b1"}, {"_id": "b2"}])
    fake_db["hydromet_bulletin_files"].insert_many([
        {"_id": "f1", "is_primary": True, "sha256": "aa", "source_file": "a.pdf"},
        {"_id": "f2", "is_primary": False, "sha256": "bb", "source_file": "b.pdf"},
    ])
    out = bulletin_checksums(fake_db)
    assert out["n_bulletins"] == 2
    assert out["n_files"] == 2
    assert out["n_primary"] == 1
    assert out["n_with_sha"] == 2
    assert out["sha_index"]["aa"] == "a.pdf"
