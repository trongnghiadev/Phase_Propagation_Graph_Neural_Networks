# apps/worker/ - Scheduler và job nền

**Chức năng:** chạy các job theo lịch hoặc theo yêu cầu từ API; 2-3 bản chạy song song, khoá phân tán đảm bảo mỗi lượt chỉ một bản chạy.

## Job

| Job | Khoá (tài nguyên) | `job_id` | Mô tả |
|---|---|---|---|
| `ingest_bulletins` | `lock:reload:bulletins` (chung với API) | `ingest_bulletins:{ngày VN}` | hằng ngày (`scheduler.ingest_hour`): `kttv_parser.py` → `kttv_load_mongo.py <parsed> <out> <raw> --staging --run-id <id>` (chỉ nạp staging) → worker kiểm tra staging, `guard.check()`, `vngis.atomic_reload.promote_staging` |
| `reload_bulletins` | `lock:reload:bulletins` (chung với API) | id việc | theo hàng đợi: nạp lại toàn bộ qua collection tạm + `renameCollection`; `actor` = `requested_by` |
| `forecast_run` | `lock:forecast_run` | `forecast_run:{ngày VN}` | hằng ngày 08:00 (`scheduler.forecast_hour/minute`) hoặc hàng đợi (`POST /forecasts/run`): PhaseGNN từ checkpoint → `model_forecasts` (`src/worker/forecast.py`); thiếu checkpoint/`phasegnn`/mẫu dữ liệu → `state="skipped"` + `reason` |
| `cache_warm` | `lock:cache_warm` | `cache_warm` (một bản ghi) | mỗi 10 phút |
| hàng đợi | theo tài nguyên của loại việc | id việc | quét `jobs` `state="queued"` mỗi 15 s, nhận nguyên tử (`claimed`), khoá bận → trả lại `queued`; loại chưa hỗ trợ (`train`, `build_features`, `qc_review`) → `failed` |

- **Tham số hàng đợi:** danh sách trắng `vngis.job_params` (API trả 422, worker đánh `failed`); không bao giờ nhận `repo_root`, `source_dir`, `documents`, `actor`, `run_g0`, `run_id` từ hàng đợi.
- **Loại trừ:** mọi tác vụ ghi `hydromet_bulletins` dùng chung tài nguyên `reload:bulletins` (khoá Redis `lock:reload:bulletins`, bộ đếm fence `lock_fences["reload:bulletins"]`, giống API). Cron gặp khoá bận → hẹn chạy lại sau 5 phút (tối đa 12 lần).
- **`__prev`:** một chính sách cho API/worker/loader (`promote_staging`): còn `__prev` sót → từ chối; promote thành công + kiểm tra số bản tin → xoá `__prev`; rename với writeConcern majority.
- **Dự báo (`forecast_run`):** checkpoint dò theo thứ tự `FORECAST_CHECKPOINT` → `models` theo registry `phasegnn/registry.py` (`status="production"`, `version` số nguyên; `model_version` của job được ép kiểu — `"3"`/`"v3"` → 3 — và chỉ chọn được bản đang production, không có thì `skipped`, không thay bằng bản khác) `.checkpoint_path`/`.artifact_path` → `results/E2/phasegnn_long/checkpoint_seed0.pt` (đường dẫn tương đối gốc repo; `*.pt` bị gitignore). Mọi đường dẫn được `resolve()` và phải nằm dưới `<repo>/results` (đổi bằng `FORECAST_CHECKPOINT_DIR`), đuôi `.pt` — ngoài đó (tuyệt đối, `..`, symlink ra ngoài) bị từ chối; checkpoint nạp bằng `torch.load(weights_only=True)`. `phasegnn`/`torch` import lười (image không có torch → `skipped`). Dự báo cho ngày phát bản tin mới nhất có mẫu G5 ≤ `issued_at` (mặc định: mới nhất) — `run_date` là ngày của dữ liệu. Ghi `model_forecasts` mỗi (`run_date`, `station`, `lead_day`) một document (`_id = {run_date}:{station}:{lead_day}`, 2 đỉnh q10/q50/q90 trong `peaks`), upsert có fence (bản ghi có fence lớn hơn → từ chối, đếm `fence_rejected`), writeConcern majority; chạy lại cùng ngày ghi đè (idempotent). Validator + index: `schemas/mongo/model_forecasts/v1.json`, migration `0007`. `FORECAST_DATA_ROOT`: gốc dữ liệu G5/đồ thị nếu khác gốc repo.
- **Áp lại sửa metadata:** `ingest_bulletins`/`reload_bulletins` gọi `vngis.atomic_reload.apply_bulletin_overrides` trên staging trước khi kiểm tra (nguồn `audit_logs` `bulletin_metadata` `applied`; idempotent; nguồn đổi → chỉ báo `conflicts`).
- **Hàm job tự bỏ qua** (`{"status": "skipped", "reason": …}`) → `jobs.state="skipped"`, `reason`, `result`, `finished_at`; việc hàng đợi KHÔNG bị trả lại `queued` (khác bỏ qua do khoá bận).
- **Việc kẹt:** `claimed`/`running` có heartbeat/`claimed_at` cũ hơn 3 × TTL khoá → trả `queued` (`attempts += 1`); quá 3 lượt → `failed`; bản ghi cron kẹt → `lost`.
- Index `jobs` (`state, priority, created_at`; `state, scheduled_at`; `state, heartbeat`) tạo khi worker khởi động.

Ghi trạng thái `jobs` có điều kiện fencing (`fence` của bản ghi ≤ fence của lượt); instance bị bỏ qua chỉ thêm vào `skips`, không đổi `state`; heartbeat ghi mỗi `scheduler.heartbeat_s`; mất khoá → tiến trình con bị dừng, `state="lost"`. Tiến trình con nhận `MONGO_URI` (từ `vngis.config`) và `MONGO_DB` (DB worker đang dùng). Biến môi trường: `BULLETIN_SOURCE_DIR` (mặc định `data/raw/kttv`), `BULLETIN_WORK_DIR` (mặc định `data/interim/kttv/runs`; mỗi lượt một thư mục `<run_id>`), `BULLETIN_KEEP_RUN_FILES=1` (giữ file JSON của lượt), `BULLETIN_INGEST_TIMEOUT_S`.

## Cấu trúc

| Thành phần | Nội dung |
|---|---|
| `src/worker/scheduler.py` | `JobRunner`: khoá phân tán trước mỗi job, ghi `jobs` (state, worker, fence, heartbeat), watchdog, `state="skipped"` khi bản khác đang chạy |
| `src/worker/jobs.py` | định nghĩa job và hàm nhiệm vụ (nạp bản tin, nạp lại nguyên tử, dự báo, làm nóng cache, kiểm tra SHA-256) |
| `src/worker/forecast.py` | runner PhaseGNN: dò checkpoint, dự báo (import lười), gom và ghi `model_forecasts` có fence |
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

Không có `uv`/`phasegnn`: chạy trong `python:3.12-slim`, cài `./packages/vngis apscheduler mongomock fakeredis pytest`, rồi `python -m pytest` trong `apps/worker` (`pythonpath = src`). Test dự báo dùng mô hình giả (không cần torch); thử thật với checkpoint: image `phasegnn:local`, `PYTHONPATH=apps/worker/src:packages/vngis/src:packages/phasegnn/src`, gọi `worker.forecast.run_forecast` trên DB tạm.
