# pipelines/spark/features/ – G1, G3, G4, G5 (đồ thị, τ vật lý, mưa theo nút, mẫu huấn luyện)

**Chức năng:** dựng đồ thị lưu vực hai chiều (G1), tính thời gian truyền vật lý (G3),
quy hoạch mưa theo nút (G4), dựng tập huấn luyện Parquet (G5) — checklist **GĐ3.3–GĐ3.6**.

| Job | Nội dung | Đầu ra chính |
|---|---|---|
| `graph_build.py` | G1 – đồ thị hai chiều từ `merit_river_reaches` (gốc 44045960) | `graph_edges` (MongoDB), `data/processed/graph/{edges,nodes}.parquet` |
| `tau_phys.py` | G3 – τ vật lý `tau_down` / `tau_up` theo cạnh | `graph_edges.tau_phys_h`, `results/G3/*.csv` |
| `rainfall_by_node.py` | G4 – mưa theo nút (fallback từ trạm mưa) | `data/processed/features/rain_by_node/year=*/`, `results/G4/*.csv` |
| `build_training_set.py` | G5 – tập huấn luyện (một dòng cho mỗi trạm–ngày) | `data/processed/features/g5/year=*/part-*.parquet`, `_meta.json` |
| `rules.py` | quy tắc thuần Python dùng chung (topology, tỉa, vật lý, ngày đỉnh) | — |
| `rain_rules.py` | quy tắc thuần Python của G4 (gán trọng số) — tách riêng để test không cần Spark | — |

Tất cả job dùng chung `common/session.py` (`build_session`, `read_mongo`, `write_mongo`, `MongoAdmin`)
và `common/jobrun.py` (`JobRun`: khoá `job_locks`, nhật ký `jobs`, `run.hold()`), ghi với
`writeConcern: {w: "majority"}`.

---

## G1 – Đồ thị hai chiều (`graph_build.py`)

### Chạy

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name g1-driver --hostname g1-driver \
  --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" \
  -v "<gốc repo>/results:/out" \
  -v "<gốc repo>/data/processed:/work/data/processed" \
  gis-spark:local /opt/spark/bin/spark-submit --master 'local[2]' \
  --conf spark.driver.host=g1-driver --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/graph_build.py
```

> **Lưu ý về `--master`.** Job ghi Parquet vào `/work/data/processed` (mount từ Windows).
> Hadoop `ChecksumFileSystem` cần tạo thư mục `_temporary` và **rename** nguyên tử trong cùng
> filesystem; filesystem Windows (NTFS qua Docker Desktop) không bảo đảm điều đó → job báo
> `Mkdirs failed to create .../_temporary`. Vì vậy **dùng `--master local[*]`** cho các job có
> ghi Parquet. Job chỉ đọc/ghi MongoDB (G3) chạy được trên cả cluster.

Tham số: `--db`, `--out-dir` (mặc định `/work/data/processed/graph`), `--min-uparea-km2` (100),
`--root-comid` (44045960), `--lock-ttl-s` (3600), `--dry-run` (chỉ ghi staging + kiểm tra, không nạp).

| Mã thoát | Nghĩa |
|---:|---|
| 0 | thành công |
| 1 | kiểm tra hỏng (trùng khóa cạnh, nút mất hạ nguồn, …) |
| 2 | thiếu `merit_river_reaches` hoặc `stations` |
| 3 | job đang chạy ở nơi khác (khoá `job_locks`) |
| 4 | đang chờ gỡ chặn rollback |
| 5 | mất khoá giữa chừng |
| 6 | lỗi không lường trước |

### Luật dựng đồ thị

1. **Hướng của MERIT** (kiểm tra trên toàn bộ 21.508 reach): `up1..up4` là các nhánh sông **chảy
   vào** reach đó; `NextDownID` là reach **hạ nguồn** (`0` = cửa sản). Vì vậy cây dựng ngược từ
   `up1..up4` có **gốc là cửa sản**; cạnh `up` trong hợp đồng là hướng ngược dòng.
2. **Cạnh `down`**: nút → `NextDownID` (mỗi nút đúng một hạ nguồn trong cây).
3. **Cạnh `up`**: chiều ngược của mọi cạnh `down`.
4. **Nút ảo biển** `SEA_VUNGTAU` (chuỗi, không có COMID): cạnh `up` ảo → **cửa sản 44045960**
   (nút duy nhất trong cây không có hạ nguồn).
5. **Cạnh ảo hồ chứa**: nút hồ → reach ngay hạ nguồn đập (theo `NextDownID` của COMID hồ).
   Ba hồ được hợp đồng nêu đều nằm trong lưu vực: Trị An (44042950), Thác Mơ (44043222),
   Dầu Tiếng (44043239). Hai hồ khác trong `stations` (Sr.P.Miêng 44045216, Sông Rây 44043517)
   **không** sinh cạnh ảo — job in cảnh báo.
6. **Cấp topo**: `topo_level_down` (khoảng cách dài nhất từ cửa sản đi ngược dòng) và
   `topo_level_up` (đường đi dài nhất từ lá tới nút), giải bằng **Kahn + longest-path** vì
   MERIT có hội tụ (nhiều nhánh đổ về cùng một reach) nên BFS đơn giản phụ thuộc thứ tự duyệt.
7. **Tỉa**: giữ nút nếu `uparea_km2 ≥ ngưỡng` **hoặc** nút là trạm/hồ **hoặc** nút nằm trên
   đường đi xuôi từ trạm/hồ tới cửa sản. Sau đó giữ thêm **một** nhánh chảy vào cho mỗi nút
   còn lại (để cạnh `up` tồn tại) — lặp tới điểm cố định.

### Kết quả đo thật (2026-11-23)

| Chỉ số | Giá trị |
|---|---|
| Nút trong cây lưu vực (trước tỉa) | **1.073** |
| Nút sau tỉa (ngưỡng `uparea ≥ 100 km²`) | **646** |
| Nút trạm/hồ được giữ bất kể tỉa | 11 |
| Điểm hội tụ (nhiều reach đổ về cùng hạ nguồn) | 535 |
| Cạnh `down` | **645** |
| Cạnh `up` | **646** |
| Cạnh ảo (biển + 3 hồ chứa) | 5 |
| **Tổng cạnh** | **1.291** |
| Nút trong `nodes.parquet` | 647 (646 + nút ảo biển) |
| Lỗi topology | 0 |

Các kiểm tra đều **đạt**: không trùng khóa `(src, dst, dir)`; mỗi nút đúng một hạ nguồn; số cạnh
`up` khớp số nhánh `up1..up4` còn lại + 1 cạnh ảo biển; mọi nút đi ngược dòng về được cửa sản
(646/646).

---

## G3 – Thời gian truyền vật lý (`tau_phys.py`)

### Chạy

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name g3-driver --hostname g3-driver \
  --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/results:/out" \
  gis-spark:local /opt/spark/bin/spark-submit --master 'local[2]' \
  --conf spark.driver.host=g3-driver --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/tau_phys.py
```

### Công thức và nguồn số

| Ký hiệu | Công thức | Ghi chú / tham chiếu |
|---|---|---|
| `h` (chiều sâu) | `1,60 · A(km²)^0,22` | proxy hình học từ `uparea`; A=100 km² → 2,52 m; A=45.551 km² → 8,99 m |
| `W` (bề rộng) | `22,0 · A(km²)^0,40` | A=100 km² → 70 m; A=45.551 km² → 730 m |
| `R` (bán kính thủy vực) | `min(h, 0,5·W)` | kênh đáy phẳng đồng bằng → `R ≈ h` |
| `v` (vận tốc Manning) | `(1/n)·R^(2/3)·S^(1/2)`, `n = 0,030` | Chow, Maidment & Mays (1994), *Hydrology and Floodplain Analysis* |
| `c_down` (sóng lũ) | `(5/3)·v` | hệ số 5/3 kiểu Leopold & Maddock (1954) |
| `c_up` (sóng triều) | `max(0,45·√(g·h) − v, 0,05)` | `√(g·h)` là vận tốc sóng nông lý thuyết; hệ số 0,45 cho kênh chảy một phần — Godin (1968), *Tidal Hydrodynamics*; Prandle (1956), J. Fluid Mech. |
| `τ` | `L / c` (L tính bằng km, c bằng m/s) | kẹp vào `[0,001 h, 72 h]` |

**Độ dốc.** MERIT có nhiều reach `slope = 0`; giá trị gốc lưu ở `slope_raw`, giá trị dùng ở
`slope_used` = **trung vị `slope` của các reach cùng `order`** trong cây (nếu nhóm `order` không
có slope > 0 thì dùng trung vị toàn cây).

**Giới hạn đã thừa nhận.** `h` và `W` là **proxy** suy từ `uparea`, không phải mặt cắt ngang đo
đạc; `n = 0,030` là giá trị đại diện cho sông tự nhiên; giá trị τ vì vậy là **đại lượng ước lượng
để so sánh tương đối giữa các trạm**, không phải thời gian truyền tuyệt đối.

### Kết quả đo thật (2026-11-23, 1.291 cạnh)

| Hướng | n cạnh | τ min (h) | τ trung vị (h) | τ max (h) | c min (m/s) | c trung vị (m/s) | c max (m/s) |
|---|---:|---:|---:|---:|---:|---:|---:|
| `down` (sóng lũ) | 645 | 0,0051 | 0,4609 | 12,7526 | 0,413 | 4,940 | 8,000 |
| `up` (sóng triều) | 646 | 0,0717 | 2,3019 | 50,5791 | 0,300 | 0,555 | 4,827 |

Kiểm tra: **1.291/1.291** τ hợp lệ (0 < τ ≤ 72 h, không NaN).

Tổng τ từ Vũng Tàu (theo cạnh `up`, đi ngược dòng):

| Trạm / hồ | COMID | τ từ biển (h) |
|---|---:|---:|
| Nhà Bè | 44042931 | 3,82 |
| Bến Lức | 44043086 | 5,35 |
| Tân An | 44042875 | 6,13 |
| Phú An | 44043114 | 6,18 |
| Thủ Dầu Một | 44043137 | 9,79 |
| Biên Hòa | 44042941 | 10,27 |
| Gò Dầu Hạ | 44043119 | 19,32 |
| Trị An (hồ) | 44042950 | 29,03 |
| Dầu Tiếng (hồ) | 44043239 | 35,44 |
| Sr.P.Miêng (hồ) | 44045216 | 55,89 |
| Thác Mơ (hồ) | 44043222 | 103,76 |

> Thác Mơ vượt trần `TAU_MAX_H = 72 h` ở **tổng đường đi** (mỗi cạnh vẫn ≤ 72 h); đây là giới hạn
> của trần vật lý theo cạnh, không phải lỗi.

---

## G4 – Mưa theo nút (`rainfall_by_node.py`)

### Chạy

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name g4-driver --hostname g4-driver \
  --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/results:/out" \
  -v "<gốc repo>/data/processed:/work/data/processed" \
  gis-spark:local /opt/spark/bin/spark-submit --master 'local[2]' \
  --conf spark.driver.host=g4-driver --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/rainfall_by_node.py
```

### Vì sao là bản dự phòng (fallback)

Chưa tải được sản phẩm mưa vệ tinh (IMERG/CHIRPS/GPM) — mưa lưu vực nằm trong vùng mây che nhiều
và độ phân giải lọc tốt hơn trạm mưa. Vì vậy G4 dùng **mưa quan trắc tại trạm**
(`rainfall_daily`, 10 trạm của TP.HCM: Bình Chánh, Cần Giờ, Củ Chi, Hóc Môn, Mạc Đĩnh Chi,
Nhà Bè, Phú An, Tân Sơn Hoa, Thủ Dầu Một, Vũng Tàu) và quy chúng về các nút đồ thị.

### Luật gán (có tài liệu, tái lập được)

1. *Ứng viên.* Khoảng cách từ trạm tới nút = khoảng cách nhỏ nhất từ điểm trạm tới **đoạn thẳng**
   (LineString) của reach — không phải tâm điểm. Công thức điểm–đoạn trên mặt cầu Trái Đất,
   bán kính 6.371.008,8 m.
2. *Chọn ứng viên.* Chỉ giữ trạm có khoảng cách ≤ `--radius-km` (mặc định **40 km**).
3. *Trọng số diện tích.* Với nút `n` và tập trạm `S(n)`:

   ```
   w(n, s) = [ uparea(n) / A(s) ] / Σ_{s'∈S(n)} [ uparea(n) / A(s') ]
   ```

   trong đó `A(s)` = tổng `uparea` của các nút mà trạm `s` được gán vào. Tổng trọng số mỗi nút
   = **1** (kiểm tra bắt buộc, hỏng → mã thoát 1).
4. *Giá trị tại nút.* `rain_node(n, d) = Σ_s w(n, s) · rainfall(s, d)`; trạm không có số liệu
   trong ngày `d` bị bỏ qua và trọng số của nó **phân bổ lại** cho các trạm còn lại của cùng nút
   (để tổng vẫn = 1 và không tạo giá trị 0 giả). Ngày không có trạm nào có số liệu → `null`
   (KHÔNG phải 0).
5. *Lũy kế.* `rain_mm` (1 ngày), `rain_mm_3d` (3 ngày), `rain_mm_7d` (7 ngày) tính trên cùng
   bộ trạm có số liệu.

### Kết quả đo thật (2026-11-23)

| Chỉ số | Giá trị |
|---|---|
| Nút trong đồ thị | 646 |
| Nút có ít nhất một trạm mưa | 155 |
| Nút không có trạm mưa trong 40 km | 491 (`rain = null`) |
| Số trạm/nút | min 1, trung vị 5, max 9 |
| Ngày có mưa | 3.595 (2016-12-31 → 2026-11-10) |
| **Tổng số dòng (nút, ngày)** | **2.322.370** |
| Kiểm tra tổng trọng số = 1 | **đạt** (155/155) |

Số dòng theo năm: 2016: 646 · 2017: 234.498 · 2018: 235.144 · 2019: 235.790 · 2020: 236.436 ·
2021: 235.144 · 2022: 235.144 · 2023: 234.498 · 2024: 236.436 · 2025: 235.790 · 2026: 202.844.

### Thay sản phẩm vệ tinh

Khi có IMERG/CHIRPS, chỉ cần thay bảng đầu vào (`rainfall_daily` → bảng lưới theo ngày) và bỏ
bước gán trạm; phần tính tích lũy và phân vùng Parquet giữ nguyên. Ma trận trọng số
`results/G4/rain_weights.csv` cũng không còn cần.

---

## G5 – Tập huấn luyện (`build_training_set.py`)

### Chạy

```bash
MSYS_NO_PATHCONV=1 docker run --rm --name g5-driver --hostname g5-driver \
  --network spark-mongodb-gis_gis-net \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 -e MONGO_URI=mongodb://mongo:27017 \
  -v "<gốc repo>:/work:ro" -v "<gốc repo>/results:/out" \
  -v "<gốc repo>/data/processed:/work/data/processed" \
  gis-spark:local /opt/spark/bin/spark-submit --master 'local[2]' \
  --conf spark.driver.host=g5-driver --conf spark.executor.memory=1g \
  /work/pipelines/spark/features/build_training_set.py
```

### Quy tắc ngày của đỉnh — **phải khớp với `pipelines/r/`**

`day` của một mẫu là **ngày quan sát của bản tin** (không phải ngày lịch của mốc thời gian
đỉnh). Đỉnh/chân sau nửa đêm vẫn thuộc ngày quan sát của bản tin → `peakN_day_offset = 0`;
chỉ khi mốc thời gian thực sự sang ngày kế tiếp mới `= 1`. Quy tắc này nằm trong
`features/rules.py::peak_day_offset` để R và Spark dùng chung một hàm.

### Kiểm tra rò rỉ (GĐ3.6)

Cột `max_obs_day` = ngày quan sát **mới nhất** thực sự được dùng cho các đặc trưng của dòng
đó. Job **assert `max_obs_day < day`** cho mọi dòng và in kết quả.

### Kết quả đo thật (2026-11-23)

| Chỉ số | Giá trị |
|---|---|
| Số dòng (trạm, ngày) | **32.352** |
| Số trạm | 9 (Vũng Tàu, Nhà Bè, Bến Lức, Tân An, Gò Dầu Hạ, Biên Hòa, Thủ Dầu Một, Dầu Tiếng, Phú An) |
| Khoảng ngày | 2016-12-31 → 2026-11-10 |
| Thiếu đỉnh 1 / đỉnh 2 | 138 / 3.975 |
| Phân bố `exceed_level` | III: 431 · II: 619 · I: 886 · rỗng: 30.416 |
| **Kiểm tra rò rỉ** | **32.343 dòng có quan trắc đầu vào, 0 dòng vi phạm** (`max_obs_day < day`) |

Số dòng theo năm: 2016: 9 · 2017: 3.265 · 2018: 3.276 · 2019: 3.285 · 2020: 3.294 ·
2021: 3.276 · 2022: 3.275 · 2023: 3.267 · 2024: 3.294 · 2025: 3.285 · 2026: 2.826.

### Chia tập (đã chốt, không đổi)

| Tập | Từ | Đến | Số ngày-quan-sát |
|---|---|---|---:|
| `train` | 2017-01-01 | 2022-12-31 | 2.186 |
| `val` | 2023-01-01 | 2024-06-30 | 545 |
| `test` | 2024-07-01 | 2026-11-23 | 863 |

### Cột thiên văn

`astro_h1`, `astro_h2`, `astro_t1_h`, `astro_t2_h` lấy từ
`data/processed/features/astro_tide.csv` (do R sinh). **Hiện chưa có file này** → job in cảnh báo
`[CẢNH BÁO] thiếu data/processed/features/astro_tide.csv – cột astro_* để null` và ghi `null`.
Khi R có file, chạy lại G5 là đủ (không cần sửa code).

`moon_phase` để `null` (chưa có bảng tra trăng trong CSDL); `lunar_day` lấy từ
`hydromet_bulletins.lunar_date.day`.

### `_meta.json`

Ghi kèm `data/processed/features/g5/_meta.json`: `data_snapshot` (số phần tử mỗi collection,
mốc thời gian lớn nhất, ngày chạy, phiên bản job), số dòng theo năm / theo trạm, kết quả kiểm
tra rò rỉ, biên chia tập và **quy tắc `peak_day_offset`**. `data_snapshot` được ghi vào model
record nên phải chính xác — đây là giá trị được sinh tự động từ CSDL tại thời điểm chạy.

---

## Việc còn lại

- **Sedona / GraphFrames / Redis không có trong image `gis-spark:local`** (đã kiểm tra `/opt/spark/jars`,
  Ivy cache và module Python). Q5 dùng Spark thuần + khoảng cách chính xác tự tính; Q6 dùng BFS
  thuần; bước làm nóng Redis ở Q1–Q4/Q7 được bảo vệ bằng biến `REDIS_URL` và bỏ qua gọn khi
  thiếu.
- **Sản phẩm mưa vệ tinh** chưa tải → G4 là fallback theo trạm (xem mục trên).
- **`astro_tide.csv`** chưa có → cột `astro_*` để null (xem mục trên).
- G4 sinh 2,3 triệu dòng; nếu cần dùng trong mô hình, nên chuyển sang định dạng rộng hơn
  (nút × ngày → ngày × nút) để join rẻ hơn.
