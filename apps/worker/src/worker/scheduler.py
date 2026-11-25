"""Scheduler của worker (GĐ7.7): APScheduler + khoá phân tán để mỗi lượt chỉ 1 instance chạy.

Mỗi job được bảo vệ bởi một khoá Redis có **watchdog** (gia hạn mỗi TTL/3) và **fencing
token** từ MongoDB (`lock_fences`). Mọi lệnh ghi trạng thái vào `jobs` của lượt đang giữ khoá
đều kèm điều kiện ``fence của bản ghi <= fence của lượt này`` để một instance đã mất khoá
(fence cũ) không thể ghi đè kết quả của instance mới đang giữ khoá.

Luồng một job:

1. Lấy khoá ``lock:{resource}`` (Redlock hoặc SingleLock tuỳ `LOCK_BACKEND`); fence lấy từ bộ đếm
   ``lock_fences[resource]`` — **cùng tên tài nguyên với API** (ví dụ ``reload:bulletins``) để khoá Redis
   và bộ đếm fence không bị tách đôi.
2. Ghi `jobs` với ``state="running"``, ``worker=<INSTANCE>``, ``fence``, ``heartbeat``.
3. Chạy hàm xử lý; heartbeat ghi định kỳ; hàm nhận ``guard`` (nếu khai báo) để tự dừng khi mất khoá.
4. Cập nhật ``state="succeeded"/"failed"/"lost"`` + ``finished_at``.
5. Nhả khoá; watchdog dừng.

Instance bị bỏ qua (khoá bận) KHÔNG đổi `state` của bản ghi, chỉ thêm một mục vào `skips`.

**Tài nguyên khoá tách khỏi `job_id`.** `job_id` dùng để nhận diện lượt (cron: theo ngày, cho
``skip_if_done``; interval: một bản ghi cố định); khoá là tài nguyên được ghi (`resources`), ví dụ mọi
tác vụ ghi `hydromet_bulletins` dùng chung ``reload:bulletins``. Cron bị bỏ qua vì khoá bận (tác vụ
khác cùng tài nguyên đang chạy) được hẹn chạy lại sau `retry_s` giây (tối đa `max_retries` lần).

Hàng đợi: `apps/api` giao việc bằng cách chèn `jobs` với ``state="queued"``
(`POST /jobs/{type}`, `/forecasts/run`, `/models/train`). `Scheduler.queue(...)` đăng ký job
quét hàng đợi: nhận nguyên tử một việc (`queued` → `claimed`), chạy dưới khoá của loại việc,
trả lại `queued` nếu khoá bận.

* `params` được kiểm tra theo danh sách trắng `vngis.job_params` (khoá lạ / tham số nội bộ → việc
  ``failed``); `actor` lấy từ `requested_by` của bản ghi việc.
* Việc `claimed`/`running` có `heartbeat`/`claimed_at` cũ hơn `stale_s` (mặc định 3 × TTL khoá) — worker
  đã chết — được trả về `queued` (tăng `attempts`); quá `max_attempts` lượt → ``failed``.
* Việc hàng đợi **mất khoá** giữa chừng (``lost``) cũng được trả về `queued` ngay (cùng giới hạn
  `max_attempts`; lượt mới lấy fence lớn hơn và ghi đè trạng thái của lượt cũ). Việc ``lost`` sót lại (worker
  chết trước khi kịp trả về) của các loại trong `REQUEUE_LOST_TYPES` (``train``) được `reclaim_stale` nhận lại.
* Hết lượt (``failed`` do mất khoá/worker chết quá `max_attempts` lần) → gọi `abandon_hooks[type]` (``train``:
  đánh `models.train_state="failed"`, xem `worker.train.abandon_training`).
"""
from __future__ import annotations

import inspect
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError
from vngis.job_params import INTERNAL_PARAMS, MAX_TRAIN_EPOCHS
from vngis.locks import LockBusy, LockGuard, LockLost

log = logging.getLogger("worker.scheduler")

#: Collection lưu trạng thái tác vụ.
JOBS_COLLECTION = "jobs"

#: Tham số job do hệ thống truyền, không nhận từ `params` của hàng đợi (xem `vngis.job_params`).
_RESERVED_KWARGS = INTERNAL_PARAMS

#: Loại việc mà bản ghi ``lost`` sót lại (worker chết ngay sau khi mất khoá) được `reclaim_stale` trả về hàng
#: đợi. Chỉ ``train`` (bản ghi `models` kẹt ``running`` nếu không chạy lại); việc khác mất khoá được trả về
#: ngay trong `run_queue_once`, bản ghi ``lost`` cũ của chúng không bị chạy lại bất ngờ.
REQUEUE_LOST_TYPES = ("train",)

#: Lý do ghi vào `jobs.reclaims`/`models.train_reason` khi việc bị ngắt do worker dừng có trật tự.
WORKER_SHUTDOWN_REASON = "worker_shutdown"


class WorkerShutdown(Exception):
    """Job bị ngắt vì worker dừng có trật tự (SIGTERM/SIGINT → `worker.jobs.kill_children`), không phải lỗi job.

    `JobRunner.run` ghi ``state="interrupted"``; `Scheduler.run_queue_once` trả việc hàng đợi về ``queued``
    **không tăng** ``attempts`` (ghi ``reclaims[].reason="worker_shutdown"``).
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _jsonable(obj: Any) -> Any:
    """Chuyển kết quả job về dạng lưu được trong MongoDB (datetime → ISO)."""
    if isinstance(obj, dict):
        return {str(k): _jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_jsonable(v) for v in obj]
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, (str, int, float, bool)) or obj is None:
        return obj
    return str(obj)


def _accepts(fn: Callable[..., Any], name: str) -> bool:
    """Hàm `fn` có nhận tham số tên `name` (hoặc ``**kwargs``) không."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return False
    return any(p.name == name or p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values())


def _keyword_names(fn: Callable[..., Any]) -> set[str] | None:
    """Tên tham số từ khoá của `fn`; ``None`` nếu `fn` nhận ``**kwargs`` (nhận mọi tên)."""
    try:
        sig = inspect.signature(fn)
    except (TypeError, ValueError):
        return set()
    names = set()
    for p in sig.parameters.values():
        if p.kind is inspect.Parameter.VAR_KEYWORD:
            return None
        if p.kind in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD):
            names.add(p.name)
    return names


class JobRunner:
    """Chạy một job dưới khoá phân tán, ghi trạng thái vào `jobs` (có điều kiện fencing)."""

    def __init__(self, db, lock, *, instance: str = "worker",
                 ttl_ms: int = 15 * 60 * 1000, watchdog: bool = True, heartbeat_s: float = 30.0):
        self.db = db
        self.lock = lock
        self.instance = instance
        self.ttl_ms = ttl_ms
        # `LockGuard` luôn bật watchdog (gia hạn mỗi TTL/3); cờ này chỉ để tắt khi test.
        self.use_watchdog = watchdog
        self.heartbeat_s = heartbeat_s

    def run(self, job_key: str, fn: Callable[..., Any], *, params: dict | None = None,
            job_id: str | None = None, skip_if_done: bool = False, job_type: str | None = None,
            **kwargs: Any) -> dict:
        """Chạy `fn(**kwargs)` dưới khoá của tài nguyên `job_key` (khoá Redis ``lock:{job_key}``).

        `job_key` là **tài nguyên** (dùng chung với API, ví dụ ``reload:bulletins``); bộ đếm fence là
        ``lock_fences[job_key]`` — giống `LockGuard(resource=...)` của API.

        Trả về dict mô tả kết quả: ``{job_id, state, fence, ...}``.

        - Khoá bận → ``state="skipped"`` (instance khác đang chạy); bản ghi `jobs` giữ nguyên `state`.
        - ``skip_if_done=True`` và `jobs[job_id]` đã ``succeeded`` → bỏ qua (instance khác đã chạy xong
          lượt này, ví dụ đồng hồ các máy lệch nhau vài giây).
        - Nếu `fn` nhận tham số ``guard`` thì được truyền `LockGuard` để tự kiểm tra mất khoá
          (``guard.check()``) trước các bước ghi dài.
        - Mất khoá giữa chừng (watchdog không gia hạn được) → ``state="lost"`` kể cả khi `fn` trả về bình thường.
        """
        params = dict(params or {})
        job_id = job_id or f"{job_key}:{_now().strftime('%Y%m%dT%H%M%SZ')}"
        job_type = job_type or job_key.split(":", 1)[0]
        lock_resource = f"lock:{job_key}"
        # Cùng quy ước với API: resource = tên tài nguyên, khoá Redis = `lock:{resource}`. Trước đây worker
        # dùng resource `lock:{job_key}` ⇒ bộ đếm fence tách đôi với API (`reload:bulletins`).
        guard = LockGuard(self.lock, db=self.db, resource=job_key, key=lock_resource, ttl_ms=self.ttl_ms)
        try:
            guard.acquire()
        except LockBusy as e:
            log.info("bỏ qua job %s: khoá đang bận (%s)", job_key, e)
            self._record_skip(job_id, job_type, params, "khoá đang bận — instance khác đang chạy")
            return {"job_id": job_id, "state": "skipped", "reason": "lock_busy"}
        except LockLost as e:
            log.warning("không xác nhận được khoá cho job %s: %s", job_key, e)
            return {"job_id": job_id, "state": "skipped", "reason": "lock_lost_on_acquire"}
        fence = guard.fence
        stop_hb = threading.Event()
        hb: threading.Thread | None = None
        try:
            if skip_if_done:
                cur = self.db[JOBS_COLLECTION].find_one({"_id": job_id}, {"state": 1})
                if cur and cur.get("state") == "succeeded":
                    log.info("bỏ qua job %s: lượt này đã chạy xong", job_id)
                    return {"job_id": job_id, "state": "skipped", "reason": "already_done", "fence": fence}
            if not self._record(job_id, {"type": job_type, "state": "running",
                                         "worker": self.instance, "fence": fence,
                                         "heartbeat": _now(), "started_at": _now(),
                                         "finished_at": None, "error": None,
                                         "params": _jsonable(params), "updated_at": _now()}, fence):
                return {"job_id": job_id, "state": "skipped", "reason": "fence_rejected", "fence": fence}
            hb = threading.Thread(target=self._heartbeat, args=(job_id, fence, guard, stop_hb),
                                  name=f"heartbeat-{job_id}", daemon=True)
            hb.start()
            call = dict(kwargs)
            if _accepts(fn, "guard") and "guard" not in call:
                call["guard"] = guard
            # `job_id` chỉ truyền cho hàm khai báo đích danh tham số này (không qua **kwargs).
            if "job_id" not in call and "job_id" in (_keyword_names(fn) or set()):
                call["job_id"] = job_id
            try:
                result = fn(**call)
                if guard.lost:
                    raise LockLost(f"mất khoá '{lock_resource}' trong lúc chạy job")
            except LockLost as e:
                self._record(job_id, {"state": "lost", "error": str(e), "finished_at": _now(),
                                      "updated_at": _now()}, fence)
                log.error("job %s mất khoá giữa chừng: %s", job_id, e)
                return {"job_id": job_id, "state": "lost", "fence": fence, "error": str(e)}
            except WorkerShutdown as e:
                self._record(job_id, {"state": "interrupted", "error": f"{WORKER_SHUTDOWN_REASON}: {e}"[:500],
                                      "finished_at": _now(), "updated_at": _now()}, fence)
                log.warning("job %s bị ngắt do worker dừng: %s", job_id, e)
                return {"job_id": job_id, "state": "interrupted", "fence": fence,
                        "reason": WORKER_SHUTDOWN_REASON, "error": str(e)}
            except Exception as e:  # noqa: BLE001 - job lỗi không được làm chết scheduler
                self._record(job_id, {"state": "failed", "error": str(e)[:500],
                                      "finished_at": _now(), "updated_at": _now()}, fence)
                log.exception("job %s thất bại", job_id)
                return {"job_id": job_id, "state": "failed", "fence": fence, "error": str(e)}
            # Hàm job tự báo bỏ qua (thiếu checkpoint, thiếu thư mục nguồn…) → `state="skipped"` + `reason`
            # (khác `skipped` do khoá bận: bản ghi có `result`, `finished_at`, không trả về hàng đợi).
            if isinstance(result, dict) and result.get("status") == "skipped":
                reason = str(result.get("reason") or "job tự bỏ qua")[:500]
                self._record(job_id, {"state": "skipped", "reason": reason, "finished_at": _now(),
                                      "heartbeat": _now(), "updated_at": _now(),
                                      "result": _jsonable(result)}, fence)
                log.info("job %s bỏ qua: %s", job_id, reason)
                return {"job_id": job_id, "state": "skipped", "reason": "job_skipped", "fence": fence,
                        "result": result}
            self._record(job_id, {"state": "succeeded", "finished_at": _now(),
                                  "heartbeat": _now(), "updated_at": _now(),
                                  "result": _jsonable(result)}, fence)
            log.info("job %s hoàn tất (fence=%s)", job_id, fence)
            return {"job_id": job_id, "state": "succeeded", "fence": fence, "result": result}
        finally:
            stop_hb.set()
            if hb is not None:
                hb.join(timeout=2.0)
            try:
                guard.release()
            except Exception:  # noqa: BLE001
                log.warning("không nhả được khoá cho job %s", job_id)

    def _heartbeat(self, job_id: str, fence: int, guard: LockGuard, stop: threading.Event) -> None:
        """Ghi `heartbeat` định kỳ (có điều kiện fencing) để API/người vận hành thấy job còn sống."""
        while not stop.wait(self.heartbeat_s):
            if guard.lost:
                return
            self._record(job_id, {"heartbeat": _now()}, fence, upsert=False)

    def _record(self, job_id: str, fields: dict, fence: int | None = None, *, upsert: bool = True) -> bool:
        """Ghi trạng thái job vào `jobs` với điều kiện fencing (`fence` của bản ghi ≤ `fence`).

        Nếu bản ghi đã có `fence` lớn hơn thì bộ lọc không khớp, `upsert` cố chèn cùng `_id` và bị
        `DuplicateKeyError` → bị từ chối. Trả ``False`` khi bị từ chối hoặc lỗi ghi.
        """
        flt: dict[str, Any] = {"_id": job_id}
        if fence is not None:
            flt["$or"] = [{"fence": None}, {"fence": {"$lte": int(fence)}}]
        try:
            res = self.db[JOBS_COLLECTION].update_one(flt, {"$set": fields}, upsert=upsert)
        except DuplicateKeyError:
            log.warning("ghi trạng thái job %s bị từ chối do fencing (fence=%s)", job_id, fence)
            return False
        except Exception as e:  # noqa: BLE001 - ghi log không được làm hỏng job
            log.warning("không ghi được trạng thái job %s: %s", job_id, e)
            return False
        if not upsert and res.matched_count == 0:
            return False
        return True

    def _record_skip(self, job_id: str, job_type: str, params: dict, note: str) -> None:
        """Ghi lần bị bỏ qua mà KHÔNG đổi `state` của lượt đang chạy / đã chạy xong."""
        try:
            self.db[JOBS_COLLECTION].update_one(
                {"_id": job_id},
                {"$setOnInsert": {"type": job_type, "state": "skipped", "note": note,
                                  "params": _jsonable(params), "fence": None},
                 "$push": {"skips": {"$each": [{"worker": self.instance, "at": _now(), "note": note}],
                                     "$slice": -20}}},
                upsert=True)
        except Exception as e:  # noqa: BLE001
            log.warning("không ghi được trạng thái bỏ qua của job %s: %s", job_id, e)


class Scheduler:
    """Bọc APScheduler: đăng ký job theo lịch, chạy trong thread, có khoá phân tán.

    Khởi tạo:

        sched = Scheduler(db, lock, instance=os.getenv("INSTANCE", "worker"),
                          resources={"ingest_bulletins": "reload:bulletins"})
        sched.cron("ingest_bulletins", "0 6 * * *", ingest_fn)
        sched.interval("cache_warm", 600, warm_fn)
        sched.start()

    Khoá và `job_id` của mỗi lượt:

    - khoá: tài nguyên ``resources.get(name, name)`` (khoá Redis ``lock:{resource}``) — **không** chứa
      ngày/mốc chu kỳ, để các tác vụ cùng ghi một tài nguyên loại trừ nhau;
    - `cron`/`date`: `job_id` = ``{name}:{ngày giờ VN}`` và bỏ qua nếu lượt của ngày đã ``succeeded`` —
      dùng cho job **mỗi ngày một lần** (lịch cron nhiều lần/ngày cần tên job riêng cho mỗi lần);
    - `interval`: `job_id` = ``{name}`` (một bản ghi `jobs` cố định, ghi đè mỗi chu kỳ — không sinh rác).
    """

    def __init__(self, db, lock, *, instance: str = "worker", ttl_ms: int = 15 * 60 * 1000,
                 timezone: str = "Asia/Ho_Chi_Minh", heartbeat_s: float = 30.0,
                 resources: dict[str, str] | None = None, stale_s: float | None = None,
                 max_attempts: int = 3, retry_s: float = 300.0, max_retries: int = 12):
        from apscheduler.schedulers.background import BackgroundScheduler

        self.db = db
        self.lock = lock
        self.instance = instance
        self.ttl_ms = ttl_ms
        self.resources = dict(resources or {})
        # Việc kẹt: heartbeat cũ hơn 3 × TTL khoá (khoá chắc chắn đã hết hạn nếu worker chết).
        self.stale_s = float(stale_s) if stale_s is not None else 3 * ttl_ms / 1000.0
        self.max_attempts = max(int(max_attempts), 1)
        #: Loại việc → hàm ``hook(db, job_doc, reason)`` gọi khi việc hàng đợi bị bỏ hẳn (hết lượt).
        self.abandon_hooks: dict[str, Callable[..., Any]] = {}
        self.retry_s = float(retry_s)
        self.max_retries = int(max_retries)
        self.runner = JobRunner(db, lock, instance=instance, ttl_ms=ttl_ms, heartbeat_s=heartbeat_s)
        self.sched = BackgroundScheduler(timezone=timezone, job_defaults={
            "coalesce": True, "max_instances": 1, "misfire_grace_time": 3600})
        self._migrate_fence_counters()
        self._ensure_indexes()

    def _ensure_indexes(self) -> None:
        """Index cho truy vấn hàng đợi và quét việc kẹt (idempotent: tạo lại cùng định nghĩa là no-op)."""
        from pymongo import ASCENDING, DESCENDING

        try:
            jobs = self.db[JOBS_COLLECTION]
            # nhận việc: {state: "queued", scheduled_at <= now}, sắp xếp priority giảm, created_at tăng
            jobs.create_index([("state", ASCENDING), ("priority", DESCENDING), ("created_at", ASCENDING)],
                              name="queue_state_priority_created")
            jobs.create_index([("state", ASCENDING), ("scheduled_at", ASCENDING)], name="queue_state_scheduled")
            # quét việc kẹt: {state in [claimed, running], heartbeat < cutoff}
            jobs.create_index([("state", ASCENDING), ("heartbeat", ASCENDING)], name="state_heartbeat")
        except Exception as e:  # noqa: BLE001 - thiếu quyền tạo index không chặn worker
            log.warning("không tạo được index cho `%s`: %s", JOBS_COLLECTION, e)

    def resource_for(self, name: str) -> str:
        """Tài nguyên khoá của job/loại việc `name`."""
        return self.resources.get(name, name)

    def _migrate_fence_counters(self) -> None:
        """Bộ đếm fence chuyển từ ``lock:{res}`` sang ``{res}``: không để fence mới nhỏ hơn fence cũ.

        Bản ghi `jobs` cũ có thể mang fence lấy từ bộ đếm cũ; `$max` đưa bộ đếm mới lên ít nhất bằng bộ
        đếm cũ (idempotent, an toàn khi nhiều instance cùng chạy).
        """
        for res in set(self.resources.values()):
            try:
                old = self.db["lock_fences"].find_one({"_id": f"lock:{res}"}, {"fence": 1})
                if old and old.get("fence"):
                    self.db["lock_fences"].update_one({"_id": res}, {"$max": {"fence": int(old["fence"])}},
                                                      upsert=True)
            except Exception as e:  # noqa: BLE001 - không chặn khởi động
                log.warning("không chuyển được bộ đếm fence %s: %s", res, e)

    # ------------------------------------------------------------ đăng ký ------
    def cron(self, name: str, trigger: str, fn: Callable[..., Any], *, lock_key: str | None = None,
             **kwargs: Any) -> None:
        """Đăng ký job theo lịch cron (biểu thức cron 5 trường, giờ Việt Nam), mỗi ngày một lượt."""
        from apscheduler.triggers.cron import CronTrigger

        self.sched.add_job(self._wrap(name, fn, kwargs, lock_key=lock_key), trigger=CronTrigger.from_crontab(
            trigger, timezone=self.sched.timezone), id=name, replace_existing=True,
            kwargs={"job_key": name})

    def interval(self, name: str, seconds: int, fn: Callable[..., Any], *, lock_key: str | None = None,
                 **kwargs: Any) -> None:
        """Đăng ký job chạy theo chu kỳ (giây); một bản ghi `jobs` cố định `{name}`."""
        self.sched.add_job(self._wrap(name, fn, kwargs, interval=True, lock_key=lock_key), trigger="interval",
                           seconds=seconds, id=name, replace_existing=True, kwargs={"job_key": name})

    def date(self, name: str, run_at: datetime, fn: Callable[..., Any], *, lock_key: str | None = None,
             **kwargs: Any) -> None:
        """Đăng ký job chạy đúng một lần."""
        self.sched.add_job(self._wrap(name, fn, kwargs, lock_key=lock_key), trigger="date", run_date=run_at,
                           id=name, replace_existing=True, kwargs={"job_key": name})

    def queue(self, handlers: dict[str, Callable[..., Any]], *, seconds: int = 15,
              lock_keys: dict[str, str] | None = None, name: str = "job_queue",
              types: list[str] | None = None, exclude_types: list[str] | None = None, **kwargs: Any) -> None:
        """Đăng ký job quét hàng đợi `jobs` (``state="queued"``) mỗi `seconds` giây.

        Mỗi bộ quét (`name`) là một job APScheduler riêng (chạy song song trong thread pool). Việc chạy lâu
        (``train``) dùng bộ quét riêng (`types`) và loại khỏi bộ quét chung (`exclude_types`) để không chặn
        các việc ngắn của instance này.
        """
        self.sched.add_job(lambda: self.run_queue_once(handlers, lock_keys=lock_keys, types=types,
                                                       exclude_types=exclude_types, **kwargs),
                           trigger="interval", seconds=seconds, id=name, replace_existing=True)

    def _wrap(self, name: str, fn: Callable[..., Any], kwargs: dict, *, interval: bool = False,
              lock_key: str | None = None):
        """Bọc hàm job: `job_id` theo ngày (cron/date) hoặc cố định (interval); khoá theo tài nguyên."""

        def _job(**call_kwargs: Any) -> dict:
            job_key = call_kwargs.pop("job_key", name)
            attempt = int(call_kwargs.pop("_retry", 0))
            day = call_kwargs.pop("_day", None)
            resource = lock_key or self.resource_for(job_key)
            if interval:
                return self.runner.run(resource, fn, job_id=job_key, job_type=job_key, **kwargs, **call_kwargs)
            if day is None:
                from vngis.admin import VN_TZ
                day = _now().astimezone(VN_TZ).strftime("%Y-%m-%d")
            job_id = f"{job_key}:{day}"
            out = self.runner.run(resource, fn, job_id=job_id, job_type=job_key, skip_if_done=True,
                                  **kwargs, **call_kwargs)
            if out.get("state") == "skipped" and out.get("reason") in ("lock_busy", "lock_lost_on_acquire"):
                self._schedule_retry(job_key, _job, job_id, day, attempt)
            return out

        return _job

    def _schedule_retry(self, job_key: str, job: Callable[..., Any], job_id: str, day: str, attempt: int) -> None:
        """Khoá tài nguyên bận (tác vụ khác đang ghi) → hẹn chạy lại lượt của ngày, trừ khi đã xong."""
        if self.retry_s <= 0 or attempt >= self.max_retries:
            return
        try:
            cur = self.db[JOBS_COLLECTION].find_one({"_id": job_id}, {"state": 1}) or {}
        except Exception:  # noqa: BLE001
            cur = {}
        if cur.get("state") == "succeeded":
            return
        try:
            self.sched.add_job(job, trigger="date", run_date=_now() + timedelta(seconds=self.retry_s),
                               id=f"{job_key}:retry", replace_existing=True,
                               kwargs={"job_key": job_key, "_retry": attempt + 1, "_day": day})
            log.info("job %s: khoá bận, hẹn chạy lại sau %.0f s (lần %d)", job_id, self.retry_s, attempt + 1)
        except Exception as e:  # noqa: BLE001
            log.warning("không hẹn chạy lại được job %s: %s", job_id, e)

    # ------------------------------------------------------------ hàng đợi -----
    def reclaim_stale(self, now: datetime | None = None) -> dict:
        """Nhận lại việc kẹt do worker chết (heartbeat/claimed_at cũ hơn `stale_s`).

        - Việc từ hàng đợi (có `requested_by`): còn lượt → ``queued`` + ``attempts += 1``; hết lượt
          (`max_attempts`) → ``failed`` (+ `abandon_hooks`).
        - Việc hàng đợi ``lost`` (mất khoá) thuộc `REQUEUE_LOST_TYPES`, cũ hơn `stale_s` — sót lại khi worker chết
          trước khi trả về hàng đợi — xử lý như trên.
        - Bản ghi của cron/interval (không có `requested_by`) còn ``running`` → ``lost`` (chỉ để hiển thị;
          lượt sau của lịch tự chạy lại).

        Fence không cần đặt lại: lượt mới lấy fence lớn hơn, worker cũ (nếu còn sống) bị fencing chặn ghi.
        """
        now = now or _now()
        cutoff = now - timedelta(seconds=self.stale_s)
        jobs = self.db[JOBS_COLLECTION]
        old = [{"$or": [{"heartbeat": None}, {"heartbeat": {"$lt": cutoff}}]},
               {"$or": [{"claimed_at": None}, {"claimed_at": {"$lt": cutoff}}]},
               {"$or": [{"started_at": None}, {"started_at": {"$lt": cutoff}}]},
               {"$or": [{"updated_at": None}, {"updated_at": {"$lt": cutoff}}]}]
        stale = [{"state": {"$in": ["claimed", "running"]}}, *old]
        # ``interrupted`` sót lại (worker thoát trước khi kịp `requeue_interrupted`) → nhận lại như ``lost``.
        lost = [{"state": {"$in": ["lost", "interrupted"]}}, {"type": {"$in": list(REQUEUE_LOST_TYPES)}},
                {"$or": [{"updated_at": None}, {"updated_at": {"$lt": cutoff}}]}]
        queued = {"requested_by": {"$exists": True}}
        stuck = {"$or": [{"$and": stale}, {"$and": lost}]}
        last = self.max_attempts - 1
        note = {"worker": self.instance, "at": now, "note": "worker ngừng (heartbeat cũ) — nhận lại việc"}
        out = {"failed": 0, "requeued": 0, "lost": 0}
        try:
            give_up = {"$and": [stuck, queued, {"attempts": {"$gte": last}}]}
            ids = [d["_id"] for d in jobs.find(give_up, {"_id": 1})]
            if ids:
                r = jobs.update_many({"$and": [give_up, {"_id": {"$in": ids}}]},
                                     {"$set": {"state": "failed", "finished_at": now, "updated_at": now,
                                               "error": f"worker ngừng/mất khoá giữa chừng quá "
                                                        f"{self.max_attempts} lần"}})
                out["failed"] = r.modified_count
                for d in jobs.find({"_id": {"$in": ids}, "state": "failed"}):
                    self._abandon(d, d.get("error") or "hết lượt")
            r = jobs.update_many({"$and": [stuck, queued, {"$or": [{"attempts": {"$exists": False}},
                                                                     {"attempts": {"$lt": last}}]}]},
                                 {"$set": {"state": "queued", "worker": None, "updated_at": now,
                                           "finished_at": None},
                                  "$inc": {"attempts": 1},
                                  "$push": {"reclaims": {"$each": [note], "$slice": -20}}})
            out["requeued"] = r.modified_count
            r = jobs.update_many({"$and": [*stale, {"requested_by": {"$exists": False}}]},
                                 {"$set": {"state": "lost", "finished_at": now, "updated_at": now,
                                           "error": "worker ngừng giữa chừng (heartbeat cũ)"}})
            out["lost"] = r.modified_count
        except Exception as e:  # noqa: BLE001 - quét lỗi không được chặn hàng đợi
            log.warning("không quét được việc kẹt: %s", e)
        if any(out.values()):
            log.warning("đã xử lý việc kẹt: %s", out)
        return out

    def _abandon(self, job_doc: dict, reason: str) -> None:
        hook = self.abandon_hooks.get(str(job_doc.get("type") or ""))
        if hook is None:
            return
        try:
            hook(self.db, job_doc, reason)
        except Exception as e:  # noqa: BLE001 - hook lỗi không được chặn hàng đợi
            log.warning("abandon hook của việc %s lỗi: %s", job_doc.get("_id"), e)

    def requeue_lost(self, job_id: Any, fence: int | None, error: str | None = None) -> str | None:
        """Việc hàng đợi vừa ``lost`` (mất khoá) → ``queued`` (``attempts += 1``) hoặc ``failed`` khi hết lượt.

        Chỉ đổi bản ghi còn đúng ``state="lost"`` và ``fence`` của lượt này (lượt khác đã nhận lại thì thôi).
        Trả trạng thái mới (``queued``/``failed``) hoặc ``None`` khi không đổi.
        """
        jobs = self.db[JOBS_COLLECTION]
        now = _now()
        flt: dict[str, Any] = {"_id": job_id, "state": "lost", "requested_by": {"$exists": True}}
        if fence is not None:
            flt["fence"] = fence
        last = self.max_attempts - 1
        note = {"worker": self.instance, "at": now, "note": f"mất khoá giữa chừng — trả về hàng đợi ({error or ''})"[:300]}
        try:
            r = jobs.update_one({**flt, "$or": [{"attempts": {"$exists": False}}, {"attempts": {"$lt": last}}]},
                                {"$set": {"state": "queued", "worker": None, "updated_at": now, "finished_at": None},
                                 "$inc": {"attempts": 1},
                                 "$push": {"reclaims": {"$each": [note], "$slice": -20}}})
            if r.modified_count:
                log.warning("việc %s mất khoá → trả về hàng đợi", job_id)
                return "queued"
            msg = f"mất khoá giữa chừng quá {self.max_attempts} lần: {error or ''}"[:500]
            doc = jobs.find_one_and_update({**flt, "attempts": {"$gte": last}},
                                           {"$set": {"state": "failed", "finished_at": now, "updated_at": now,
                                                     "error": msg}},
                                           return_document=ReturnDocument.AFTER)
        except Exception as e:  # noqa: BLE001
            log.warning("không trả được việc %s về hàng đợi: %s", job_id, e)
            return None
        if doc is not None:
            self._abandon(doc, msg)
            return "failed"
        return None

    def requeue_interrupted(self, job_id: Any, fence: int | None) -> str | None:
        """Việc hàng đợi bị ngắt do worker dừng có trật tự → ``queued`` **không tăng** ``attempts``.

        Không phải lỗi của việc (worker bị dừng/triển khai lại) ⇒ không tốn lượt; ghi ``reclaims[]`` với
        ``reason="worker_shutdown"`` và ``last_interrupt``. Chỉ đổi bản ghi còn đúng ``state="interrupted"`` và
        ``fence`` của lượt này. Trả ``"queued"`` hoặc ``None``.
        """
        now = _now()
        flt: dict[str, Any] = {"_id": job_id, "state": "interrupted", "requested_by": {"$exists": True}}
        if fence is not None:
            flt["fence"] = fence
        note = {"worker": self.instance, "at": now, "reason": WORKER_SHUTDOWN_REASON,
                "note": "worker dừng có trật tự — trả về hàng đợi (không tính lượt)"}
        try:
            r = self.db[JOBS_COLLECTION].update_one(
                flt, {"$set": {"state": "queued", "worker": None, "updated_at": now, "finished_at": None,
                               "last_interrupt": {"reason": WORKER_SHUTDOWN_REASON, "at": now}},
                      "$push": {"reclaims": {"$each": [note], "$slice": -20}}})
        except Exception as e:  # noqa: BLE001
            log.warning("không trả được việc %s về hàng đợi sau khi worker dừng: %s", job_id, e)
            return None
        if r.modified_count:
            log.warning("việc %s bị ngắt do worker dừng → trả về hàng đợi", job_id)
            return "queued"
        return None

    def run_queue_once(self, handlers: dict[str, Callable[..., Any]], *,
                       lock_keys: dict[str, str] | None = None, types: list[str] | None = None,
                       exclude_types: list[str] | None = None, **kwargs: Any) -> dict | None:
        """Nhận nguyên tử MỘT việc `queued` (ưu tiên `priority` cao, cũ trước) và chạy nó.

        - Trước tiên nhận lại việc kẹt (`reclaim_stale`).
        - Loại việc không có trong `handlers` → ``state="failed"`` (không bỏ mặc ở `queued`).
        - `params` sai danh sách trắng (`vngis.job_params`: khoá lạ, tham số nội bộ, sai kiểu) → ``failed``.
        - `actor` (nếu hàm job nhận) = `requested_by` của bản ghi việc, không bao giờ lấy từ `params`.
        - Khoá của loại việc bận → trả về ``queued`` để lượt quét sau chạy.
        - Mất khoá giữa chừng (``lost``) → `requeue_lost` (``queued``, ``attempts += 1``; hết lượt → ``failed``).
        - `types` / `exclude_types`: chỉ nhận / không nhận các loại việc này (bộ quét riêng cho ``train``).
        """
        from vngis.job_params import JobParamsError, validate_job_params

        jobs = self.db[JOBS_COLLECTION]
        self.reclaim_stale()
        now = _now()
        flt: dict[str, Any] = {"state": "queued", "$or": [{"scheduled_at": None}, {"scheduled_at": {"$lte": now}}]}
        if types is not None:
            flt["type"] = {"$in": list(types)}
        elif exclude_types:
            flt["type"] = {"$nin": list(exclude_types)}
        doc = jobs.find_one_and_update(
            flt,
            {"$set": {"state": "claimed", "worker": self.instance, "claimed_at": now, "updated_at": now}},
            sort=[("priority", -1), ("created_at", 1)], return_document=ReturnDocument.AFTER)
        if doc is None:
            return None
        job_id, job_type = doc["_id"], str(doc.get("type") or "")

        def _fail(reason: str, error: str) -> dict:
            jobs.update_one({"_id": job_id, "state": "claimed", "worker": self.instance},
                            {"$set": {"state": "failed", "finished_at": _now(), "updated_at": _now(),
                                      "error": error[:500]}})
            return {"job_id": job_id, "state": "failed", "reason": reason, "error": error}

        fn = handlers.get(job_type)
        if fn is None:
            return _fail("unsupported", f"worker chưa hỗ trợ loại việc {job_type!r}")
        try:
            params = validate_job_params(job_type, doc.get("params"))
        except JobParamsError as e:
            log.warning("job %s: tham số bị từ chối: %s", job_id, e)
            msg = (f"{e} — bị từ chối theo danh sách trắng hiện hành (vngis.job_params); việc giao trước khi "
                   f"siết giới hạn (ví dụ train.epochs ≤ {MAX_TRAIN_EPOCHS}) cần giao lại với tham số mới")
            out = _fail("invalid_params", msg)
            # `train`: bản ghi `models` không được kẹt `queued` (giao lại qua API cần train_state=failed).
            self._abandon({**doc, "state": "failed"}, msg)
            return out
        names = _keyword_names(fn)
        call = {k: v for k, v in params.items()
                if k not in _RESERVED_KWARGS and (names is None or k in names)}
        ignored = sorted(set(params) - set(call))
        if ignored:
            log.info("job %s: hàm job không dùng tham số %s", job_id, ignored)
        extra = {k: v for k, v in kwargs.items() if k not in ("actor",)}
        if _accepts(fn, "actor"):
            extra["actor"] = str(doc.get("requested_by") or doc.get("created_by") or "queue")
        key = (lock_keys or {}).get(job_type) or self.resource_for(job_type)
        out = self.runner.run(key, fn, params=params, job_id=job_id, job_type=job_type, **extra, **call)
        if out.get("state") == "lost":
            # Mất khoá: trả về hàng đợi (lượt sau fence lớn hơn), hết lượt → failed + abandon hook.
            out["requeue"] = self.requeue_lost(job_id, out.get("fence"), out.get("error"))
        if out.get("state") == "interrupted":
            out["requeue"] = self.requeue_interrupted(job_id, out.get("fence"))
        if out.get("state") == "skipped" and out.get("reason") != "job_skipped":
            # Khoá bận: trả việc về hàng đợi (chỉ khi vẫn do instance này nhận). Hàm job tự bỏ qua
            # (`job_skipped`) là trạng thái cuối, không trả về hàng đợi.
            jobs.update_one({"_id": job_id, "state": "claimed", "worker": self.instance},
                            {"$set": {"state": "queued", "worker": None, "updated_at": _now()}})
        return out

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
