# pipelines/spark/ – Job PySpark

**Chức năng:** xử lý phân tán trên Spark cluster (đề cương Chương 3).

| Thư mục | Nội dung | Trạng thái |
|---|---|---|
| `common/` | SparkSession, đọc MongoDB có đẩy `$match`/`$project` xuống server, lệnh quản trị MongoDB qua driver Java của connector, khóa `job_locks`; bảo đảm import được `vngis` | có |
| `flatten/` | G0: bản tin → `tide_extremes`, `official_forecasts`, `reservoir_daily`, `rainfall_daily` | có |
| `tests/` | test quy tắc thuần Python của G0 (không cần Spark) | có |
| `analytics/` | Q1–Q7: thống kê triều, hồ, MERIT/OSM, toàn vẹn dữ liệu, ghép OSM ↔ MERIT, đồ thị, độ chính xác KTTV | dự kiến |
| `matching/` | G2: gán trạm vào reach, ghép OSM ↔ MERIT (Sedona) | dự kiến |
| `repair/` | QC, sửa `slope = 0`, gắn cờ nút biên | dự kiến |
| `features/` | G1 đồ thị hai chiều, G3 τ vật lý, G4 mưa theo nút, G5 mẫu huấn luyện (Parquet) | dự kiến |

**Dùng `vngis`.** Job import phần thuần Python của `vngis` (`vngis.text`, `vngis.admin`, `vngis.province_labels`;
chỉ thư viện chuẩn, vì image Spark không có pymongo/pydantic/pyyaml). `common/__init__.py` thêm
`<gốc repo>/packages/vngis/src` vào `sys.path` khi `vngis` chưa import được (repo gắn vào `/work`); cũng có thể gửi
`vngis` qua `--py-files`. Các quy tắc này chỉ chạy trên driver (không có UDF), executor không cần `vngis`.
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
| 3 | job đang chạy ở nơi khác (khóa `job_locks` còn hạn) |
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

**Bản tin chính trùng nội dung.** 4 cặp bản tin chính có cùng `source.sha256` (một file nằm sai thư mục ngày).
Mỗi cặp giữ bản có ngày trên tên file trùng ngày trong nội dung, bỏ bản còn lại; danh sách in ra khi chạy:
`2019/01/10/…20190110.pdf`, `2019/02/11/…20190211.pdf`, `2020/02/07/…20200207.pdf`, `2020/07/05/…20200705.pdf`.
Còn lại 3.590 bản tin. Nếu không bỏ, mỗi cặp sinh ra đỉnh/chân và dự báo trùng khóa.

**Ngày phát (`issued_date`)** tính một lần cho mỗi bản tin, dùng chung cho `official_forecasts` và `reservoir_daily`:
= ngày bản tin (`bulletin_date`, giờ VN). Ngoại lệ: `2025/11/08/HCMC_TVHN_20251108.pdf` ghi ngày bản tin trong nội dung
là 07/11/2025 (trùng số bản tin 311/2025 với ngày hôm trước), nhưng ngày dự báo bắt đầu 08/11. Khi ngày dự báo đầu tiên
khác ngày bản tin, lấy ngày dự báo đầu tiên làm `issued_date` và ghi `issued_date_note`
(15 document `official_forecasts`, 4 document `reservoir_daily`; cả hai đều `issued_date = 2025-11-08`).

### Đầu ra

Mọi ngày giờ lưu BSON Date UTC; chuỗi ngày (`day`, `issued_date`, `target_date`) theo giờ Việt Nam `YYYY-MM-DD`.
**Các bản ghi mẫu dưới đây là giá trị MINH HỌA** (để thấy cấu trúc), không phải số liệu trích từ bản tin.

**`tide_extremes`** – time series (`timeField: ts`, `metaField: meta`, `granularity: hours`), index `station_ts_idx {meta.station: 1, ts: 1}`
(MongoDB tự tạo thêm `meta_1_ts_1`). Mỗi đỉnh/chân trong `observed_water_levels` là một document:

```json
{ "ts": ISODate("2024-03-10T02:00:00Z"),
  "meta": { "station": "PHU_AN", "kind": "high" },
  "value": 1.23, "qc": "ok", "ts_placeholder": false,
  "source_file": "2024/03/11/HCMC_TVHN_20240311.pdf" }
```

- `meta` chỉ gồm `{station, kind}` (khóa nhận dạng chuỗi). `qc`, `ts_placeholder`, `source_file` là field thường:
  mỗi giá trị khác nhau của `meta` mở bucket riêng, nên để `source_file`/`qc` trong `meta` làm mỗi bucket chỉ chứa
  vài document của một bản tin. Đổi sang meta 2 khóa: **72.155 → 17.390 / 18.158 bucket** (`db.system.buckets.tide_extremes`,
  hai lần chạy; số bucket phụ thuộc thứ tự connector ghi song song), cùng 129.240 document. Lọc theo `qc`/`source_file` vẫn được (field thường của time series).
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

  Các mã QC theo quy tắc (`suspect`, `rejected`) để cho GĐ2.3. Khi dùng làm nhãn hoặc đầu vào, lọc `qc = "ok"`.
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

### Kiểm tra (lần chạy gần nhất, cluster 1 worker × 2 core)

| Kiểm tra | Kết quả |
|---|---|
| Tên trạm mực nước, trạm dự báo, hồ, trạm mưa đều có mã trong `stations` | đạt, không có tên lạc |
| Tên tỉnh của hồ → `{era, code}` | đạt, 14 đoạn nhãn, 0 không tra được |
| `tide_extremes` có `value ≠ null` = số đỉnh/chân có số trong bản tin chính (aggregation độc lập trên `hydromet_bulletins`) | 121.578 = 121.578 (trước khi bỏ 4 bản trùng: 121.718) |
| Tổng document `tide_extremes` = tổng ô đỉnh/chân | 129.240 = 3.590 × 9 trạm × 4 |
| `official_forecasts`: mỗi bản tin chính = 5 ngày × số trạm dự báo, `lead_day` đủ 1…5 | đạt cho 3.590 bản tin (3.099 bản 2 trạm, 491 bản 3 trạm), 0 sai; 7.671 document mỗi `lead_day` |
| Không trùng (station, kind, ts) trong `tide_extremes` | 0 khóa trùng (9 cặp cùng giờ trong cùng bản tin được xử lý bằng `duplicate_time`) |
| 5 bản ghi ngẫu nhiên: giờ UTC + 7 = giờ `HH:MM` gốc | 5/5 khớp |
| Số document staging = số kỳ vọng (cả 4 collection) | đạt |

| Collection | Số document |
|---|---:|
| `tide_extremes` | 129.240 (qc: `ok` 121.543, `not_occurred` 4.099, `missing` 3.391, `no_data` 86, `no_value` 75, `bad_time` 22, `duplicate_time` 13, `excel_error` 9, `unparseable` 2); 17–18 nghìn bucket |
| `official_forecasts` | 38.355 (7.671 mỗi `lead_day`) |
| `reservoir_daily` | 15.290 |
| `rainfall_daily` | 13.110 |

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
- Quy tắc QC `suspect` (GĐ2.3) chạy trên `tide_extremes`.
- Đóng gói `vngis` thành `.zip` cho `--py-files` khi chạy không gắn repo.
