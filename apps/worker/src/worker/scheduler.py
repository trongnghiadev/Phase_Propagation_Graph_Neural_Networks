"""Scheduler của worker (GĐ7.7): APScheduler + khoá phân tán để mỗi lượt chỉ 1 instance chạy.

Mỗi job được bảo vệ bởi một khoá Redis có **watchdog** (gia hạn mỗi TTL/3) và **fencing
token** từ MongoDB (`lock_fences`). Mọi lệnh ghi vào `jobs` đều kèm điều kiện
``last_fence < fence`` để một instance đã mất khoá không thể ghi đè kết quả của instance
mới đang giữ khoá (xem `vngis.locks.LockGuard.write`).

Luồng một job:

1. Lấy khoá ``lock:{job_key}`` (Redlock hoặc SingleLock tuỳ `LOCK_BACKEND`).
2. Ghi `jobs` với ``state="running"``, ``worker=<INSTANCE>``, ``fence``, ``heartbeat``.
3. Chạy hàm xử lý (trong thread, có bảo vệ fencing).
4. Cập nhật ``state="succeeded"/"failed"`` + ``finished_at``.
5. Nhả khoá; watchdog dừng.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable

from vngis.locks import LockBusy, LockGuard, LockLost

log = logging.getLogger("worker.scheduler")

#: Collection lưu trạng thái tác vụ.
JOBS_COLLECTION = "jobs"


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(obj: Any) -> Any:
    """Chuyển kết quả job về dạng lưu được trong MongoDB (datetime → ISO)."""
    if isinstance(obj, dict):
        return {k: _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


class JobRunner:
    """Chạy một job dưới khoá phân tán, ghi trạng thái vào `jobs`."""

    def __init__(self, db, lock, *, instance: str = "worker",
                 ttl_ms: int = 15 * 60 * 1000, watchdog: bool = True):
        self.db = db
        self.lock = lock
        self.instance = instance
        self.ttl_ms = ttl_ms
        # `LockGuard` luôn bật watchdog (gia hạn mỗi TTL/3); cờ này chỉ để tắt khi test.
        self.use_watchdog = watchdog

    def run(self, job_key: str, fn: Callable[..., Any], *, params: dict | None = None,
            job_id: str | None = None, **kwargs: Any) -> dict:
        """Chạy `fn(**kwargs)` dưới khoá `lock:{job_key}`.

        Trả về dict mô tả kết quả: ``{job_id, state, fence, ...}``.
        Nếu khoá bận → trả ``state="skipped"`` (một instance khác đang chạy).
        """
        params = dict(params or {})
        job_id = job_id or f"{job_key}:{_now().strftime('%Y%m%dT%H%M%SZ')}"
        lock_resource = f"lock:{job_key}"
        guard = LockGuard(self.lock, db=self.db, resource=lock_resource, ttl_ms=self.ttl_ms)
        try:
            guard.acquire()
        except LockBusy as e:
            log.info("bỏ qua job %s: khoá đang bận (%s)", job_key, e)
            self._record(job_id, {"type": job_key.split(":", 1)[0], "state": "skipped",
                                  "note": "khoá đang bận — instance khác đang chạy",
                                  "params": params})
            return {"job_id": job_id, "state": "skipped", "reason": "lock_busy"}
        fence = guard.fence
        try:
            self._record(job_id, {"type": job_key.split(":", 1)[0], "state": "running",
                                  "worker": self.instance, "fence": fence,
                                  "heartbeat": _now(), "started_at": _now(),
                                  "params": params, "updated_at": _now()})
            try:
                result = fn(**kwargs)
            except LockLost as e:
                self._record(job_id, {"state": "lost", "error": str(e), "updated_at": _now()})
                log.error("job %s mất khoá giữa chừng: %s", job_id, e)
                return {"job_id": job_id, "state": "lost", "fence": fence, "error": str(e)}
            except Exception as e:  # noqa: BLE001 - job lỗi không được làm chết scheduler
                self._record(job_id, {"state": "failed", "error": str(e)[:500],
                                      "finished_at": _now(), "updated_at": _now()})
                log.exception("job %s thất bại", job_id)
                return {"job_id": job_id, "state": "failed", "fence": fence, "error": str(e)}
            self._record(job_id, {"state": "succeeded", "finished_at": _now(),
                                  "heartbeat": _now(), "updated_at": _now(),
                                  "result": _jsonable(result)})
            log.info("job %s hoàn tất (fence=%s)", job_id, fence)
            return {"job_id": job_id, "state": "succeeded", "fence": fence, "result": result}
        finally:
            try:
                guard.release()
            except Exception:  # noqa: BLE001
                log.warning("không nhả được khoá cho job %s", job_id)

    def _record(self, job_id: str, fields: dict) -> None:
        """Ghi trạng thái job vào `jobs`."""
        try:
            self.db[JOBS_COLLECTION].update_one({"_id": job_id}, {"$set": fields}, upsert=True)
        except Exception as e:  # noqa: BLE001 - ghi log không được làm hỏng job
            log.warning("không ghi được trạng thái job %s: %s", job_id, e)


class Scheduler:
    """Bọc APScheduler: đăng ký job theo lịch, chạy trong thread, có khoá phân tán.

    Khởi tạo:

        sched = Scheduler(db, lock, instance=os.getenv("INSTANCE", "worker"))
        sched.cron("ingest_bulletins", "0 6 * * *", ingest_fn)
        sched.interval("cache_warm", 300, warm_fn)
        sched.start()
    """

    def __init__(self, db, lock, *, instance: str = "worker", ttl_ms: int = 15 * 60 * 1000,
                 timezone: str = "Asia/Ho_Chi_Minh"):
        from apscheduler.schedulers.background import BackgroundScheduler

        self.db = db
        self.lock = lock
        self.instance = instance
        self.ttl_ms = ttl_ms
        self.runner = JobRunner(db, lock, instance=instance, ttl_ms=ttl_ms)
        self.sched = BackgroundScheduler(timezone=timezone, job_defaults={
            "coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})

    # ------------------------------------------------------------ đăng ký ------
    def cron(self, name: str, trigger: str, fn: Callable[..., Any], **kwargs: Any) -> None:
        """Đăng ký job theo lịch cron (biểu thức cron 5 trường, giờ Việt Nam)."""
        self.sched.add_job(self._wrap(name, fn, kwargs), trigger="cron", id=name,
                           replace_existing=True, kwargs={"job_key": name})

    def interval(self, name: str, seconds: int, fn: Callable[..., Any], **kwargs: Any) -> None:
        """Đăng ký job chạy theo chu kỳ (giây)."""
        self.sched.add_job(self._wrap(name, fn, kwargs), trigger="interval", seconds=seconds,
                           id=name, replace_existing=True, kwargs={"job_key": name})

    def date(self, name: str, run_at: datetime, fn: Callable[..., Any], **kwargs: Any) -> None:
        """Đăng ký job chạy đúng một lần."""
        self.sched.add_job(self._wrap(name, fn, kwargs), trigger="date", run_date=run_at,
                           id=name, replace_existing=True, kwargs={"job_key": name})

    def _wrap(self, name: str, fn: Callable[..., Any], kwargs: dict):
        """Bọc hàm job: sinh `job_key` theo ngày (để các instance không chạy trùng ngày)."""

        def _job(**call_kwargs: Any) -> None:
            job_key = call_kwargs.pop("job_key", name)
            from vngis.admin import VN_TZ
            day = _now().astimezone(VN_TZ).strftime("%Y-%m-%d")
            self.runner.run(f"{job_key}:{day}", fn, job_id=f"{job_key}:{day}",
                            **kwargs, **call_kwargs)

        return _job

    # ------------------------------------------------------------ vòng đời -----
    def start(self) -> None:
        self.sched.start()
        log.info("scheduler đã khởi động (instance=%s, jobs=%s)", self.instance,
                 [j.id for j in self.sched.get_jobs()])

    def shutdown(self, wait: bool = False) -> None:
        try:
            self.sched.shutdown(wait=wait)
        except Exception:  # noqa: BLE001
            pass

    def jobs(self) -> list[dict]:
        """Danh sách job đã đăng ký (cho endpoint chẩn đoán)."""
        out = []
        for j in self.sched.get_jobs():
            # `next_run_time` chỉ tồn tại khi job đã được lên lịch (scheduler đã start).
            nrt = getattr(j, "next_run_time", None)
            out.append({"id": j.id, "next_run": nrt.isoformat() if nrt else None,
                        "trigger": str(j.trigger)})
        return out

    def run_pending(self) -> None:
        """Chạy các job đã tới hạn (dùng cho chế độ chạy một lần / test)."""
        self.sched._process_jobs()  # noqa: SLF001
