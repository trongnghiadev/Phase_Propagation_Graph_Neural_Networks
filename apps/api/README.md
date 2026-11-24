# apps/api/ - REST API (FastAPI)

**Chức năng:** phục vụ dữ liệu, dự báo, bản tin, quản trị cho website (đề cương 5.3-5.8, GĐ7).

## Cấu trúc

| Thành phần | Nội dung |
|---|---|
| `src/api/main.py` | factory `create_app()`, middleware `request_id` + `INSTANCE`, handler lỗi thống nhất, endpoint `/health`, `/health/live`, `/api/v1/system/info` |
| `src/api/router/` | 14 router dưới tiền tố `/api/v1`: `auth`, `bulletins`, `stations`, `admin_units`, `reservoirs`, `stats`, `forecasts`, `alerts`, `features`, `review`, `jobs`, `audit`, `users`, `admin` |
| `src/api/auth/` | `password.py` (argon2id), `jwt.py` (access 15 phút / refresh 7 ngày), `blacklist.py` (chặn `jti`), `users.py` (kho `users`/`roles`, cập nhật quyền `$bit`), `seed.py` (8 vai trò mặc định) |
| `src/api/permissions.py` | Bitmask 12 bit (`IntFlag`), `require(flag, collection)`, cache quyền `perm:{user_id}` TTL 5 phút |
| `src/api/deps.py` | dependency: người dùng hiện tại, kiểm tra quyền, giới hạn tần suất (429) theo phạm vi |
| `src/api/state.py` | trạng thái ứng dụng: config, kết nối Mongo/Redis, khoá phân tán, tên instance |
| `tests/` | ma trận quyền, cache, khoá, endpoint (không cần dịch vụ ngoài) |
| `Dockerfile` | image nhẹ, không có torch |

## Phiên bản API

Mọi đường dẫn nghiệp vụ có tiền tố `/api/v1`. Thay đổi phá vỡ thì tạo `/api/v2`, giữ v1 tới khi frontend chuyển xong. Tài liệu OpenAPI tương tác: `/docs` (Swagger), `/redoc`, `/openapi.json`.

## Xác thực và phân quyền

- `POST /api/v1/login` trả `access_token` (15 phút) + `refresh_token` (7 ngày), JWT chứa `user_id` và `perm_ver`.
- `POST /api/v1/logout` chặn `jti` trong Redis; `POST /api/v1/refresh` cấp lại access token.
- Đổi quyền làm tăng `perm_ver` -> token cũ hết hiệu lực ngay (trả 401).
- Mọi endpoint đều gắn `require(flag, collection)`; `viewer` (mask 1) chỉ đọc, `admin` (mask 4095) đủ quyền.
- Quyền lưu trong `users.perm` dạng bitmask theo collection; cập nhật nguyên tử bằng toán tử `$bit` của MongoDB.

## Cache và khoá

- Cache Redis (cache-aside) do `vngis.cache` quản lý: khoá có phiên bản `ver:{coll}`, TTL kèm jitter, khoá `stale:` dự phòng, chống stampede bằng `lock:rebuild:{key}`.
- Khoá phân tán do `vngis.locks` quản lý: `LOCK_BACKEND=single|redlock`, watchdog gia hạn, fencing token từ MongoDB.
- PDF bản tin trả kèm `ETag` (SHA-256) + `Cache-Control`; PDF không nhét vào Redis.

## Chạy

```powershell
uvicorn api.main:app --host 0.0.0.0 --port 8000
```

## Build

Từ gốc repo, vì image cần cả `packages/vngis`:

```powershell
docker build -f apps/api/Dockerfile -t gis-dist-api:local .
```

## Test

```powershell
uv run --project apps/api pytest apps/api/tests -q
```
