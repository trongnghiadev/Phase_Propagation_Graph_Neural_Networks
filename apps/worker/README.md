# apps/worker/ – Scheduler và job nền

**Chức năng:** chạy các job theo lịch hoặc theo yêu cầu từ API; 2–3 bản chạy song song, khóa phân tán đảm bảo mỗi lượt chỉ một bản chạy.

| Job (dự kiến) | Khóa | Mô tả |
|---|---|---|
| `ingest_bulletin` | `lock:ingest:{date}` | nạp bản tin mới hằng ngày |
| `reload_bulletins` | `lock:reload:bulletins` | nạp lại toàn bộ qua collection tạm + `renameCollection` |
| `forecast` | `lock:forecast:{basin}:{date}` | dự báo hằng ngày bằng mô hình production, ghi `forecasts`, `alerts`, Redis |
| `warm_cache` | `lock:rebuild:{key}` | tính sẵn thống kê nặng |
| `train` | `lock:train:{basin}` | huấn luyện theo yêu cầu |

Cấu trúc: `src/worker/scheduler.py`, `src/worker/jobs/`, `tests/`, `Dockerfile` (có torch).
