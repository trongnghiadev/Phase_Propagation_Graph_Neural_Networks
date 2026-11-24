# results/E1 – Điều hòa thủy triều và độ trễ pha

> **TRẠNG THÁI (24/11/2026): fit trên cả test – cần chạy lại.**
> Mọi file trong thư mục này (`harmonic_*.csv/png`, `astro_tide_by_station.csv`, `astro_residual*`,
> `phase_lag_*`, `h5_*`, `h6_*`, `h7_*`, `crosscorr_*`) và `data/processed/features/astro_tide.csv` do bản cũ
> của `pipelines/r/harmonic.R` sinh: hệ số điều hòa fit trên `FIT_YEARS <- 2017:2026`, gồm cả tập test
> (từ 2024-07-01). `harmonic_validation.csv` cũ kiểm tra trên 2024–2026 (chạm tập test).

## Đã sửa trong code

- `harmonic.R` chỉ fit trên các tập `harmonic_fit_splits` của `config/splits.json` (hiện: `train`,
  2017-01-01 → 2022-12-31); kiểm tra độ chính xác = fit train → dự tính val. Kiểm tra tự động
  `assert_no_test_days` dừng script nếu dữ liệu fit có ngày ≥ mốc test.
- Ghi thêm `data/processed/features/astro_tide_meta.json` (`fit_from`, `fit_to`, `test_from`). Spark G5,
  `phasegnn` (`check_astro_provenance`) và `scripts/run_baselines.py` từ chối file triều **không có meta** hoặc
  `fit_to >= test_from` — nên file hiện có sẽ bị từ chối cho tới khi chạy lại.
- `harmonic_fit_summary.csv` có thêm cột `fit_from`, `fit_to`; `harmonic_validation.csv` có `train_window`,
  `val_window`.

## Chạy lại

```powershell
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/harmonic.R
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/phase_lag.R
```

Ước lượng trước khi chạy lại (bản port Python của LSQ, tái lập bảng R tới ~1e-5 m;
`scripts/estimate_harmonic_leak.py`, bảng `results/baselines/leak_fix_estimate_20261124.csv`): hệ số M2 đổi
≤ 0.02 m, nhưng mực trung bình `a0` + xu hướng tuyến tính ngoại suy sang 2024–2026 lệch nhiều hơn
(ví dụ Đầu Tiếng `a0` 0.17 → −0.41 m) → B_harm test lệch dương hơn (bias +0.061 → +0.149 m).
