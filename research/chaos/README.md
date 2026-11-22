# research/chaos/ – Giả lập sự cố

**Chức năng:** script tự động: bật tải → gây sự cố → khôi phục → thu số liệu, lặp được nhiều lần.

| Sự cố | Cách gây | Thực nghiệm |
|---|---|---|
| Primary MongoDB chết | `docker stop` | E18 |
| Một/hai node khóa chết hoặc mất mạng | `docker stop`, `docker network disconnect` | E19 |
| Redis cache master chết | `docker stop` | E20 |
| Tiến trình treo quá TTL | `docker pause` / `unpause` | E16 |
| Mạng chậm | Pumba `netem delay` | E22 |

Danh sách lệnh thủ công tương ứng: `infra/compose/README.md`.
