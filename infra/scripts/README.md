# infra/scripts/ – Script vận hành

**Chức năng:** thao tác vận hành chạy từ Windows.

| Script (dự kiến) | Nội dung |
|---|---|
| `migrate-data.ps1` | `mongodump` từ MongoDB một node, `mongorestore` vào replica set hoặc mongos, đối chiếu số document |
| `backup.ps1` | sao lưu định kỳ ra ngoài repo |

File sao lưu (`*.archive`) **không** commit; đặt ở `infra/backup/` (đã gitignore) hoặc ổ khác.
