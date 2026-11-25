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
├── 0009_anonymize_actors.py      # dữ liệu: tên đăng nhập cũ trong nhật ký → user:<account_code>; down không khả thi
├── 0010_models_version_unique.py # models.version: index unique partial ({$type: "number"}), không validator; trùng → dừng
└── tests/test_migrations.py      # chạy trên DB tạm riêng (tạo/xoá trong test)
```

- Số `0001`–`0003` để dành cho các collection đã tạo bằng script trước khi có migration (`stations`, `admin_units`/`admin_unit_mappings`/`province_timeline`, `stations.admin`); runner chạy các bước chưa áp theo thứ tự số, không cần đủ số.
- Mỗi bước **idempotent** (`collMod` cùng validator, `createIndexes` cùng định nghĩa là no-op); đã có trong `_migrations` → bỏ qua (`--force` áp lại). Sau `up` runner chạy `verify` (validator, `validationLevel`, `validationAction`, index khớp file schema); sai → dừng, không ghi `_migrations`.
- **`validationLevel: moderate`**: chèn mới và sửa document đang hợp lệ đều bị kiểm tra; document cũ không hợp lệ vẫn sửa được (không chặn job/API cũ). Trước khi áp, runner đếm document cũ không khớp (`n_invalid_existing` trong báo cáo, `$nor: [validator]`).
- `down NNNN` khôi phục validator cũ đã lưu trong `_migrations.previous` (không có → tắt validator); không xoá index.
- Kết nối: `--uri` hoặc `MONGO_URI` (mặc định `mongodb://localhost:27017`); DB: `--db` hoặc `MONGO_DB` — **bắt buộc**, không có mặc định.
- `--dry-run`: mỗi bước chỉ đọc/đếm; runner **không** ghi `_migrations` (đã kiểm: băm nội dung `_migrations`, `users`, `audit_logs`, `qc_flags`, `jobs`, `alerts` của `vietnam_gis` trước/sau dry-run 0009 trùng nhau).
- Thay thế dữ liệu lớn dùng mẫu đã có: ghi collection `__staging`, kiểm tra, rồi `renameCollection(dropTarget=True)`.

## 0009 — ẩn danh hoá người thực hiện

Bước dữ liệu (không validator). Giá trị bằng tên đăng nhập hoặc `users._id` đã biết → `user:<account_code>`:
`audit_logs.actor`, `reverted_by`, `revert_claim.by`, `before`/`after`.{`decided_by`, `retired_by`, `unretired_by`,
`ack_by`, `requested_by`, `promoted_by`, `reverted_by`}; `audit_logs` loại `user_perm`: `doc_id` → `<account_code>`,
`target` → `{account_code}` (bỏ `user_id`/`username`, đúng định dạng API hiện ghi); `qc_flags.decided_by`,
`retired_by`, `unretired_by`; `jobs.requested_by`; `alerts.ack_by`; `models.promoted_by`, `requested_by`.

- **Vai trò:** ghi `user:<account_code>` không kèm vai trò — bản ghi cũ không lưu vai trò lúc thao tác, gắn vai trò hiện tại có thể sai lịch sử. API (`parse_actor`, `actor_label`) đọc được dạng này.
- Cấp `account_code` cho mọi tài khoản chưa có (lệnh có điều kiện "vẫn chưa có mã", không ghi đè mã API vừa cấp).
- **Giữ nguyên:** giá trị đã đúng định dạng `user:acc_…`, vai trò hệ thống (`manual_review`, `qc_job`, `worker`, `cli`, … — `SYSTEM_ACTORS`), `null`; chuỗi không khớp tài khoản nào chỉ được đếm (`n_unmapped`, không in giá trị).
- `audit_logs` không bị validator 0005 chặn sửa (chỉ kiểm kiểu, `moderate`) và code vốn cập nhật bản ghi (`state`, `reverted_*`); vì là nhật ký kiểm toán, khi có thay đổi bước này chèn một bản ghi `action: "anonymize_actors"`, `actor: "migration:0009"`, `after` = số lượng theo trường (không tên).
- Mỗi lệnh sửa có điều kiện giá trị cũ; idempotent (chạy lại: 0 thay đổi, không thêm audit). `verify`: không còn tên đã biết ở các trường trên và mọi tài khoản có mã. `down` báo lỗi (không lưu bản đồ ngược — cố ý, không giữ tên ngoài `users`); tra ngược qua `users.account_code`.
- Báo cáo (`_migrations.report`) chỉ chứa số lượng: `fields_changed` (theo `collection.field`), `documents_changed` (theo collection), `n_users_code_assigned`, `n_unmapped`.

Dry-run trên `vietnam_gis` (25/11/2026): 8 tài khoản, cả 8 chưa có `account_code` (sẽ được cấp); 0 bản ghi chứa tên đăng nhập (người thực hiện cũ đều là `manual_review`); 0 giá trị lạ. Chưa có collection `models`.

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

`MIGRATION_SOURCE_DB` chỉ được **đọc** (chép `jobs`, `qc_flags`, `audit_logs`, `users`, `alerts`, `models` sang DB tạm `pgnn_test_migr_*` rồi áp migration).
