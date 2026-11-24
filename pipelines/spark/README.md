# pipelines/spark/ – Job PySpark

**Chức năng:** xử lý phân tán trên Spark cluster (đề cương Chương 3).

| Thư mục | Nội dung | Trạng thái |
|---|---|---|
| `common/` | SparkSession, đọc MongoDB có đẩy `$match`/`$project` xuống server, lệnh quản trị MongoDB qua driver Java của connector, khóa `job_locks`; bảo đảm import được `vngis` | có |
| `flatten/` | G0: bản tin → `tide_extremes`, `official_forecasts`, `reservoir_daily`, `rainfall_daily` | có |
| `qc/` | QC bản tin (GĐ2.3): quy tắc R1–R3 trên `tide_extremes` → `qc_flags`; báo cáo `results/qc/` | có |
| `tests/` | test quy tắc thuần Python của G0, báo cáo QC, G1/G3/G4/G5 và Q1–Q7 (không cần Spark) | có |
| `analytics/` | Q1–Q7: thống kê triều, hồ, MERIT/OSM, toàn vẹn dữ liệu, ghép OSM ↔ MERIT, đồ thị, độ chính xác KTTV | có |
| `matching/` | G2: gán trạm vào reach, ghép OSM ↔ MERIT (Sedona) | dự kiến |
| `repair/` | sửa `slope = 0`, gắn cờ nút biên | dự kiến |
| `features/` | G1 đồ thị hai chiều, G3 τ vật lý, G4 mưa theo nút, G5 mẫu huấn luyện (Parquet) | có |

**Dùng `vngis`.** Job import phần thuần Python của `vngis` (`vngis.text`, `vngis.admin`, `vngis.province_labels`;
chỉ thư viện chuẩn, vì image Spark không có pymongo/pydantic/pyyaml). `common/__init__.py` thêm
`<gốc repo>/packages/vngis/src` vào `sys.path` khi `vngis` chưa import được (repo gắn vào `/work`); cũng có thể gửi
`vngis` qua `--py-files`. Ở G0 các quy tắc chỉ chạy trên driver (không có UDF), executor không cần `vngis`.
Job QC chạy hàm `vngis.qc_rules` trên executor: `common/pyfiles.ship_vngis` nén gói `vngis` đang import (chỉ `*.py`)
và gửi bằng `SparkContext.addPyFile`. Khóa `job_locks` + nhật ký `jobs` dùng chung qua `common/jobrun.JobRun`.
Job **không** đọc `config/*.yaml` (cần pyyaml): URI/DB lấy từ tham số hoặc `MONGO_URI`/`MONGO_DB` (cùng tên biến với
`vngis.config`); `--max-lag-days` mặc định 31 = `admin.max_label_lag_days` trong `config/default.yaml`
(test `test_province_labels.py::test_mac_dinh_khop_config` giữ hai giá trị khớp nhau).

---

## G0 – Làm phẳng bản tin (`flatten/flatten_bulletins.py`)

### Chạy

Image `gis-spark:local` (Spark 3.5.9, MongoDB Spark Connector 10.4.1 Scala 2.12, Python 3.10) đã có connector,
không cần `--packages`. Không cần pymongo: lệnh quản trị chạy qua driver Java nằm trong jar connector.

```bash
# local[2] trong một container (gắn repo chỉ đọc vào /work)
docker run --rm --network spark-mongodb-gis_gis-net -e PYTHONIOENCODING=utf-8 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" gis-spark:local \
  /opt/spark/bin/spark-submit --master 'local[2]' /work/pipelines/spark/flatten/flatten_bulletins.py

# Trên cluster spark://spark-master:7077 (driver chạy trong container, executor phải gọi ngược về driver)
docker run --rm --name g0-driver --hostname g0-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" gis-spark:local \
  /opt/spark/bin/spark-submit --master spark://spark-master:7077 \
  --conf spark.driver.host=g0-driver --conf spark.executor.memory=1g \
  /work/pipelines/spark/flatten/flatten_bulletins.py
```

Trên Git Bash (Windows) thêm `MSYS_NO_PATHCONV=1` trước `docker run` để đường dẫn `/work/...` không bị đổi.

Tham số: `uri` (mặc định biến `MONGO_URI`, rồi `MONGODB_URI` (tên cũ, dự phòng; `infra/compose` nay đặt `MONGO_URI`), rồi `mongodb://mongo:27017`),
`--db` (mặc định `MONGO_DB`/`MONGODB_DB`, rồi `vietnam_gis`),
`--max-lag-days` (31), `--lock-ttl-s` (3600), `--dry-run` (chỉ ghi staging và kiểm tra), `--keep-staging`
(giữ staging của `tide_extremes` sau khi nạp), `--ack-rollback RUN_ID` (gỡ chặn mã 4, xem dưới; không chạy G0).
Staging của lần chạy `--dry-run` hoặc kiểm tra hỏng (mã 1) được giữ để xem và **bị dọn ở lần chạy sau**.

| Mã thoát | Nghĩa |
|---:|---|
| 0 | thành công; hoặc `--ack-rollback` đã gỡ chặn |
| 1 | kiểm tra hỏng, bản chính giữ nguyên; hoặc `--ack-rollback` với run_id không phải run đang chặn (không đổi gì) |
| 2 | có tên trạm/hồ/tỉnh không tra được mã (nhãn tỉnh rỗng KHÔNG tính) |
| 3 | job đang chạy ở nơi khác (khóa `job_locks` còn hạn), hoặc job QC đang chạy (G0 đọc `qc_flags`) |
| 4 | còn `__prev` của một lần chạy hỏng giữa chừng: lùi lại rồi `--ack-rollback` |
| 5 | mất khóa giữa chừng (không thay thêm collection nào) |
| 6 | lỗi không lường trước (traceback in ra, `jobs.status = failed`) |

`jobs.status`: `running` (đang chạy, hoặc process đã chết) → `ok` / `failed` / `dry_run` / `blocked` (mã 4) /
`rolled_back` (sau `--ack-rollback`). Đã kết thúc = mọi trạng thái trừ `running`.

**Gỡ chặn mã 4** (thông điệp lỗi in đúng các bước, kèm run_id):
(a) lùi lại từng collection trong `replaced` (và `replacing`, nếu process chết giữa lúc thay) của run hỏng từ bản
`<tên>__prev`: collection thường `renameCollection('<db>.<tên>__prev', '<db>.<tên>', dropTarget=true)`;
`tide_extremes`: aggregation trên `tide_extremes__prev` với `$out: {coll: "tide_extremes", timeseries: {…}}`;
(b) chạy job với `--ack-rollback <run_id>`: giữ khóa, kiểm tra run_id đúng là run đang chặn, in và xóa mọi `<tên>__prev`
còn lại, đặt `jobs[run_id].status = rolled_back`. Sau đó chạy G0 bình thường.
Chạy lại nhiều lần được; mỗi lần thay toàn bộ 4 collection. Thời gian: khoảng 1,5 phút trên cluster 1 worker × 2 core.

Test quy tắc (không cần Spark; cần `packages/vngis/src` trong repo):

```bash
python -X utf8 pipelines/spark/tests/test_flatten_rules.py
```

### Đầu vào

`hydromet_bulletins`, chỉ bản chính: pipeline `[{$match: {"source.is_primary": true, bulletin_type: "tide_forecast_5day"}}, {$project: {full_text: 0}}]`
đẩy xuống MongoDB; schema đọc tường minh (`flatten/schemas.py`). Thêm `stations` (mã trạm/hồ) và `province_timeline` (mã tỉnh).

**Bản tin chính trùng nội dung.** `vngis.bulletins.duplicate_exclusions` vẫn chạy mỗi lần: nhiều bản chính cùng
`source.sha256` thì giữ bản có ngày trên tên file trùng ngày trong nội dung, bỏ bản còn lại (danh sách in ra khi chạy).
Trước 23/11/2026 có 4 cặp như vậy (một file nằm sai thư mục ngày) và G0 dùng 3.590 bản tin. Sau kiểm tra tay K6
(23/11/2026, có `audit_logs`): 4 file sai thư mục (`2019/01/10/…20190110.pdf`, `2019/02/11/…20190211.pdf`,
`2020/02/07/…20200207.pdf`, `2020/07/05/…20200705.pdf`) đặt `source.is_primary = false`, 5 bản `khac/` là bản thật duy
nhất của ngày đó đặt `true` (bổ sung ngày quan trắc 2019-01-09, 2019-02-10, 2020-02-06, 2020-07-04, 2026-08-29).
Nay **danh sách bỏ rỗng** và G0 dùng **3.595 bản tin**.

**Ngày phát (`issued_date`)** tính một lần cho mỗi bản tin, dùng chung cho `official_forecasts` và `reservoir_daily`:
= ngày bản tin (`bulletin_date`, giờ VN). Khi ngày dự báo đầu tiên khác ngày bản tin, lấy ngày dự báo đầu tiên làm
`issued_date` và ghi `issued_date_note`. Trường hợp duy nhất từng gặp là `2025/11/08/HCMC_TVHN_20251108.pdf` (tiêu đề
chép lại bản 07/11: 15 document `official_forecasts`, 4 document `reservoir_daily`); sau kiểm tra tay K2 (23/11/2026)
`bulletin_date` của bản này đã sửa thành 08/11/2025 nên **nay 0 document có `issued_date_note`**; quy tắc vẫn giữ.

### Đầu ra

Mọi ngày giờ lưu BSON Date UTC; chuỗi ngày (`day`, `issued_date`, `target_date`) theo giờ Việt Nam `YYYY-MM-DD`.
**Các bản ghi mẫu dưới đây là giá trị MINH HỌA** (để thấy cấu trúc), không phải số liệu trích từ bản tin.

**`tide_extremes`** – time series (`timeField: ts`, `metaField: meta`, `granularity: hours`), index `station_ts_idx {meta.station: 1, ts: 1}`
(MongoDB tự tạo thêm `meta_1_ts_1`). Mỗi đỉnh/chân trong `observed_water_levels` là một document:

```json
{ "ts": ISODate("2024-03-10T02:00:00Z"),
  "meta": { "station": "PHU_AN", "kind": "high" },
  "value": 1.23, "qc": "ok", "ts_placeholder": false,
  "raw_value": 1.23, "raw_qc": "ok", "qc_flag": null,
  "source_file": "2024/03/11/HCMC_TVHN_20240311.pdf" }
```

- `meta` chỉ gồm `{station, kind}` (khóa nhận dạng chuỗi). `qc`, `ts_placeholder`, `source_file` là field thường:
  mỗi giá trị khác nhau của `meta` mở bucket riêng, nên để `source_file`/`qc` trong `meta` làm mỗi bucket chỉ chứa
  vài document của một bản tin. Đổi sang meta 2 khóa: **72.155 → 17.390 / 18.158 bucket** (`db.system.buckets.tide_extremes`,
  hai lần chạy; số bucket phụ thuộc thứ tự connector ghi song song), cùng 129.240 document (số lúc đo, trước K2/K6; nay 129.420). Lọc theo `qc`/`source_file` vẫn được (field thường của time series).
- `meta.station`: mã trong `stations` tra theo tên chuẩn hoặc alias, chỉ trong `kind` ∈ {`water_level`, `sea_level`}.
- **Giữ cả phần tử không có số** (quyết định cho mục checklist GĐ2.2): mọi ô đỉnh/chân của bản tin đều có một document, để
  chuỗi thời gian biết rõ ô nào trống và vì sao. Phần tử có cờ thì `value: null`, `qc` = cờ của bản tin.

| `qc` | Nghĩa | `value` |
|---|---|---|
| `ok` | có số và có giờ | số |
| `not_occurred`, `no_data`, `no_value`, `excel_error`, `unparseable` | cờ gốc của bản tin (`flag`) | `null` |
| `missing` | ô trống, bản tin không ghi cờ | `null` |
| `bad_time` | có số nhưng giờ không đọc được (ví dụ `112.30`) | số |
| `duplicate_time` | bản tin ghi 2 đỉnh (hoặc 2 chân) cùng giờ: khác giá trị thì cả hai mang mã này; cùng giá trị thì phần tử đầu giữ `ok`, phần tử sau mang mã này | số |

  | `rejected` | quyết định QC: loại (giá trị gốc ở `raw_value`) | `null` |
  | `corrected` | quyết định QC: sửa (giá trị gốc ở `raw_value`) | `corrected_value` |

  Khi dùng làm nhãn hoặc đầu vào, lọc `qc = "ok"` (và `qc_flag != "suspect"` nếu muốn bỏ cả phần tử nghi ngờ).
- **Áp `qc_flags` (GĐ2.3):** G0 đọc các cờ có hiệu lực (`vngis.qc_rules.is_active`: có quyết định, hoặc `suspect`
  chưa `auto_cleared`) và nối theo (`meta.station`, `meta.kind`, `ts`). `rejected` → `value: null`, `qc: "rejected"`;
  `corrected` → `value = corrected_value`, `qc: "corrected"`; `suspect`/`confirmed_ok` giữ `value`, `qc`.
  Mọi document có `raw_value`, `raw_qc` (giá trị và mã gốc của bản tin, không bao giờ đổi) và `qc_flag` (status của
  cờ, `null` nếu không có cờ) để lọc. Cờ có hiệu lực mà không khớp phần tử nào → kiểm tra hỏng (mã 1).
- **`ts` giữ chỗ:** phần tử không có giờ (và phần tử sau trong cặp `duplicate_time`) có `ts` = 00:00:0*k* giờ Việt Nam
  của ngày quan trắc, *k* = vị trí phần tử + 1 (1 hoặc 2 giây), và `ts_placeholder: true`. Giờ thật luôn tròn phút
  nên `ts` giữ chỗ không trùng giờ thật; khóa (station, kind, ts) vẫn duy nhất.
- Time series **không nhận `validator`** trên MongoDB 8.0 (`'timeseries' is not allowed with 'validator'`); job tự kiểm tra thay cho validator.

**`official_forecasts`** – mỗi (bản tin, trạm, ngày dự báo) một document; validator `$jsonSchema`;
index `station_issued_target_unique` (unique), `station_target_lead_idx`, `source_file_idx`.

```json
{ "station": "PHU_AN", "issued_date": "2024-03-11", "target_date": "2024-03-13", "lead_day": 3,
  "high": [ { "ts": ISODate("2024-03-13T03:00:00Z"), "value": 1.30, "qc": "ok" }, { "ts": null, "value": null, "qc": "not_occurred" } ],
  "low":  [ { "ts": ISODate("2024-03-12T20:00:00Z"), "value": -1.80, "qc": "ok" }, { "ts": ISODate("2024-03-13T09:30:00Z"), "value": -0.40, "qc": "ok" } ],
  "issued_date_note": null, "source_file": "2024/03/11/HCMC_TVHN_20240311.pdf" }
```

- `lead_day` = `target_date − issued_date + 1` (ngày phát = 1).
- Phần tử đỉnh/chân giữ nguyên 2 + 2 như bản tin; `ts` `null` khi không có giờ; `qc` cùng bảng mã như trên (trừ `duplicate_time`).

**`reservoir_daily`** – mỗi (ngày quan trắc, hồ) một document; validator; index `reservoir_date_unique` (unique), `province_date_idx`.

```json
{ "date": ISODate("2025-07-02T17:00:00Z"), "day": "2025-07-03", "issued_date": "2025-07-04", "issued_date_note": null,
  "reservoir": "RES_DAU_TIENG", "river": "Sài Gòn",
  "upstream_water_level_m": 20.00, "inflow_m3s": 100, "turbine_discharge_m3s": null, "spillway_discharge_m3s": 0,
  "rainfall_mm": null, "notes": null, "flags": { "turbine_discharge_m3s": "no_value" },
  "province": { "era": "before_2025_07", "code": 72, "note": "bản tin còn ghi tên tỉnh cũ sau mốc 01/07/2025 (đổi tên chậm); …",
                "provisional": false },
  "province_flag": null,
  "source_file": "2025/07/04/HCMC_TVHN_20250704.pdf" }
```

- `date`/`day` = `observation_date`; `reservoir` = mã `stations` (`kind: reservoir`); `flags` luôn là object (rỗng nếu không có cờ);
  `rainfall_mm` chỉ có số ở mẫu bản tin cũ, `notes` chỉ ở mẫu mới.
- **Tỉnh** (`vngis.province_labels`, cùng quy tắc với `stations.admin_history`): nhãn tỉnh trong bản tin → `province: {era, code, note}`
  tra trong `province_timeline` (so theo `norm_name`, ví dụ `TPHCM` → `tphcm`). **Thời kỳ theo NGÀY QUAN TRẮC** so với mốc
  `2025-06-30T17:00:00Z` (ADR 0003, `vngis.admin.era_of`). Đổi tên chậm: đoạn sau mốc dùng lại đúng nhãn (đã chuẩn hóa) của
  đoạn trước mốc **và** nhãn khác xuất hiện trong `--max-lag-days` (31) ngày kể từ mốc → tra thời kỳ cũ, ghi `note`.
  Cần vì "Tây Ninh" có ở cả hai thời kỳ với hai mã khác nhau (72 cũ, 80 mới). Ngoài giới hạn: tra thời kỳ mới, không có thì
  báo không khớp (mã thoát 2), không tự lùi về thời kỳ cũ.
  - **`province.era` là thời kỳ ĐEM TRA mã**, không phải thời kỳ của ngày quan trắc: ở đoạn đổi tên chậm ngày quan trắc
    thuộc `from_2025_07` nhưng `province.era = before_2025_07`. Thời kỳ của ngày quan trắc luôn suy ra được từ `date`
    (`vngis.admin.era_of`). Tương ứng trong `stations.admin_history[]`: `era` = thời kỳ theo ngày quan trắc của đoạn,
    `matched_era` = thời kỳ đem tra (= `province.era` ở đây).
  - **Đoạn cuối dữ liệu** (sau mốc, chưa có nhãn mới phía sau) KHÔNG bao giờ được coi là đổi tên chậm (tra thời kỳ mới).
    Nếu nó dùng lại nhãn trước mốc và bản ghi cuối còn nằm trong `--max-lag-days` kể từ mốc thì `province.provisional = true`
    (chưa chắc: bản tin có thể đổi tên ngay sau đó; chạy lại khi có thêm dữ liệu). Dữ liệu hiện tại: không có đoạn nào như vậy.
  - **Nhãn tỉnh rỗng/`None`** = không có thông tin: không làm đứt đoạn và không được dùng làm đoạn trước/sau khi xét đổi tên
    chậm; bản ghi đó có `province = null`, `province_flag = "empty_label"` và không chặn job. Dữ liệu hiện tại: 0 bản ghi.
  Kết quả: 14 đoạn nhãn, 0 không khớp; 18 document có `note` (Thác Mơ, Sr.P.Miêng → Bình Phước 70; Dầu Tiếng → Tây Ninh 72;
  ngày quan trắc 01–06/07/2025, nhãn mới từ 07/07/2025). `stations.admin_history` của Dầu Tiếng nay cũng gán đoạn này vào 72.

**`rainfall_daily`** – mỗi (ngày quan trắc, trạm mưa) một document; validator; index `station_date_unique` (unique).

```json
{ "date": ISODate("2024-03-09T17:00:00Z"), "day": "2024-03-10", "station": "RAIN_MAC_DINH_CHI",
  "rainfall_mm": null, "flag": "no_rain", "period": "từ 7h 10/03 đến 7h 11/03", "source_file": "2024/03/11/HCMC_TVHN_20240311.pdf" }
```

- `station` = mã `stations` (`kind: rain`, tra cả alias như `Thủ Dầu I`); `day` = `observation_date`; `period` giữ khoảng đo gốc.
- `flag`: cờ gốc của bản tin (`no_rain`…); không có số và bản tin không ghi cờ → `"missing"` (100 document). Validator:
  `rainfall_mm` là số, hoặc `flag` là chuỗi.

### Chạy an toàn và nạp nguyên tử

1. **Khóa chống chạy chồng**: document `{_id: "g0_flatten_bulletins", run_id, host, acquired_at, expires_at}` trong
   `job_locks` (index TTL `expires_at`). Lấy nguyên tử bằng `findAndModify` + upsert (chỉ khớp khi khóa cũ hết hạn; khóa còn hạn →
   trùng `_id` → mã thoát 3). Lỗi khác khi lấy khóa → thử nhả theo `run_id` rồi báo lỗi.
   **Gia hạn và xác nhận** (`findAndModify {_id, run_id}` đặt lại `expires_at`) trước mỗi pha dài (ghi staging, kiểm tra,
   sao lưu) và trước MỖI lần thay; không khớp (khóa đã hết hạn và bị lần chạy khác lấy) → dừng, mã thoát 5, không thay gì thêm.
   Nhả trong `try/finally` riêng (chỉ xóa khóa có đúng `run_id`); ghi `jobs` lỗi ở bước cuối chỉ in log, không chặn nhả khóa.
2. **`run_id`** = `YYYYMMDDTHHMMSSZ_<6 hex>`. Bản ghi `jobs` (`_id = run_id`): `job`, `generated_at`, `status`
   (`running` → `ok` / `failed` / `dry_run` / `blocked` / `rolled_back`), `staging`, `expected`, `checks`, `replacing`,
   `replaced`, `counts`, `finished_at` (`rolled_back_at` khi gỡ chặn).
3. Staging `<tên>__staging_<run_id>` (time series, hoặc collection thường có validator); khi đã giữ khóa, chỉ xóa staging của
   lần chạy ĐÃ KẾT THÚC (`jobs.status` ∈ `ok`/`failed`/`dry_run`/`blocked`/`rolled_back`), hoặc không có trong `jobs` / vẫn `running` nhưng cũ hơn hạn
   khóa (`--lock-ttl-s`); staging trẻ của lần chạy khác được giữ lại. Ghi bằng connector mode `append` (mode `overwrite` drop collection, mất cấu hình), `writeConcern.w = majority`.
4. Tạo index trên staging (index unique cũng là một bước kiểm tra trùng khóa), chạy các kiểm tra, gồm **số document staging
   = số kỳ vọng** (tính độc lập từ `hydromet_bulletins` hoặc từ số dòng làm phẳng). Hỏng → dừng, bản chính giữ nguyên.
5. **Sao lưu**: mỗi bản chính được chép (`$out`) sang `<tên>__prev` với đúng tùy chọn (validator, time series) và index
   của BẢN CHÍNH HIỆN TẠI (đọc bằng `listCollections`/`listIndexes`, không dùng validator mới – bản cũ có thể không thỏa
   validator mới), `$out` với `bypassDocumentValidation: true` (chép nguyên trạng). Nếu đã có `__prev` mà kể từ lần
   chạy `ok` gần nhất có run khác với `status` ∈ {`failed`, `running`} và `replaced` hoặc `replacing` khác rỗng (hỏng
   giữa chừng: thay xong nhưng bước sau hỏng, hoặc process chết giữa lúc thay; `__prev` là bản để lùi lại) → dừng,
   mã thoát 4, `jobs.status = blocked`, in quy trình gỡ chặn, KHÔNG xóa `__prev` (kiểm tra cả lúc bắt đầu và ngay
   trước khi sao lưu). Quyết định nằm trong hàm thuần Python `rules.pending_rollback` (dọn staging: `rules.stale_staging`),
   có unit test.
6. **Thay** từng collection, ngay trước đó kiểm tra lại số document staging = kỳ vọng và ghi `jobs.replacing = <tên>`;
   ngay sau lệnh thay thành công ghi `replaced` (thêm tên) và `replacing = null`, rồi mới đếm lại bản chính:
   - Collection thường: `renameCollection(dropTarget: true)`.
   - **`tide_extremes`**: đã thử trên MongoDB 8.0.38 (FCV 8.0): `renameCollection` với time series báo lỗi
     `cannot rename view` (time series là view trên `system.buckets.*`). Job dùng aggregation
     `[{$out: {db, coll: "tide_extremes", timeseries: {…}}}]` trên staging: MongoDB (≥ 7.0.3) ghi vào collection tạm rồi
     thay collection đích **nguyên tử**, giữ index của collection đích.
   - Mọi `renameCollection`/`$out`/`create`/`drop` dùng `writeConcern: {w: "majority"}`.
7. Thay xong cả 4 → xóa `__prev`. Lỗi giữa chừng → in collection nào đã thay / chưa thay, giữ `__prev` và in quy trình
   gỡ chặn (lùi lại, rồi `--ack-rollback <run_id>`; xem mục mã thoát ở trên).

### Kiểm tra (lần chạy gần nhất `20261123T082046Z_bf6707`, sau K2/K6, QC và bản sửa `decided_value`; cluster 1 worker × 2 core)

| Kiểm tra | Kết quả |
|---|---|
| Tên trạm mực nước, trạm dự báo, hồ, trạm mưa đều có mã trong `stations` | đạt, không có tên lạc |
| Tên tỉnh của hồ → `{era, code}` | đạt, 14 đoạn nhãn, 0 không tra được |
| `tide_extremes` có `raw_value ≠ null` = số đỉnh/chân có số trong bản tin chính (aggregation độc lập trên `hydromet_bulletins`, N_src); `raw_qc` ∈ {ok, duplicate_time} = số có số và có giờ (N_src_time) | 121.751 = 121.751 (không còn bản trùng bị bỏ); 121.729 = 121.729 |
| Sau khi áp QC: `value ≠ null` = N_src − R_num + C_null; `qc` ∈ {ok, duplicate_time} = N_src_time − D_ok (công thức dưới) | 121.750 = 121.751 − 1 + 0; 121.728 = 121.729 − 1 |
| Mỗi cờ có hiệu lực áp đúng một phần tử: số `qc_flag = s` = số cờ status s (và `value_mismatch` = số cờ không áp vì giá trị gốc đổi); số `qc = rejected`/`corrected` = số cờ tương ứng | 343 suspect, 36 confirmed_ok, 1 rejected, 0 corrected, 0 value_mismatch |
| Cờ đã quyết định/thủ công đều trỏ tới một phần tử (cờ mồ côi `auto` chưa quyết định: cảnh báo, bỏ qua) | 380 cờ có hiệu lực; 0 mồ côi |
| `rainfall_daily`: không có `rainfall_mm` và `flag` cùng null | 0 (100 document `flag = missing`) |
| Tổng document `tide_extremes` = tổng ô đỉnh/chân | 129.420 = 3.595 × 9 trạm × 4 |
| `official_forecasts`: mỗi bản tin chính = 5 ngày × số trạm dự báo, `lead_day` đủ 1…5 | đạt cho 3.595 bản tin (3.103 bản 2 trạm, 492 bản 3 trạm), 0 sai; 7.682 document mỗi `lead_day` |
| Không trùng (station, kind, ts) trong `tide_extremes` | 0 khóa trùng (9 cặp cùng giờ trong cùng bản tin được xử lý bằng `duplicate_time`) |
| 5 bản ghi ngẫu nhiên: giờ UTC + 7 = giờ `HH:MM` gốc | 5/5 khớp |
| Số document staging = số kỳ vọng (cả 4 collection) | đạt |

**Công thức số lượng sau khi áp QC.** Đếm độc lập trên `hydromet_bulletins` (bản chính đã bỏ trùng): N_src = số ô
đỉnh/chân có số, N_src_time = số ô có số và có giờ. Trên staging: R_num = số phần tử `qc = rejected` có `raw_value ≠ null`
(loại một số thật → mất một giá trị); C_null = số `qc = corrected` có `raw_value = null` (điền số vào ô trống → thêm một
giá trị; corrected trên ô đã có số không đổi số lượng); D_ok = số phần tử `rejected`/`corrected` có `raw_qc` ∈ {ok,
duplicate_time} (rời nhóm "ok"). Khi đó `raw_value ≠ null` = N_src, `value ≠ null` = N_src − R_num + C_null và
`qc ∈ {ok, duplicate_time}` = N_src_time − D_ok. Tổng document không đổi (QC không thêm/bớt phần tử).

| Collection | Số document |
|---|---:|
| `tide_extremes` | 129.420 (qc: `ok` 121.715, `rejected` 1, `not_occurred` 4.106, `missing` 3.391, `no_data` 86, `no_value` 75, `bad_time` 22, `duplicate_time` 13, `excel_error` 9, `unparseable` 2) |
| `official_forecasts` | 38.410 (7.682 mỗi `lead_day`) |
| `reservoir_daily` | 15.313 |
| `rainfall_daily` | 13.127 (`flag = missing` 100) |

Trước K2/K6 (run `20261123T034710Z_7c45a8`): 3.590 bản tin; `tide_extremes` 129.240 (giá trị gốc có số 121.578),
`official_forecasts` 38.355, `reservoir_daily` 15.290, `rainfall_daily` 13.110.

**Cờ QC mồ côi và giá trị gốc đổi.** Cờ có hiệu lực mà phần tử không còn (ví dụ đổi bản chính): cờ `auto` chưa ai xem
→ G0 in `[CẢNH BÁO]`, bỏ qua, job QC xóa ở lần chạy sau; cờ đã có quyết định hoặc cờ thủ công → G0 dừng mã 1, in
`[CHẶN]` kèm lệnh `pipelines/qc/qc_decide.py retire <id>` (người xem rồi đánh dấu `retired`, có audit). Quyết định
(`confirmed_ok`/`rejected`/`corrected`) mà giá trị gốc hiện tại ≠ giá trị quyết định đang gắn vào → KHÔNG áp, `qc_flag =
"value_mismatch"`, in cảnh báo (job QC ghi `value_mismatch`/`current_value` vào cờ); người xem rồi quyết định lại.
Hàm phân loại: `flatten/rules.classify_unmatched` (có test).

> **Giá trị nào được so sánh:** G0 so `raw_value` hiện tại với `qc_flags.decided_value` (giá trị quyết định đang gắn
> vào) nếu có, ngược lại với `qc_flags.value`. `decided_value` do `vngis.qc.decide` ghi khi người xem quyết định; `value`
> giữ nguyên làm mốc lúc gắn cờ. Nhờ vậy người xem đã quyết định một cờ `value_mismatch` không bị gắn lại mismatch vô
> hạn khi G0 chưa kịp cập nhật `tide_extremes` (đường thoát duy nhất của cờ này — xem `pipelines/qc/README.md`).

### Quyết định

- `meta` của `tide_extremes` = `{station, kind}`; `source_file`, `qc`, `ts_placeholder` là field thường (ít bucket, nén tốt hơn).
- Thời kỳ địa giới của `reservoir_daily` theo ngày quan trắc (không theo ngày bản tin); quy tắc tỉnh dùng chung với `stations`.
- `issued_date` một nguồn cho cả `official_forecasts` và `reservoir_daily`.
- Khóa chạy chồng bằng document MongoDB (`job_locks`) vì job chỉ cần chống chạy hai bản cùng lúc và image Spark không có
  client Redis; job chạy từ worker/web về sau vẫn nên giữ thêm `vngis.locks`.
- `run_id`/`generated_at` ghi vào một bản ghi `jobs`, không lặp lại trong từng document.

### Việc còn lại

- Bộ đếm phiên bản `ver:tide_extremes` sau mỗi lần nạp (Redis hoặc collection `meta`).
- Validator `$jsonSchema` và migration đánh số cho 4 collection và `job_locks`/`jobs` trong `schemas/mongo/`, `migrations/mongo/`.
- Đóng gói `vngis` thành `.zip` cho `--py-files` khi chạy không gắn repo.

---

## QC – Quy tắc tự động trên `tide_extremes` (`qc/qc_tide_extremes.py`, GĐ2.3)

### Chạy

```bash
# Trên cluster; results/qc gắn ghi được vào /out để xuất báo cáo (bỏ --report-dir thì không ghi file)
MSYS_NO_PATHCONV=1 docker run --rm --name qc-driver --hostname qc-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/results/qc:/out" gis-spark:local \
  /opt/spark/bin/spark-submit --master spark://spark-master:7077 --conf spark.driver.host=qc-driver \
  --conf spark.executor.memory=1g /work/pipelines/spark/qc/qc_tide_extremes.py --report-dir /out
```

Tham số: `uri`, `--db` (như G0), `--params JSON` (ghi đè tham số, ví dụ `'{"r1": {"margin": 0.2}}'`),
`--lock-ttl-s` (3600), `--report-dir`, `--dry-run` (tính cờ, in kế hoạch, không ghi). Khoảng 1 phút trên cluster.
Mã thoát: 0 ok / dry-run, 1 kiểm tra hỏng, 3 job này hoặc G0 đang chạy, 4 G0 còn dở thay thế (còn `<tên>__prev`,
hoặc run G0 `failed`/`running` đã thay một phần sau lần `ok` gần nhất – `flatten/rules.g0_unsettled`, dùng lại
`pending_rollback`; xử lý theo hướng dẫn của G0 trước), 5 mất khóa, 6 lỗi không lường trước.
Khóa `job_locks._id = "qc_tide_extremes"`, nhật ký `jobs` (`job`, `params`, `stats` gồm biên R1 và thống kê độ trễ R3
theo trạm, `ops`, `flags`). G0 và QC kiểm tra khóa của nhau sau khi giữ khóa của mình (không chạy chồng).

Thứ tự: G0 → QC → ghi quyết định (`pipelines/qc/qc_decide.py`) → G0 (áp quyết định) → QC (báo cáo `results/qc/`).

### Quy tắc (`vngis.qc_rules`, tham số `config/default.yaml` mục `qc`)

Xét trên giá trị GỐC (`raw_value`/`raw_qc`; bản `tide_extremes` cũ chưa có thì `value`/`qc`), nên quyết định đã áp
không làm cờ biến mất.

| Quy tắc | Điều kiện gắn `suspect` |
|---|---|
| R1 | theo (trạm, đỉnh/chân): giá trị ngoài [p0,1% − 0,1·(p99,9% − p0,1%), p99,9% + 0,1·(p99,9% − p0,1%)] (phân vị chính xác `percentile` của Spark), hoặc ngoài ngưỡng vật lý: Vũng Tàu (mốc Hải đồ) [−1; 5] m, trạm sông [−3,5; 3,5] m. Đúng bằng biên không gắn |
| R2 | cùng trạm, cùng ngày quan trắc (cùng bản tin): đỉnh thấp nhất ngày < chân cao nhất ngày (`min_gap_m` 0). Chỉ gắn cặp vi phạm lớn nhất (đỉnh thấp nhất và chân cao nhất), không gắn mọi phần tử của ngày |
| R3 | trạm sông, đỉnh có giờ thật và qc gốc `ok`: độ trễ = giờ đỉnh − giờ đỉnh Vũng Tàu trước đó (≤ 14 h). Lượt 1: đỉnh Vũng Tàu gần nhất trước đó → trung vị sơ bộ; lượt 2: trong các đỉnh Vũng Tàu trước đó, đỉnh làm độ trễ gần trung vị sơ bộ nhất → trung vị, IQR. Gắn khi \|trễ − trung vị\| > max(3·IQR, 3 h). Bỏ qua khi ngày (giờ VN) của thời điểm tham chiếu kỳ vọng có < 2 đỉnh Vũng Tàu tin được (đỉnh kia "không xảy ra": không biết ghép chu kỳ nào) |

Thử trên dữ liệu: R3 chỉ với "đỉnh gần nhất trước đó" cho 1.155 cờ R3, phần lớn ở ngày Vũng Tàu chỉ có 1 đỉnh (độ trễ
nhảy 9–13 h do ghép sang chu kỳ trước); thêm hai điều kiện trên còn 191 cờ R3. Đỉnh Vũng Tàu có quyết định `rejected`
bị loại khỏi tập tham chiếu (hiện chưa có); các trạm vẫn đánh giá trên giá trị gốc.

**Giới hạn của R3 hai lượt:**
- Lượt 2 chỉ chọn khác lượt 1 khi cửa sổ 14 h trước đỉnh sông chứa hai đỉnh Vũng Tàu, tức độ trễ lượt 1 ≤ 14 − 12,4
  ≈ 1,6 h (chu kỳ M2 khoảng 12,4 h): chỉ sửa được trường hợp đỉnh sông đến rất sớm sau một đỉnh Vũng Tàu không tương ứng.
- Không bắt được lệch đúng một chu kỳ (khoảng 12,4 h hoặc 24,8 h): giờ sai đúng một chu kỳ vẫn ghép được với một đỉnh
  Vũng Tàu khác và cho độ trễ bình thường.
- Đỉnh sát nửa đêm: ngày tham chiếu kỳ vọng (giờ đỉnh − trung vị) có thể rơi vào ngày trước, điều kiện "đủ 2 đỉnh
  Vũng Tàu" xét ngày đó; ngày quan trắc trong bản tin tính theo ngày lịch nên cặp đỉnh qua nửa đêm có thể bị bỏ qua.
- Thiên về bắt thiếu: bỏ qua mọi ngày Vũng Tàu chỉ có 1 đỉnh và mọi đỉnh không có giờ thật / qc khác `ok`; giới hạn
  ±max(3·IQR, 3 h) rộng so với IQR 0,5–0,75 h. Cờ R3 nên coi là "chắc chắn bất thường", không phải danh sách đầy đủ.

**R2 và triều hỗn hợp:** ở trạm sông xa (Dầu Tiếng, Gò Dầu Hạ) và những ngày nhật triều, đỉnh phụ rất thấp và chân phụ
rất cao nên đỉnh thấp nhất có thể thấp hơn chân cao nhất mà số liệu vẫn đúng; một phần cờ R2 là hiện tượng thật. Chỉ gắn
cặp cực trị nên mỗi ngày tối đa 2 cờ R2 (trước vòng sửa này: gắn mọi đỉnh/chân vi phạm, 177 cờ R2; nay 154).

Thực thi: đọc `tide_extremes` (SinglePartitionPartitioner – time series là view), phân vị R1 bằng Spark SQL; đỉnh tham
chiếu gom về driver (khoảng 6,7 nghìn) rồi broadcast; thống kê độ trễ theo trạm (`groupByKey`) và đánh giá theo
(trạm, bản tin) (`groupByKey` + `flatMap(evaluate_group)`) chạy trên executor.

### Gộp vào `qc_flags`

`_id = "tide_extremes:<trạm>:<kind>:<ts UTC ISO>"`, index unique (`collection`, `station`, `kind`, `ts`). Kế hoạch
(`qc_rules.plan_updates`, thuần Python, có test): cờ mới → insert `suspect`, `origin: auto`, `version 1`; cờ có sẵn mà
quy tắc/giá trị đổi → cập nhật `rules`, `rules_version`, `auto_cleared` (`version + 1`, điều kiện `{_id, version}`,
xung đột thì đọc lại và tính lại); không đổi gì → chỉ `last_seen_run` (không tăng version); quy tắc không còn kích hoạt
→ cờ `auto` chưa ai xem (`decided_at = null`) bị xóa, còn lại `auto_cleared: true`. Không bao giờ ghi
`status`/`corrected_value`/`reason`/`decided_by`/`decided_at`. Có quyết định mà giá trị gốc đổi → `value_mismatch: true`,
`current_value` (so với `decided_value` nếu có, ngược lại so với `value`). Cờ `retired` (người đánh dấu mồ côi) → job
không đụng tới. Cờ thủ công luôn có hiệu lực
(`qc_rules.is_active`), kể cả khi quy tắc tự động gắn rồi bỏ. Kiểm tra: số cờ có quy tắc đang kích hoạt (trừ `retired`)
= số phần tử bị quy tắc gắn. Executor chỉ nhận các module thuần Python của `vngis` (`qc_rules.PURE_MODULES`).

### Kết quả lần chạy gần nhất (`20261123T082255Z_9f041d`, 3.595 bản tin)

344 phần tử bị gắn (R1 13, R2 154, R3 191; một phần tử có thể nhiều quy tắc). Lần đổi R2 sang "cặp cực trị"
(`20261123T043115Z_833946`) xóa 23 cờ auto chưa quyết định. Lần chạy này (`ops`: `touch` 343 + `update` 1) chỉ làm mới
`rules` của cờ K1 (`metric` R1 nay có thêm `station`/`kind`), **không** đụng field quyết định nào.

| Trạm | R1 | R2 (trước → sau) | R3 |
|---|---:|---:|---:|
| BEN_LUC | 2 | 23 → 18 | 12 |
| BIEN_HOA | 3 | 6 → 6 | 9 |
| DAU_TIENG | 1 | 64 → 58 | 76 |
| GO_DAU_HA | 3 | 47 → 40 | 33 |
| NHA_BE | 1 | 2 → 2 | 11 |
| PHU_AN | 0 | 2 → 2 | 7 |
| TAN_AN | 1 | 4 → 4 | 22 |
| THU_DAU_MOT | 0 | 14 → 12 | 21 |
| VUNG_TAU | 2 | 15 → 12 | – |

Biên Hòa 11,18 m (đỉnh 2021-04-17T02:00:00Z, bản tin 2021/04/18) bị R1 gắn (> 2,2775 m và > ngưỡng vật lý 3,5 m).
`qc_flags`: 380 cờ (344 `auto` + 36 `manual`) = 343 `suspect`, 36 `confirmed_ok` (thủ công, K5), 1 `rejected` (K1);
0 `retired`, 0 `auto_cleared`, 0 `value_mismatch`. Báo cáo: `results/qc/`.

**`decided_value` sau bản sửa 23/11/2026: vẫn 0.** `decided_value` chỉ được ghi bởi `vngis.qc.decide` (người xem
quyết định), **không** phải bởi G0 hay job QC. 37 quyết định K1–K5 đã lưu **trước** khi có field này → vẫn chỉ có
`value`; job QC so giá trị gốc với `decided_value` rồi **fallback `value`** → 0 `value_mismatch` là đúng. Đã kiểm
tra cả 37 cờ có quyết định: `value` khớp tuyệt đối `raw_value` hiện tại trong `tide_extremes`. `decided_value` chỉ
xuất hiện từ quyết định MỚI (lần xem lại một cờ `value_mismatch`).

### Việc còn lại (QC)

- Validator `qc_flags`, `audit_logs` hiện nằm trong `vngis.qc_rules` (tạo bởi job/CLI); cần bản trong `schemas/mongo/` + migration đánh số.
- Tăng `ver:tide_extremes` khi quyết định QC thay đổi dữ liệu dùng cho mô hình.

---

## Đặc tính kỹ thuật – G1/G3/G4/G5 (`features/`)

Bốn job đặc tính, chạy theo thứ tự **G1 → G3 → G4 → G5** (G5 cần đồ thị + τ + mưa).

| Job | Viết gì | Đọc gì |
|---|---|---|
| `features/graph_build.py` (G1) | `graph_edges` (MongoDB), `data/processed/graph/{edges,nodes}.parquet` | `merit_river_reaches`, `stations` |
| `features/tau_phys.py` (G3) | `graph_edges.tau_phys_h`, `results/G3/*.csv` | `graph_edges`, `merit_river_reaches` |
| `features/rainfall_by_node.py` (G4) | `data/processed/features/rain_by_node/year=*/*.parquet`, `results/G4/*.csv` | `rainfall_daily`, `stations`, `graph_edges` |
| `features/build_training_set.py` (G5) | `data/processed/features/g5/year=*/*.parquet`, `data/processed/features/g5/_meta.json` | `tide_extremes`, `reservoir_daily`, `rainfall_daily`, `hydromet_bulletins`, `stations`, `data/processed/features/astro_tide.csv` (nếu có) |

### Chạy

```bash
# G1 – dựng đồ thị hai chiều
MSYS_NO_PATHCONV=1 docker run --rm --name g1-driver --hostname g1-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/data/processed:/work/data/processed" gis-spark:local \
  /opt/spark/bin/spark-submit --master "local[*]" --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/graph_build.py --min-uparea-km2 100

# G3 – thời gian truyền vật lý
MSYS_NO_PATHCONV=1 docker run --rm --name g3-driver --hostname g3-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/data/processed:/work/data/processed" -v "<gốc repo>/results:/out" gis-spark:local \
  /opt/spark/bin/spark-submit --master "local[*]" --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/tau_phys.py --out-dir /out/G3

# G4 – mưa theo nút (fallback dựa trạm)
MSYS_NO_PATHCONV=1 docker run --rm --name g4-driver --hostname g4-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/data/processed:/work/data/processed" -v "<gốc repo>/results:/out" gis-spark:local \
  /opt/spark/bin/spark-submit --master "local[*]" --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/rainfall_by_node.py --out-dir /work/data/processed/features/rain_by_node --report-dir /out/G4

# G5 – mẫu huấn luyện
MSYS_NO_PATHCONV=1 docker run --rm --name g5-driver --hostname g5-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/data/processed:/work/data/processed" gis-spark:local \
  /opt/spark/bin/spark-submit --master "local[*]" --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/build_training_set.py
```

> **Lưu ý:** các job `features/` ghi Parquet nên chạy với `--master "local[*]"` (driver trong container).
> Dùng `--master spark://spark-master:7077` sẽ lỗi `Mkdirs failed to create .../_temporary/0/_temporary`
> vì Hadoop `ChecksumFileSystem` không đổi tên được thư mục tạm trên volume Windows gắn vào container.
> Các job `analytics/` chỉ ghi CSV nên chạy trên cụm `spark://spark-master:7077` như bình thường.

Tham số chung: `uri`, `--db` (mặc định `MONGO_URI`/`MONGO_DB`), `--lock-ttl-s` (3600), `--dry-run` (G1/G3/G4/G5 đều hỗ trợ:
tính toán và in kế hoạch, **không** ghi MongoDB/Parquet). Tham số riêng:
G1 `--min-uparea-km2` (mặc định 100); G3/G4 `--out-dir` (mặc định `results/G3`, `results/G4`).
Mã thoát: 0 ok / dry-run, 1 kiểm tra hỏng (vd. G1 phát hiện chu trình), 2 không phân giải được tên (trạm/hồ),
3 job đang chạy ở nơi khác, 4 còn dở thay thế (`<collection>__prev`), 5 mất khóa, 6 lỗi không lường trước.
Khóa `job_locks._id` lần lượt là `g1_graph_build`, `g3_tau_phys`, `g4_rainfall_by_node`, `g5_build_training_set`;
nhật ký `jobs` ghi `stats` (số nút/cạnh, τ, số dòng, kết quả kiểm tra rò rỉ).

### G1 – Đồ thị hai chiều (`features/graph_build.py`)

**Kết quả đo thật** (lần chạy 23/11/2026, `uparea ≥ 100 km²`):

| Đại lượng | Giá trị |
|---|---|
| Reach trong cây gốc 44045960 | 1 073 (tổng CSDL MERIT 21 508) |
| Nút sau khi tỉa | **646** (trước tỉa 1 073 → giữ 646) |
| Cạnh `down` | 645 |
| Cạnh `up` | 646 |
| Tổng cạnh trong `graph_edges` | **1 291** |
| Cạnh ảo (`virtual = true`) | cạnh `up` từ `SEA_VUNGTAU` + cạnh `down` từ các hồ chứa |
| Cấp topo tối đa (`topo_level_down`) | theo chiều ngược dòng tới gốc |

**Quy tắc đã dùng (cần coordinator xác nhận):**

1. **Gốc lưu vực** = COMID `44045960`. Cây MERIT là DAG có hội tụ: `up1..up4` là các nhánh đổ về một reach,
   `NextDownID` là reach hạ nguồn (mỗi reach có **đúng một** hạ nguồn → cấu trúc cây, đã assert).
2. **Cửa sả (outlet)** = reach có `NextDownID == 0` trong cây = **COMID 44045960** (gốc). Cạnh `up` ảo
   `SEA_VUNGTAU → 44045960` biểu diễn mặt biển làm nguồn triều.
   *(Lưu ý: hướng cây trong MERIT là `NextDownID` đi **về** 44045960, nên 44045960 vừa là gốc vừa là cửa sả.)*
3. **Cạnh hồ chứa (ảo)**: với mỗi hồ trong `stations` (`merit.COMID` → COMID reach hồ), tạo cạnh `down` **ảo**
   từ nút hồ tới reach **ngay hạ nguồn** của reach hồ (đập). Ba hồ được đưa vào đồ thị:
   `RES_TRI_AN` (44042950), `RES_THAC_MO` (44043222), `RES_DAU_TIENG` (44043239).
   Cạnh ảo có `virtual = true`, `length_km = 0` → không cộng vào đường đi.
4. **Tỉa (pruning)**: giữ nút nếu `uparea_km2 ≥ 100` **HOẶC** nút nằm trên đường đi từ một trạm/hồ tới cửa sả.
   **Mọi nút trạm và hồ chứa được giữ bất kể ngưỡng.**
5. `topo_level_down` = số bước đi ngược `NextDownID` tới gốc; `topo_level_up` = số bước đi xuôi `up1..up4` tới lá.

**Đầu ra**

- `graph_edges` (MongoDB): `{src, dst, dir, virtual, length_km, slope_used, slope_raw, celerity_ms, tau_phys_h, source_comid}`.
  `src`/`dst` là COMID dạng **số**, trừ node biển là **chuỗi** `"SEA_VUNGTAU"`.
  Ghi theo mẫu **staging + thay thế nguyên tử + bản sao lưu `__prev`** (giống job flatten), validator `$jsonSchema`,
  index unique `{src, dst, dir}` + `{dir, src}` + `{dst}`.
- `data/processed/graph/edges.parquet` – `src`/`dst` **ép về chuỗi** (đồng nhất với node biển).
- `data/processed/graph/nodes.parquet` – kèm `station_codes` (mảng mã trạm gắn vào nút đó).

### G3 – Thời gian truyền vật lý (`features/tau_phys.py`)

**Kết quả đo thật** (`results/G3/tau_phys_summary.csv`):

| Hướng | Số cạnh | τ min (h) | τ trung vị (h) | τ max (h) | Σ τ (h) | celerity min (m/s) | celerity trung vị (m/s) | celerity max (m/s) |
|---|---|---|---|---|---|---|---|---|
| `down` (lũ) | 645 | 0,0051 | 0,4609 | 12,75 | 650,20 | 0,413 | 4,940 | 8,000 |
| `up` (triều) | 646 | 0,0717 | 2,3019 | 50,58 | 3 355,80 | 0,300 | 0,555 | 4,827 |

**τ từ Vũng Tàu tới các trạm** (`results/G3/tau_from_sea_by_station.csv`):

| Trạm | COMID | τ từ biển (h) |
|---|---|---|
| Nhà Bè | 44042931 | 3,82 |
| Bến Lức | 44043086 | 5,35 |
| Tân An | 44042875 | 6,13 |
| Phú An | 44043114 | 6,18 |
| Thủ Dầu Một | 44043137 | 9,79 |
| Biên Hòa | 44042941 | 10,27 |
| Gò Dầu Hạ | 44043119 | 19,32 |
| Trị An (hồ) | 44042950 | 29,03 |
| Dầu Tiếng | 44043239 | 35,44 |
| Sr.P.Miêng (hồ) | 44045216 | 55,89 |
| Thác Mơ (hồ) | 44043222 | 103,76 |

**Công thức và giả định** (chi tiết trong docstring của `features/tau_phys.py`):

- **`τ_down` – sóng lũ (Manning, sóng kinematic)**: `v = (1/n)·R^{2/3}·S^{1/2}`, `c = (5/3)·v`,
  `τ = L/c`. Tham chiếu Chow/Maidment/Mays (kinematic wave, celerity ≈ 5/3·V); phong cách Leopold & Maddock cho
  quan hệ `S^{1/2}`. `n` và `R` là **proxy** tài liệu hoá trong docstring.
- **`τ_up` – sóng triều (thuỷ tĩnh)**: `c = sqrt(g·h)`, rồi trừ vận tốc dòng chảy dọc tuyến
  (sóng triều đi ngược dòng), `h` suy ra từ `uparea` theo hình học kênh proxy.
- **`slope = 0`**: MERIT có nhiều reach bằng 0 (103 reach trong cây, 1 049 trên toàn CSDL). Thay bằng **trung vị
  slope của các reach cùng `order`**; giá trị gốc lưu ở `slope_raw`, giá trị dùng ở `slope_used`.
- Vận tốc được **kẹp** trong khoảng thực tế; assert không có τ vô hạn / âm / NaN.
- Có `results/G3/README.md` mô tả cấu hình và ngày chạy.

### G4 – Mưa theo nút đồ thị (`features/rainfall_by_node.py`)

**Fallback dựa trạm** – chưa có sản phẩm vệ tinh (IMERG/CHIRPS) trong repo.

**Quy tắc (tài liệu hoá, tái lập được):**

1. Mỗi **trạm mưa** được gán cho các nút đồ thị **trong bán kính 40 km** (khoảng cách đường thẳng trên mặt cầu Trái Đất).
2. Trọng số cho nút `n` và trạm `s`: `w(n,s) = [uparea(n)/A(s)] / Σ_{s'∈S(n)} [uparea(n)/A(s')]`
   (`A(s)` = diện tích lưu vực của trạm, dùng `uparea` của reach gắn với trạm).
3. **Chuẩn hoá mỗi nút tổng trọng số = 1** (assert trong job).
4. Lưu ma trận trọng số ra `results/G4/rain_weights.csv` để kiểm chứng.

**Kết quả đo thật:** 646 nút; **10 trạm mưa**; 155 nút có ít nhất một trạm mưa trong 40 km
(số trạm/nút: min 1, trung vị 5, max 9); **491 nút không có trạm mưa nào trong 40 km → `rain = null`**;
2 322 370 dòng mưa theo nút–ngày; 3 595 ngày có mưa (2016-12-31 → 2026-11-10).
Job **assert** tổng trọng số mỗi nút = 1 (155/155 nút đạt).

| Năm | Số dòng | Dòng có mưa | TB ngày (mm) | Max ngày (mm) |
|---|---|---|---|---|
| 2016 | 646 | 0 | – | – |
| 2017 | 234 498 | 27 626 | 10,64 | 178,1 |
| 2018 | 235 144 | 15 983 | 9,72 | 401,0 |
| 2019 | 235 790 | 15 531 | 8,46 | 87,0 |
| 2020 | 236 436 | 16 002 | 10,10 | 212,0 |
| 2021 | 235 144 | 15 821 | 9,12 | 109,0 |
| 2022 | 235 144 | 18 875 | 8,64 | 96,2 |
| 2023 | 234 498 | 16 244 | 9,05 | 98,9 |
| 2024 | 236 436 | 14 919 | 11,52 | 126,3 |
| 2025 | 235 790 | 20 614 | 9,81 | 112,0 |
| 2026 | 202 844 | 22 079 | 10,98 | 258,0 |

Đầu ra: `data/processed/features/rain_by_node/year=<YYYY>/part-*.parquet` (cột `rain_mm_d1`, `rain_mm_d3`, `rain_mm_d7`).

**Khi có vệ tinh**: chỉ cần thay bảng nguồn đầu vào (`rainfall_daily` → bảng lưới IMERG/CHIRPS); toàn bộ logic
trọng số và tích lũy giữ nguyên.

### G5 – Mẫu huấn luyện (`features/build_training_set.py`)

**Kết quả đo thật** (run `20261123T114057Z_988b88`, ngày 23/11/2026):

| Đại lượng | Giá trị |
|---|---|
| Tổng số dòng | **32 352** |
| Số trạm | 9 (Biên Hòa, Bến Lức, Dầu Tiếng, Gò Dầu Hạ, Nhà Bè, Phú An, Tân An, Thủ Dầu Một, Vũng Tàu) |
| Khoảng ngày | 2016-12-31 → 2026-11-10 |
| **Kiểm tra rò rỉ (`max_obs_day < day`)** | **0 vi phạm** / 32 343 dòng có quan sát đầu vào |
| `astro_*` | `null` (chưa có `data/processed/features/astro_tide.csv` từ R) |

Số dòng theo năm: 2016 = 9; 2017 = 3 265; 2018 = 3 276; 2019 = 3 285; 2020 = 3 294; 2021 = 3 276;
2022 = 3 275; 2023 = 3 267; 2024 = 3 294; 2025 = 3 285; 2026 = 2 826.

**Quy tắc ngày của đỉnh (peak day offset)** – *cần coordinator thống nhất với `pipelines/r/`*:

> `day` = ngày quan sát của bản tin. `peakN_day_offset = 0` nếu đỉnh thuộc ngày quan sát,
> `peakN_day_offset = 1` nếu đỉnh rơi sang ngày kế tiếp (đỉnh sau nửa đêm vẫn thuộc bản tin của ngày D).
> Đỉnh **thiếu** được đánh dấu bằng `missing_peak1` / `missing_peak2` = `true` và giá trị đo = `null`
> (không coi là 0).

**Cột chính** (đúng theo `schemas/features/v1.md`): `station, day, year, h_peak1, h_peak2, t_peak1_h,
t_peak2_h, h_low1, h_low2, t_low1_h, t_low2_h, peak1_day_offset, peak2_day_offset, missing_peak1,
missing_peak2, exceed_level, exceed_level_known, res_inflow_m3s, res_spill_m3s, res_turbine_m3s, rain_mm,
lunar_day, moon_phase, astro_h1, astro_h2, astro_t1_h, astro_t2_h, max_obs_day, n_reservoirs, n_rain_stations`.

**Ngưỡng báo động (I/II/III)**: lấy từ `stations.alarm_history` – chọn **mục còn hiệu lực tại ngày đó**
(ngày bắt đầu ≤ `day` < ngày kết thúc); `exceed_level` = "" nếu không vượt ngưỡng nào.

**Mốc rò rỉ (GĐ3.6)**: cột `max_obs_day` = ngày quan sát mới nhất thực sự dùng cho dòng đó;
job **assert** `max_obs_day < day` cho mọi dòng (kết quả: 0 vi phạm).

**Lịch chia tập** (ghi trong `_meta.json`):

| Tập | Từ | Đến |
|---|---|---|
| train | 2017-01-01 | 2022-12-31 |
| val | 2023-01-01 | 2024-06-30 |
| test | 2024-07-01 | 2026-11-23 |

`data/processed/features/g5/_meta.json` chứa `data_snapshot` (đếm số bản ghi mỗi collection, timestamp lớn nhất,
ngày chạy, `job_version`) – giá trị này sau đó được ghi vào model record nên **phải chính xác**.

### Việc còn lại (G1–G5)

- `astro_h1/h2/t1_h/t2_h` đang `null`; cần `pipelines/r/` sinh `data/processed/features/astro_tide.csv`
  rồi chạy lại G5 (job tự đọc file nếu có, in cảnh báo `[CẢNH BÁO] thiếu …` nếu không).
- `moon_phase` đang `null` (chỉ có `lunar_date` từ bản tin) – cần bảng tra chu kỳ mặt trăng.
- `uparea` của trạm mưa dùng `uparea` của reach gắn trạm; cần bảng `catchment_area` riêng cho từng trạm khi có.
- `slope = 0` vẫn dùng giá trị thay thế theo `order`; job `repair/` (chưa làm) sẽ sửa tận gốc dữ liệu MERIT.

---

## Đặc tính kỹ thuật – Q1–Q7 (`analytics/`)

Bảy job truy vấn phân tích, mỗi job một câu hỏi riêng, ghi CSV + `README.md` vào `results/Q<n>/`.

| Job | Câu hỏi | File kết quả chính |
|---|---|---|
| `q1_tide_peaks.py` | Đỉnh triều theo trạm/năm/tháng + số ngày vượt từng cấp báo động | `q1_peaks_by_station_year_month.csv`, `q1_exceedance_summary.csv` |
| `q2_reservoirs.py` | Lưu lượng xả hồ chứa theo mùa, số ngày xả tràn | `q2_reservoir_by_month.csv`, `q2_reservoir_by_season.csv` |
| `q3_merit_osm.py` | Thống kê MERIT theo `order`, OSM theo `fclass` | `q3_merit_by_order.csv`, `q3_osm_by_fclass.csv` |
| `q4_integrity.py` | Toàn vẹn dữ liệu: ngày thiếu, ngày trùng, cờ QC, topology | `q4_missing_days.csv`, `q4_duplicate_bulletins.csv`, `q4_topology_checks.csv` |
| `q5_spatial_join.py` | Ghép OSM ↔ MERIT theo khoảng cách 50/100/250 m | `q5_spatial_matches.csv`, `q5_buffer_summary.csv` |
| `q6_paths_to_outlet.py` | Đường đi từ mỗi trạm tới cửa sả + nhánh chung | `q6_station_paths.csv`, `q6_shared_branches.csv` |
| `q7_forecast_accuracy.py` | Độ chính xác dự báo nghiệp vụ theo chân trời | `q7_by_lead_day.csv`, `q7_by_station.csv` |

### Chạy

```bash
# Ví dụ Q4 (thay <n> bằng 1..7 và <tên-file>.py bằng tên job tương ứng)
MSYS_NO_PATHCONV=1 docker run --rm --name q4-driver --hostname q4-driver --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/results:/out" gis-spark:local \
  /opt/spark/bin/spark-submit --master spark://spark-master:7077 --conf spark.driver.host=q4-driver \
  --conf spark.executor.memory=1g /work/pipelines/spark/analytics/q4_integrity.py --out-dir /out/Q4
```

> **Lưu ý:** các job có ghi Parquet (`features/`) cần `--master "local[*]"` (driver trong container)
> vì Hadoop `ChecksumFileSystem` không đổi tên được thư mục `_temporary` khi ghi trên volume
> Windows gắn vào container. Các job analytics chỉ ghi CSV nên chạy được trên cụm
> `spark://spark-master:7077` như bình thường.

Tham số chung: `uri`, `--db`, `--out-dir` (mặc định `results/Q<n>`), `--lock-ttl-s` (3600), `--dry-run`,
`--no-cache`. Mã thoát: 0 ok / dry-run, 1 kiểm tra hỏng, 3 job đang chạy ở nơi khác, 5 mất khóa, 6 lỗi bất ngờ.
Khóa `job_locks._id` = tên job (ví dụ `q4_data_integrity`); nhật ký `jobs` ghi `stats` và `outputs`.
**Redis**: nếu đặt biến môi trường `REDIS_URL` thì kết quả Q1–Q4, Q7 được đẩy lên cache; **không** có
`REDIS_URL` thì job in `[CẢNH BÁO] bỏ qua Redis` và chạy tiếp bình thường (hiện môi trường chưa có Redis).

### Kết quả đo thật

**Q1 – đỉnh triều và vượt ngưỡng** (`results/Q1/q1_exceedance_summary.csv`):

| Trạm | Số ngày | TB đỉnh (m) | Max đỉnh (m) | Vượt cấp I | Vượt cấp II | Vượt cấp III | Ngày có ngưỡng |
|---|---|---|---|---|---|---|---|
| Phú An | 3 585 | 1,2289 | 1,78 | 496 | 362 | 212 | 3 585 |
| Nhà Bè | 2 406 | 1,2380 | 1,78 | 320 | 191 | 123 | 2 406 |
| Thủ Dầu Một | 474 | 1,3863 | 1,88 | 70 | 66 | 96 | 474 |

Dùng `stations.alarm_history` (ngưỡng **thay đổi theo thời gian**), **không** dùng ngưỡng cố định.

**Q2 – hồ chứa** (`results/Q2/q2_reservoir_by_{month,season,yearly}.csv`) — lưu lượng xả theo mùa
(`mua_kho` = mùa khô, `mua_mua` = mùa mưa) và số ngày xả tràn (`turbine + spillway > 0`):

| Hồ | Mùa | Số ngày | TB lưu lượng vào (m³/s) | TB xả (m³/s) | Max xả (m³/s) | Thể tích (tỷ m³) | Ngày xả tràn | Ngày chạy thủy điện | TB mực nước thượng nguồn (m) |
|---|---|---|---|---|---|---|---|---|---|
| Trị An | mùa khô | 1 758 | 224,40 | 273,55 | 1 659 | 41,55 | 55 | 1 754 | 60,98 |
| Trị An | mùa mưa | 1 837 | 730,69 | 664,82 | 2 180 | 105,52 | 267 | 1 835 | 56,28 |
| Thác Mơ | mùa khô | 1 758 | 36,56 | 86,17 | 404 | 13,09 | 48 | 1 750 | 214,07 |
| Thác Mơ | mùa mưa | 1 837 | 184,70 | 135,24 | 615 | 21,47 | 106 | 1 830 | 206,71 |
| Sr.P.Miêng | mùa khô | 1 758 | 108,66 | 103,82 | 485 | 15,77 | 57 | 1 717 | 71,54 |
| Sr.P.Miêng | mùa mưa | 1 837 | 243,27 | 240,01 | 882 | 38,09 | 491 | 1 823 | 71,66 |
| Dầu Tiếng | mùa khô | 1 758 | 69,11 | 16,62 | 200 | 2,52 | 626 | 0 | 22,95 |
| Dầu Tiếng | mùa mưa | 1 837 | 126,99 | 26,73 | 200 | 4,24 | 733 | 0 | 20,38 |
| Đa Đen | mùa khô | 127 | 3,64 | 4,23 | 5 | 0,05 | 116 | 126 | 43,41 |
| Đa Đen | mùa mưa | 184 | 9,06 | 8,74 | 770 | 0,14 | 175 | 177 | 42,60 |
| Sông Hòa | mùa khô | 127 | 0,01 | 0,09 | 0,1 | 0,001 | 0 | 108 | 24,49 |
| Sông Hòa | mùa mưa | 184 | 0,18 | 0,26 | 3,9 | 0,004 | 30 | 94 | 23,30 |
| Sông Rây | mùa khô | 127 | 0,26 | 7,23 | 16 | 0,08 | 0 | 109 | 67,21 |
| Sông Rây | mùa mưa | 184 | 22,98 | 10,43 | 66,5 | 0,17 | 21 | 148 | 64,37 |

Nhận xét: **Trị An** xả lớn nhất (mùa mưa: 730,7 m³/s vào, 664,8 m³/s xả, thể tích 105,5 tỷ m³, đỉnh
2 180 m³/s) và là hồ có **đóng góp lớn nhất** cho dòng chảy hạ nguồn; **Thác Mơ** và **Sr.P.Miêng** có
số ngày xả gần như toàn bộ chu kỳ (xả tràn 48–491 ngày). Ba hồ lớn có chu kỳ dữ liệu 1 758 (mùa khô)
+ 1 837 (mùa mưa) ngày; các hồ nhỏ (Đa Đen, Sông Hòa, Sông Rây) chỉ có 127 + 184 ngày.

**Q3 – MERIT và OSM** (`results/Q3/`):

| `order` | Số reach | Tổng chiều dài (km) | Trung vị chiều dài (km) | Trung vị slope | Max `uparea` (km²) | Số slope = 0 |
|---|---|---|---|---|---|---|
| 1 | 538 | 4 504,80 | 5,771 | 0,00135 | 260,26 | 39 |
| 2 | 252 | 2 233,98 | 7,485 | 0,00064 | 963,23 | 26 |
| 3 | 103 | 1 039,99 | 8,847 | 0,00046 | 1 961,11 | 12 |
| 4 | 149 | 1 305,82 | 6,932 | 0,00032 | 9 621,82 | 17 |
| 5 | 29 | 220,28 | 5,298 | 2,90e-05 | 29 606,46 | 9 |
| 6 | 2 | 15,44 | 5,120 | 1,96e-05 | 45 551,10 | 0 |

Tổng cây 44045960: 1 073 reach, 9 320,30 km, trung vị chiều dài 6,771 km, trung vị slope 6,77e-04, **103** reach slope = 0,
max `uparea` 45 551,10 km². OSM: 38 877 đoạn — `stream` 15 552, `canal` 13 918, `river` 7 643, `drain` 1 764.

**Q4 – toàn vẹn dữ liệu** (`results/Q4/q4_topology_checks.csv`):

| Kiểm tra | Kết quả | Giá trị |
|---|---|---|
| Cây lưu vực không chu trình hướng xuôi, mỗi nút ≤ 1 hạ nguồn | đạt | 1 073 reach |
| Cửa sả duy nhất | đạt | 1 nút |
| Mọi nút đi được về cửa sả theo `NextDownID` | đạt | 1 073 reach |
| Điểm hội tụ (nhiều reach đổ về cùng hạ nguồn) | **đặc trưng hợp lệ**, không phải lỗi | 535 nút |
| `graph_edges` đã được G1 nạp | đạt | 1 291 cạnh |
| Số nút trong `graph_edges` (đã tỉa) | đã tỉa theo `uparea` | 646 nút |

Ngày thiếu: 7 ngày cho mỗi trạm trong khoảng 2016-12-31 → 2026-11-10 (3 595/3 602 ngày) — ví dụ
2017-04-27, 2017-09-07, 2018-04-22, 2021-02-22, 2022-04-24, 2023-06-12, 2023-07-05.
Ngày trùng (2 bản tin cùng trạm – ngày): ví dụ Biên Hòa 2017-03-18, 2017-06-20, 2017-08-13, 2017-12-09.
Phân bố cờ QC trên `tide_extremes`: `ok` 121 337; `not_occurred` 4 105; `missing` 3 391; `ok` + `suspect` 343;
`no_data` 86; `no_value` 75; `ok` + `confirmed_ok` 35; `bad_time` 22; `duplicate_time` 13; `excel_error` 9;
`unparseable` 2; `not_occurred` + `confirmed_ok` 1; `rejected` 1.

**Q5 – ghép OSM ↔ MERIT** (`results/Q5/q5_buffer_summary.csv`, lần chạy 23/11/2026):

| Ngưỡng | Số cặp khớp | OSM có khớp | MERIT có khớp | % OSM | % MERIT | Khoảng cách TB (m) |
|---|---|---|---|---|---|---|
| 50 m | 3 199 | 1 950 / 38 877 | 872 / 1 073 | 5,016 % | 81,267 % | 7,53 |
| 100 m | 3 683 | 2 140 / 38 877 | 898 / 1 073 | 5,505 % | 83,691 % | 15,83 |
| 250 m | 4 331 | 2 419 / 38 877 | 923 / 1 073 | 6,222 % | 86,021 % | 38,05 |

Ứng viên sau prefilter lưới + bbox: 7 741 cặp; thời gian chạy ~78 s trên cluster.
Tỉ lệ OSM khớp thấp (5–6 %) là **đúng**: `waterways_segments` là toàn bộ CSDL OSM cả Việt Nam
(38 877 đoạn), không lọc theo lưu vực — chỉ 2 419 đoạn nằm trong bán kính 250 km quanh các
reach MERIT của lưu vực Đồng Nai – Sài Gòn.

> **Sedona KHÔNG có trong image** `gis-spark:local` (đã kiểm tra `/opt/spark/jars`, Ivy cache và
> module Python `sedona`) → spatial join làm bằng **Spark thuần**: prefilter lưới không gian + bbox,
> rồi khoảng cách **thực** giữa hai đoạn thẳng bằng phép chiếu equirectangular **cục bộ quanh
> vĩ độ của từng cặp** (sai số < 0,1 % → dưới 1 m). Đây là khoảng cách giữa hai **đường nằm**,
> không phải khoảng cách tới tâm/đỉnh. Hình học được rút gọn Douglas–Peucker (sai số 0,0005°
> ≈ 55 m, nhỏ hơn ngưỡng khớp nhỏ nhất) để giảm số đoạn phải so sánh.

**Q6 – đường tới cửa sả** (`results/Q6/q6_station_paths.csv`):

| Trạm | COMID | Số reach tới cửa sả | Tổng chiều dài (km) | Trung vị… tổng slope | Số điểm hội tụ | τ↑ lớn nhất (h) |
|---|---|---|---|---|---|---|
| Nhà Bè | 44042931 | 6 | 40,06 | 0,00095 | 6 | 1,2932 |
| Tân An | 44042875 | 7 | 87,34 | 0,00029 | 7 | 2,3222 |
| Bến Lức | 44043086 | 9 | 74,69 | 0,00042 | 9 | 2,3222 |
| Phú An | 44043114 | 11 | 68,06 | 0,00120 | 11 | 9,1565 |
| Biên Hòa | 44042941 | 16 | 110,35 | 0,00192 | 16 | 9,1565 |
| Thủ Dầu Một | 44043137 | 18 | 105,47 | 0,00135 | 18 | 9,1565 |
| Gò Dầu Hạ | 44043119 | 19 | 157,57 | 0,00248 | 19 | 7,4616 |
| Dầu Tiếng | 44043239 | 26 | 200,34 | 0,00417 | 26 | 15,4552 |
| Trị An (hồ) | 44042950 | 22 | 171,28 | 0,00647 | 22 | 23,0724 |
| Sr.P.Miêng (hồ) | 44045216 | 39 | 302,18 | 0,01228 | 38 | 25,6909 |
| Thác Mơ (hồ) | 44043222 | 50 | 414,83 | 0,01703 | 50 | 25,6909 |

Cột `max_tau_up_h` = τ vật lý lớn nhất trên **một** cạnh `up` nằm trên đường đi xuôi của trạm đó
(giá trị lấy từ `graph_edges.tau_phys_h`, nơi `dir = "up"`). Vì MERIT có điểm hội tụ, **không** có
một đường đi `up` duy nhất từ biển tới mỗi trạm, nên job **không** cộng dồn τ mọi nhánh — chỉ báo
τ lớn nhất (xem "Giới hạn" của `results/Q6/README.md`). Số cặp trạm chia sẻ đường đi: 66.

Đường đi **dùng chung** giữa các cặp trạm được ghi ở `q6_shared_branches.csv` (ví dụ Bến Lức ↔ Gò Dầu Hạ:
9 reach chung, đoạn sâu nhất 44045967).

**Q7 – độ chính xác dự báo nghiệp vụ** (`results/Q7/`):

| Chân trời (ngày) | Số cặp | MAE độ cao (m) | MAE giờ đỉnh (h) | Sai lệch giờ trung vị (h) | Sai lệch giờ max (h) |
|---|---|---|---|---|---|
| 1 | 29 045 | 0,0489 | 0,3371 | 0,03 | 3,10 |
| 2 | 29 139 | 0,0872 | 0,5902 | 0,06 | 3,10 |
| 3 | 29 122 | 0,1066 | 0,7504 | 0,08 | 3,02 |
| 4 | 29 090 | 0,1186 | 0,8826 | 0,09 | 3,10 |
| 5 | 29 083 | 0,1276 | 0,9856 | 0,10 | 31,35 |

Theo trạm (tất cả chân trời): Phú An 68 263 cặp, MAE 0,0934 m; Nhà Bè 68 045 cặp, MAE 0,1011 m;
Thủ Dầu Một 9 171 cặp, MAE 0,1063 m (chỉ có dữ liệu từ 2025, tách riêng theo yêu cầu).
Phân bố sai số: 87,5 % cặp trong ±0,2 m; 7,0 % trong [−0,5; −0,2) m; 4,9 % trong (0,2; 0,5] m.

### Việc còn lại (Q1–Q7)

- **Redis cache** cho Q1–Q4, Q7: đã viết mã, nhưng **môi trường hiện chưa có Redis** → bước cache bị bỏ qua có cảnh báo.
  Cần dựng Redis trong `infra/compose` rồi đặt `REDIS_URL`.
- **Sedona**: chưa có trong image; nếu muốn dùng `ST_DWithin` thật thì phải thêm `geotools` + `sedona` vào image
  (ngoài phạm vi phần này, `infra/` không được sửa).
- Q2, Q6, Q3 dùng **BFS/aggregation thuần Spark**, không dùng GraphFrames (image không có GraphFrames) — tuân thủ
  ràng buộc cấp 1/2 trong `schemas/README.md` (`pipelines/` không import `packages/phasegnn`).
- Chưa có bảng tra `exceed_level` chuẩn hoá cho mọi trạm — Q1 dùng `alarm_history` nhưng phải kiểm tra lại
  các mục thiếu `valid_to`.

### Ghi chú kỹ thuật chung cho các job Spark

**JSON mở rộng của MongoDB.** Driver Java `toJson()` trả **canonical extended JSON**: BSON `int64`
thành `{"$numberLong": "44042931"}`, BSON `Date` thành `{"$date": …}`. `MongoAdmin.command`
trong `common/session.py` giờ **tự giải mã** qua `decode_extjson()` nên COMID đọc từ MongoDB là `int`
thật và ngày giờ là `datetime`. Trước đó, các job đọc `graph_edges` bằng `adm.aggregate` dễ so khớp
khoá sai (ví dụ Q6: `max_tau_up_h` luôn bằng 0 vì `str(src)` không bằng `"44042931"`).
Hàm `_iso`/`from_date`/`_vn_date` ở `flatten/`, `qc/`, `features/` đều chấp nhận cả hai dạng.

**`$max` phải dùng `$group`, không dùng `$project`.** `{$project: {m: {$max: "$ts"}}}` chỉ lấy giá trị
của tài liệu **đầu tiên** (MongoDB coi `$max` ở ngữ cảnh projection là biểu thức đơn lẻ), còn
`{$group: {_id: null, m: {$max: "$ts"}}}` mới gom trên toàn luồng. `build_training_set.adm_max` đã dùng
dạng `$group` — nếu không, `data_snapshot.max_timestamps` trong `_meta.json` sẽ báo sai.

**Ghi Parquet cần `--master "local[*]".** Hadoop `ChecksumFileSystem` không đổi tên được thư mục
`_temporary` khi executor ghi vào volume Windows gắn vào container (`Mkdirs failed to create
file:/work/.../_temporary/0/_temporary`). Vì vậy các job `features/` (G1, G3, G4, G5) chạy với
`--master "local[*]"` trên container driver; các job `analytics/` chỉ ghi CSV nên chạy được trên cụm
`spark://spark-master:7077`.
