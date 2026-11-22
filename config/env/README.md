# config/env/ – Cấu hình theo môi trường

**Chức năng:** ghi đè một phần `config/default.yaml` cho từng môi trường. Chọn bằng biến `APP_ENV`.

| File (dự kiến) | Dùng khi |
|---|---|
| `dev.yaml` | Máy cá nhân, MongoDB một node `localhost:27017` |
| `test.yaml` | Chạy test tự động (database tạm, tắt scheduler) |
| `cluster.yaml` | Cụm Docker: replica set `rs0`, Redis Sentinel, Redlock (xem `infra/compose/`) |

Chỉ ghi các khóa khác mặc định.
