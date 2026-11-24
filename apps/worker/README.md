# apps/worker/ - Scheduler và job nền

**Chức năng:** chạy các job theo lịch hoặc theo yêu cầu từ API; 2-3 bản chạy song song, khoá phân tán đảm bảo mỗi lượt chỉ một bản chạy.

## Job

| Job | Khoá | Mô tả |
|---|---|---|
| `ingest_bulletin` | `lock:ingest:{date}` | nạp bản tin mới hằng ngày |
| `reload_bulletins` | `lock:reload:bulletins` | nạp lại toàn bộ qua collection tạm + `renameCollection` |
| `forecast` | `lock:forecast:{basin}:{date}` | dự báo hằng ngày bằng mô hình production, ghi `forecasts`, `alerts`, Redis |
| `warm_cache` | `lock:rebuild:{key}` | tính sẵn thống kê nặng |
| `train` | `lock:train:{basin}` | huấn luyện theo yêu cầu |

## Cấu trúc

| Thành phần | Nội dung |
|---|---|
| `src/worker/scheduler.py` | `JobRunner`: khoá phân tán trước mỗi job, ghi `jobs` (state, worker, fence, heartbeat), watchdog, `state="skipped"` khi bản khác đang chạy |
| `src/worker/jobs.py` | định nghĩa job và hàm nhiệm vụ (nạp bản tin, nạp lại nguyên tử, dự báo, làm nóng cache, kiểm tra SHA-256) |
| `src/worker/__main__.py` | điểm chạy `python -m worker` |
| `tests/` | test scheduler, chạy đơn instance, fencing token tăng đơn điệu |
| `Dockerfile` | có torch |

## Chạy

```powershell
uv run --project apps/worker python -m worker
```

## Test

```powershell
uv run --project apps/worker pytest apps/worker/tests -q
```
