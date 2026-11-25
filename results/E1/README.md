# results/E1 – Điều hòa thủy triều và độ trễ pha

> **TRẠNG THÁI (24/11/2026): đã chạy lại, không còn rò rỉ tập test.**
> `harmonic.R` (fit **chỉ trên train** 2017-01-01 → 2022-12-31 theo `config/splits.json`) chạy lại lúc 11:34;
> `phase_lag.R` chạy lại lúc 15:12 (log: `_lag_log.txt`). Mọi file trong thư mục này là của lần chạy mới.
> Bản sao của bảng cũ: `git show 76ceb4c:results/E1/<file>`.

## Đã sửa trong code

- `harmonic.R` chỉ fit trên các tập `harmonic_fit_splits` của `config/splits.json` (hiện: `train`);
  kiểm tra độ chính xác = fit train → dự tính val (2023-01-01 → 2024-06-30). `assert_no_test_days` dừng
  script nếu dữ liệu fit có ngày ≥ mốc test.
- Ghi `data/processed/features/astro_tide_meta.json` (`fit_from`, `fit_to`, `test_from`); Spark G5,
  `phasegnn` (`check_astro_provenance`) và `scripts/run_baselines.py` từ chối file triều không có meta hoặc
  `fit_to >= test_from`.
- `phase_lag.R` ghép đỉnh theo **thế hệ chu kỳ triều** (`R/rules.R::assign_peak_generation`, thế hệ = 2 chu kỳ
  M2 tính từ đỉnh Vũng Tàu đầu tiên, trong thế hệ xếp theo **thời gian**) thay cho thứ tự giờ trong ngày quan sát.

## Điều hòa – kiểm tra trên val (`harmonic_validation.csv`)

| | Bản cũ (fit 2017–2023, kiểm tra 2024–2026 — chạm test) | Bản mới (fit train, kiểm tra val) |
|---|---|---|
| MAE độ cao đỉnh | 0.213 – 0.416 m | **0.186 – 0.369 m** (Phú An thấp nhất, Dầu Tiếng cao nhất) |
| MAE giờ đỉnh | 0.71 – 1.51 h | **0.67 – 1.56 h** |

Biên độ M2 (m, fit train): Vũng Tàu 0.824; Biên Hòa 1.085; Nhà Bè 1.039; Phú An 1.009; Thủ Dầu Một 0.802;
Bến Lức 0.731; Tân An 0.636; Dầu Tiếng 0.587; Gò Dầu Hạ 0.245.

## Độ trễ pha so với Vũng Tàu (`phase_lag_by_station.csv`)

Trung vị von Mises của hiệu giờ đỉnh (giờ), CI 95 % bootstrap 2 000 lần; "điều hòa" = hiệu pha M2 quy ra giờ.

| Trạm | km | Đỉnh 1 mới [CI] | Đỉnh 1 cũ | Đỉnh 2 mới | Đỉnh 2 cũ | Điều hòa mới | Điều hòa cũ |
|---|---:|---|---:|---:|---:|---:|---:|
| Nhà Bè | 40.1 | **2.05** [2.00, 2.11] | 1.73 | 1.37 | 1.58 | 1.61 | 1.37 |
| Phú An | 68.1 | **3.04** [2.98, 3.10] | 2.69 | 2.25 | 2.50 | 2.39 | 2.26 |
| Bến Lức | 74.7 | **3.02** [2.95, 3.08] | 2.71 | 2.26 | 2.41 | 2.56 | 2.32 |
| Tân An | 87.3 | **3.18** [3.12, 3.24] | 2.90 | 2.46 | 2.60 | 2.77 | 2.61 |
| Thủ Dầu Một | 105.5 | **4.49** [4.43, 4.55] | 4.14 | 3.64 | 3.98 | 3.78 | 3.63 |
| Biên Hòa | 110.4 | **3.88** [3.82, 3.95] | 3.64 | 3.17 | 3.29 | 3.18 | 2.92 |
| Gò Dầu Hạ | 157.6 | **6.70** [6.63, 6.78] | 6.61 | 5.78 | 6.20 | 5.38 | 5.05 |
| Dầu Tiếng | 200.3 | **8.68** [8.61, 8.76] | 10.36 [−11.85, 11.87] | **8.01** | **−7.02** | **7.36** | **−5.10** |

- **Dầu Tiếng đã hết sai dấu:** đỉnh 2 từ −7.02 h → +8.01 h, điều hòa từ −5.10 h → +7.36 h, CI đỉnh 1 không còn
  trải hết (−12, 12). Các trạm khác lệch 0.1 – 0.4 h so với bản cũ (đỉnh 1 tăng, đỉnh 2 giảm do xếp theo thời gian
  trong thế hệ thay vì theo giờ trong ngày).
- Phương pháp tương quan chéo (`lag_xcorr_h`) vẫn cho độ trễ âm ở mọi trạm (−1 … −8, r 0.22 – 0.60), trái dấu với hai phương pháp
  trên → không dùng làm kết quả, chỉ để đối chiếu.

## Giả thuyết

| | Bản cũ | Bản mới |
|---|---|---|
| **H5** `lag_median_h ~ dist_to_root_km` (n = 8) | slope 0.0532 h/km, R² 0.942, p 6.3e-05 | **slope 0.0424 h/km** [0.0349, 0.0499], **R² 0.970**, p 8.9e-06; Spearman ρ 0.952 (p 0.001) |
| H5 theo pha điều hòa | slope −0.027 h/km, R² 0.21 (p 0.26) — không phù hợp | **slope 0.0359 h/km**, R² 0.972, p 7.0e-06 — nay **khớp** với phương pháp pha tròn |
| **H6** `lag ~ q_release + season` (n = 13 009) | `Mùa mưa` +0.594 h (p 6.3e-12) | `Mùa mưa` −0.013 h (p 0.88); `q_release` −2.1e-4 h/(m³/s) (p 0.018); **R² 0.0007** |
| **H7** `log(tỷ số biên độ M2) ~ log(km)` | hệ số −0.591 (p 0.094), R² 0.40 | hệ số −0.594 (p 0.105), R² 0.38 |

**Kết luận mới:** H5 được ủng hộ mạnh hơn (hai phương pháp độc lập cùng cho ~0.036 – 0.042 h/km). **H6 đổi kết
luận:** hiệu ứng mùa +0.6 h của bản cũ biến mất sau khi sửa ghép đỉnh — nó là hệ quả của ghép sai chu kỳ;
lưu lượng xả có hệ số âm, nhỏ, ý nghĩa biên (p 0.018) và gần như không giải thích phương sai (R² < 0.001).
H7 không đổi: biên độ giảm theo khoảng cách nhưng không có ý nghĩa ở mức 5 % (n = 8).

## Chạy lại

```powershell
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/harmonic.R
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/phase_lag.R
```
