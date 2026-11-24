# pipelines/r/ – Phân tích bằng R (GĐ4, Chương 4)

Môi trường: **R 4.6.2**, thư viện nằm ở `.rlib/` (thư mục gốc repo, đã có trong `.gitignore`).
MongoDB: `mongodb://localhost:27017`, db `vietnam_gis`.

```r
.libPaths(c(".rlib", .libPaths()))   # luôn goi truoc khi source() / library()
```

> **Luật phụ thuộc (ADR 0001):** `pipelines/` không import từ `packages/`, R không gọi Python.
> Toàn bộ logic dùng chung nằm trong `pipelines/r/R/`.

---

## 1. Cấu trúc

| File | Nội dung |
|---|---|
| `R/connect.R` | `mongolite` + quy đổi UTC → giờ Việt Nam (`to_vn()`, `vn_day()`, `vn_hour()`) + quy tắc ngày đỉnh |
| `R/io.R` | đọc/ghi Parquet (`read_parquet_df`, `write_parquet_df`), CSV, JSON, `save_fig()` |
| `R/theme.R` | mẫu hình thống nhất: `theme_pgnn()`, `palette_pgnn()`, `PGNN_FONT` |
| `R/harmonics.R` | điều hòa bình phương nhỏ nhất tự viết: `fit_harmonics()`, `astro_at()`, `find_extremes_t()` |
| `R/rules.R` | quy tắc ngày đỉnh (đồng bộ Spark) + nạp dữ liệu triều / mưa / hồ / trạm / ngưỡng báo động |
| `R/evaluation.R` | hàm đánh giá **chung cho mọi mô hình** (theo `schemas/prediction/v1.schema.json`) |
| `R/data.R` | bản nháp ban đầu của tải dữ liệu — **không source cùng `R/rules.R`** (trùng tên hàm) |
| `descriptive.R` | 4.2 thống kê mô tả + 4.6 ngoại lệ → `results/E8/` |
| `harmonic.R` | 4.3 điều hòa thủy triều → `results/E1/` + `data/processed/features/astro_tide.csv` + `astro_tide_meta.json`; **chỉ fit trên `harmonic_fit_splits` của `config/splits.json` (mặc định: train)** |
| `phase_lag.R` | 4.4 độ trễ pha, H5–H7 → `results/E1/` |
| `evaluation.R` | 4.5 chạy đánh giá (B0 / B_harm / B_KTTV) → `results/E5/` |
| `tests/test_evaluation.R` | unit test đánh giá (chạy bằng `Rscript`, dùng `stopifnot`; mục 13–14 thêm 24/11/2026: khóa ghép cặp kiểm định, chống rò rỉ khi fit điều hòa) |

**Biên chia tập** đọc từ `config/splits.json` (`R/io.R::load_splits`) — cùng file với
`phasegnn.data.SPLITS` và Spark G5 (`pipelines/spark/features/splits.py`); không viết cứng năm/ngày trong script.

## 2. Cách chạy

```powershell
# (A) đặt thư viện repo vào đường dẫn rồi chạy từ gốc repo
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/descriptive.R
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/harmonic.R
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/phase_lag.R
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/evaluation.R
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/tests/test_evaluation.R
```

Các script tự tìm gốc repo (biến môi trường `PGNN_REPO_ROOT` ưu tiên), nên có thể gọi từ bất kỳ đâu.

> **Lưu ý Windows:** `arrow::open_dataset()` trong bản `arrow` 26.0.1 trong `.rlib` bị lỗi (R segfault `0xC0000005`)
> → `R/io.R::read_parquet_df()` đọc **từng file `part-*.parquet`** rồi `rbind`.
> `save_fig()` gọi `png(units = "px", res = 96)` vì `units = "in"` bị lỗi *"unable to allocate bitmap"*.

## 3. Quy tắc dữ liệu (bắt buộc dùng chung)

| Nội dung | Quy ước |
|---|---|
| Múi giờ | Dữ liệu gốc là **giờ Việt Nam (UTC+7)**; MongoDB lưu `Date` theo UTC. `to_vn()` **giữ nguyên epoch** và chỉ đổi cách hiển thị → `format(x, tz="Asia/Ho_Chi_Minh")` ra giờ Việt Nam. `vn_day()` trả về ngày lịch Việt Nam. |
| Ngày của đỉnh | `day` = **ngày quan sát của bản tin**; đỉnh thứ N = phần tử thứ N **theo giờ tăng dần** trong ngày quan sát; `peakN_day_offset` = 0 nếu thuộc ngày quan sát, 1 nếu sang ngày kế tiếp. Khai báo lại trong `R/rules.R` (đồng bộ `pipelines/spark/features/rules.py::peak_day_offset`), **không gọi Python**. |
| Ngưỡng báo động | `stations.alarm_history` có nhiều đoạn `from/to` với `level_1_m/2_m/3_m` → ngưỡng **thay đổi theo thời gian**, đối chiếu theo **ngày**. Không dùng ngưỡng cố định. |
| Địa giới | Các truy vấn phân vùng địa giới đều kèm `era` (`before_2025_07` / `from_2025_07`); tên tỉnh so qua `name_norm`/`aliases_norm`. |

---

## 4. Kết quả đo thật

> Toàn bộ số dưới đây lấy từ **lần chạy thật** ngày 2026-11-24, nguồn: MongoDB `vietnam_gis`
> (129 420 tài liệu `tide_extremes`, 38 410 `official_forecasts`, 15 313 `reservoir_daily`,
> 13 127 `rainfall_daily`, 26 `stations`, 3 636 `hydromet_bulletins`, 21 508 `merit_river_reaches`).

### 4.1 Thư viện dùng chung — `results/E8/`

| Kết quả | Số liệu thật |
|---|---|
| Số ngày quan sát có bản tin | **3 595** ngay, **9** trạm triều, 2016–2026 |
| Số đỉnh (peak1 + peak2) dùng cho thống kê | **60 591** đỉnh (32 214 đỉnh 1 + 28 377 đỉnh 2; mỗi trạm ~6 489–6 868) |
| Độ cao đỉnh TB / trung vị theo trạm | Vũng Tàu 3.65 / 3.68 m; Biên Hòa 1.39 / 1.43; Thủ Dầu Một 1.24 / 1.26; Dầu Tiếng 1.17 / 1.16; Nhà Bè 1.14 / 1.18; Phú An 1.14 / 1.18; Tân An 1.08 / 1.08; Bến Lức 1.03 / 1.04; Gò Dầu Hạ 0.64 / 0.63 m |
| **Mùa triều cường** (3 tháng đỉnh trung vị cao nhất) | **Tháng 10 – 11 – 12** (trung vị 1.43 / 1.42 / 1.34 m; thấp nhất là tháng 6: 1.00 m) |
| Ngưỡng báo động theo thời gian | 32 214 dòng trạm–ngày, **6 462** dòng có ngưỡng BĐ I (3 trạm có ngưỡng: Nhà Bè, Phú An, Thủ Dầu Một) |
| Hồ chứa | 7 hồ; **231 đợt xả tràn** (`e8_reservoir_spill_events.csv`) |
| Mưa | 10 trạm, 5 285 ngày có số liệu |
| **Xu hướng Phú An / Nhà Bè** (Mann–Kendall + Sen) | 11 năm (2016–2026) |
| Phân phối MERIT cây 44045960 | **1 072 đoan sông** (trong tổng 21 508) |

Bảng: `e8_peak_summary_by_station.csv`, `e8_peak_by_{year,month,mday,hour}.csv`,
`e8_flood_season_profile.csv`, `e8_alarm_frequency_by_year.csv`, `e8_reservoir_*.csv`,
`e8_rainfall_*.csv`, `e8_trend_mk_sen.csv`, `e8_merit_*.csv`.

**MERIT — cây 44045960 (1 072 đoan):**

| Thuộc tính | TB | Độ lệch chuẩn | Min | Trung vị | Max |
|---|---|---|---|---|---|
| `lengthkm` (km) | 8.68 | 7.53 | 0.09 | 6.77 | 54.63 |
| `lengthdir` (km) | 5.66 | 4.47 | 0.09 | 4.60 | 37.09 |
| `sinuosity` | 1.47 | 0.32 | 1.00 | 1.41 | 3.58 |
| `slope` | 3.12e-3 | 7.72e-3 | 0 | 6.77e-4 | 9.45e-2 |
| `uparea` (km²) | 1 454.8 | 4 155.8 | 25.0 | 108.2 | 45 471.0 |
| `order` (bậc) | 1.96 | 1.19 | 1 | 1 | 6 |

Theo bậc: bậc 1 = 538 đoan (4 505 km), bậc 2 = 252 (2 234 km), bậc 3 = 103 (1 040 km),
bậc 4 = 149 (1 306 km), bậc 5 = 29 (220 km), bậc 6 = 1 (5 km).

> **CẢNH BÁO (24/11/2026) — số liệu mục 4.2 và 4.4 dưới đây là của bản cũ, fit trên cả tập test, cần chạy lại.**
> Bản cũ của `harmonic.R` fit hệ số điều hòa trên `FIT_YEARS <- 2017:2026` (gồm tập test từ 2024-07-01), nên
> `astro_tide*.csv`, B_harm, B1 và mọi chỉ số dùng chúng bị rò rỉ dữ liệu test. B0 (climatology) trong
> `evaluation.R` cũng lấy trung bình trên mọi năm, kể cả năm test. Kiểm định Wilcoxon/DM cũ ghép cặp thiếu
> `issued_date`/`lead_day` và kiểm định sai đại lượng (xem 5.4). Thứ tự chạy lại: `harmonic.R` → `phase_lag.R` →
> (Spark G5) → `scripts/run_baselines.py` → `evaluation.R --pred results/baselines/predictions_all_test.csv`.
> Ước lượng bằng bản port Python khớp R tới 1e-5 m: B_harm test MAE 0.198 → **0.245 m**, B1 0.187 → **0.216 m**.

### 4.2 Điều hòa thủy triều — `results/E1/`

| Kết quả | Số liệu thật |
|---|---|
| Phương pháp | **Bình phương nhỏ nhất tự viết** (không dùng `oce`/`TideHarmonics` — cần GDAL/PROJ). Xem mục 6. |
| Thành phần | 11 thành phần: M2, S2, N2, K1, O1, P1, Q1, M4, MS4, M6, Mf (+ xu hướng tuyến tính) |
| Số quan sát dùng để fit | 13 002 – 13 747 mẫu / trạm (đỉnh **không đều** theo thời gian) |
| Biến độ M2 (m) | Vũng Tàu 0.828; Biên Hòa 1.073; Nhà Bè 1.022; Phú An 0.990; Thủ Dầu Một 0.787; Bến Lức 0.728; Tân An 0.639; Dầu Tiếng 0.563; Gò Dầu Hạ 0.253 |
| Biến độ K1 (m) | 0.132 – 0.638; O1 0.071 – 0.419 |
| Phần phương sai giải thích | 0.443 (Gò Dầu Hạ) – 0.960 (Nhà Bè); Vũng Tàu 0.949 |
| **Kiểm định (bản cũ: train ≤ 2023, val 2024–2026 — chạm tập test; bản mới: fit train, kiểm tra trên val)** | MAE độ cao **0.213 – 0.416 m**; MAE giờ đỉnh **0.71 – 1.51 h** (chi tiết `harmonic_validation.csv`) |
| `data/processed/features/astro_tide.csv` | **3 607 dòng dữ liệu** (+1 dòng header), cột `day,h1,h2,t1_h,t2_h`, từ **2016-12-31** đến **2026-11-15** (toàn giai đoạn + 5 ngày tương lai) |
| `results/E1/astro_tide_by_station.csv` | **32 463 dòng** — triêu thiên van DU TINH THEO TUNG TRAM (moi tram co moc datum rieng) |
| Phần dư phi thiên văn | `results/E1/astro_residual.csv` — 60 573 dòng (trạm–ngày) |

> **Lưu ý quan trọng về mốc datum:** `data/processed/features/astro_tide.csv` theo hợp đồng dữ liệu với
> Spark G5 chỉ chứa triều thiên văn tại **Vũng Tàu** (hằng số `a0` = 2.58 m). Nếu áp file này cho mọi trạm
> thì sai số hệ thống do lệch mốc datum lớn tới **~2.8 m** (Vũng Tàu 2.58 m vs Bến Lức −0.22 m) → MAE
> của B_harm tăng vọt lên 2.33 m. Vì vậy `evaluation.R` **ưu tiên** `astro_tide_by_station.csv`
> (triều thiên văn tính riêng cho từng trạm) và chỉ fallback về `astro_tide.csv` khi thiếu file đó.

### 4.3 Độ trễ pha — `results/E1/`

Độ trễ so với Vũng Tàu (giờ), uốc lượng **chính** bằng **chênh lệch pha tròn** (trung vị von Mises
trên chu kỳ 24 h — xem mục 5.3); khoảng tin cậy bootstrap **2 000 lần** theo ngày:

| Trạm | Khoảng cách dọc sông (km) | Trung vị (đỉnh 1) | IQR | CI 95% bootstrap | Phương pháp điều hòa |
|---|---|---|---|---|---|
| Nhà Bè | 40.1 | **1.73 h** | 0.026 | [1.695, 1.769] | 1.37 h |
| Phú An | 68.1 | **2.69 h** | 0.031 | [2.646, 2.734] | 2.26 h |
| Bến Lức | 74.7 | **2.71 h** | 0.033 | [2.668, 2.760] | 2.32 h |
| Tân An | 87.3 | **2.90 h** | 0.035 | [2.854, 2.955] | 2.61 h |
| Biên Hòa | 110.4 | **3.64 h** | 0.041 | [3.575, 3.700] | 2.92 h |
| Thủ Dầu Một | 105.5 | **4.14 h** | 0.040 | [4.080, 4.198] | 3.63 h |
| Gò Dầu Hạ | 157.6 | **6.61 h** | 0.079 | [6.493, 6.722] | 5.05 h |
| Dầu Tiếng | 200.3 | **10.36 h** | 1.574 | [−11.85, 11.87] | −5.10 h |

**H5 — độ trễ theo khoảng cách dọc sông** (`lag_median_h ~ dist_to_root_km`, n = 8):

- slope = **0.0532 h/km** (SE 0.0054), CI 95% [0.0400, 0.0664], **R² = 0.942**, Pearson r = 0.970 (p = 6.3e-05), Spearman ρ = 0.976 (p = 4.0e-04)
- Phương pháp điều hòa: slope = −0.0267 h/km, R² = 0.208 (p = 0.256) → **kém phù hợp hơn**, nên lấy kết quả pha tròn làm chính.

> **Ngoại lệ địa hình:** Dầu Tiếng nằm trên nhánh phụ của lưu vụ Bến Lức (lưu vụ nhỏ 2 674 km²) —
> độ trễ đỉnh 1 là **+10.36 h** nhưng đỉnh 2 lại là **−7.02 h** (đảo chiều pha), nên CI bootstrap
> rộng ([−11.85, 11.87]) và điểm này kéo R² của H5 xuống. Bỏ Dầu Tiếng thì slope = **0.0407 h/km** (R² = 0.940, p = 3.1e-04, n = 7) — cùng kết luận về chiều và độ lớn.

**H6 — độ trễ theo mùa và lưu lượng xả hồ** (`lag_day_h ~ q_release + season`, n = 12 933):

| Mô hình | R² | Hệ số | p |
|---|---|---|---|
| `lag ~ q_release + season` | 0.0062 | `q_release` = 4.57e-05 (p = 0.613); `Mùa mưa` = **+0.594 h** (p = 6.3e-12) | |
| `lag ~ q_release` | 0.0025 | `q_release` = 4.16e-04 (p = 1.1e-08) | |
| `lag ~ season` | 0.0061 | `Mùa mưa` = **+0.620 h** (p = 4.5e-19) | |
| `lag ~ q_release + season + dist_to_root_km` | **0.0124** | `dist_to_root_km` = 0.0089 h/km (p = 1.5e-19) | |

→ **Mùa mưa làm độ trễ dài thêm ~0.6 h**; lưu lượng xả hồ **không** có tác dụng rõ rệt khi đã có `season` và khoảng cách.

**H7 — tỉ số biên độ theo khoảng cách** (`log(amp_ratio) ~ log(dist_to_root_km)`, M2, n = 8):
slope = **−0.591** (SE 0.297, p = 0.094), R² = 0.397 → biên độ giảm theo khoảng cách
( ví dụ: Nhà Bè 1.234, Phú An 1.196, Thủ Dầu Một 0.951, Bến Lức 0.879, Tân An 0.772,
Dầu Tiếng 0.680, Gò Dầu Hạ 0.305 so với Vũng Tàu).

**Tương quan chéo xả hồ ↔ phần dư phi thiên văn** (độ trễ 0–5 ngày):

| Hồ | Trạm | Độ trễ tốt nhất (ngày) | r | n |
|---|---|---|---|---|
| Sr. P. Miêng | Biên Hòa | **5** | **0.230** | 3 567 |
| Trị An | Biên Hòa | 5 | 0.187 | 3 553 |
| Thác Mơ | Biên Hòa | 5 | 0.160 | 3 574 |
| Sr. P. Miêng | Nhà Bè | 5 | 0.068 | 3 553 |
| Thác Mơ | Nhà Bè | 5 | 0.041 | 3 560 |
| Trị An | Nhà Bè | 5 | 0.020 | 3 538 |

→ Tương quan thuận yếu, đỉnh ở **độ trễ 5 ngày** (giới hạn trên của cửa sổ) — dấu hiệu giả định giới hạn cửa sổ;
nên kết luận "xả hồ ảnh hưởng yếu đến phần dư phi thiên văn trong ≤5 ngày", không đủ cơ sở để khẳng định quan hệ nhân quả.

### 4.4 Danh gia mo hinh - `results/E5/`

Ham danh gia chay tren 3 baseline, ghep du bao - thuc do trong +-6 gio
(86 760 cap ghep duoc / 203 592 ban ghi du bao; bang thuc do 32 352 dong tram x ngay,
2016-12-31 den 2026-11-10):

| Mo hinh | n | MAE (m) | RMSE (m) | bias (m) | MAE dt (h) | bias dt (h) | NSE | KGE |
|---|---|---|---|---|---|---|---|---|
| B_KTTV (du bao chinh thuc) | 14 287 | **0.1271** | 0.1952 | -0.0073 | 5.192 | +4.427 | 0.628 | 0.800 |
| B0 (climatology) | 60 573 | 0.1776 | 0.2380 | +0.0039 | NA | NA | **0.925** | **0.949** |
| B_harm (trieu thien van theo tram) | 11 900 | 0.2285 | 0.2935 | +0.0607 | 4.802 | -2.102 | 0.896 | 0.923 |

> `B0` khong co `t_peak` -> ghep theo ngay, khong co chi so dt.
> `B_harm` dung trieu thien van **theo tung tram** (`results/E1/astro_tide_by_station.csv`);
> neu chi dung `astro_tide.csv` (Vung Tau) thi bias se la +2.31 m do khac muc datum.
> `B_harm` co `t_peak` (thoi gian dinh du tinh) -> `MAE dt` 4.802 h, `bias dt` -2.102 h:
> thuy trieu du tinh **den som** ~2 gio so quan sat. Do pha thu trieu he thu khong
> truyen tin cuc bo, dung chung cho 9 tram, nen MAE dt van lon hon mong doi.
> `B_KTTV` co MAE do cao tot nhung **MAE gio lon** (5.19 h) vi KTTV chi dua muc nuoc
> dinh trong ngay ma khong gan thoi diem cu the.

Chi so bao dong (theo nguong bao dong theo thoi gian, `evaluation_contingency.csv`):

| Mo hinh | Muc | n | POD | FAR | CSI |
|---|---|---|---|---|---|
| B_KTTV | I | 3 687 | **0.624** | 0.176 | **0.550** |
| B_KTTV | II | 3 687 | 0.572 | 0.236 | 0.486 |
| B_KTTV | III | 3 651 | 0.489 | 0.327 | 0.395 |
| B0 | I | 6 462 | 0.364 | 0.263 | 0.322 |
| B0 | II | 6 462 | 0.176 | 0.280 | 0.165 |
| B0 | III | 6 400 | 0.021 | 0.471 | 0.021 |
| B_harm | I | 2 070 | 0.203 | 0.711 | 0.135 |
| B_harm | II | 2 070 | 0.145 | 0.765 | 0.099 |
| B_harm | III | 2 050 | 0.034 | 0.889 | 0.027 |

Kiem dinh (Wilcoxon theo cap + Diebold-Mariano HAC, hieu chinh Holm) --
`evaluation_tests.csv`:

| Cap doi | Wilcoxon (n, p, p_Holm) | Diebold-Mariano (n, t, p_Holm) |
|---|---|---|
| B_harm vs B_KTTV | 827, p < 1e-300, p_Holm < 1e-300 | 827, t = 30.08, p_Holm = 8.47e-199 |
| B_harm vs B0 | 10 157, p = 4.07e-09, p_Holm = 4.07e-09 | 10 157, t = 69.62, p_Holm < 1e-300 |
| B_KTTV vs B0 | 5 123, p = 3.64e-51, p_Holm = 7.29e-51 | 5 123, t = 80.68, p_Holm < 1e-300 |

Ca 3 cap doi deu **khac biet co y nghia thong ke** o muc 5 % (sau hieu chinh Holm).
Ve do cao: B_KTTV tot nhat. Ve **skill canh bao**: B_KTTV vuot troi (CSI_I = 0.550).

Chi so theo chan troi (`evaluation_by_lead.csv`): MAE cua B_KTTV tang nhe tu
0.0985 m (lead 1) len 0.1228 m (lead 5), NSE giam tu 0.738 xuong 0.647.

Xem them `evaluation_by_station.csv`, `evaluation_by_peak.csv`,
`evaluation_wilcoxon.csv`, `evaluation_diebold_mariano.csv`,
`evaluation_matched_sample.csv`.

### 4.5 Unit test — `pipelines/r/tests/test_evaluation.R`
```
=== Unit test ham danh gia (pipelines/r/R/evaluation.R) ===
PASS:  58    FAIL:  0
=> TAT CA TEST DUNG.
```

9 nhóm: quy tắc ngày đỉnh · quy đổi thời gian UTC→VN · điều hòa (khôi phục biên độ biết trước) ·
ghép dự báo–thực đo ±6 h · chỉ số (MAE/RMSE/bias/NSE/KGE) · POD/FAR/CSI ·
kiểm định (Wilcoxon, Diebold–Mariano, Holm) · `evaluate_predictions` · `read_predictions`.

---

## 5. Phương pháp (để phần mô hình dùng chung)

### 5.1 Điều hòa thủy triều (LSQHA tự viết)

Dữ liệu là **đỉnh/chân không đều** (mỗi ngày 2–4 điểm, thiếu ngày) nên không dùng được
phương pháp Fourier trên lưới đều. Ta fit trực tiếp:

\[
h(t) = a_0 + \sum_{j}\big[a_j\cos(\omega_j t) + b_j\sin(\omega_j t)\big] + c\cdot t
\]

bằng **bình phương nhỏ nhất** (`qr.solve`, không tạo ma trận bình phương → ổn định với
mẫu ~13 500 và 24 tham số). 11 thành phần: M2, S2, N2, K1, O1, P1, Q1, M4, MS4, M6, Mf.
Biên độ \(A_j=\sqrt{a_j^2+b_j^2}\), pha \(\phi_j=\text{atan2}(b_j,a_j)\) (độ, quy ước chuẩn).
T0 = 2020-01-01 00:00 UTC; \(t\) tính bằng **giờ**, điểm gốc là **ngày quan sát của bản tin**.

> **Lý do không dùng `oce`/`TideHarmonics`:** cả hai đều cần GDAL/PROJ (không cài được trên máy này),
> và `oce::tide` giả định chuỗi **đều**. Phương án dự phòng bằng R thuần là hợp lệ cho dữ liệu
> đỉnh/chân không đều và đã được kiểm chứng (MAE 0.21–0.42 m).

### 5.2 Ghép dự báo – thực đo (±6 giờ)

- `day` = ngày quan sát của bản tin; đỉnh 1 / đỉnh 2 theo giờ tăng dần trong ngày đó.
- Với mỗi `(station, target_date)`, lấy **tất cả** đỉnh thực đo của ngày và **mọi** bản ghi dự báo
  (`peak_idx` 0/1), ghép **1–1** sao cho \(|t_{\text{dự báo}} - t_{\text{thực đo}}| \le 6\) giờ.
- **Ưu tiên ghép cùng chỉ số đỉnh** (0↔0, 1↔1) trước; nếu không có thì mới ghép theo thời gian.
- So sánh giờ theo **vòng 24 h**: \(dd = |a-b| \bmod 24\), \(dd_c = \min(dd, 24-dd)\).
- `d_h` = sai số độ cao (m).
- `d_t_h` = sai số giờ **có dấu** theo vòng 24 h:
  \(\Delta t = ((t_{\text{dự báo}} - t_{\text{thực đo}}) \bmod 24)\), chuẩn hóa về \([-12, 12)\) h.
  (Dùng **giờ trong ngay** 0..24 của cả hai bên — không dùng giờ tuyệt đối, vì dùng giờ tuyệt đối
  làm `Δt` của KTTV sai lệch ~7 h.)
- Dự báo **không có `t_peak`** (ví dụ climatology B0) được ghép theo
  `(station, target_date, peak_idx)` — không áp dụng lọc ±6 h và không tính `Δt` cho các cặp đó.

### 5.3 Cách tính độ trễ pha

- **Chính — chênh lệch pha tròn (circular phase difference, trung vị von Mises).**
  Giờ đỉnh là một pha số trong chu kỳ 24 h, nên sai số phải xử lý **tròn vòng**:
  \[
  d_i = ((t_{\text{trạm},i} - t_{\text{VT},i} + 12) \bmod 24) - 12
  \]
  \[
  \text{lag} = \operatorname{atan2}\!\big(\overline{\sin(2\pi d/24)},\ \overline{\cos(2\pi d/24)}\big)\cdot\frac{24}{2\pi}
  \]
  Dấu quy ước: **lag > 0 = trạm chậm hơn Vũng Tàu**.
- **Bổ sung — chênh lệch pha thành phần điều hòa M2/S2** (từ `harmonic.R`): `lag_h_harmonic`.
- Hai phương pháp có độ lệch 0.29–15.5 h (`phase_lag_method_compare.csv`); **H5 dùng phương pháp
  pha tròn** vì R² cao hơn nhiều (0.942 vs 0.208).
- **Bootstrap theo ngày** (2 000 lần, resample *ngày* để giữ phụ thuộc giữa đỉnh 1 và đỉnh 2
  của cùng ngày) → trung vị, IQR (Q1–Q3) và CI 95% percentile.
- **Lưu ý:** Dầu Tiếng là ngoại lệ do địa hình (lưu vụ nhỏ, nằm trên nhánh phụ) — độ trễ đỉnh 1
  là +10.36 h nhưng đỉnh 2 là −7.02 h (đảo chiều pha), nên CI bootstrap rất rộng ([−11.85, 11.87]).

### 5.4 Chỉ số & kiểm định

> **Sửa 24/11/2026.** (1) Cặp kiểm định ghép theo `(station, target_date, peak_idx, issued_date, lead_day)` —
> cùng ngày phát, cùng chân trời; mô hình không phụ thuộc chân trời (`B0`, `B_harm`, tham số `horizon_free`)
> được ghép với mọi `(issued_date, lead_day)` của mô hình kia. (2) Wilcoxon so `|e_a|` với `|e_b|` (trước: sai số
> có dấu). (3) Diebold–Mariano trên chênh lệch loss `|e_a| − |e_b|` (trước: `|e_a − e_b|`, luôn "có ý nghĩa").
> (4) `evaluation.R` mặc định chỉ đánh giá tập test (`--split test`; `--split all` = cách cũ).

- Độ cao: MAE, RMSE, bias (ΔH). Thời gian: MAE `d_t_h`, bias `d_t_h` (Δt).
- NSE, KGE (gói `hydroGOF`).
- POD, FAR, CSI theo ngưỡng báo động **theo thời gian** (`stations.alarm_history`), ngày có báo động
  khi mức thực tế ≥ mức ngưỡng tương ứng.
- Skill so với KTTV: dùng chính `official_forecasts` làm baseline `B_KTTV`.
- Kiểm định: **Wilcoxon** theo cặp (`wilcox.test(paired=TRUE)`), **Diebold–Mariano** (biến phân ước lượng
  HAC/Newey–West, `lag = floor(4(n/100)^(2/9))`), **hiệu chỉnh Holm** (step-down, có hiệu chỉnh đơn điệu).
  Trường hợp sai số hằng số (phương sai = 0) → p = 1 (không bằng chứng khác biệt).

---

## 6. Moi truong chay (sessionInfo)

```
R version 4.6.2 (2026-10-29 ucrt)
platform: x86_64-w64-mingw32
libPaths: <repo>/.rlib
```

Ngôn ngữ: `LANGUAGE = "en"` (mặc định), locale hệ thống `English (United States)`, múi giờ máy `Asia/Ho_Chi_Minh`.

| Gói | Phiên bản | Gói | Phiên bản |
|---|---|---|---|
| mongolite | 4.1.0 | ggplot2 | 4.0.4 |
| arrow | 26.0.1 | hydroGOF | 0.7.0 |
| dplyr | 1.2.1 | jsonlite | 2.0.0 |
| lubridate | 1.9.5 | tidyr | 1.3.2 |
| readr | 2.2.0 | purrr | 1.2.2 |
| trend | 1.1.10 | rstatix | 1.1.0 |
| scales | 1.4.0 | | |

## 7. Việc còn lại

- **`evaluation.R` mới đánh giá 3 mô hình có sẵn** (B0 climatology, B_harm thiên văn, B_KTTV).
  Khi worker phần mô hình xong, chỉ cần xuất bản ghi theo `schemas/prediction/v1.schema.json`
  (CSV hoặc Parquet, nhiều model trong cùng file, tách theo cột `model`) rồi chạy lại
  `pipelines/r/evaluation.R` — hàm đánh giá là **chung**, không cần sửa.
- **Mô hình H7** hiện mới có thành phần M2; nên mở rộng sang K1/O1 khi có thêm dữ liệu.
- **Tương quan chéo xả hồ** chỉ đạt r ≤ 0.23 và đỉnh ở biên cửa sổ 5 ngày → cần mở rộng cửa sổ
  (0–30 ngày) và kiểm tra thêm vùng trũng bán tháng để kết luận chắc chắn hơn.
- Chưa có mô hình dự báo nào trong `results/E5/` ngoài 3 baseline trên.
