# apps/worker/ - Scheduler và job nền

**Chức năng:** chạy các job theo lịch hoặc theo yêu cầu từ API; 2-3 bản chạy song song, khoá phân tán đảm bảo mỗi lượt chỉ một bản chạy.

## Job

| Job | Khoá (tài nguyên) | `job_id` | Mô tả |
|---|---|---|---|
| `ingest_bulletins` | `lock:reload:bulletins` (chung với API) | `ingest_bulletins:{ngày VN}` | hằng ngày (`scheduler.ingest_hour`): `kttv_parser.py` → `kttv_load_mongo.py <parsed> <out> <raw> --staging --run-id <id>` (chỉ nạp staging) → worker kiểm tra staging, `guard.check()`, `vngis.atomic_reload.promote_staging` |
| `reload_bulletins` | `lock:reload:bulletins` (chung với API) | id việc | theo hàng đợi: nạp lại toàn bộ qua collection tạm + `renameCollection`; `actor` = `requested_by` |
| `forecast_run` | `lock:forecast_run` | `forecast_run:{ngày VN}` | hằng ngày 08:00 (`scheduler.forecast_hour/minute`) hoặc hàng đợi (`POST /forecasts/run`): PhaseGNN từ checkpoint → `model_forecasts` (`src/worker/forecast.py`); thiếu checkpoint/`phasegnn`/mẫu dữ liệu → `state="skipped"` + `reason` |
| `cache_warm` | `lock:cache_warm` | `cache_warm` (một bản ghi) | mỗi 10 phút |
| `train` | `lock:train` (một lượt huấn luyện mỗi lúc) | `train:<n>` (giao lại: `train:<n>:<lượt>`) | hàng đợi (`POST /models/train`), bộ quét riêng `train_queue` mỗi 30 s: `python -m phasegnn.train` → `results/models/phasegnn-<n>/attempt-<fence>/`, cập nhật `models[phasegnn-<n>]` (`src/worker/train.py`) |
| hàng đợi | theo tài nguyên của loại việc | id việc | quét `jobs` `state="queued"` mỗi 15 s (trừ `train`), nhận nguyên tử (`claimed`), khoá bận → trả lại `queued`; loại chưa hỗ trợ (`build_features`, `qc_review`) → `failed` |

- **Tham số hàng đợi:** danh sách trắng `vngis.job_params` (API trả 422, worker đánh `failed`); không bao giờ nhận `repo_root`, `source_dir`, `documents`, `actor`, `run_g0`, `run_id` từ hàng đợi.
- **Loại trừ:** mọi tác vụ ghi `hydromet_bulletins` dùng chung tài nguyên `reload:bulletins` (khoá Redis `lock:reload:bulletins`, bộ đếm fence `lock_fences["reload:bulletins"]`, giống API). Cron gặp khoá bận → hẹn chạy lại sau 5 phút (tối đa 12 lần).
- **`__prev`:** một chính sách cho API/worker/loader (`promote_staging`): còn `__prev` sót → từ chối; promote thành công + kiểm tra số bản tin → xoá `__prev`; rename với writeConcern majority.
- **Dự báo (`forecast_run`):** checkpoint dò theo thứ tự `FORECAST_CHECKPOINT` → `models` theo registry `phasegnn/registry.py` (`status="production"`, `version` số nguyên; `model_version` của job được ép kiểu — `"3"`/`"v3"` → 3 — và chỉ chọn được bản đang production, không có thì `skipped`, không thay bằng bản khác) `.checkpoint_path`/`.artifact_path` → mặc định `results/E2/phasegnn_spread_v2/checkpoint_seed0.pt` (PhaseGNN mẫu `spread` 9 trạm, đồ thị con v2, seed có val MAE đỉnh thấp nhất 0.149 m; trước 25/11/2026 là `phasegnn_long` — chỉ học BEN_LUC) (đường dẫn tương đối gốc repo; `*.pt` bị gitignore). Mọi đường dẫn được `resolve()` và phải nằm dưới `<repo>/results` (đổi bằng `FORECAST_CHECKPOINT_DIR`), đuôi `.pt` — ngoài đó (tuyệt đối, `..`, symlink ra ngoài) bị từ chối; checkpoint nạp bằng `torch.load(weights_only=True)`. `phasegnn`/`torch` import lười (image không có torch → `skipped`). Dự báo cho ngày phát bản tin mới nhất có mẫu G5 ≤ `issued_at` (mặc định: mới nhất) — `run_date` là ngày của dữ liệu. Ghi `model_forecasts` mỗi (`run_date`, `station`, `lead_day`) một document (`_id = {run_date}:{station}:{lead_day}`, 2 đỉnh q10/q50/q90 trong `peaks`), upsert có fence (bản ghi có fence lớn hơn → từ chối, đếm `fence_rejected`), writeConcern majority; chạy lại cùng ngày ghi đè (idempotent). Validator + index: `schemas/mongo/model_forecasts/v1.json`, migration `0007`. `FORECAST_DATA_ROOT`: gốc dữ liệu G5/đồ thị nếu khác gốc repo.
- **Quy ước `lead_day` (đã đối chiếu, không lệch):** `lead_day = target_date − run_date + 1`, tức `lead_day=1` có `target_date = run_date` (ngày phát bản tin là ngày dự báo thứ 1). Khớp `phasegnn.data.build_samples` (nhãn `issue … issue+T_out−1`, "ngày phát bản tin = ngày dự báo thứ 1"), `phasegnn.forecast.predict_split` (`target = issue + d`, `lead_day = d + 1`) và `official_forecasts` của KTTV (`lead_day = target_date − issued_date + 1`, ngày phát = 1). Worker lấy `target_date` nguyên từ bản ghi của `phasegnn` (`group_records`), `run_date` = `issue_date` của mẫu; test `test_lead_day_1_la_ngay_phat_ban_tin` giữ quy ước.
- **Huấn luyện (`train`):** tham số chỉ từ `vngis.job_params.TrainParams` (`epochs` 1–500, `learning_rate`, `max_train_samples` 100–50000, `max_val_samples` 50–50000, `subsample` `spread`|`head` — mặc định `spread`; `station`/`basin` chưa được trình huấn luyện hỗ trợ → `result.params_ignored`); dòng lệnh `--variant phasegnn --epochs --lr --seed 0 --out-dir results/models/phasegnn-<n>/attempt-<fence> --no-mlflow --model-version <n> --train-fence <fence>` + `--max-train-samples`/`--max-val-samples`/`--subsample` chỉ khi có giới hạn mẫu (tiến trình con chỉ cập nhật đúng bản ghi, có điều kiện fence). Bỏ trống số mẫu = toàn bộ (~19,6 nghìn mẫu train, ~25 phút/epoch trên CPU); `vngis.job_params.estimate_train_seconds` ước lượng (hệ số `PGNN_TRAIN_SEC_PER_SAMPLE`), API trả 422 `train_exceeds_timeout` nếu ngay cả khi dừng sớm (`patience`+1 epoch) vẫn vượt `TRAIN_TIMEOUT_S`, chỉ cảnh báo (`warning`) nếu đủ `epochs` mới vượt; worker ghi `result.estimate`. **Mỗi lượt một thư mục** `attempt-<fence>/`: tiến trình con mồ côi (worker chết khi chạy ngoài container) không ghi đè checkpoint của lượt mới; chỉ lượt thắng (ghi `models` có điều kiện fence thành công) đặt `checkpoint_path` vào thư mục của nó rồi xoá `attempt-<k>` với `k < fence` (`result.removed_attempts`); lượt thua tự xoá thư mục của mình. Tiến trình con chạy nhóm tiến trình riêng, `worker.jobs.kill_children` (`atexit` + khi nhận SIGINT/SIGTERM) dừng cả nhóm — best effort, SIGKILL worker thì chỉ còn fence + thư mục riêng bảo vệ. Dừng có trật tự (SIGTERM/SIGINT): tiến trình con bị dừng ⇒ `WorkerShutdown` (không ghi `failed` mã -9), việc → `interrupted` → `queued` **không tăng** `attempts` (`reclaims[].reason="worker_shutdown"`), `models.train_state` về `queued` có fence (`train_reason="worker_shutdown"`). Việc `train` cũ còn trong hàng đợi có tham số ngoài danh sách trắng hiện hành (ví dụ `epochs` > 500) → `failed` có lý do rõ + `models.train_state="failed"`. `estimate_train_seconds` tính theo thiết bị (`cuda` ≈ CPU/4, `PGNN_TRAIN_SEC_PER_SAMPLE_CUDA`). `models.train_state`: `queued` → `running` → `succeeded` (`checkpoint_path` tương đối dưới `results/`, kiểm tra như `forecast_run`; `metrics` val) / `failed` (`train_error`) / `skipped` (image không có `phasegnn`/`torch`, `train_reason`); mọi lệnh ghi có điều kiện `train_fence` ≤ fence của lượt; bản đã `succeeded` → bỏ qua. Heartbeat `models.train_heartbeat` mỗi `TRAIN_HEARTBEAT_S` (30); mất khoá → dừng tiến trình con, không ghi thêm, việc `lost` được scheduler trả về `queued` ngay (`attempts += 1`, tối đa `max_attempts` = 3 lượt) và lượt mới (fence lớn hơn) chuyển `train_state` `running → running` rồi ghi đè; hết lượt → việc `failed` và `worker.train.abandon_training` đánh `models.train_state="failed"` (chỉ khi `models.job_id` còn trỏ tới việc đó). Bản `failed`/`skipped`/`running` kẹt giao lại qua `POST /models/train` với `model_version=n` (việc mới `train:<n>:<lượt>`). `TRAIN_TIMEOUT_S` (6 h), `TRAIN_DATA_ROOT`/`FORECAST_DATA_ROOT` (gốc dữ liệu). `status` giữ `staging` — promote là thao tác riêng. Bản production: `vngis.model_registry.PRODUCTION_SORT` (lượt promote mới nhất).
- **Thiết bị (GPU):** việc `train` luôn truyền `--device`: `TrainParams.device` (`auto`|`cpu`|`cuda`) → `PGNN_TRAIN_DEVICE` → `auto` (giá trị lạ ⇒ lỗi trước mọi lệnh ghi); ghi vào `models.train_params.device`. Dự báo độc lập qua `PGNN_FORECAST_DEVICE` (mặc định `cpu`; dùng `auto` hoặc `cuda`). Worker GPU là dịch vụ compose `worker-gpu` (profile `gpu`, image `phasegnn:cuda`). Build và bật cùng HA:
  ```powershell
  docker build -f packages/phasegnn/Dockerfile.cuda -t phasegnn:cuda .
  docker compose -f infra/compose/docker-compose.yml --profile ha --profile gpu up -d
  ```
  Chỉ bật profile `gpu` sẽ thiếu MongoDB/Redis của profile `ha`; cần NVIDIA driver/WSL2 và `--gpus all` khi chạy container kiểm tra. Xem thêm hợp đồng API tại [apps/api/README.md](../api/README.md).
- **Áp lại sửa metadata:** `ingest_bulletins`/`reload_bulletins` gọi `vngis.atomic_reload.apply_bulletin_overrides` trên staging trước khi kiểm tra (nguồn `audit_logs` `bulletin_metadata` `applied`; idempotent; nguồn đổi → chỉ báo `conflicts`).
- **Hàm job tự bỏ qua** (`{"status": "skipped", "reason": …}`) → `jobs.state="skipped"`, `reason`, `result`, `finished_at`; việc hàng đợi KHÔNG bị trả lại `queued` (khác bỏ qua do khoá bận).
- **Việc kẹt / mất khoá:** `claimed`/`running` có heartbeat/`claimed_at` cũ hơn 3 × TTL khoá → trả `queued` (`attempts += 1`); việc hàng đợi mất khoá giữa chừng (`lost`) → trả `queued` ngay (`Scheduler.requeue_lost`, cùng giới hạn); việc `train` `lost` sót lại (worker chết trước khi trả về) cũ hơn 3 × TTL → `reclaim_stale` trả `queued` (loại khác `lost` cũ không bị chạy lại); quá 3 lượt → `failed` + `Scheduler.abandon_hooks[type]`; bản ghi cron kẹt → `lost`.
- Index `jobs` (`state, priority, created_at`; `state, scheduled_at`; `state, heartbeat`) tạo khi worker khởi động.

Ghi trạng thái `jobs` có điều kiện fencing (`fence` của bản ghi ≤ fence của lượt); instance bị bỏ qua chỉ thêm vào `skips`, không đổi `state`; heartbeat ghi mỗi `scheduler.heartbeat_s`; mất khoá → tiến trình con bị dừng, `state="lost"`. Tiến trình con nhận `MONGO_URI` (từ `vngis.config`) và `MONGO_DB` (DB worker đang dùng). Biến môi trường: `BULLETIN_SOURCE_DIR` (mặc định `data/raw/kttv`), `BULLETIN_WORK_DIR` (mặc định `data/interim/kttv/runs`; mỗi lượt một thư mục `<run_id>`), `BULLETIN_KEEP_RUN_FILES=1` (giữ file JSON của lượt), `BULLETIN_INGEST_TIMEOUT_S`.

## Cấu trúc

| Thành phần | Nội dung |
|---|---|
| `src/worker/scheduler.py` | `JobRunner`: khoá phân tán trước mỗi job, ghi `jobs` (state, worker, fence, heartbeat), watchdog, `state="skipped"` khi bản khác đang chạy |
| `src/worker/jobs.py` | định nghĩa job và hàm nhiệm vụ (nạp bản tin, nạp lại nguyên tử, dự báo, làm nóng cache, kiểm tra SHA-256) |
| `src/worker/forecast.py` | runner PhaseGNN: dò checkpoint, dự báo (import lười), gom và ghi `model_forecasts` có fence |
| `src/worker/train.py` | việc `train`: tiến trình con `phasegnn.train`, cập nhật `models.train_state` có fence, heartbeat |
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
