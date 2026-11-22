# migrations/ – Nâng cấp database có đánh số

**Chức năng:** mọi thay đổi cấu trúc MongoDB (tạo collection, đổi validator, thêm field, tạo index) được viết thành một bước migration có số thứ tự, chạy được lại và đảo ngược được.

| Thư mục | Nội dung |
|---|---|
| [`mongo/`](mongo/) | Các bước migration và trình chạy |

**Quy tắc**
- Đổi schema thì PR phải kèm một file migration mới; không sửa migration đã chạy.
- Mỗi migration có `up()` và `down()`.
- Thứ tự triển khai: migration → worker → API.
