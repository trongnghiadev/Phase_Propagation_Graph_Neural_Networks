# pipelines/ingest/ – Nạp dữ liệu nguồn

**Chức năng:** đưa dữ liệu nguồn vào MongoDB `vietnam_gis`, có kiểm tra và nạp nguyên tử.

| Thư mục (dự kiến) | Nội dung | Code hiện có |
|---|---|---|
| `kttv/` | parse PDF bản tin, chép tay bản scan, ánh xạ sang schema tiếng Anh, nạp `hydromet_bulletins` và `hydromet_bulletin_files` | `datamongo/kttv_*.py` |
| `admin_units/` | tải địa giới từ provinces.open-api.vn, nạp `admin_units`, `admin_unit_mappings`, `province_timeline` | `bigdatanckh/etl/fetch_admin_units.py`, `create_admin_units.py` |
| `stations/` | tạo `stations`: tọa độ (từ `config/stations.yaml`), gán MERIT, cấp báo động, tỉnh cũ/mới | `bigdatanckh/etl/create_stations.py` |

Thứ tự: `kttv` → `admin_units` → `stations`.
