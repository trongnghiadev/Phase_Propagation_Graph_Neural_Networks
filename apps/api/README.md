# apps/api/ – REST API (FastAPI)

**Chức năng:** phục vụ dữ liệu, dự báo, bản tin, quản trị cho website (đề cương 5.4–5.8).

| Thành phần (dự kiến) | Nội dung |
|---|---|
| `src/api/main.py` | khởi tạo ứng dụng, middleware, `request_id` |
| `src/api/v1/` | router theo phiên bản: `bulletins`, `stations`, `tides`, `reservoirs`, `admin_units`, `forecasts`, `alerts`, `models`, `review`, `jobs`, `audit`, `users`, `admin` |
| `src/api/auth/` | JWT, argon2 |
| `src/api/permissions.py` | Bitmask 12 bit, vai trò, `require(flag, collection)` |
| `tests/` | ma trận quyền, cache, khóa, tích hợp |
| `Dockerfile` | image nhẹ, không có torch |

**Phiên bản API:** mọi đường dẫn có tiền tố `/api/v1`. Thay đổi phá vỡ thì tạo `/api/v2`, giữ v1 tới khi frontend chuyển xong. Ảnh chụp hợp đồng: `docs/api/openapi-v1.json`.

**Hiện trạng:** bản tối giản ở `src/api/main.py`: `/health/live`, `/health`, `/stations`, `/lock/{resource}` (chưa có tiền tố `/api/v1`). Khóa phân tán và fencing token dùng `vngis.locks`.

**Build:** từ gốc repo, vì image cần cả `packages/vngis`:

```powershell
docker build -f apps/api/Dockerfile -t gis-dist-api:local .
```
