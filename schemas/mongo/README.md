# schemas/mongo/ – Validator MongoDB

**Chức năng:** mỗi collection một thư mục, mỗi phiên bản schema một file.

```
schemas/mongo/
├── qc_flags/v1.json         # migration 0004
├── audit_logs/v1.json       # migration 0005
├── jobs/v1.json             # migration 0006
├── model_forecasts/v1.json  # migration 0007
└── …                        # stations, admin_units, province_timeline… (chưa tách)
```

Định dạng file: `{collection, version, description, validationLevel, validationAction, validator: {$jsonSchema}, indexes: [{name, key, unique?}], notes}`.

- Migration (`migrations/mongo/`) đọc file ở đây để `create`/`collMod` validator và tạo index.
- Validator có bản trong code thì test kiểm tra **khớp** với file: `qc_flags`, `audit_logs` ↔ `vngis.qc_rules` (`migrations/mongo/tests`), `model_forecasts` ↔ `apps/worker/src/worker/forecast.py` (`apps/worker/tests/test_forecast.py`). Sửa một bên phải sửa bên kia và thêm phiên bản `v<n+1>` + migration mới.
- **`jobs`**: hai bộ field cùng tồn tại — job Spark/nạp nguyên tử (`job` + `status` ∈ running/ok/failed/dry_run/blocked/rolled_back/lost) và API/worker (`type` + `state` ∈ queued/claimed/running/succeeded/failed/lost/skipped/sample). Validator dùng `anyOf` (khớp ít nhất một bộ); **không thống nhất** tên field vì nhiều nơi đọc/ghi (Spark `common/jobrun.py`, `vngis.atomic_reload`, API, web).
- Mọi file dùng `validationLevel: moderate` (không chặn sửa document cũ không hợp lệ).
- Validator còn viết trong code chưa tách: `create_stations.py`, `create_admin_units.py`, parser KTTV, job Spark G0.
