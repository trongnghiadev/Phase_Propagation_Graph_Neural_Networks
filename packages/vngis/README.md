# packages/vngis/ – Thư viện lõi

**Chức năng:** thư viện nhẹ dùng chung cho API, worker, pipeline Spark và script nạp dữ liệu. Không phụ thuộc torch hay Spark để image API luôn nhẹ.

| Module | Trạng thái | Nội dung |
|---|---|---|
| `locks.py` | **đã có** | Redlock trên N node (`Redlock.acquire/release/extend/ping`), `next_fence` (fencing token từ MongoDB), `parse_nodes`. Đang dùng ở `apps/api` |
| `text.py` | **đã có** | `norm_name` (NFC, bỏ một tiền tố cấp hành chính, bỏ dấu, `-` → khoảng trắng, gộp khoảng trắng), `slug` (mã không dấu), `strip_accents`. Bản **duy nhất** trong repo: `pipelines/ingest/*` và job Spark đều import từ đây |
| `config.py` | **đã có** | `load_config` / `get_config`: gộp `config/default.yaml` + `config/env/{APP_ENV}.yaml` + biến môi trường, kiểm tra bằng pydantic v2; sai → `ConfigError` liệt kê từng khóa lỗi. `load_stations_catalog`: đọc và kiểm tra `config/stations.yaml` |
| `db.py` | **đã có** | `mongo_client` / `get_db` / `collection` (một node, replica set, mongos), `redis_cache` (một node hoặc master/replica qua Sentinel), `redis_sentinel`, `redlock`. Không kết nối khi import |
| `admin.py` | **đã có** | `BEFORE`, `FROM`, `EFFECTIVE_UTC`; `era_of(dt)`, `find_province(db, era, label)`, `province_at(db, dt)`, `successor_of(db, old_code)` trên `province_timeline` |
| `province_labels.py` | **đã có** | gán `{era, code}` cho nhãn tỉnh trong bản tin (hồ chứa): thời kỳ theo ngày quan trắc, quy tắc "đổi tên chậm" giới hạn `admin.max_label_lag_days`. Dùng chung cho `create_stations.py` và job G0 |
| `bulletins.py` | **đã có** | `duplicate_exclusions`: bản tin chính trùng sha256 → bản bị loại (G0 và `create_stations.py`) |
| `cache.py` | dự kiến | cache-aside, khóa cache theo phiên bản, chống stampede (TTL đã có trong `config/default.yaml`, mục `cache`) |
| `models/` | dự kiến | pydantic: `Station`, `TideExtreme`, `Forecast`… khớp `schemas/` |

Cấu trúc: `src/vngis/`, `tests/`, `pyproject.toml`. Phụ thuộc: pymongo, redis, pydantic, pyyaml.

**Phần thuần Python** (chỉ thư viện chuẩn khi chạy): `text`, `admin`, `province_labels`, `bulletins`. Job Spark (image không có pymongo/pydantic/pyyaml, Python 3.10) chỉ import các module này, qua `--py-files` hoặc thêm `packages/vngis/src` vào `sys.path` (`pipelines/spark/common/__init__.py`). Test `test_province_labels.py` kiểm tra điều này.

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

## Test

```powershell
docker run --rm -v "<gốc repo>:/repo" -w /repo python:3.12-slim sh -c "pip -q install pytest ./packages/vngis && python -m pytest packages/vngis -q"
```

- Không cần Redis thật (`test_locks.py` dùng client giả).
- Test đánh dấu `mongo` cần MongoDB có `province_timeline` (chỉ đọc); tự bỏ qua khi không kết nối được. Chọn server bằng `VNGIS_TEST_MONGO_URI` (mặc định `mongodb://localhost:27017`) và `VNGIS_TEST_MONGO_DB` (mặc định `vietnam_gis`); chạy trong container thì thêm `--network <mạng của MongoDB>` và `-e VNGIS_TEST_MONGO_URI=mongodb://<host>:27017`.
