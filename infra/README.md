# infra/ – Hạ tầng

**Chức năng:** triển khai cụm phân tán giả lập bằng Docker trên một máy (đề cương 5.9).

| Thư mục / file | Nội dung |
|---|---|
| [`compose/`](compose/) | `docker-compose.yml` (profile `ha`, `spark`, `shard`), cấu hình MongoDB, Redis Sentinel, Nginx |
| [`scripts/`](scripts/) | script vận hành: chuyển dữ liệu, sao lưu |
| [`wslconfig.example`](wslconfig.example) | cấp 10 GB RAM cho Docker trên WSL2 |

**Phiên bản:** image tự build gắn tag theo phiên bản hệ thống (`vX.Y.Z`); image ngoài ghim phiên bản cụ thể (`mongo:8.0`, `redis:7.4-alpine`, `nginx:1.27.5-alpine`).

Hướng dẫn chạy đầy đủ: [compose/README.md](compose/README.md). Image API build từ gốc repo (`context: ../..`, `dockerfile: apps/api/Dockerfile`). File sao lưu ghi vào `infra/backup/` (đã gitignore).
