# data/admin_units/ – Địa giới hành chính gốc

**Chức năng:** dữ liệu gốc (không sửa) dùng để nạp `admin_units`, `admin_unit_mappings`, `province_timeline`.

| File | Nguồn |
|---|---|
| `v1_depth3.json` | `/api/v1/?depth=3`: 63 tỉnh, 696 huyện, 10.051 xã (trước 01/07/2025) |
| `v2_depth2.json` | `/api/v2/?depth=2`: 34 tỉnh, 3.321 xã (từ 01/07/2025) |
| `v2_to_legacies.jsonl` | `/api/v2/w/{code}/to-legacies/`: xã mới → các xã cũ |
| `fetch_meta.json`, `load_report.json` | thời điểm tải, báo cáo kiểm tra khi nạp |

Nguồn: https://provinces.open-api.vn/ (tải 22/11/2026). sha256 ghi trong `data/manifest.yaml`.

Tải lại: `python -X utf8 pipelines/ingest/admin_units/fetch_admin_units.py` (có resume). Nạp vào MongoDB: `python -X utf8 pipelines/ingest/admin_units/create_admin_units.py [mongodb_uri]`.
