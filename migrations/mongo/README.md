# migrations/mongo/ – Migration MongoDB

**Chức năng:** các bước nâng cấp database `vietnam_gis` (validator, index, collection mới), có số thứ tự, ghi lịch sử vào collection `_migrations`.

```
migrations/mongo/
├── runner.py                     # status / up [--dry-run] [--only NNNN] [--force] / verify / down NNNN
├── _lib.py                       # áp validator + index từ schemas/mongo/<collection>/v<n>.json
├── 0004_qc_flags_validator.py    # qc_flags (bản gốc: vngis.qc_rules)
├── 0005_audit_logs_validator.py  # audit_logs (bản gốc: vngis.qc_rules)
├── 0006_jobs_validator.py        # jobs: anyOf (job/status của Spark | type/state của API/worker) + index hàng đợi
├── 0007_model_forecasts.py       # model_forecasts (dự báo PhaseGNN của worker)
├── 0008_users_account_code.py    # users.account_code: index unique partial ({$type: "string"}), không validator
└── tests/test_migrations.py      # chạy trên DB tạm riêng (tạo/xoá trong test)
```

- Số `0001`–`0003` để dành cho các collection đã tạo bằng script trước khi có migration (`stations`, `admin_units`/`admin_unit_mappings`/`province_timeline`, `stations.admin`); runner chạy các bước chưa áp theo thứ tự số, không cần đủ số.
- Mỗi bước **idempotent** (`collMod` cùng validator, `createIndexes` cùng định nghĩa là no-op); đã có trong `_migrations` → bỏ qua (`--force` áp lại). Sau `up` runner chạy `verify` (validator, `validationLevel`, `validationAction`, index khớp file schema); sai → dừng, không ghi `_migrations`.
- **`validationLevel: moderate`**: chèn mới và sửa document đang hợp lệ đều bị kiểm tra; document cũ không hợp lệ vẫn sửa được (không chặn job/API cũ). Trước khi áp, runner đếm document cũ không khớp (`n_invalid_existing` trong báo cáo, `$nor: [validator]`).
- `down NNNN` khôi phục validator cũ đã lưu trong `_migrations.previous` (không có → tắt validator); không xoá index.
- Kết nối: `--uri` hoặc `MONGO_URI` (mặc định `mongodb://localhost:27017`); DB: `--db` hoặc `MONGO_DB` — **bắt buộc**, không có mặc định.
- Thay thế dữ liệu lớn dùng mẫu đã có: ghi collection `__staging`, kiểm tra, rồi `renameCollection(dropTarget=True)`.

## Áp vào `vietnam_gis` (người vận hành chạy)

Đã thử trên DB tạm (bản sao `jobs` 93, `qc_flags` 380, `audit_logs` 84 document ngày 24/11/2026: 0 document không hợp lệ). Xem trước, áp, kiểm tra:

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network spark-mongodb-gis_gis-net -v "<gốc repo>:/repo" -w /repo \
  -e MONGO_URI=mongodb://mongo:27017 pgnn-testdeps:3.12 \
  sh -c "python -X utf8 migrations/mongo/runner.py --db vietnam_gis up --dry-run && \
         python -X utf8 migrations/mongo/runner.py --db vietnam_gis up && \
         python -X utf8 migrations/mongo/runner.py --db vietnam_gis verify"
```

Cụm replica set: `-e MONGO_URI="mongodb://mongo1:27101,mongo2:27102,mongo3:27103/?replicaSet=rs0"`.

## Test

```bash
MSYS_NO_PATHCONV=1 docker run --rm --network spark-mongodb-gis_gis-net -e MONGO_TEST_URI=mongodb://mongo:27017 \
  [-e MIGRATION_SOURCE_DB=vietnam_gis] -v "<gốc repo>:/repo" -w /repo pgnn-testdeps:3.12 \
  python -m pytest migrations/mongo/tests -q
```

`MIGRATION_SOURCE_DB` chỉ được **đọc** (chép `jobs`, `qc_flags`, `audit_logs` sang DB tạm `pgnn_test_migr_*` rồi áp migration).
