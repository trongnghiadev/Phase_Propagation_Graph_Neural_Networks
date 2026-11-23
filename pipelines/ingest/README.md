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
- Chuẩn hóa tên dùng `vngis.text.norm_name` (bản duy nhất). Script tự thêm `packages/vngis/src` vào `sys.path` nếu chưa cài `vngis`; cài thì dùng bản cài.
- `create_stations.py` đọc cấu hình bằng `vngis.config` (tôn trọng `APP_ENV`, `MONGO_URI`, `MONGO_DB`; tham số dòng lệnh ghi đè URI), nên cần các phụ thuộc của `vngis` (pymongo, pydantic, pyyaml, redis): tọa độ ước lượng + tỉnh của trạm đọc từ `config/stations.yaml` (thiếu khóa → dừng, báo rõ khóa nào); bán kính gán trạm, gốc cây MERIT, ngưỡng uparea, `admin.max_label_lag_days` từ `config/default.yaml` (chạy ngoài repo thì đặt `VNGIS_CONFIG_DIR`). Cần `province_timeline` đã có. Sửa tọa độ trong YAML rồi chạy lại, không sửa code.
- Tỉnh của hồ chứa (`stations.admin_history`, `stations.admin`): `vngis.province_labels`, thời kỳ theo **ngày quan trắc**; `admin_history[].from/to` là ngày quan trắc đầu/cuối của đoạn nhãn. Cùng quy tắc với `reservoir_daily.province` của job G0.
  - Hai nghĩa `era` khác nhau: `admin_history[].era` = thời kỳ theo **ngày quan trắc** của đoạn; `admin_history[].matched_era` (và `reservoir_daily.province.era`) = thời kỳ **đem tra mã**. Hai giá trị khác nhau ở đoạn "đổi tên chậm" (ví dụ Dầu Tiếng "Tây Ninh" 01–06/07/2025: `era = from_2025_07`, `matched_era = before_2025_07`, mã 72).
  - `admin_history[].provisional: true` (chỉ ghi khi đúng): đoạn sau mốc ở cuối dữ liệu, dùng lại nhãn trước mốc và còn trong `max_label_lag_days` → chưa biết có đổi tên chậm hay không; chạy lại khi có thêm bản tin.
  - Nhãn tỉnh rỗng: không làm đứt đoạn, được gom thành đoạn riêng `province_code = null`, `note = "nhãn tỉnh rỗng"`.
  - Bỏ bản tin chính trùng nội dung (cùng sha256) bằng `vngis.bulletins.duplicate_exclusions` – cùng hàm với job G0 – nên `bulletin_rows`, `alarm_history[].n_bulletins`, `admin_history[].n_bulletins` đếm trên cùng tập bản tin với các collection G0. Từ 23/11/2026 (K6: 4 file nằm sai thư mục ngày đặt `source.is_primary = false`, 5 bản `khac/` thay thế đặt `true`, có `audit_logs`) không còn cặp bản chính trùng sha256: danh sách bỏ rỗng, 3.595 bản tin (`bulletin_rows` của trạm mực nước = 3.595; trước đó 3.590 sau khi bỏ 4 bản trùng). Hàm vẫn chạy mỗi lần để chặn trùng nếu dữ liệu nguồn đổi.
  - Thiếu hoặc rỗng `province_timeline` → dừng với thông báo (không ghi `admin` rỗng).
- `create_admin_units.py` cũng đọc kết nối bằng `vngis.config` (APP_ENV, MONGO_URI, MONGO_DB; tham số dòng lệnh ghi đè URI), tên collection theo `mongo.collections`.
- Chạy trong container (không cài gì vào máy): `docker run --rm --network <mạng MongoDB> -e MONGO_URI=mongodb://mongo:27017 -v "<gốc repo>:/repo" -w /repo python:3.12-slim sh -c "pip -q install ./packages/vngis && python -X utf8 pipelines/ingest/stations/create_stations.py"`.
- Chi tiết field: `docs/data/du_lieu_kttv.md` (tài liệu private, submodule `docs/`).

## Việc còn lại
- `kttv_load_mongo.py` đang xóa rồi nạp lại collection: đổi sang nạp vào `__staging` rồi `renameCollection` (đề cương 5.6.4).
