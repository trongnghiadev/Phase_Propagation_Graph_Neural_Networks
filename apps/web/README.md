# apps/web/ – Giao diện web (Next.js + Leaflet)

**Chức năng:** các màn hình ở đề cương 5.7: bản đồ dự báo triều, lan truyền pha, so sánh với KTTV, cảnh báo, bản tin + PDF gốc, kiểm soát chất lượng số liệu, hồ chứa, danh mục trạm, địa giới theo thời gian, quản trị (mô hình, người dùng, cache, khóa).

- Next.js (React); component bản đồ Leaflet nạp phía client (`dynamic(..., { ssr: false })`).
- Gọi API qua Nginx (`/api/v1/...`); không truy cập MongoDB trực tiếp.
- Ẩn/hiện chức năng theo bitmask quyền; kiểm tra thật luôn ở API.
- Phụ thuộc khóa trong `package-lock.json`.

## Gọi API v1 (khoá lạc quan, lý do bắt buộc)

- Hàng đợi duyệt (`/hang-doi-duyet`): `GET /review?status=suspect|confirmed_ok|corrected|rejected`; quyết định gửi `POST /review/{id}/approve` với `status`, `reason` (bắt buộc), `corrected_value` (chỉ khi `corrected`), `version` của cờ đã xem.
- QC số liệu (`/qc`): `PATCH /tides/{station}/{ts}` với `status`, `corrected_value`, `kind`, `reason`, `version`. Cờ đã có mà chưa biết `version` → API trả 428 kèm phiên bản hiện tại; giao diện yêu cầu người dùng xác nhận lại, không tự gửi lại.
- Người dùng (`/nguoi-dung`): `PUT /users/{id}/perm` gửi `perm_ver` đã xem và `reason` bắt buộc.
- Nhật ký (`/nhat-ky`): chỉ hiện nút hoàn tác cho `qc_decision` và `bulletin_metadata` đã `applied` và chưa hoàn tác.
- Lỗi 409/422/423/428/429/503 hiện thông báo tiếng Việt (`friendlyError`, `ErrorNotice`); `Retry-After` được đọc và hiển thị.

## Token

Token: access token (15 phút) chỉ giữ trong **bộ nhớ** của trang (không `localStorage`/`sessionStorage`); refresh token (7 ngày) nằm trong cookie `httpOnly; SameSite=Strict` do API đặt, `Path` giới hạn ở `/api/v1/auth/refresh` và `/api/v1/auth/logout`, JavaScript không đọc được. Mọi request gửi `credentials: "include"` và header `X-Requested-With: XMLHttpRequest` (API dùng để chọn chế độ cookie và chống CSRF). Tải trang → `POST /auth/refresh` lấy access token mới; không có cookie hợp lệ → về màn hình đăng nhập. Refresh token dùng một lần nên việc làm mới được tuần tự hoá giữa các tab bằng Web Locks API (tab sau gửi cookie mới mà tab trước vừa nhận); đăng xuất báo các tab khác qua `BroadcastChannel`. Vẫn giữ: không dùng `dangerouslySetInnerHTML`, header `X-Frame-Options`/`nosniff` trong `next.config.ts`.

Dev: proxy Next.js (`NEXT_PUBLIC_API_PROXY=1`) giữ cùng origin nên cookie và CSRF không cần cấu hình thêm. Gọi thẳng API khác cổng (`NEXT_PUBLIC_API_PROXY=0`) thì API phải có origin của web trong `auth.cors_origins` (`CORS_ORIGINS`; `config/env/dev.yaml` đã có `http://localhost:3000`). Dev chạy http nên API đặt `auth.cookie_secure: false` (`AUTH_COOKIE_SECURE=false`); production qua HTTPS giữ `Secure`.

Trang QC dùng `flag_version` mà `GET /stations/{code}/tides` trả kèm từng điểm có cờ QC để gửi đúng `version` khi `PATCH /tides/...` (không dựa vào lỗi 428). Nhật ký, việc nền, hàng đợi duyệt, cảnh báo hiển thị người thực hiện bằng nhãn API trả (`actor_label`, `requested_by_label`); dữ liệu lưu chỉ có `user:<account_code>:<role>`.
