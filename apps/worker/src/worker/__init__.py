"""Scheduler và job nền của worker (GĐ7.7).

- `worker.scheduler`: bọc APScheduler, khoá phân tán + watchdog + fencing token.
- `worker.jobs`: các job nền (nạp bản tin, nạp lại nguyên tử, dự báo, làm nóng cache).
- `worker.__main__`: điểm khởi chạy (`python -m worker`).
"""
__version__ = "0.2.0"

__all__ = ["__version__"]
