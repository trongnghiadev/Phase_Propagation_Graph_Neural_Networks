# packages/vngis/ – Thư viện lõi

**Chức năng:** thư viện nhẹ dùng chung cho API, worker, pipeline Spark và script nạp dữ liệu. Không phụ thuộc torch hay Spark để image API luôn nhẹ.

| Module | Trạng thái | Nội dung |
|---|---|---|
| `locks.py` | **đã có** | Redlock trên N node (`Redlock.acquire/release/extend/ping`), `next_fence` (fencing token từ MongoDB), `parse_nodes`. Đang dùng ở `apps/api` |
| `text.py` | **đã có** | `norm_name` (bỏ dấu, bỏ tiền tố cấp hành chính), `slug` (mã không dấu), `strip_accents` |
| `config.py` | dự kiến | đọc `config/*.yaml` + biến môi trường, kiểm tra bằng pydantic |
| `db.py` | dự kiến | kết nối MongoDB (một node / replica set / mongos), Redis (Sentinel) |
| `admin.py` | dự kiến | hằng số thời kỳ (`before_2025_07`, `from_2025_07`), tra `province_timeline`, quy đổi tỉnh cũ ↔ mới (đang nằm trong `pipelines/ingest/stations/create_stations.py`) |
| `cache.py` | dự kiến | cache-aside, khóa cache theo phiên bản, chống stampede |
| `models/` | dự kiến | pydantic: `Station`, `TideExtreme`, `Forecast`… khớp `schemas/` |

Cấu trúc: `src/vngis/`, `tests/`, `pyproject.toml`. Phụ thuộc: pymongo, redis, pydantic, pyyaml.
