"""Điểm khởi chạy của worker (`python -m worker`).

Worker chạy APScheduler với các job nền (nạp bản tin, dự báo, làm nóng cache). Mỗi job được
bảo vệ bởi khoá phân tán (`vngis.locks`) nên khi chạy nhiều instance, **chỉ một instance**
thực sự chạy job trong mỗi lượt; các instance còn lại ghi `state="skipped"` vào `jobs`.

Biến môi trường:

| Biến | Ý nghĩa | Mặc định |
|---|---|---|
| `INSTANCE` | tên instance (đưa vào log và `jobs.worker`) | `worker` |
| `LOCK_BACKEND` | `single` hoặc `redlock` | `redlock` |
| `WORKER_ONESHOT` | `1` = chạy các job đã tới hạn rồi thoát (hữu ích cho CI) | `0` |
"""
from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from typing import Any

log = logging.getLogger("worker")


def configure_logging(instance: str = "worker", level: str = "INFO") -> None:
    """Log có tên instance (không có `request_id` vì worker không phục vụ HTTP)."""
    root = logging.getLogger()
    root.setLevel(level)
    if not root.handlers:
        h = logging.StreamHandler()
        h.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)-7s [%(instance)s] %(name)s: %(message)s"))
        root.addHandler(h)

    class _InstanceFilter(logging.Filter):
        def filter(self, record: logging.LogRecord) -> bool:
            record.instance = instance  # noqa: A003
            return True

    for h in root.handlers:
        h.addFilter(_InstanceFilter())


def build_scheduler(instance: str) -> Any:
    """Dựng scheduler + đăng ký các job nền."""
    from vngis.config import get_config
    from vngis.db import get_db, redlock

    from worker.jobs import forecast_run, ingest_bulletins, warm_cache
    from worker.scheduler import Scheduler

    cfg = get_config()
    db = get_db(cfg)
    lock = redlock(cfg)
    cache = None
    try:
        from vngis.db import redis_cache
        from vngis.cache import build_cache
        cache = build_cache(redis_cache(cfg), cfg)
    except Exception as e:  # noqa: BLE001 - worker vẫn chạy được nếu Redis lỗi
        log.warning("không khởi tạo được cache: %s", e)

    sched = Scheduler(db, lock, instance=instance,
                      ttl_ms=int(getattr(cfg.scheduler, "lock_ttl_s", 600)) * 1000)

    # Nạp bản tin mới hằng ngày (06:00 giờ Việt Nam) — khoá `lock:ingest:{date}`.
    sched.cron("ingest_bulletins", "0 6 * * *", ingest_bulletins, db=db, cache=cache)
    # Dự báo hằng ngày (07:00) — khoá `lock:forecast:{basin}:{date}`.
    sched.cron("forecast_run", "0 7 * * *", forecast_run, db=db, cache=cache)
    # Làm nóng cache mỗi 10 phút.
    sched.interval("cache_warm", 600, warm_cache, db=db, cache=cache)
    return sched


def main(argv: list[str] | None = None) -> int:
    """Điểm vào CLI. Mã thoát: 0 = thành công, 1 = lỗi."""
    ap = argparse.ArgumentParser(description="Worker nền (APScheduler)")
    ap.add_argument("--instance", default=os.getenv("INSTANCE", "worker"), help="tên instance")
    ap.add_argument("--once", action="store_true",
                    help="chạy các job đã tới hạn một lần rồi thoát")
    args = ap.parse_args(argv)

    configure_logging(args.instance)
    try:
        sched = build_scheduler(args.instance)
    except Exception as e:  # noqa: BLE001
        log.error("không khởi động được worker: %s", e)
        return 1

    if args.once or os.getenv("WORKER_ONESHOT") == "1":
        sched.run_pending()
        return 0

    sched.start()
    stop = threading.Event()

    def _handle(signum, _frame):  # pragma: no cover
        log.info("nhận tín hiệu %s, đang dừng worker", signum)
        stop.set()

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)
    try:
        stop.wait()
    finally:
        sched.shutdown()
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
