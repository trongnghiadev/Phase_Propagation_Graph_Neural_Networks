# schemas/ – Hợp đồng dữ liệu

**Chức năng:** định nghĩa cấu trúc dữ liệu dùng chung giữa các khối (MongoDB, pipeline, mô hình, API). Đây là "nguồn sự thật" về hình dạng dữ liệu; code và validator sinh ra hoặc kiểm tra theo các file này.

| Thư mục | Nội dung |
|---|---|
| [`mongo/`](mongo/) | `$jsonSchema` validator của từng collection, có phiên bản |
| [`prediction/`](prediction/) | Định dạng dự báo chung cho mọi mô hình (PhaseGNN, B0–B5, B_KTTV) |

**Quy tắc phiên bản:** không sửa file đã phát hành; thay đổi thì tạo `v2.json` và migration tương ứng trong `migrations/mongo/`.
