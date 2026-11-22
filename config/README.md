# config/ – Cấu hình dùng chung

**Chức năng:** mọi tham số của hệ thống nằm ở đây, tách khỏi code. Đổi tham số thì sửa YAML, không sửa Python.

| Nội dung | Mô tả |
|---|---|
| `default.yaml` | Cấu hình mặc định: tên database, tên collection, mốc địa giới 01/07/2025, ngưỡng QC, bán kính gán trạm, TTL cache… Có `config_version` |
| `stations.yaml` | Tọa độ trạm/hồ/trạm mưa và tỉnh cũ/mới (hiện đang viết cứng trong `create_stations.py`, sẽ chuyển sang đây) |
| [`env/`](env/) | Ghi đè theo môi trường: `dev`, `test`, `cluster` |

**Quy tắc**
- Không chứa mật khẩu, token. Bí mật đặt trong biến môi trường (xem `.env.example` ở gốc).
- Cấu hình được kiểm tra bằng pydantic (`packages/vngis/config.py`) khi khởi động; sai thì dịch vụ dừng ngay.
- Tăng `config_version` khi đổi tên hoặc bỏ một khóa.
