# Cụm phân tán giả lập (Docker)

Cấu hình cho mục 5.9 của đề cương (repo private, `docs/private/proposal/`). Chạy mọi lệnh dưới đây từ **gốc repo**. Toàn bộ chạy trên một máy bằng Docker Compose, project `gis-dist`, mạng `gis-dist-net`.

| Profile | Kịch bản | Thành phần | RAM ước tính |
|---|---|---|---|
| `ha` | A – chịu lỗi | MongoDB replica set `rs0` (3 node), Redis cache + replica + 3 Sentinel, 3 node Redis cho Redlock, 3 API + Nginx, mongo-express | khoảng 4,5 GB |
| `spark` | dùng chung | Spark master + N worker (2 core / 1,5 GB mỗi worker) | 0,8 GB + 2 GB × N |
| `shard` | B – mở rộng | config server, 2 shard (mỗi shard 1 node), mongos | khoảng 3 GB |

Cổng không trùng cụm cũ `spark-mongodb-gis` (thư mục `tienxuly`), nên hai cụm chạy song song được trong lúc chuyển dữ liệu:

| Dịch vụ | Địa chỉ |
|---|---|
| MongoDB `rs0` | `mongo1:27101`, `mongo2:27102`, `mongo3:27103` |
| mongos (kịch bản B) | `localhost:27200` |
| API qua Nginx | http://localhost:8088 |
| mongo-express | http://localhost:8082 |
| Spark master | http://localhost:8090, `spark://localhost:7078` |
| Redis cache / Sentinel | `localhost:6380` / `26381–26383` |
| Redis khóa (Redlock) | `localhost:6391–6393` |

> Môi trường dev: **không bật xác thực** MongoDB và Redis. Không mở các cổng này ra mạng ngoài.

## 1. Chuẩn bị (một lần)

**Tăng RAM cho Docker.** Chép [infra/wslconfig.example](../wslconfig.example) thành `%USERPROFILE%\.wslconfig`, sau đó:

```powershell
wsl --shutdown
```

rồi mở lại Docker Desktop.

**Tên host của replica set.** Driver MongoDB kết nối replica set bằng tên host mà replica set công bố (`mongo1`, `mongo2`, `mongo3`). Muốn kết nối từ Windows (Python, Compass, R), thêm dòng sau vào `C:\Windows\System32\drivers\etc\hosts` (mở Notepad bằng quyền Administrator):

```
127.0.0.1 mongo1 mongo2 mongo3
```

Không cần bước này nếu chỉ chạy client bên trong mạng Docker.

**Giải phóng RAM.** Cụm cũ dùng khoảng 2,2 GB. Có thể dừng Spark cũ (giữ `gis-mongo` để chuyển dữ liệu):

```powershell
docker stop gis-spark-worker gis-spark-master
```

## 2. Kịch bản A: replica set

```powershell
docker compose -f infra/compose/docker-compose.yml --profile ha up -d --build
```

```powershell
docker logs gis-dist-mongo-init
```

Log cần có 1 dòng `PRIMARY` và 2 dòng `SECONDARY`.

**Chuyển dữ liệu** từ `gis-mongo` sang `rs0`. Script sao lưu ra `infra/backup/`, nạp vào cụm mới rồi so số document từng collection; container cũ không bị thay đổi:

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/migrate-data.ps1 -Target rs
```

**Kiểm tra:**

```powershell
curl.exe -s http://localhost:8088/health
```

```powershell
curl.exe -s -X POST "http://localhost:8088/lock/demo?ttl_ms=5000&hold_ms=200"
```

Gọi lại nhiều lần sẽ thấy `instance` đổi giữa `api-1`, `api-2`, `api-3` (Nginx chia tải) và `fence` tăng dần.

Bảng đối chiếu cần có đủ các collection sau, mọi dòng `OK` (số liệu tại 22/11/2026):

| Collection | Số document |
|---|---|
| `hydromet_bulletins` | 3.636 |
| `hydromet_bulletin_files` | 3.636 |
| `merit_river_reaches` | 21.508 |
| `waterways_segments` | 38.877 |
| `stations` | 26 |
| `admin_units` | 14.165 |
| `admin_unit_mappings` | 10.554 |
| `province_timeline` | 97 |

Sau khi đã kiểm tra dữ liệu trên `rs0` đầy đủ, cho các script dùng chuỗi kết nối mới:

```
mongodb://mongo1:27101,mongo2:27102,mongo3:27103/vietnam_gis?replicaSet=rs0
```

Nếu cần tạo lại dữ liệu sinh ra trên cụm mới, chạy **đúng thứ tự** (`stations` cần `admin_units` để gán tỉnh cũ/mới):

```powershell
python -X utf8 pipelines/ingest/admin_units/create_admin_units.py "mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0"
```

```powershell
python -X utf8 pipelines/ingest/stations/create_stations.py "mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0"
```

`create_admin_units.py` đọc dữ liệu đã tải sẵn trong `data/admin_units/`, không gọi lại API.

## 3. Spark

```powershell
docker compose -f infra/compose/docker-compose.yml --profile spark up -d --scale spark-worker=2
```

Mã dự án được mount ở `/opt/spark/work/project` (chỉ đọc). Ví dụ:

```powershell
docker exec gis-dist-spark-master /opt/spark/bin/spark-submit --master spark://spark-master:7077 /opt/spark/work/project/pipelines/spark/flatten/job.py
```

Đổi số worker (E21): chạy lại lệnh `up` với `--scale spark-worker=1|2|3`.

## 4. Kịch bản B: sharded cluster

Nên tắt kịch bản A trước để đủ RAM:

```powershell
docker compose -f infra/compose/docker-compose.yml --profile ha stop
```

```powershell
docker compose -f infra/compose/docker-compose.yml --profile shard up -d
```

```powershell
docker logs gis-dist-shard-init
```

`init-shard.js` thêm 2 shard và tạo sẵn 2 collection phân mảnh: `hydromet_bulletin_files` (hashed `source_file`) và `tide_extremes` (time series, khóa `meta.station`, `ts`). Sau đó nạp dữ liệu (không dùng `--drop` để giữ các collection đã shard):

```powershell
powershell -ExecutionPolicy Bypass -File infra/scripts/migrate-data.ps1 -Target shard
```

Xem phân bố dữ liệu giữa các shard:

```powershell
docker exec gis-dist-mongos mongosh --port 27200 --quiet --eval "db.getSiblingDB('vietnam_gis').hydromet_bulletin_files.getShardDistribution()"
```

## 5. Giả lập sự cố (ví dụ thủ công)

| Thực nghiệm | Lệnh gây sự cố | Khôi phục |
|---|---|---|
| E18 primary MongoDB chết | `docker stop gis-dist-mongo1` | `docker start gis-dist-mongo1` |
| E19 một node khóa chết | `docker stop gis-dist-redis-lock-2` | `docker start gis-dist-redis-lock-2` |
| E19 tách mạng một node khóa | `docker network disconnect gis-dist-net gis-dist-redis-lock-3` | `docker network connect gis-dist-net gis-dist-redis-lock-3` |
| E20 Redis cache master chết | `docker stop gis-dist-redis-cache` | `docker start gis-dist-redis-cache` |
| Tiến trình treo (fencing) | `docker pause gis-dist-api-2` | `docker unpause gis-dist-api-2` |
| API chết | `docker stop gis-dist-api-3` | `docker start gis-dist-api-3` |

Theo dõi trong lúc gây sự cố:

```powershell
while ($true) { curl.exe -s http://localhost:8088/health; ""; Start-Sleep 1 }
```

Các script tự động (bật tải, gây sự cố, thu số liệu) sẽ nằm ở thư mục `chaos/`.

## 6. Dừng và dọn

Dừng, giữ dữ liệu:

```powershell
docker compose -f infra/compose/docker-compose.yml --profile ha --profile spark --profile shard down
```

Dừng và **xóa toàn bộ dữ liệu của cụm mới** (không ảnh hưởng `gis-mongo` cũ và file sao lưu):

```powershell
docker compose -f infra/compose/docker-compose.yml --profile ha --profile spark --profile shard down -v
```

## Tệp

| Tệp | Vai trò |
|---|---|
| `docker-compose.yml` | toàn bộ dịch vụ, chia theo profile |
| `mongo/init-rs.js` | khởi tạo `rs0`, đặt `defaultWriteConcern = majority` |
| `mongo/init-shard-rs.sh` | khởi tạo replica set `cfgrs`, `sh1`, `sh2` |
| `mongo/init-shard.js` | thêm shard, tạo collection phân mảnh |
| `redis/sentinel.conf` | Sentinel theo dõi master `cache`, quorum 2 |
| `nginx/nginx.conf` | cân bằng tải 3 API, tự thử instance khác khi lỗi |
| `../scripts/migrate-data.ps1` | sao lưu từ `gis-mongo`, nạp vào `rs0` hoặc mongos, đối chiếu số document |
| `../wslconfig.example` | cấp 10 GB RAM cho Docker |
| `../../apps/api/` | API tối giản: `/health`, `/stations`, `/lock/{resource}`; build từ gốc repo (cần `packages/vngis`) |
| `../../packages/vngis/` | Redlock và fencing token dùng chung (`vngis.locks`) |
