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

## Tọa độ trạm

MongoDB lưu `stations.location` theo **GeoJSON Point** (`{"type": "Point", "coordinates": [lon, lat]}`) — bắt buộc cho index `location_2dsphere`. API **chuẩn hoá khi trả về client**: `GET /stations`, `GET /stations/{code}` và `GET /trace/{comid}` trả `location` là mảng `[lon, lat]` (`null` khi trạm chưa có tọa độ); `GET /forecasts/map` dựng `geometry` GeoJSON đúng chuẩn. `PATCH /stations/{code}` nhận `location` dạng mảng `[lon, lat]` rồi ghi lại thành GeoJSON Point (422 nếu sai định dạng). Hàm dùng chung: `api.router.common.to_lonlat`, `to_point`, `clean_station`.

## Xác thực và phân quyền

- `POST /api/v1/auth/login` trả `access_token` (15 phút) + `refresh_token` (7 ngày), JWT chứa `user_id` và `perm_ver`.
  Không tiết lộ tài khoản nào tồn tại: luôn chạy argon2 (hash giả nếu không có tài khoản); sai tên/sai mật khẩu, kể cả tài khoản đang khoá hoặc vô hiệu mà mật khẩu sai, đều trả cùng 401. Chỉ khi mật khẩu đúng mới trả 423 (đang khoá) hoặc 403 (vô hiệu).
- `POST /api/v1/auth/logout` chặn `jti` trong Redis; `POST /api/v1/auth/refresh` cấp cặp token mới. Refresh token dùng **một lần**, tiêu thụ nguyên tử bằng `SET refresh_used:{jti} NX EX`; dùng lại một refresh token đã tiêu thụ thì mọi phiên của tài khoản bị thu hồi (tăng `perm_ver`) và trả 401.
- **Trình duyệt (cookie):** request có header `X-Requested-With` (`auth.csrf_header`) thì login/refresh đặt refresh token vào cookie `pgnn_refresh` `HttpOnly; SameSite=Strict; Secure` (tắt `Secure` khi dev http bằng `auth.cookie_secure: false` / `AUTH_COOKIE_SECURE=false`), mỗi `Path` trong `auth.cookie_paths` (`/api/v1/auth/refresh`, `/api/v1/auth/logout`) một cookie, và **không** trả `refresh_token` trong JSON. Refresh/logout không có body thì đọc cookie; khi đó chống CSRF: bắt buộc header tuỳ biến, `Sec-Fetch-Site: cross-site` bị từ chối, `Origin` (nếu có) phải nằm trong `auth.cors_origins` hoặc trùng host của request (`Host`; `X-Forwarded-Host` chỉ khi request đến từ proxy trong `TRUSTED_PROXIES`) → sai trả 403. Logout xoá cookie và blacklist refresh token, không bắt buộc access token còn hạn.
- **Client khác** (script, test): không gửi header đó → vẫn nhận/gửi `refresh_token` trong JSON body như trước.
- **CORS:** chỉ bật khi `auth.cors_origins` có origin cụ thể (`*` bị từ chối khi đọc cấu hình), `allow_credentials=true`. Rỗng (web cùng origin qua Nginx/proxy dev) = không CORS.
- Đổi quyền làm tăng `perm_ver` -> token cũ hết hiệu lực ngay (trả 401).
- **Người thực hiện trong nhật ký:** `audit_logs.actor`, `reverted_by`, `qc_flags.decided_by`, `jobs.requested_by`, `alerts.ack_by`, `models.promoted_by` ghi `user:<account_code>:<role>` — `account_code` (`acc_<12 hex>`, ngẫu nhiên, lưu ở `users`, index unique partial `account_code_unique` tạo khi API khởi động / migration `0008`) không suy ra được từ tên; tài khoản cũ (thiếu mã hoặc `null`) được cấp mã ở request đầu. Không ghi tên đăng nhập; không cấp được mã → thao tác ghi trả 503 (không dự phòng bằng `user_id`). Đổi quyền (`user_perm`) ghi người bị tác động bằng `account_code`. Khi đọc, API gắn nhãn (tra `users`, không lưu): `GET /audit` `actor_label`/`reverted_by_label`, `GET /jobs` `requested_by_label`, `GET /alerts` `ack_by_label`, `GET /review` và `PATCH /tides` `decided_by_label`, `GET /stations/{code}/tides` `flag_decided_by_label`, `POST /models/{version}/promote` `promoted_by_label`. `?actor=` nhận tên đăng nhập (khớp cả bản ghi cũ ghi tên), `user:<account_code>` (mọi vai trò) hoặc id đầy đủ. Bản ghi cũ còn tên đăng nhập: chạy migration `0009_anonymize_actors` (đổi sang `user:<account_code>`, xem `migrations/mongo/README.md`).
- **Mô hình và dự báo mẫu:** `POST /models/train` cấp `version` số nguyên tăng dần nguyên tử (`vngis.model_registry`: bộ đếm `counters._id="models.version"`, không trùng `version` đã có), ghi `models` `{_id: "phasegnn-<version>", status: "staging", train_state: "queued", job_id}` và giao việc `train:<version>` (chèn `jobs` lỗi → bản ghi `models` đánh `failed` + `train_error`, trả 503). **BREAKING:** `model_version` do client tự đặt không còn tạo phiên bản (chưa có → 404, không phải số → 422) và phản hồi `model_version` là **số nguyên** (trước: chuỗi `v<timestamp>`; không giữ trường chuỗi tương thích). `model_version=n` của bản đã có: `train_state` `failed`/`skipped`/`running` kẹt (không còn việc `train` hiệu lực, `train_heartbeat` cũ hơn `PGNN_TRAIN_STALE_S`, mặc định 2700 s) → **giao lại** việc mới `train:<n>:<lượt>` (`models.train_attempt` tăng nguyên tử; tham số = `hyperparams` cũ + trường gửi kèm); còn lại → trả bản ghi hiện có với `created: false`. Index unique `models.version` (partial, chỉ giá trị số) tạo idempotent khi khởi động (`vngis.model_registry.ensure_model_indexes`, giống migration `0010`). `POST /models/{version}/promote` theo registry `phasegnn/registry.py` — `version` số nguyên (`3`/`v3`; không phải số → 422), đặt `status="production"`, bản production khác → `archived` (worker `forecast_run` chỉ chọn bản production, lượt promote mới nhất theo `PRODUCTION_SORT`); giữ khoá `models:promote` (bận → 423), ghi qua `vngis.model_registry.promote_model` (transaction khi có replica set, không thì thứ tự ghi theo `promote_seq` — không để 2 bản production tồn tại lâu, lượt cũ bị lượt mới hơn thắng → 409 `promote_superseded`, trừ khi lượt thắng promote chính bản đó → thành công); bản có `train_state` khác `succeeded` → 409 `model_not_trained` (kiểm tra trước và cả trong điều kiện của lệnh ghi đặt production). Khi chưa có dự báo của mô hình, endpoint đọc chỉ trả dự báo mẫu từ tệp dự báo tập test đúng cột schema dự báo (`PGNN_RESULTS_DIR`, mặc định `results/`: `E2/phasegnn_spread_v2/test_full_seed0/predictions_test.csv` (cùng mô hình với checkpoint mặc định của worker) → `E2/phasegnn_long/test_full/predictions_test.csv` → `E2/*/test_full/predictions_test.csv` → `baselines/*/predictions_test.csv`; tệp bị gitignore, chỉ có trên máy đã chạy thực nghiệm); không có tệp hợp lệ hoặc không có trạm yêu cầu → 503.
- Mọi endpoint đều gắn `require(flag, collection)`; `viewer` (mask 1) chỉ đọc, `admin` (mask 4095) đủ quyền.
- Quyền lưu trong `users.perm` dạng bitmask theo collection. `PUT /api/v1/users/{id}/perm` (body `grant`, `revoke`, `collections`, `perm_ver`, `reason` bắt buộc) ghi mọi thay đổi bằng **một** lệnh `find_one_and_update` có điều kiện `perm_ver` (409 nếu lệch), kiểm tra tên collection (không `.`/`$`, phải nằm trong danh sách đã biết, sai → 422) và ghi `audit_logs` (`action: user_perm`).

### Biến môi trường bảo mật

| Biến | Ý nghĩa |
|---|---|
| `AUTH_SECRET` | Khoá ký JWT (HS256). Tên biến lấy từ `auth.secret_env` (mặc định `AUTH_SECRET`; không dùng `JWT_SECRET`). `APP_ENV` là `dev`/`test`: thiếu thì dùng khoá tạm theo tiến trình (cảnh báo). Môi trường khác (ví dụ `cluster`): thiếu khoá hoặc khoá < 32 byte thì API **dừng khởi động** với thông báo rõ. Sinh khoá: `python -c "import secrets; print(secrets.token_urlsafe(48))"`. |
| `AUTH_COOKIE_SECURE` | `true`/`false` → `auth.cookie_secure` (cờ `Secure` của cookie refresh). Mặc định `true`; `config/env/dev.yaml` đặt `false` cho http://localhost. |
| `CORS_ORIGINS` | Danh sách origin (phẩy) → `auth.cors_origins`, ví dụ `http://localhost:3000`. Dùng cho CORS có credentials và kiểm tra `Origin`. |
| `TRUSTED_PROXIES` | IP/CIDR của proxy tin cậy (ví dụ Nginx), cách nhau bằng dấu phẩy (`rate_limit.trusted_proxies`). Chỉ khi kết nối đến từ các địa chỉ này API mới đọc `X-Forwarded-For` (lấy IP không tin cậy ngoài cùng bên phải). Mặc định rỗng: không tin `X-Forwarded-For`, dùng IP kết nối. |

### Giới hạn tần suất

- Khoá `rl:{scope}:{ip}:{user}`; `INCR`, chỉ đặt `EXPIRE` khi `INCR` trả 1 (cửa sổ cố định, không bị kéo dài khi gọi liên tục); key mất TTL được đặt lại khi phát hiện.
- **Redis lỗi:** scope `login` chọn **fail-closed**: trả 503 kèm `Retry-After` (không cho dò mật khẩu không giới hạn khi Redis chết; không dùng bộ đếm cục bộ vì mỗi instance đếm riêng sẽ nhân ngưỡng lên theo số instance). Các scope khác cho qua kèm log cảnh báo.
- Blacklist token: Redis lỗi khi kiểm tra `jti` thì trả 503 (không 500, và không cho qua token có thể đã đăng xuất).

### QC, hoàn tác và khoá ghi

- `POST /api/v1/review/{flag_id}/approve` (APPROVE trên `qc_flags`; body `status` ∈ `confirmed_ok|rejected|corrected|suspect`, `corrected_value`, `reason`, `version`) gọi `vngis.qc.decide` có `expected_version`. Không nhận tên collection từ client.
- `PATCH /api/v1/tides/{station}/{ts}` (UPDATE trên `tide_extremes`; body `status` ∈ `rejected|corrected|confirmed_ok`, `corrected_value`, `kind`, `reason`, `version`) tạo cờ thủ công nếu chưa có (`vngis.qc.create_manual_flag`) rồi `decide`. API **không** ghi `tide_extremes` và không ghi đè `value` của cờ: G0 áp quyết định ở lần chạy sau. Cờ đã có mà thiếu `version` → 428.
- `GET /api/v1/stations/{code}/tides` trả kèm mỗi điểm `flag_id`, `flag_version`, `flag_status`, `flag_retired` (đọc `qc_flags` bằng một truy vấn `$in` theo `_id` ổn định; `null` khi chưa có cờ) để client gửi đúng `version` cho `PATCH /tides/...` thay vì chờ 428.
- `POST /api/v1/audit/{id}/revert` (VIEW_AUDIT trên `audit_logs` **và** quyền ghi trên collection đích): chỉ hoàn tác `qc_decision` (qua `qc.decide`, cần APPROVE trên `qc_flags`) và `bulletin_metadata` (qua `qc.update_bulletin_fields` với giá trị cũ, cần UPDATE trên `hydromet_bulletins`); loại khác → 422. Lần hoàn tác tạo audit mới (`source: api_revert`); audit gốc chỉ được gắn `reverted_at`/`revert_audit_id`; hoàn tác lặp → 409.
- Endpoint giữ `LockGuard` gọi `guard.check()` ngay trước khi ghi; mất khoá → 503.

### Log

`app.log_level: DEBUG` chỉ áp cho code dự án: logger `pymongo.*` luôn ≥ INFO (tắt log command monitoring của driver, vốn in nguyên reply, kể cả `users.password_hash`), và mọi handler có bộ lọc chặn dòng log của driver nhắc tới `users`/`password_hash`.

## Cache và khoá

- Cache Redis (cache-aside) do `vngis.cache` quản lý: khoá có phiên bản `ver:{coll}`, TTL kèm jitter, khoá `stale:` dự phòng, chống stampede bằng `lock:rebuild:{key}`. Redis chết: đọc/ghi cache bị bỏ qua (log cảnh báo), endpoint tính thẳng từ MongoDB.
- Khoá phân tán do `vngis.locks` quản lý: `LOCK_BACKEND=single|redlock`, watchdog gia hạn, fencing token từ MongoDB (sau khi lấy fence, `LockGuard` xác nhận vẫn còn giữ khoá).
- PDF bản tin (`GET /api/v1/bulletins/{source_file}/pdf`, EXPORT) trả kèm `ETag` (SHA-256) + `Cache-Control: private, max-age=604800, immutable` (cần quyền nên proxy dùng chung không được lưu); PDF không nhét vào Redis. Route `/pdf` và `/files` được khai báo trước route `{source_file:path}`.

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
