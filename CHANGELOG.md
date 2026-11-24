# Changelog

Định dạng theo [Keep a Changelog](https://keepachangelog.com/vi/1.1.0/); phiên bản theo [SemVer](https://semver.org/lang/vi/). Commit theo [Conventional Commits](https://www.conventionalcommits.org/vi/v1.0.0/).

## [Chưa phát hành]

### Thêm
- Khung thư mục monorepo, README cho thư mục cấp 1 và 2, ADR 0001–0003.
- `config/default.yaml`, `config/env/{dev,test,cluster}.yaml`: tham số hệ thống (MongoDB, Redis cache/khóa, mốc địa giới, gán trạm, TTL cache, scheduler); `admin.max_label_lag_days` (31). `config/stations.yaml`: tọa độ ước lượng và tỉnh cũ/mới của trạm.
- `vngis.config`: gộp default → env → biến môi trường, kiểm tra bằng pydantic (`ConfigError` liệt kê từng khóa lỗi); `load_stations_catalog` kiểm tra `config/stations.yaml`.
- `vngis.db`: kết nối MongoDB (một node / replica set / mongos), Redis cache (một node / Sentinel), Redlock từ cấu hình.
- `vngis.admin`: `era_of`, `find_province`, `province_at`, `successor_of` theo hai thời kỳ địa giới.
- `vngis.text`: `norm_name`, `slug`, `strip_accents` – bản chuẩn hóa tên duy nhất của repo.
- `vngis.province_labels`: gán `{era, code}` cho nhãn tỉnh trong bản tin, thời kỳ theo ngày quan trắc, quy tắc "đổi tên chậm" có giới hạn; dùng chung cho `stations` và G0.
- `vngis.locks`: Redlock + fencing token từ MongoDB.
- `pipelines/ingest/stations/create_stations.py` đọc tọa độ/tham số từ `config/` qua `vngis.config`.
- Job PySpark G0 (`pipelines/spark/flatten/`): `hydromet_bulletins` → `tide_extremes` (time series), `official_forecasts`, `reservoir_daily`, `rainfall_daily`; nạp qua staging có kiểm tra, sao lưu `__prev`, khóa `job_locks`, nhật ký `jobs`.

- QC số liệu bản tin (GĐ2.3): `vngis.qc_rules` (thuần Python: quy tắc R1 ngoài khoảng hợp lý theo phân vị/ngưỡng vật lý của trạm, R2 đỉnh thấp hơn chân cùng ngày, R3 giờ đỉnh lệch so với Vũng Tàu + độ trễ điển hình; khóa cờ, gộp vào `qc_flags` không ghi đè quyết định của người; validator/index `qc_flags`, `audit_logs`); `config/default.yaml` mục `qc` và `vngis.config.QcConfig`.
- `vngis.qc`: ghi quyết định QC có kiểm toán (`find_one_and_update` có điều kiện `version`, `VersionConflict`; `audit_logs` trong transaction hoặc pending → applied), cờ thủ công, sửa metadata bản tin được phép có điều kiện giá trị cũ, ghi lần kiểm tra tay không đổi dữ liệu.
- CLI `pipelines/qc/qc_decide.py` (list/show/decide/flag/bulletin-set/review/ensure/reconcile).
- Job PySpark QC `pipelines/spark/qc/qc_tide_extremes.py`: R1–R3 chạy trên executor (gửi `vngis` bằng `addPyFile`), ghi `qc_flags`, xuất `results/qc/summary.csv`, `exceedance.csv` (số ngày vượt BĐ I/II/III theo cấp báo động của chính bản tin, K9) và `results/qc/runs/<run_id>/`.
- `pipelines/spark/common/jobrun.py` (`JobRun`: run_id, khóa `job_locks`, nhật ký `jobs`, dùng chung cho G0 và QC), `common/pyfiles.py` (`ship_vngis`); `read_mongo(options=…)`.
- **GĐ7.1 Khung backend**: `apps/api/src/api/` gồm `main.py` (factory `create_app()`, middleware `request_id` + `INSTANCE`, xử lý lỗi thống nhất), `router/` (13 router dưới tiền tố `/api/v1`), `auth/`, `permissions.py`, `deps.py`, `state.py`; `packages/vngis` có `cache.py`, `locks.py`, `atomic_reload.py`; `apps/worker/src/worker/` có `scheduler.py`, `jobs.py`, `__main__.py`.
- **GĐ7.2 Xác thực**: `users` + `roles` trong MongoDB; mật khẩu băm **argon2id** (`api/auth/password.py`); JWT access 15 phút + refresh 7 ngày mang `user_id` + `perm_ver`; đăng xuất chặn `jti` trong Redis (`api/auth/blacklist.py`); CLI seed 8 vai trò mặc định với mật khẩu **ngẫu nhiên** (`api/auth/seed.py`) — chỉ lưu hash, không ghi mật khẩu rõ vào log/báo cáo.
- **GĐ7.3 Phân quyền bitmask**: `IntFlag` 12 bit (`api/permissions.py`), 8 vai trò mặc định `viewer=1, analyst=17, editor=23, reviewer=183, data_engineer=255, hydrologist=1169, forecast_officer=3249, admin=4095`; dependency `require(flag, collection)` áp cho **mọi** endpoint; cache quyền `perm:{user_id}` trong Redis (TTL 5 phút), vô hiệu khi đổi quyền và tăng `perm_ver`; cập nhật quyền nguyên tử bằng `$bit`.
- **GĐ7.4 Cache Redis**: `vngis.cache.Cache` hỗ trợ Sentinel, cache-aside với khoá có phiên bản `ver:{coll}`, TTL kèm jitter, khoá `stale:` dự phòng, chống stampede bằng `lock:rebuild:{key}` (có trần chờ), write-through cho dự báo, con trỏ `fc:{station}:latest`, PDF trả kèm `ETag` (SHA-256) + `Cache-Control` (**không** nhét PDF vào Redis), nén zstd + msgpack cho giá trị lớn, đo hit/miss theo loại khoá.
- **GĐ7.5 Khoá phân tán**: `vngis.locks` chọn backend theo `LOCK_BACKEND=single|redlock`; script Lua nhả khoá (`GET` == token → `DEL`) và gia hạn (`PEXPIRE`); watchdog gia hạn mỗi TTL/3, đặt cờ `lock.lost` khi thất bại; **fencing token** từ MongoDB (`lock_fences`) với điều kiện `last_fence < fence` cho mọi lệnh ghi được bảo vệ; khoá sửa bản ghi + kiểm tra lạc tiến `version` (409 Conflict / 423 Locked).
- **GĐ7.6 Nạp lại bản tin nguyên tử**: `vngis.atomic_reload.reload_bulletins_atomic` — staging `hydromet_bulletins__staging_<run_id>` → validator + index → đối chiếu với `hydromet_bulletin_files` (số bản tin, số bản chính, SHA-256) → sao lưu `__prev` → `renameCollection(..., dropTarget=True)` → tăng `ver:hydromet_bulletins` → chạy lại G0; bảo vệ bằng `lock:reload:bulletins` + watchdog. `pipelines/ingest/kttv/kttv_load_mongo.py` hỗ trợ `--staging/--run-id/--promote/--g0-only`.
- **GĐ7.7 Scheduler**: `apps/worker` chạy APScheduler; mỗi job khoá phân tán để **chỉ một instance** chạy, ghi `jobs` (state, worker, fence, heartbeat); job nền: nạp bản tin (`lock:ingest:{date}`), nạp lại nguyên tử, dự báo, làm nóng cache.
- **GĐ7.8 Endpoint**: 13 router (`auth`, `bulletins`, `stations`, `admin_units`, `reservoirs`, `stats`, `forecasts`, `alerts`, `features`, `review`, `jobs`, `audit`, `users`, `admin`) — trả 409/423/429/503 đúng ngữ nghĩa; endpoint dự báo **không** bịa số liệu khi chưa có mô hình PhaseGNN (trả 503 hoặc dùng bộ dự báo mẫu trong `results/`); OpenAPI tại `/docs`.

### Sửa
- `vngis.qc`: mọi lệnh ghi (`decide`, `create_manual_flag`, `retire_flag`, `update_bulletin_fields`) dùng chung `_audited` với write concern `majority` (transaction khi có replica set); lỗi mơ hồ giữ audit `pending`, chỉ xóa khi chắc chắn không ghi được; đặt `applied` có điều kiện `{_id, state: "pending"}` (`AuditStateError`). `reconcile_pending` so nội dung theo `action` (`qc_decision`, `qc_flag_create`, `qc_flag_retire`, `bulletin_metadata`; trước đây luôn tra `qc_flags` nên xóa nhầm audit `bulletin_metadata`) và chỉ xử lý bản cũ hơn `qc.audit_pending_grace_s` (600 s). `update_bulletin_fields` kiểm tra kiểu từng field, xung đột báo `FieldConflict` (field, giá trị mong đợi, giá trị hiện có). Thêm `retire_flag` (cờ mồ côi).
- Cờ `value_mismatch` có đường thoát (sửa theo ý kiến reviewer 23/11/2026): `vngis.qc.decide` gắn quyết định vào `decided_value` (giá trị người xem đã xem) và giữ `current_value` (giá trị đã thấy), `value` giữ nguyên làm mốc lúc gắn cờ; `vngis.qc_rules.plan_one` so với `decided_value` và không gắn lại mismatch khi G0 chưa kịp cập nhật `tide_extremes` (trước đây cờ mắc kẹt vô hạn, không có đường thoát); G0 so `raw_value` với `decided_value`; validator `qc_flags` thêm `decided_value`; `qc_decide.py list --mismatch`.
- `vngis.qc_rules`: R2 chỉ gắn cặp vi phạm lớn nhất trong ngày (177 → 154 cờ R2); cờ thủ công luôn có hiệu lực, cờ `retired` hết hiệu lực và job không đụng tới; tập tham chiếu R3 bỏ đỉnh Vũng Tàu `rejected`; validator nhánh `corrected` bắt buộc `corrected_value`; `PURE_MODULES` (executor chỉ nhận module thuần).
- G0: cờ mồ côi `auto` chưa quyết định → cảnh báo, bỏ qua (job QC xóa sau); cờ đã quyết định/thủ công mồ côi → chặn, hướng dẫn `qc_decide.py retire`; quyết định mà giá trị gốc đã đổi → không áp, `qc_flag = "value_mismatch"`.
- Job QC dừng mã 4 khi G0 còn dở thay thế (`flatten/rules.g0_unsettled`, dùng lại `pending_rollback`).
- CLI `qc_decide.py`: lệnh `retire`, `reconcile --grace-s`; lỗi argparse trả mã 1 (mã 2 chỉ dành cho xung đột), mã 3 khi audit không còn pending.
- Sau kiểm tra tay K1–K6 (23/11/2026): G0 dùng 3.595 bản tin (danh sách bản trùng sha256 bị bỏ nay rỗng), `tide_extremes` 129.420 document, không còn `issued_date_note`; `stations.bulletin_rows`/`n_bulletins` đếm trên 3.595 bản tin.
- G0: áp `qc_flags` vào `tide_extremes` (`rejected` → `value: null`; `corrected` → `corrected_value`; thêm `raw_value`, `raw_qc`, `qc_flag` cho mọi document); kiểm tra số lượng tính theo giá trị gốc và theo công thức N_src − R_num + C_null; không chạy khi job QC đang giữ khóa; `rainfall_daily` không có số và không có cờ → `flag: "missing"` (validator bắt buộc một trong hai).
- `norm_name` chuẩn hóa NFC trước khi so tiền tố (chữ tổ hợp NFD) và gộp mọi khoảng trắng: hết `name_norm` có hai dấu cách (11 đơn vị trong `admin_units`, ví dụ "ba ria vung tau"); bỏ các bản sao trong `pipelines/ingest/*` và `pipelines/spark/common/text.py`.
- Tỉnh của hồ chứa: so nhãn theo `norm_name`, thời kỳ theo ngày quan trắc (ADR 0003), "đổi tên chậm" chỉ khi nhãn mới xuất hiện trong `max_label_lag_days`; ngoài giới hạn không tự lùi về thời kỳ cũ; nhãn rỗng không lỗi. `stations.admin_history` của hồ Dầu Tiếng: đoạn "Tây Ninh" 01–06/07/2025 nay là Tây Ninh cũ (72), không phải 80.
- G0: `tide_extremes.meta` chỉ `{station, kind}` (72.155 → khoảng 17–18 nghìn bucket); kiểm tra số lượng staging trước khi thay; staging có hậu tố `run_id`; `writeConcern: majority`; dùng `MONGO_URI`/`MONGO_DB`; `reservoir_daily.issued_date` nhất quán với `official_forecasts` (thêm `issued_date_note`).
- `vngis.config.deep_merge`: khóa `null` trong file env không còn xóa mapping mặc định.
- `vngis.province_labels`: nhãn tỉnh rỗng/`None` không làm đứt đoạn và không kích hoạt/làm gãy quy tắc đổi tên chậm; đoạn sau mốc ở cuối dữ liệu không bao giờ là đổi tên chậm, có cờ `provisional` khi còn trong `max_label_lag_days`.
- G0: `reservoir_daily.province` cho phép `null` kèm `province_flag = "empty_label"` (nhãn rỗng không chặn job); thêm `province.provisional`.
- G0: khóa `job_locks` được gia hạn và xác nhận (`findAndModify {_id, run_id}`) trước mỗi pha dài và trước mỗi lần thay (mất khóa → mã thoát 5); nhả khóa trong `try/finally` riêng, lỗi ghi `jobs` không chặn nhả khóa; lỗi lấy khóa khác E11000 thì thử nhả theo `run_id`; chỉ xóa staging của lần chạy đã kết thúc hoặc quá hạn khóa.
- G0: còn `__prev` của lần chạy hỏng giữa chừng (đã thay một phần) → dừng với mã thoát 4, hướng dẫn lùi lại, không xóa `__prev`; `__prev` chép đúng validator/index của bản chính hiện tại (trước đây dùng validator mới nên bản cũ có thể không chép được).
- `create_stations.py`: bỏ 4 bản tin trùng sha256 bằng `vngis.bulletins.duplicate_exclusions` (dùng chung với G0) – `bulletin_rows`, `n_bulletins` khớp 3.590 bản tin; thiếu `province_timeline` thì dừng trước khi ghi.
- `create_admin_units.py` đọc kết nối bằng `vngis.config` (APP_ENV, MONGO_URI, MONGO_DB).
- G0: ghi `jobs.replacing` trước mỗi lần thay và `replaced` ngay sau lệnh thay (trước khi đếm lại); run `failed`/`running` có `replaced`/`replacing` sau lần ok cuối đều chặn (mã 4) khi còn `__prev`. Gỡ chặn bằng `--ack-rollback <run_id>` (trạng thái `rolled_back`, xóa `__prev`). Sao lưu `__prev` dùng `bypassDocumentValidation`. Lỗi không lường trước → mã thoát 6, `jobs.status = failed`. Logic quyết định (`pending_rollback`, `stale_staging`) tách thành hàm thuần Python có unit test.
- `vngis.province_labels`: `provisional` chỉ khi bản ghi cuối < mốc + `max_label_lag_days` (không gồm đầu mút).
- **GĐ7 — `$bit` của MongoDB dùng số nguyên 64 bit *không dấu***: mặt nạ `and` phải là số **dương** (`PERM_ALL ^ flag`), không phải `~flag` (số âm bị bỏ qua → quyền thu hồi không có tác dụng). Sửa `UserRepo.revoke`/`apply_bits`/`set_collection_perm`; `set_collection_perm(0)` nay **ghi 0** thay vì `$unset` (nếu xoá hẳn field thì `mask_of` quay về `perm["*"]` và quyền bị thu hồi tự quay lại).
- **GĐ7 — `effective_perm` không được gộp lại vai trò** khi `perm["*"]` đã khai báo (nếu gộp thì mọi thao tác thu hồi quyền trên `*` bị vô hiệu hoá).
- **GĐ7 — `LockGuard.write` không dùng `upsert=True`** (upsert bỏ qua điều kiện fencing → tiến trình đã lỗi thời ghi đè được lên tiến trình mới); nay cập nhật không upsert rồi thử chèn, `DuplicateKeyError` → `FenceRejected`.
- **GĐ7 — `vngis.locks.next_fence`** đọc lại giá trị đã tăng từ CSDL (mongomock trả bản ghi *trước* khi cập nhật và trả `None` khi vừa upsert).
- **GĐ7 — Router FastAPI không được vừa khai báo `prefix=` vừa đặt đường dẫn đầy đủ trong decorator** (sinh `/api/v1/users/users` → 404). Đã bỏ `prefix` ở `APIRouter`, giữ nguyên đường dẫn đầy đủ trong decorator.
- **GĐ7 — `clean()` trong `api/common.py` xoá `_id` trước khi `audit` ánh xạ `id`** → mất định danh; nay chuyển `str(_id)` sang `id` **trước** khi làm sạch.
- **GĐ7 — `api/deps.py` giới hạn tần suất theo phạm vi**: `scope="login"` dùng bộ riêng (`login_requests`, `login_window_s`) để không bị dùng chung hạn mức với API chung.
- **GĐ7 — `Config` là model Pydantic đóng băng** → cấu hình hạn mức trong test phải cập nhật bằng `model_copy(update=...)` chứ không gán trực tiếp.
- `infra/compose`: API đặt `APP_ENV: cluster`.
- `vngis` hỗ trợ Python ≥ 3.10 (image Spark); `infra/compose` (profile spark) dùng `MONGO_URI`/`MONGO_DB`; image API chép `config/` và đặt `VNGIS_CONFIG_DIR`.
- **GĐ7.1–GĐ7.8 (backend phân tán)**: `apps/api` (FastAPI, tiền tố `/api/v1`, `create_app()` + OpenAPI `/docs`), `apps/worker` (APScheduler + job nền), `packages/vngis` bổ sung `cache.py`, `locks.py`, `atomic_reload.py`; 101 test pytest (`apps/api/tests`, `apps/worker/tests`) chạy thật, không cần dịch vụ ngoài.
- **Bảo mật và đồng thời của `apps/api` (sửa theo review)**:
  - Log: logger `pymongo.*` luôn ≥ INFO kể cả khi `log_level: DEBUG` (trước đây command monitoring in nguyên document `users` kèm `password_hash`); thêm bộ lọc chặn log driver nhắc tới `users`/`password_hash`.
  - `POST /audit/{id}/revert` hết leo thang quyền: cần VIEW_AUDIT **và** quyền ghi trên collection đích; chỉ hoàn tác `qc_decision` (qua `vngis.qc.decide`, `expected_version`) và `bulletin_metadata` (qua `vngis.qc.update_bulletin_fields`, điều kiện giá trị cũ); loại khác 422. Ghi audit mới (`source: api_revert`), không đặt `state: "reverted"` (trái validator), chặn hoàn tác lặp bằng đánh dấu nguyên tử (409).
  - `PATCH /tides/{station}/{ts}` không ghi `tide_extremes` nữa: tạo cờ thủ công + `decide` (rejected/corrected/confirmed_ok, `version` bắt buộc khi cờ đã có, 428/409) để G0 áp; không ghi đè `value` của cờ. **Đổi body**: `status`, `corrected_value`, `kind`, `reason`, `version` (bỏ `value`, `qc`).
  - `POST /review/{id}/approve` khớp mô hình `qc_flags` (field `status`), gọi `vngis.qc.decide` có `expected_version`, bỏ tham số `collection` của client. **Đổi body**: `status`, `corrected_value`, `reason`, `version` (bỏ `decision`, `new_value`, `collection`); `GET /review` lọc theo `status` (mặc định `suspect`).
  - Khoá JWT: `.env.example` dùng `AUTH_SECRET` (tên code đọc; trước ghi nhầm `JWT_SECRET`); `APP_ENV` khác dev/test mà thiếu khoá hoặc khoá < 32 byte thì API dừng khởi động (`JwtConfigError`).
  - Refresh token tiêu thụ nguyên tử bằng `SET NX EX` (`refresh_used:{jti}`); dùng lại → thu hồi mọi phiên (tăng `perm_ver`).
  - Giới hạn tần suất: chỉ tin `X-Forwarded-For` từ `rate_limit.trusted_proxies` (`TRUSTED_PROXIES`, mặc định rỗng); `EXPIRE` chỉ khi `INCR` trả 1; scope `login` fail-closed (503) khi Redis lỗi.
  - `vngis.cache`: Redis chết thì `get`/`set`/`version`/`get_or_compute` bỏ qua cache, gọi `compute()`, log cảnh báo (không dùng phiên bản 0 khi không đọc được `ver:`). Blacklist Redis lỗi → 503 thay vì 500.
  - `PUT /users/{id}/perm`: dùng `perm_ver` (không phải `version`), ghi bằng một lệnh có điều kiện `perm_ver`, `set_collection_perm` nguyên tử, kiểm tra tên collection, ghi `audit_logs` (`user_perm`) với `reason` bắt buộc.
  - Bản tin: route `/pdf`, `/files` khai báo trước `{source_file:path}` (trước đây không bao giờ được gọi); `Cache-Control: private`; `Content-Disposition` có `filename*` UTF-8; projection của `/files` không trộn include/exclude.
  - `vngis.locks.LockGuard`: sau `next_fence` xác nhận còn giữ khoá (thời gian < validity hoặc gia hạn được, nếu không thì `LockLost`); `write()` không đọc lại bằng `find_one` sau insert; endpoint giữ khoá gọi `guard.check()` trước khi ghi (503 khi mất khoá). Giữ sửa `next_fence` lấy giá trị từ `ReturnDocument.AFTER`, watchdog gia hạn mỗi TTL/3 giây.
  - Đăng nhập không lộ tài khoản: luôn chạy `verify_password` (hash giả), trạng thái khoá/vô hiệu chỉ báo khi mật khẩu đúng.
- `vngis.locks.next_fence`: lấy fence từ kết quả `find_one_and_update` (trước đây đọc lại bằng `find_one` → hai worker có thể nhận cùng một fence); `Watchdog` gia hạn mỗi TTL/3 giây (trước đây chia thừa 1000 → 50 ms). `vngis.config` bỏ validator bắt buộc `redis.lock.nodes` (thiếu node báo lỗi khi gọi `vngis.db.redlock()`, như tài liệu).
- `infra/compose`: API nhận `AUTH_SECRET` từ `infra/compose/.env` (bắt buộc ≥ 32 byte khi `APP_ENV=cluster`).
- `apps/worker` test scheduler dùng datetime có múi giờ (trước đây lệch 7 giờ → misfire).
- **Rò rỉ dữ liệu test vào triều thiên văn (sửa theo review 24/11/2026)**: `pipelines/r/harmonic.R` trước đây fit hệ số điều hòa trên 2017–2026 (gồm cả tập test từ 2024-07-01), nên B_harm/B1 và cột `astro_*` là in-sample trên test. Nay fit **chỉ trên train** (2017–2022), mốc tách tập dùng chung ở `config/splits.json` (đọc bởi `phasegnn.data`, `pipelines/spark/features/splits.py`, `pipelines/r/R/io.R::load_splits`); script dừng nếu dữ liệu fit có ngày ≥ mốc test; ghi `data/processed/features/astro_tide_meta.json` và G5, `phasegnn`, `scripts/run_baselines.py` từ chối file triều thiếu meta hoặc fit lấn sang test. B0 trong `evaluation.R` chỉ dùng train. Kết quả E1/E2/E5/`results/baselines` trước ngày này **cần chạy lại** (đã ghi trong README từng thư mục).
- `phasegnn.data`: G5 dựng mẫu trên lưới ngày lịch liên tục (trước đây theo vị trí phần tử → lệch ngày ở 4,3 % mẫu train khi trạm thiếu ngày). `phasegnn.evaluate.match_predictions` lấy nguyên một dòng (trước đây `groupby().first()` trộn cột của các dòng khác nhau); giờ đỉnh thực đo lấy đúng đỉnh cao hơn; `_hours_to_iso` trong script baseline không còn sinh `T41:04Z`.
- `pipelines/r/R/evaluation.R`: khóa ghép cặp kiểm định thêm `issued_date`, `lead_day`; Wilcoxon so `|e_a|` với `|e_b|`, DM dùng `|e_a| − |e_b|` theo thời gian (kết luận cũ của E5 "cả 3 cặp khác biệt có ý nghĩa" không dùng được).
- Job features G1/G3/G4/G5 ghi Parquet vào `<đích>.tmp_<run_id>` rồi đổi tên (`features/outdir.py`), G4/G5 kiểm tra khóa G0 và `g0_unsettled` (`features/g0guard.py`, mã 3/4).
- Repo: gỡ khỏi git các file tạm (`tmp_*`), bản sao lưu `results/_backup_*`, log, mẫu chạy thử, checkpoint `.pt` và CSV dự báo theo từng bản ghi (file vẫn ở máy); `.gitignore` có luật tương ứng.

### Đặc tính và phân tích (PySpark)

- G1 `pipelines/spark/features/graph_build.py`: dựng đồ thị hai chiều từ cây MERIT `44045960`
  (1.073 reach) – 645 cạnh `down` + 646 cạnh `up` (1.291 cạnh); nút ảo `SEA_VUNGTAU`; cạnh ảo hồ chứa;
  tỉa theo `uparea ≥ 100 km²` (1.073 → 646 nút); `graph_edges` có validator `$jsonSchema` + index
  `{src,dst,dir}` unique, `{dir,src}`, `{dst}`; ghi `data/processed/graph/{edges,nodes}.parquet`.
- G3 `pipelines/spark/features/tau_phys.py`: τ vật lý theo cạnh – `tau_down` (Manning kinematic wave,
  `c = 5/3 · v`) và `tau_up` (shallow water, `c = √(g·h)` trừ vận tốc dòng chảy); `slope = 0` thay bằng
  trung vị cùng `order`, giữ `slope_raw`/`slope_used`; kẹp vận tốc, assert τ hợp lệ;
  `results/G3/tau_phys_summary.csv` + `tau_from_sea_by_station.csv`.
- G4 `pipelines/spark/features/rainfall_by_node.py`: mưa theo nút từ `rainfall_daily` (fallback dựa trạm
  cho sản phẩm vệ tinh chưa có) – 10 trạm trong bán kính 40 km, trọng số theo `uparea`, chuẩn hoá tổng = 1;
  `rain_mm_d1/d3/d7` ra `data/processed/features/rain_by_node/year=*/*.parquet` (2.322.370 dòng).
- G5 `pipelines/spark/features/build_training_set.py`: mẫu huấn luyện (trạm × ngày) từ `tide_extremes`
  (qc = ok, không suspect) – 32.352 dòng, cột đỉnh/chân + `peakN_day_offset`, `missing_peakN`,
  `exceed_level` theo `alarm_history`, hồ chứa, mưa, `lunar_day`, `moon_phase`, `astro_*` (đọc
  `data/processed/features/astro_tide.csv` của R nếu có, không có thì null + cảnh báo);
  **kiểm tra rò rỉ** `max_obs_day < day` (0 vi phạm); `_meta.json` có `data_snapshot` + ranh giới tập.
- Q1–Q7 `pipelines/spark/analytics/`: 7 job truy vấn (đỉnh triều & vượt cấp báo động, hồ chứa,
  MERIT/OSM, toàn vẹn dữ liệu, ghép OSM ↔ MERIT, đường tới cửa sả, độ chính xác dự báo nghiệp vụ);
  mỗi job ghi CSV + `README.md` vào `results/Q<n>/`; cache Redis tuỳ chọn qua `REDIS_URL`
  (thiếu thì bỏ qua có cảnh báo).

### Sửa (đặc tính và phân tích)
- `common/session.py`: `MongoAdmin.command` **tự giải mã JSON mở rộng** của MongoDB
  (`decode_extjson`) – driver Java `toJson()` trả canonical extended JSON nên BSON `int64` thành
  `{"$numberLong": "…"}` và BSON `Date` thành `{"$date": …}`. Trước đó COMID đọc từ `graph_edges` là
  dict nên so khớp khoá thất bại (Q6: `max_tau_up_h` luôn bằng 0). `_iso`/`from_date`/`_vn_date` ở
  `flatten/`, `qc/`, `features/` nhận cả hai dạng.
- `build_training_set.adm_max`: dùng `{$group: {_id: null, m: {$max: "$ts"}}}` thay cho
  `{$project: {m: {$max: "$ts"}}}` – dạng `$project` chỉ lấy giá trị tài liệu **đầu tiên**, làm
  `data_snapshot.max_timestamps` trong `_meta.json` sai (báo `2016-12-30` thay vì `2026-11-10`).
- `common/session.py`: `_parse_iso` chấp nhận phần thập phân 2 chữ số (`...46.21+00:00`) mà
  `datetime.fromisoformat` của Python 3.10 từ chối.
- `analytics/q6_paths_to_outlet.py`: khoá `tau_up` theo COMID kiểu `int` (khớp với đường đi) và
  chuẩn hoá `src`/`dst` qua `_comid()`.
- `pipelines/spark/README.md`: tên file job trong bảng Q1–Q7 và lệnh chạy đã đúng tên file thật;
  ghi rõ job `features/` cần `--master "local[*]"` (lỗi `ChecksumFileSystem` `_temporary` khi ghi
  Parquet trên volume Windows gắn vào container).
