# packages/vngis/ – Thư viện lõi

**Chức năng:** thư viện nhẹ dùng chung cho API, worker, pipeline Spark và script nạp dữ liệu. Không phụ thuộc torch hay Spark để image API luôn nhẹ.

| Module | Trạng thái | Nội dung |
|---|---|---|
| `locks.py` | **đã có** | Redlock trên N node (`Redlock.acquire/release/extend/ping`), `next_fence` (fencing token từ MongoDB), `parse_nodes`. Ngoài ra: backend `single|redlock` theo `LOCK_BACKEND`, script Lua `RELEASE`/`EXTEND`, watchdog gia hạn mỗi TTL/3 với cờ `lost`, `LockGuard.write` kiểm tra `last_fence < fence`. Đang dùng ở `apps/api` và `apps/worker` |
| `text.py` | **đã có** | `norm_name` (NFC, bỏ một tiền tố cấp hành chính, bỏ dấu, `-` → khoảng trắng, gộp khoảng trắng), `slug` (mã không dấu), `strip_accents`. Bản **duy nhất** trong repo: `pipelines/ingest/*` và job Spark đều import từ đây |
| `config.py` | **đã có** | `load_config` / `get_config`: gộp `config/default.yaml` + `config/env/{APP_ENV}.yaml` + biến môi trường, kiểm tra bằng pydantic v2; sai → `ConfigError` liệt kê từng khóa lỗi. `load_stations_catalog`: đọc và kiểm tra `config/stations.yaml` |
| `db.py` | **đã có** | `mongo_client` / `get_db` / `collection` (một node, replica set, mongos), `redis_cache` (một node hoặc master/replica qua Sentinel), `redis_sentinel`, `redlock`. Không kết nối khi import |
| `admin.py` | **đã có** | `BEFORE`, `FROM`, `EFFECTIVE_UTC`; `era_of(dt)`, `find_province(db, era, label)`, `province_at(db, dt)`, `successor_of(db, old_code)` trên `province_timeline` |
| `province_labels.py` | **đã có** | gán `{era, code}` cho nhãn tỉnh trong bản tin (hồ chứa): thời kỳ theo ngày quan trắc, quy tắc "đổi tên chậm" giới hạn `admin.max_label_lag_days`. Dùng chung cho `create_stations.py` và job G0 |
| `bulletins.py` | **đã có** | `duplicate_exclusions`: bản tin chính trùng sha256 → bản bị loại (G0 và `create_stations.py`) |
| `qc_rules.py` | **đã có** | QC bản tin, thuần Python: quy tắc R1–R3 (`r1_check`, `r2_check`, `r3_check`, `evaluate_group`), `DEFAULT_PARAMS` (= `config/default.yaml` mục `qc`), khóa cờ `flag_id`, `effective` (giá trị sau quyết định), `plan_updates` (gộp kết quả quy tắc vào `qc_flags`, không đụng field quyết định), validator/index của `qc_flags`, `audit_logs`. Dùng ở job Spark QC và G0 |
| `qc.py` | **đã có** | ghi quyết định QC có kiểm toán (pymongo): `decide` (`find_one_and_update` có điều kiện `version`, `VersionConflict`), `create_manual_flag`, `update_bulletin_fields` (metadata bản tin được phép, điều kiện giá trị cũ), `record_review`, `reconcile_pending`, `ensure_collections`. Dùng ở CLI `pipelines/qc/qc_decide.py`, API về sau |
| `cache.py` | **đã có** | cache-aside trên Redis (một node hoặc Sentinel): khoá có phiên bản `ver:{coll}`, TTL kèm jitter, khoá `stale:` dự phòng, chống stampede bằng `lock:rebuild:{key}`, write-through cho dự báo, con trỏ `fc:{station}:latest`, nén zstd + msgpack, đo hit/miss theo loại khoá |
| `atomic_reload.py` | **đã có** | nạp lại bản tin nguyên tử (ADR 0007): staging `hydromet_bulletins__staging_<run_id>` -> validator + index -> đối chiếu `hydromet_bulletin_files` (số bản tin, số bản chính, SHA-256) -> sao lưu `__prev` -> `renameCollection(..., dropTarget=True)` -> tăng `ver:hydromet_bulletins` -> chạy lại G0; bảo vệ bằng `lock:reload:bulletins` + watchdog |
| `model_registry.py` | **đã có** | quy ước collection `models`: `version` int cấp nguyên tử `allocate_model_version(db)` (bộ đếm `counters._id="models.version"`, `$max` theo bản đã có rồi `$inc`), `MODEL_STATUSES` (`staging|production|archived`), `model_doc_id`; `promote_model` (transaction khi có replica set, không thì thứ tự ghi theo `promote_seq` + tự nhường), `repair_production`, `find_production`/`PRODUCTION_SORT` (lượt promote mới nhất thắng), `PROMOTE_LOCK_RESOURCE="models:promote"`. Dùng ở `apps/api` (`POST /models/train`, `/promote`), `apps/worker` và `phasegnn.registry` |
| `models/` | dự kiến | pydantic: `Station`, `TideExtreme`, `Forecast`… khớp `schemas/` |

Cấu trúc: `src/vngis/`, `tests/`, `pyproject.toml`. Phụ thuộc: pymongo, redis, pydantic, pyyaml.

**Phần thuần Python** (chỉ thư viện chuẩn khi chạy): `text`, `admin`, `province_labels`, `bulletins`, `qc_rules` (danh sách `qc_rules.PURE_MODULES`; `pipelines/spark/common/pyfiles.ship_vngis` chỉ gửi các module này sang executor; test `test_module_thuan_khong_can_pymongo_pydantic_yaml` import chúng khi chặn pymongo/pydantic/yaml/redis). Job Spark (image không có pymongo/pydantic/pyyaml, Python 3.10) chỉ import các module này, qua `--py-files` hoặc thêm `packages/vngis/src` vào `sys.path` (`pipelines/spark/common/__init__.py`). Test `test_province_labels.py` kiểm tra điều này.

## Cấu hình (`vngis.config`)

```python
from vngis.config import get_config
cfg = get_config()              # đọc một lần cho cả tiến trình; get_config.cache_clear() để đọc lại
cfg.mongo.db, cfg.mongo.collections.stations, cfg.redis.lock.nodes, cfg.stations.snap_radius_m
```

- Thứ tự gộp: `default.yaml` → `env/{APP_ENV}.yaml` (APP_ENV mặc định `dev`, file phải tồn tại) → biến môi trường.
- Biến môi trường: `MONGO_URI`, `MONGO_DB`, `MONGO_MODE`, `REDIS_URL`, `REDIS_MODE`, `REDIS_SENTINELS`, `REDIS_SENTINEL_MASTER`, `REDIS_LOCK_NODES`, `LOCK_BACKEND`. Danh sách node viết `host:port,host:port`; đặt `REDIS_SENTINELS` thì Redis cache tự chuyển sang Sentinel. Biến rỗng bị bỏ qua.
- Thư mục cấu hình: `VNGIS_CONFIG_DIR`, nếu không thì đi ngược từ vị trí file `config.py`, rồi từ thư mục hiện hành, tìm `config/default.yaml`. Khi cài `vngis` vào image (không phải bản editable), đặt `VNGIS_CONFIG_DIR`.
- Khóa để trống trong file env (YAML `null`, ví dụ `redis:` không có khóa con) không xóa mapping mặc định; `null` đè giá trị thường (`write_concern: null`) vẫn có hiệu lực.
- Khóa lạ, thiếu khóa, sai kiểu, `config_version` khác 1, `mode: replica_set` mà URI không có `replicaSet=`, Sentinel không có node… đều là lỗi.

## Địa giới (`vngis.admin`)

```python
from vngis.admin import BEFORE, FROM, era_of, find_province, province_at, successor_of
era_of(bulletin_date)                     # naive = UTC; aware đổi về UTC; date = ngày lịch Việt Nam
find_province(db, BEFORE, "Tỉnh Long An")  # → {"era": "before_2025_07", "code": 80, ...}
province_at(db, datetime(2025, 1, 1))      # 63 tỉnh; từ 2025-06-30T17:00:00Z: 34 tỉnh
successor_of(db, 80)                       # tỉnh mới kế thừa Long An → Tây Ninh (mã 80)
```

Mọi truy vấn kèm `era` vì mã tỉnh có thể trùng giữa hai thời kỳ.

## Nhãn tỉnh trong bản tin (`vngis.province_labels`)

```python
from vngis.province_labels import assign_runs
runs = assign_runs([(observation_date, "Tây Ninh"), ...], timeline, max_lag_days=cfg.admin.max_label_lag_days)
# mỗi đoạn: label, first, last, n, obs_era, era, code, name, note, reason
```

1. Thời kỳ theo **ngày quan trắc** (như `era_of`); nhãn so theo `norm_name`.
2. Đoạn sau mốc dùng lại đúng nhãn của đoạn ngay trước mốc, và nhãn khác xuất hiện trong `max_lag_days` (mặc định 31) ngày kể từ mốc → tra thời kỳ cũ, ghi `note` ("đổi tên chậm"; ví dụ Dầu Tiếng "Tây Ninh" 01–06/07/2025 → 72 cũ, không phải 80 mới).
3. Ngoài giới hạn: tra thời kỳ mới; không có thì không khớp (không tự lùi về thời kỳ cũ).
4. Đoạn sau mốc ở cuối dữ liệu (chưa có nhãn mới phía sau) không bao giờ là đổi tên chậm; nếu dùng lại nhãn trước mốc và bản ghi cuối còn trong `max_lag_days` → `provisional = True` (chưa chắc).
5. Nhãn rỗng/`None` = không có thông tin: không làm đứt đoạn, không là đoạn trước/sau khi xét đổi tên chậm; gom thành đoạn riêng (mỗi thời kỳ một đoạn), không khớp, `reason = "nhãn tỉnh rỗng"`.

`era` của đoạn = thời kỳ đem tra; `obs_era` = thời kỳ theo ngày quan trắc.

`vngis.bulletins.duplicate_exclusions`: chọn bản tin bỏ đi khi nhiều bản tin chính trùng sha256 (dùng chung cho G0 và `create_stations.py`).

## Cache (`vngis.cache`)

Cache-aside tren Redis (mot node hoac Sentinel). Khoa co phien ban `ver:{coll}` de vo hieu hoa khi du lieu doi; TTL lay tu `config/default.yaml` muc `cache` co jitter; ban `stale:` de du phong khi nguon loi; `lock:rebuild:{key}` chong stampede; gia tri lon nen zstd + msgpack (co fallback JSON); dem hit/miss theo loai khoa. Write-through dung cho du bao, con tro `fc:{station}:latest` tro toi ban moi nhat. PDF khong nhet vao Redis: tra `ETag` (SHA-256) + `Cache-Control`.

## Nạp lại nguyên tử (`vngis.atomic_reload`)

`reload_bulletins_atomic` theo ADR 0007: tao collection staging `<coll>__staging_<run_id>`, gan validator + index, doi chieu voi `hydromet_bulletin_files` (so ban tin, so ban chinh, SHA-256), sao luu `<coll>__prev`, `renameCollection(..., dropTarget=True)`, tang `ver:hydromet_bulletins`, chay lai G0. Toan bo thao tac duoc bao ve bang `lock:reload:bulletins` co watchdog.

## QC (`vngis.qc_rules`, `vngis.qc`)

```python
from vngis.db import get_db
from vngis import qc
db = get_db()
f = qc.get_flag(db, qc.flag_id("BIEN_HOA", "high", ts))      # _id = "tide_extremes:BIEN_HOA:high:<ts UTC ISO>"
qc.decide(db, f["_id"], "rejected", reason="…", decided_by="…", expected_version=f["version"])
```

- `qc_flags`: một document cho mỗi phần tử bị gắn cờ, `_id` ổn định theo (collection, station, kind, ts); field
  `value` (giá trị gốc lúc gắn cờ), `decided_value` (giá trị quyết định đang gắn vào), `current_value` (giá trị gốc mới
  phát hiện khi lệch), `value_mismatch`, `rules` [{rule, detail, metric}], `origin` (`auto`/`manual`), `status`
  (`suspect`/`confirmed_ok`/`rejected`/`corrected`), `corrected_value`, `reason`, `decided_by`, `decided_at`,
  `source_file`, `first_flagged_run`, `last_seen_run`, `auto_cleared`, `rules_version`, `created_at`, `updated_at`,
  `version`, `retired`/`retired_at`/`retired_by`/`retired_reason` (cờ mồ côi). Validator: `corrected` ⇔ có
  `corrected_value` (nhánh `corrected` bắt buộc field này).
- Hiệu lực (`is_active`): không `retired`, và (có quyết định, hoặc cờ thủ công, hoặc suspect chưa `auto_cleared`).
- Job tự động chỉ sửa field của quy tắc; quyết định của người không bao giờ bị ghi đè. Quy tắc không còn kích hoạt:
  cờ `auto` chưa ai xem thì xóa, còn lại đặt `auto_cleared: true`; cờ `retired` không bị đụng.
- `audit_logs`: `at`, `actor`, `action` (`qc_decision`, `qc_flag_create`, `qc_flag_retire`, `bulletin_metadata`,
  `review_confirmed`), `collection`, `doc_id`, `target`, `before`/`after` (status, giá trị hiệu lực…; với bản tin là
  từng field), `reason`, `state`.
- Mọi lệnh ghi dùng chung `_audited`, write concern `majority`: replica set/mongos thì ghi dữ liệu và audit trong một
  transaction; một node thì audit `pending` → ghi có điều kiện → `applied` (điều kiện `{_id, state: "pending"}`,
  không khớp → `AuditStateError`). Chắc chắn không ghi được (điều kiện không khớp, trùng khóa) → xóa pending, ném
  `VersionConflict`/`FieldConflict`/`FlagExists`; lỗi mơ hồ (mạng, timeout) → giữ pending. `reconcile_pending(db,
  grace_s)` chỉ xử lý bản pending cũ hơn `grace_s` (`qc.audit_pending_grace_s`, 600 s) và so NỘI DUNG theo `action`.
- `update_bulletin_fields` kiểm tra kiểu: `source.is_primary` bool, `bulletin_date` datetime có múi giờ, `weekday`,
  `qc_note` chuỗi hoặc null (sai → `ValueError`).
- **Đường thoát cho cờ `value_mismatch`:** khi giá trị gốc đổi sau khi đã quyết định, G0/job QC gắn `value_mismatch:
  true` và `current_value`. Người xem **quyết định lại** (`qc.decide`): quyết định gắn vào `decided_value`, `value` giữ
  nguyên làm mốc lúc gắn cờ, `current_value` giữ lại làm giá trị đã thấy, `value_mismatch` đặt `false`. Job QC lần sau
  so giá trị gốc hiện tại với `decided_value`, nên khi G0 chưa kịp cập nhật `tide_extremes` thì không gắn lại mismatch
  vô hạn (cờ không mắc kẹt). Chi tiết: `pipelines/qc/README.md`.

## Test

```powershell
docker run --rm -v "<gốc repo>:/repo" -w /repo python:3.12-slim sh -c "pip -q install pytest ./packages/vngis && python -m pytest packages/vngis -q"
```

- Không cần Redis thật (`test_locks.py` dùng client giả).
- `test_qc.py` tạo database tạm `vngis_test_qc_<hex>` rồi xóa (không ghi vào `vietnam_gis`); gồm test xung đột version (6 luồng ghi cùng version, chỉ 1 thắng), lỗi mơ hồ giữ pending, `reconcile_pending` cho 3 loại action và ngưỡng grace, kiểu field bản tin, `retire`.
- Test đánh dấu `mongo` cần MongoDB có `province_timeline` (chỉ đọc); tự bỏ qua khi không kết nối được. Chọn server bằng `VNGIS_TEST_MONGO_URI` (mặc định `mongodb://localhost:27017`) và `VNGIS_TEST_MONGO_DB` (mặc định `vietnam_gis`); chạy trong container thì thêm `--network <mạng của MongoDB>` và `-e VNGIS_TEST_MONGO_URI=mongodb://<host>:27017`.
