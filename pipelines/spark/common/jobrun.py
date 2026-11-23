"""Vòng đời một lần chạy job: `run_id`, khóa `job_locks`, bản ghi `jobs`, nhả khóa (dùng chung cho G0 và QC).

    run = JobRun(adm, "qc_tide_extremes", ttl_s=3600)
    run.acquire()                      # JobLockError nếu job đang chạy nơi khác
    run.start(db=..., params=...)      # jobs[run_id] = {job, run_id, generated_at, status: running, ...}
    run.hold()                         # gia hạn + xác nhận còn giữ khóa (LockLostError)
    run.update({...})
    run.finish("ok")                   # ghi status/finished_at (lỗi chỉ in log), nhả khóa theo run_id

Hai job đọc/ghi chung dữ liệu (G0 đọc `qc_flags`, QC ghi `qc_flags`) kiểm tra khóa của nhau bằng `other_running`
SAU khi đã giữ khóa của mình: nếu hai job khởi động cùng lúc, ít nhất một bên thấy khóa bên kia và dừng.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timezone

from .session import MongoAdmin, bson_date


def new_run_id(now: datetime) -> str:
    """`YYYYMMDDTHHMMSSZ_<6 hex>` (giờ UTC)."""
    return now.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)


class JobRun:
    def __init__(self, adm: MongoAdmin, job: str, ttl_s: int, now: datetime | None = None):
        self.adm, self.job, self.ttl_s = adm, job, ttl_s
        self.generated_at = now or datetime.now(timezone.utc)
        self.run_id = new_run_id(self.generated_at)
        self.locked = False

    def acquire(self) -> None:
        self.adm.acquire_lock(self.job, self.run_id, self.ttl_s)
        self.locked = True

    def start(self, **fields) -> None:
        self.update({"job": self.job, "run_id": self.run_id, "generated_at": bson_date(self.generated_at),
                     "status": "running", **fields})

    def update(self, fields: dict) -> None:
        self.adm.upsert("jobs", {"_id": self.run_id}, {"$set": fields})

    def hold(self) -> None:
        """Gia hạn khóa và xác nhận vẫn giữ (mất khóa → LockLostError)."""
        self.adm.renew_lock(self.job, self.run_id, self.ttl_s)

    def other_running(self, other_job: str) -> dict | None:
        """Khóa còn hạn của job khác trong `job_locks`, hoặc None."""
        return self.adm.find_one("job_locks", {"_id": other_job,
                                               "expires_at": {"$gt": bson_date(datetime.now(timezone.utc))}})

    def finish(self, status: str | None) -> None:
        """Ghi trạng thái cuối (`status` None = không ghi `jobs`) rồi nhả khóa; lỗi ở đây chỉ in log."""
        if not self.locked:
            return
        if status is not None:
            try:
                self.update({"status": status, "finished_at": bson_date(datetime.now(timezone.utc))})
            except Exception as e:  # noqa: BLE001
                print(f"[JOBS] không ghi được trạng thái cuối ({status}): {e}", flush=True)
        try:
            self.adm.release_lock(self.job, self.run_id)
            self.locked = False
        except Exception as e:  # noqa: BLE001
            print(f"[KHÓA] không nhả được khóa {self.job}/{self.run_id} (tự hết hạn theo TTL): {e}", flush=True)
