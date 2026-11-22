# apps/web/ – Giao diện web (Next.js + Leaflet)

**Chức năng:** các màn hình ở đề cương 5.7: bản đồ dự báo triều, lan truyền pha, so sánh với KTTV, cảnh báo, bản tin + PDF gốc, kiểm soát chất lượng số liệu, hồ chứa, danh mục trạm, địa giới theo thời gian, quản trị (mô hình, người dùng, cache, khóa).

- Next.js (React); component bản đồ Leaflet nạp phía client (`dynamic(..., { ssr: false })`).
- Gọi API qua Nginx (`/api/v1/...`); không truy cập MongoDB trực tiếp.
- Ẩn/hiện chức năng theo bitmask quyền; kiểm tra thật luôn ở API.
- Phụ thuộc khóa trong `package-lock.json`.
