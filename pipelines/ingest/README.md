# pipelines/ingest/ – Nạp dữ liệu nguồn

**Chức năng:** đưa dữ liệu nguồn vào MongoDB `vietnam_gis`, có kiểm tra và nạp nguyên tử.

| Thư mục | Nội dung | Collection tạo ra |
|---|---|---|
| `kttv/` | parse PDF bản tin, dữ liệu chép tay của 2 bản scan, ánh xạ sang schema tiếng Anh, nạp vào MongoDB | `hydromet_bulletins`, `hydromet_bulletin_files` |
| `admin_units/` | tải địa giới từ provinces.open-api.vn (`fetch_admin_units.py`), nạp có kiểm tra (`create_admin_units.py`) | `admin_units`, `admin_unit_mappings`, `province_timeline` |
| `stations/` | tạo danh mục trạm: tọa độ, gán reach MERIT, cấp báo động, tỉnh cũ/mới | `stations` |

## Chạy (từ gốc repo, theo thứ tự)

```powershell
python -X utf8 pipelines/ingest/kttv/kttv_parser.py data/raw/kttv data/interim/kttv/kttv_ban_tin_parsed.json
```
```powershell
python -X utf8 pipelines/ingest/kttv/kttv_load_mongo.py data/interim/kttv/kttv_ban_tin_parsed.json data/interim/kttv/hydromet_bulletins.json data/raw/kttv
```
```powershell
python -X utf8 pipelines/ingest/kttv/kttv_load_files.py data/raw/kttv
```
```powershell
python -X utf8 pipelines/ingest/admin_units/create_admin_units.py
```
```powershell
python -X utf8 pipelines/ingest/stations/create_stations.py
```

- URI MongoDB truyền làm tham số cuối (mặc định `mongodb://localhost:27017`); với replica set dùng `mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0`.
- `create_stations.py` cần `admin_units` đã có để gán tỉnh cũ/mới.
- Chi tiết field: `docs/data/du_lieu_kttv.md` (tài liệu private, submodule `docs/`).

## Việc còn lại
- Tọa độ trạm đang viết cứng trong `create_stations.py`: chuyển sang `config/stations.yaml`.
- Các script còn bản sao riêng của hàm chuẩn hóa tên; chuyển sang `vngis.text` khi dùng `uv sync`.
- `kttv_load_mongo.py` đang xóa rồi nạp lại collection: đổi sang nạp vào `__staging` rồi `renameCollection` (đề cương 5.6.4).
