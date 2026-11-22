# config/ – Cấu hình dùng chung

**Chức năng:** mọi tham số của hệ thống nằm ở đây, tách khỏi code. Đổi tham số thì sửa YAML, không sửa Python.

| Nội dung | Mô tả |
|---|---|
| `default.yaml` | Cấu hình mặc định (`config_version: 1`): MongoDB (URI, database `vietnam_gis`, tên collection), Redis cache/khóa, mốc địa giới `2025-06-30T17:00:00Z` (= 01/07/2025 00:00 giờ Việt Nam), giới hạn "đổi tên chậm" của nhãn tỉnh `admin.max_label_lag_days` (31 ngày), bán kính gán trạm 3000 m, gốc cây MERIT 44045960, ngưỡng uparea, TTL cache cơ bản, scheduler |
| `stations.yaml` | **Tọa độ ước lượng** (chưa chính thức) của trạm mực nước, hồ chứa, trạm mưa và tỉnh cũ/mới của trạm; đọc bởi `pipelines/ingest/stations/create_stations.py` qua `vngis.config.load_stations_catalog` (thiếu khóa, sai kiểu → dừng, báo theo đường dẫn khóa) |
| [`env/`](env/) | Ghi đè theo môi trường: `dev`, `test`, `cluster` |

**Cách đọc:** `vngis.config.get_config()` gộp `default.yaml` → `env/{APP_ENV}.yaml` → biến môi trường (`MONGO_URI`, `MONGO_DB`, `REDIS_*`, `LOCK_BACKEND`), xem `packages/vngis/README.md`. Script chạy ngoài repo đặt `VNGIS_CONFIG_DIR` trỏ tới thư mục này.

**Quy tắc**
- Không chứa mật khẩu, token. Bí mật đặt trong biến môi trường (xem `.env.example` ở gốc).
- Cấu hình được kiểm tra bằng pydantic (`packages/vngis/src/vngis/config.py`) khi khởi động; khóa lạ, thiếu khóa hoặc sai kiểu thì dịch vụ dừng ngay.
- Thêm khóa mới: khai báo trong `default.yaml` **và** mô hình pydantic trong `config.py`.
- Tăng `config_version` khi đổi tên hoặc bỏ một khóa.
- Sửa tọa độ trong `stations.yaml` rồi chạy lại `create_stations.py`; bản ghi đã xác nhận (`location_meta.method != "estimated"`) không bị ghi đè.
