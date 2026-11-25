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
    # Bộ đếm fencing lưu theo tên tài nguyên (giống API: `resource="reload:bulletins"`), không có tiền tố.
    assert fake_db["lock_fences"].find_one({"_id": "job:fence"})["fence"] == fences[-1]
    assert fake_db["lock_fences"].find_one({"_id": "lock:job:fence"}) is None

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
        # Datetime CÓ múi giờ: scheduler chạy theo Asia/Ho_Chi_Minh, datetime naive (giờ UTC của
        # container) bị hiểu là giờ VN → lệch 7 giờ về quá khứ → misfire, job không chạy.
        run_at = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(milliseconds=50)
        sched.date("now", run_at, job)
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


# ------------------------------------------------ fencing / bỏ qua / hàng đợi ----
def test_instance_bi_bo_qua_khong_ghi_de_trang_thai(fake_db, fake_lock):
    """Instance bị bỏ qua (khoá bận) không được đổi `state` của lượt đang chạy / đã xong."""
    runner1 = JobRunner(fake_db, fake_lock, instance="worker-1")
    runner2 = JobRunner(fake_db, fake_lock, instance="worker-2")
    started, release = threading.Event(), threading.Event()

    def slow():
        started.set()
        release.wait(timeout=10)
        return "xong"

    t = threading.Thread(target=lambda: runner1.run("job:skip", slow, job_id="JS"))
    t.start()
    assert started.wait(timeout=10)
    assert runner2.run("job:skip", lambda: None, job_id="JS")["state"] == "skipped"
    doc = fake_db["jobs"].find_one({"_id": "JS"})
    assert doc["state"] == "running" and doc["worker"] == "worker-1"
    assert doc["skips"][-1]["worker"] == "worker-2"
    release.set()
    t.join(timeout=10)
    assert fake_db["jobs"].find_one({"_id": "JS"})["state"] == "succeeded"


def test_ghi_trang_thai_voi_fence_cu_bi_tu_choi(fake_db, fake_lock):
    """Bản ghi `jobs` đã thuộc lượt có fence lớn hơn → lượt fence cũ không ghi đè được."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-old")
    fake_db["jobs"].insert_one({"_id": "JOLD", "state": "succeeded", "fence": 10_000, "worker": "worker-new"})
    out = runner.run("job:old", lambda: "cũ", job_id="JOLD")
    assert out["state"] == "skipped" and out["reason"] == "fence_rejected"
    doc = fake_db["jobs"].find_one({"_id": "JOLD"})
    assert doc["worker"] == "worker-new" and doc["state"] == "succeeded"


def test_mat_khoa_giua_chung_ghi_lost(fake_db, fake_lock):
    """Job nhận `guard`; mất khoá trong lúc chạy → `state="lost"` dù hàm trả về bình thường."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")

    def job(guard):
        guard.lock.mark_lost()
        return "xong"

    out = runner.run("job:lost", job, job_id="JL")
    assert out["state"] == "lost"
    assert fake_db["jobs"].find_one({"_id": "JL"})["state"] == "lost"


def test_skip_if_done_khong_chay_lai_luot_da_xong(fake_db, fake_lock):
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")
    calls = []
    assert runner.run("job:day", lambda: calls.append(1), job_id="JD", skip_if_done=True)["state"] == "succeeded"
    out = runner.run("job:day", lambda: calls.append(2), job_id="JD", skip_if_done=True)
    assert out["state"] == "skipped" and out["reason"] == "already_done"
    assert calls == [1]


def test_khoa_redis_dung_ten(fake_db, fake_lock, fake_redis):
    """Khoá Redis là `lock:{job_key}` (không lặp tiền tố) để loại trừ được với API."""
    runner = JobRunner(fake_db, fake_lock, instance="worker-1")
    seen = []
    runner.run("reload:bulletins", lambda: seen.append(sorted(k.decode() for k in fake_redis.keys("lock:*"))),
               job_id="JR")
    assert seen == [["lock:reload:bulletins"]]


def test_cron_dung_bieu_thuc(fake_db, fake_lock):
    """Biểu thức cron được dùng thật (trước đây bị bỏ qua → chạy mỗi phút)."""
    sched = Scheduler(fake_db, fake_lock, instance="worker-1")
    try:
        sched.cron("ingest_bulletins", "0 6 * * *", lambda **kw: None)
        trig = sched.sched.get_job("ingest_bulletins").trigger
        fields = {f.name: str(f) for f in trig.fields}
        assert fields["hour"] == "6" and fields["minute"] == "0"
    finally:
        sched.shutdown()


def test_hang_doi_nhan_va_chay_viec(fake_db, fake_lock):
    """Việc `queued` do API giao được nhận nguyên tử, chạy, chỉ nhận tham số hàm khai báo."""
    import datetime

    now = datetime.datetime.now(datetime.timezone.utc)
    fake_db["jobs"].insert_many([
        {"_id": "q1", "type": "cache_warm", "state": "queued", "params": {"collections": ["stations"]},
         "priority": 0, "fence": None, "created_at": now},
        {"_id": "q2", "type": "train", "state": "queued", "params": {}, "priority": 0, "fence": None,
         "created_at": now + datetime.timedelta(seconds=1)},
    ])
    got = []

    def warm(db, cache, *, collections=None):
        got.append(collections)
        return {"ok": True}

    sched = Scheduler(fake_db, fake_lock, instance="worker-1")
    try:
        out = sched.run_queue_once({"cache_warm": warm}, db=fake_db, cache=None)
        assert out["state"] == "succeeded" and got == [["stations"]]
        doc = fake_db["jobs"].find_one({"_id": "q1"})
        assert doc["state"] == "succeeded" and doc["type"] == "cache_warm" and doc["worker"] == "worker-1"
        out2 = sched.run_queue_once({"cache_warm": warm}, db=fake_db, cache=None)
        assert out2["state"] == "failed"
        assert fake_db["jobs"].find_one({"_id": "q2"})["state"] == "failed"
        assert sched.run_queue_once({"cache_warm": warm}, db=fake_db, cache=None) is None
    finally:
        sched.shutdown()


def _fake_repo(tmp_path):
    kttv = tmp_path / "pipelines" / "ingest" / "kttv"
    kttv.mkdir(parents=True)
    (kttv / "kttv_parser.py").write_text("", encoding="utf-8")
    (kttv / "kttv_load_mongo.py").write_text("", encoding="utf-8")
    raw = tmp_path / "data" / "raw" / "kttv"
    raw.mkdir(parents=True)
    return raw


def test_ingest_goi_script_dung_thu_tu_tham_so(tmp_path, fake_db, monkeypatch):
    """Parser rồi loader (đường dẫn ở argv[1..3], cờ đứng sau); loader KHÔNG promote; worker promote."""
    import subprocess

    from vngis import atomic_reload
    from worker import jobs

    raw = _fake_repo(tmp_path)
    calls, envs, promoted = [], [], []

    def fake_run(cmd, **kw):
        calls.append(cmd)
        envs.append(kw["env"])
        return subprocess.CompletedProcess(cmd, 0, "ok", "")

    monkeypatch.setattr(jobs, "_run_proc", fake_run)
    monkeypatch.setattr(atomic_reload, "validate_ingest_staging", lambda db, st: [{"name": "x", "ok": True}])
    monkeypatch.setattr(atomic_reload, "promote_staging",
                        lambda db, st, guard=None: promoted.append(st) or {"count": 1})
    monkeypatch.delenv("BULLETIN_SOURCE_DIR", raising=False)
    monkeypatch.delenv("BULLETIN_WORK_DIR", raising=False)
    out = jobs.ingest_bulletins(fake_db, None, repo_root=str(tmp_path))
    assert out["status"] == "ok"
    assert calls[0][3].endswith("kttv_parser.py") and calls[0][4] == str(raw)
    load = calls[1]
    assert load[3].endswith("kttv_load_mongo.py")
    # file JSON riêng theo run_id (không dùng chung `data/interim/kttv/kttv_ban_tin_parsed.json`)
    assert load[4].endswith("kttv_ban_tin_parsed.json") and out["run_id"] in load[4]
    assert calls[0][5] == load[4] and load[6] == str(raw)
    assert load[7] == "--staging" and load[8:10] == ["--run-id", out["run_id"]] and "--promote" not in load
    assert promoted == [f"hydromet_bulletins__staging_{out['run_id']}"]
    # tiến trình con dùng đúng DB của worker
    assert envs[1]["MONGO_DB"] == fake_db.name
    # thư mục lượt chạy được dọn
    assert not (tmp_path / "data" / "interim" / "kttv" / "runs" / out["run_id"]).exists()


def test_ingest_hai_luot_khong_dung_chung_file(tmp_path, fake_db, monkeypatch):
    import subprocess

    from vngis import atomic_reload
    from worker import jobs

    _fake_repo(tmp_path)
    parsed = []
    monkeypatch.setattr(jobs, "_run_proc", lambda cmd, **kw: (parsed.append(cmd[5]) if "kttv_parser.py" in cmd[3]
                                                             else None) or subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(atomic_reload, "validate_ingest_staging", lambda db, st: [])
    monkeypatch.setattr(atomic_reload, "promote_staging", lambda db, st, guard=None: {})
    monkeypatch.delenv("BULLETIN_SOURCE_DIR", raising=False)
    a = jobs.ingest_bulletins(fake_db, None, repo_root=str(tmp_path))
    b = jobs.ingest_bulletins(fake_db, None, repo_root=str(tmp_path))
    assert a["run_id"] != b["run_id"] and len(set(parsed)) == 2


def test_ingest_kiem_tra_hong_khong_promote_va_xoa_staging(tmp_path, fake_db, monkeypatch):
    import subprocess

    from vngis import atomic_reload
    from worker import jobs

    _fake_repo(tmp_path)
    promoted = []
    monkeypatch.setattr(jobs, "_run_proc", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(atomic_reload, "validate_ingest_staging",
                        lambda db, st: [{"name": "co_ban_tin", "ok": False, "detail": "staging=0"}])
    monkeypatch.setattr(atomic_reload, "promote_staging", lambda *a, **k: promoted.append(1))
    monkeypatch.delenv("BULLETIN_SOURCE_DIR", raising=False)
    with pytest.raises(atomic_reload.ReloadError):
        jobs.ingest_bulletins(fake_db, None, repo_root=str(tmp_path))
    assert promoted == []
    assert not [c for c in fake_db.list_collection_names() if c.startswith("hydromet_bulletins__staging_")]


def test_ingest_mat_khoa_truoc_promote(tmp_path, fake_db, monkeypatch):
    """Worker gọi `guard.check()` sau khi loader xong, trước promote → mất khoá thì không promote."""
    import subprocess

    from vngis import atomic_reload
    from vngis.locks import LockLost
    from worker import jobs

    _fake_repo(tmp_path)
    promoted = []

    class Guard:
        lost = False
        n = 0

        def check(self):
            self.n += 1
            if self.n >= 2:  # lần 1: sau parse; lần 2: trước promote
                raise LockLost("mất khoá (giả lập)")

    monkeypatch.setattr(jobs, "_run_proc", lambda cmd, **kw: subprocess.CompletedProcess(cmd, 0, "", ""))
    monkeypatch.setattr(atomic_reload, "validate_ingest_staging", lambda db, st: [])
    monkeypatch.setattr(atomic_reload, "promote_staging", lambda *a, **k: promoted.append(1))
    monkeypatch.delenv("BULLETIN_SOURCE_DIR", raising=False)
    with pytest.raises(LockLost):
        jobs.ingest_bulletins(fake_db, None, repo_root=str(tmp_path), guard=Guard())
    assert promoted == []


def test_subprocess_env_dung_db_cua_worker(fake_db, monkeypatch):
    from worker import jobs

    monkeypatch.setenv("MONGO_DB", "vietnam_gis")
    env = jobs._subprocess_env(fake_db)
    assert env["MONGO_DB"] == fake_db.name
    assert env.get("MONGO_URI", "mongodb://").startswith("mongodb")


def test_ingest_khong_co_thu_muc_pdf(tmp_path, fake_db, monkeypatch):
    kttv = tmp_path / "pipelines" / "ingest" / "kttv"
    kttv.mkdir(parents=True)
    (kttv / "kttv_parser.py").write_text("", encoding="utf-8")
    (kttv / "kttv_load_mongo.py").write_text("", encoding="utf-8")
    monkeypatch.delenv("BULLETIN_SOURCE_DIR", raising=False)
    from worker import jobs
    assert jobs.ingest_bulletins(fake_db, None, repo_root=str(tmp_path))["status"] == "skipped"


# ----------------------------------------------------- danh sách trắng tham số ----
def _queued(db, _id, typ, params, **extra):
    import datetime

    doc = {"_id": _id, "type": typ, "state": "queued", "params": params, "priority": 0, "fence": None,
           "requested_by": "nguoi_dung_api", "created_at": datetime.datetime.now(datetime.timezone.utc)}
    doc.update(extra)
    db["jobs"].insert_one(doc)


@pytest.mark.parametrize("params", [
    {"documents": [{"source_file": "x"}], "actor": "admin"},
    {"actor": "admin"},
    {"run_id": "../../x"},
    {"khong_ton_tai": 1},
])
def test_hang_doi_tu_choi_tham_so_noi_bo_reload(fake_db, fake_lock, params):
    """`documents`/`actor`/`run_id`/khoá lạ cho reload_bulletins → việc `failed`, hàm job KHÔNG chạy."""
    _queued(fake_db, "r1", "reload_bulletins", params)
    called = []
    sched = Scheduler(fake_db, fake_lock, instance="w1")
    try:
        out = sched.run_queue_once({"reload_bulletins": lambda db, cache=None, **kw: called.append(kw)},
                                   db=fake_db, cache=None)
    finally:
        sched.shutdown()
    assert out["state"] == "failed" and out["reason"] == "invalid_params"
    assert called == []
    assert fake_db["jobs"].find_one({"_id": "r1"})["state"] == "failed"


@pytest.mark.parametrize("params", [{"repo_root": "/tmp/x"}, {"source_dir": "/etc"}, {"run_g0": True}])
def test_hang_doi_tu_choi_tham_so_noi_bo_ingest(fake_db, fake_lock, params):
    _queued(fake_db, "i1", "ingest_bulletins", params)
    called = []
    sched = Scheduler(fake_db, fake_lock, instance="w1")
    try:
        out = sched.run_queue_once({"ingest_bulletins": lambda db, cache=None, **kw: called.append(kw)},
                                   db=fake_db, cache=None)
    finally:
        sched.shutdown()
    assert out["state"] == "failed" and called == []


def test_hang_doi_sai_kieu_bi_tu_choi(fake_db, fake_lock):
    _queued(fake_db, "c1", "cache_warm", {"collections": ["users"]})
    sched = Scheduler(fake_db, fake_lock, instance="w1")
    try:
        out = sched.run_queue_once({"cache_warm": warm_cache}, db=fake_db, cache=None)
    finally:
        sched.shutdown()
    assert out["state"] == "failed"


def test_actor_lay_tu_requested_by(fake_db, fake_lock):
    """`actor` của reload_bulletins là người giao việc (`requested_by`), không phải giá trị trong `params`."""
    _queued(fake_db, "r2", "reload_bulletins", {})
    got = []

    def reload(db, cache=None, *, documents=None, actor="worker", run_id=None, guard=None):
        got.append((actor, documents, run_id))
        return {"ok": True}

    sched = Scheduler(fake_db, fake_lock, instance="w1")
    try:
        out = sched.run_queue_once({"reload_bulletins": reload}, db=fake_db, cache=None)
    finally:
        sched.shutdown()
    assert out["state"] == "succeeded"
    assert got == [("nguoi_dung_api", None, None)]


# ---------------------------------------------------- khoá tài nguyên dùng chung ----
def test_ingest_va_reload_dung_chung_khoa_voi_api(fake_db, fake_lock, fake_redis):
    """Khi API đang giữ `reload:bulletins`, việc ingest trong hàng đợi phải chờ (trả về `queued`)."""
    from vngis.locks import LockGuard

    from worker.__main__ import _job_resources

    _queued(fake_db, "i2", "ingest_bulletins", {})
    api_guard = LockGuard(fake_lock, db=fake_db, resource="reload:bulletins", ttl_ms=5000)
    api_guard.acquire()
    api_fence = api_guard.fence
    ran = []
    sched = Scheduler(fake_db, fake_lock, instance="w1", resources=_job_resources())
    try:
        out = sched.run_queue_once({"ingest_bulletins": lambda db, cache=None: ran.append(1)},
                                   db=fake_db, cache=None)
        assert out["state"] == "skipped" and ran == []
        assert fake_db["jobs"].find_one({"_id": "i2"})["state"] == "queued"
        api_guard.release()
        out = sched.run_queue_once({"ingest_bulletins": lambda db, cache=None: ran.append(1)},
                                   db=fake_db, cache=None)
        assert out["state"] == "succeeded" and ran == [1]
        # một bộ đếm fence duy nhất cho tài nguyên (API + worker)
        assert out["fence"] == api_fence + 1
        assert fake_db["lock_fences"].find_one({"_id": "lock:reload:bulletins"}) is None
    finally:
        api_guard.release()
        sched.shutdown()


def test_cron_dung_khoa_tai_nguyen_va_job_id_theo_ngay(fake_db, fake_lock, fake_redis):
    seen = []
    sched = Scheduler(fake_db, fake_lock, instance="w1", resources={"ingest_bulletins": "reload:bulletins"})
    try:
        job = sched._wrap("ingest_bulletins",
                          lambda: seen.append(sorted(k.decode() for k in fake_redis.keys("lock:*"))), {})
        out = job(job_key="ingest_bulletins")
    finally:
        sched.shutdown()
    assert seen == [["lock:reload:bulletins"]]
    assert out["job_id"].startswith("ingest_bulletins:") and len(out["job_id"]) == len("ingest_bulletins:2026-01-01")


def test_cron_khoa_ban_hen_chay_lai(fake_db, fake_lock):
    from vngis.locks import LockGuard

    g = LockGuard(fake_lock, db=fake_db, resource="reload:bulletins", ttl_ms=5000)
    g.acquire()
    sched = Scheduler(fake_db, fake_lock, instance="w1", resources={"ingest_bulletins": "reload:bulletins"},
                      retry_s=60)
    try:
        job = sched._wrap("ingest_bulletins", lambda: None, {})
        out = job(job_key="ingest_bulletins")
        assert out["state"] == "skipped" and out["reason"] == "lock_busy"
        retry = sched.sched.get_job("ingest_bulletins:retry")
        assert retry is not None and retry.kwargs["_retry"] == 1
    finally:
        g.release()
        sched.shutdown()


def test_interval_mot_ban_ghi_co_dinh(fake_db, fake_lock):
    """`interval` không sinh bản ghi `jobs` mới mỗi chu kỳ và dùng khoá/fence cố định."""
    sched = Scheduler(fake_db, fake_lock, instance="w1")
    try:
        job = sched._wrap("cache_warm", lambda: "ok", {}, interval=True)
        f1 = job(job_key="cache_warm")["fence"]
        f2 = job(job_key="cache_warm")["fence"]
    finally:
        sched.shutdown()
    assert f2 > f1
    assert fake_db["jobs"].count_documents({"type": "cache_warm"}) == 1
    assert fake_db["jobs"].find_one({"_id": "cache_warm"})["state"] == "succeeded"
    assert list(fake_db["lock_fences"].find({}, {"_id": 1})) == [{"_id": "cache_warm"}]


def test_chuyen_bo_dem_fence_cu(fake_db, fake_lock):
    """Bộ đếm mới (`reload:bulletins`) không nhỏ hơn bộ đếm cũ (`lock:reload:bulletins`)."""
    fake_db["lock_fences"].insert_one({"_id": "lock:reload:bulletins", "fence": 41})
    sched = Scheduler(fake_db, fake_lock, instance="w1", resources={"reload_bulletins": "reload:bulletins"})
    sched.shutdown()
    assert fake_db["lock_fences"].find_one({"_id": "reload:bulletins"})["fence"] == 41
    out = JobRunner(fake_db, fake_lock).run("reload:bulletins", lambda: None, job_id="x")
    assert out["fence"] == 42


def test_index_hang_doi(fake_db, fake_lock):
    sched = Scheduler(fake_db, fake_lock, instance="w1")
    sched.shutdown()
    names = set(fake_db["jobs"].index_information())
    assert {"queue_state_priority_created", "queue_state_scheduled", "state_heartbeat"} <= names


# -------------------------------------------------------------- việc kẹt ----------
def test_nhan_lai_viec_ket(fake_db, fake_lock):
    import datetime

    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    fresh = datetime.datetime.now(datetime.timezone.utc)
    _queued(fake_db, "k1", "cache_warm", {}, state="claimed", claimed_at=old, worker="chet")
    _queued(fake_db, "k2", "cache_warm", {}, state="running", claimed_at=old, heartbeat=old,
            started_at=old, worker="chet", attempts=2)
    _queued(fake_db, "k3", "cache_warm", {}, state="running", claimed_at=old, heartbeat=fresh,
            started_at=old, worker="song")
    fake_db["jobs"].insert_one({"_id": "ingest_bulletins:2026-01-01", "type": "ingest_bulletins",
                                "state": "running", "heartbeat": old, "started_at": old})
    sched = Scheduler(fake_db, fake_lock, instance="w1", ttl_ms=60_000, max_attempts=3)  # stale = 180 s
    try:
        out = sched.reclaim_stale()
    finally:
        sched.shutdown()
    assert out == {"failed": 1, "requeued": 1, "lost": 1}
    k1 = fake_db["jobs"].find_one({"_id": "k1"})
    assert k1["state"] == "queued" and k1["attempts"] == 1 and k1["worker"] is None
    k2 = fake_db["jobs"].find_one({"_id": "k2"})
    assert k2["state"] == "failed" and "quá 3 lần" in k2["error"]
    assert fake_db["jobs"].find_one({"_id": "k3"})["state"] == "running"   # heartbeat còn mới
    assert fake_db["jobs"].find_one({"_id": "ingest_bulletins:2026-01-01"})["state"] == "lost"


def test_viec_ket_duoc_chay_lai_qua_hang_doi(fake_db, fake_lock):
    import datetime

    old = datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(hours=2)
    _queued(fake_db, "k4", "cache_warm", {}, state="claimed", claimed_at=old, worker="chet")
    ran = []
    sched = Scheduler(fake_db, fake_lock, instance="w1", ttl_ms=60_000)
    try:
        out = sched.run_queue_once({"cache_warm": lambda db, cache=None: ran.append(1)}, db=fake_db, cache=None)
    finally:
        sched.shutdown()
    assert out["state"] == "succeeded" and ran == [1]
    doc = fake_db["jobs"].find_one({"_id": "k4"})
    assert doc["worker"] == "w1" and doc["attempts"] == 1
