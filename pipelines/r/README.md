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
| `evaluation.R` | 4.5 chạy đánh giá (B0 / B_harm / B_KTTV + mọi file dự báo `--pred`) → `results/evaluation/` |
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

> **CẬP NHẬT (24/11/2026) — đã chạy lại không rò rỉ:** `harmonic.R` (fit chỉ train) → `phase_lag.R` → Spark G5 →
> `scripts/run_baselines.py` → PhaseGNN/B3/B4/B5 → `evaluation.R --split test`. Số liệu chính thức mới nằm ở
> `results/E1/README.md`, `results/baselines/README.md`, `results/E2/README.md`, `results/evaluation/README.md`;
> các mục 4.2–4.4 dưới đây chỉ còn con trỏ tới các README đó (bảng số cũ — fit 2017–2026, đánh giá toàn giai đoạn — đã bỏ; xem lịch sử git).
> `evaluation.R` đổi tên mô hình ngoài trùng tên mô hình nội bộ (vd. B0 persistence của Python → `B0_py`).

### 4.2 Điều hòa thủy triều — `results/E1/`

Số chính thức (fit **chỉ trên train** 2017–2022, kiểm tra trên val): xem `results/E1/README.md`
(`harmonic_validation.csv`, `astro_tide_by_station.csv`, `astro_tide_meta.json`). Phương pháp: mục 5.1.
Lưu ý mốc datum: `evaluation.R` ưu tiên `results/E1/astro_tide_by_station.csv` (triều thiên văn theo
từng trạm); `data/processed/features/astro_tide.csv` chỉ là Vũng Tàu (áp cho mọi trạm sẽ lệch ~2,8 m).

### 4.3 Độ trễ pha — `results/E1/`

Độ trễ so với Vũng Tàu và H5–H7, tương quan chéo xả hồ: xem `results/E1/README.md`
(`phase_lag_by_station.csv`, `h5_lag_vs_distance.csv`, `h6_lag_vs_discharge.csv`,
`h7_amplitude_ratio*.csv`, `crosscorr_release_residual*.csv`). Phương pháp: mục 5.3.

### 4.4 Đánh giá mô hình — `results/evaluation/`

Đầu ra của `evaluation.R --split test` (ghép ±6 h, Wilcoxon trên |e|, DM trên |e_a| − |e_b|, Holm) nằm ở
`results/evaluation/` (trước 25/11/2026 là `results/E5/`; `results/E5/` nay dành cho E5 "Tổng quát hóa").
Bảng chỉ số, kiểm định và kết luận: `results/evaluation/README.md`. Tóm tắt (tập test 2024-07-01 → 2026-11-23):
B_KTTV vẫn tốt nhất (mọi p_Holm < 1e-9); PhaseGNN tốt nhất trong các mô hình học máy nhưng không tốt hơn
persistence (B0_py). Ablation 3 seed (phasegnn, B3, B4, B5a, B5b): `results/E2/README.md` và
`results/E2/ablation_*.csv`.

### 4.5 Unit test — `pipelines/r/tests/test_evaluation.R`
```
=== Unit test ham danh gia (pipelines/r/R/evaluation.R) ===
PASS:  114    FAIL:  0   (chạy lại 25/11/2026)
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
- `results/evaluation/` đã gộp B0_py/B1/B2, PhaseGNN và các biến thể (xem `results/evaluation/README.md`).
