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

### Sửa
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
- `infra/compose`: API đặt `APP_ENV: cluster`.
- `vngis` hỗ trợ Python ≥ 3.10 (image Spark); `infra/compose` (profile spark) dùng `MONGO_URI`/`MONGO_DB`; image API chép `config/` và đặt `VNGIS_CONFIG_DIR`.
