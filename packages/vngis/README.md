# packages/vngis/ – Thư viện lõi

**Chức năng:** thư viện nhẹ dùng chung cho API, worker, pipeline Spark và script nạp dữ liệu. Không phụ thuộc torch hay Spark để image API luôn nhẹ.

| Module (dự kiến) | Nội dung | Lấy từ code hiện có |
|---|---|---|
| `config.py` | đọc `config/*.yaml` + biến môi trường, kiểm tra bằng pydantic | — |
| `db.py` | kết nối MongoDB (một node / replica set / mongos), Redis (Sentinel) | `backend/app/main.py` |
| `text.py` | chuẩn hóa tên tiếng Việt (bỏ dấu, bỏ tiền tố cấp hành chính), tạo mã không dấu | `etl/create_admin_units.py`, `etl/create_stations.py` |
| `admin.py` | hằng số thời kỳ (`before_2025_07`, `from_2025_07`), tra `province_timeline`, quy đổi tỉnh cũ ↔ mới | `etl/create_stations.py` |
| `locks.py` | khóa một node, Redlock, fencing token, watchdog | `backend/app/main.py` |
| `cache.py` | cache-aside, khóa cache theo phiên bản, chống stampede | — |
| `models/` | pydantic: `Station`, `TideExtreme`, `Forecast`… khớp `schemas/` | — |

Cấu trúc: `src/vngis/`, `tests/`, `pyproject.toml`.
