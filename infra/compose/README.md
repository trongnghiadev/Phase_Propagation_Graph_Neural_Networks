# infra/compose/ – Docker Compose

**Chức năng:** định nghĩa toàn bộ dịch vụ của cụm, chia theo profile.

| Profile | Kịch bản | Thành phần |
|---|---|---|
| `ha` | A – chịu lỗi | MongoDB replica set 3 node, Redis cache + Sentinel, 3 node Redlock, API ×3, worker ×2, Nginx, mongo-express |
| `spark` | dùng chung | Spark master + N worker |
| `shard` | B – mở rộng | config server, 2 shard, mongos |

Thư mục con (dự kiến): `mongo/` (khởi tạo replica set, sharding), `redis/` (`sentinel.conf`), `nginx/` (`nginx.conf`).

Hướng dẫn chạy chi tiết, bảng cổng, lệnh giả lập sự cố: chuyển từ `bigdatanckh/deploy/README.md`.
