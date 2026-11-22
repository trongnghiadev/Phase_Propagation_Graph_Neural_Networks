# config/env/ – Cấu hình theo môi trường

**Chức năng:** ghi đè một phần `config/default.yaml` cho từng môi trường. Chọn bằng biến `APP_ENV` (mặc định `dev`); `APP_ENV` trỏ tới file không tồn tại là lỗi.

| File | Dùng khi | Ghi đè |
|---|---|---|
| `dev.yaml` | Máy cá nhân, MongoDB một node `localhost:27017` | `debug`, log `DEBUG`, `mongo.mode: single`; Redis Sentinel `localhost:26381-26383`, khóa `localhost:6391-6393` (cổng của cụm `infra/compose`) |
| `test.yaml` | Chạy test tự động | database `vietnam_gis_test`, chờ server 1 s, TTL cache 5 s, tắt scheduler |
| `cluster.yaml` | Cụm Docker (`infra/compose/docker-compose.yml`, profile `ha`) | replica set `mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0`, `w`/`readConcern` majority; Sentinel `sentinel-1..3:26379` (master `cache`); Redlock `redis-lock-1..3:6379` |

- Chỉ ghi các khóa khác mặc định; mapping được gộp theo khóa, list bị thay cả list.
- Biến môi trường (`MONGO_URI`, `REDIS_SENTINELS`…) thắng file env, ví dụ sharding (profile `shard`): `APP_ENV=cluster` + `MONGO_URI=mongodb://localhost:27200` + `MONGO_MODE=mongos`.
