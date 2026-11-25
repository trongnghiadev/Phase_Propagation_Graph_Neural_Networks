# results/baselines – Mô hình cơ sở trên tập test (GĐ5)

Sinh bởi `scripts/run_baselines.py` (B0, B_harm, B1, B2, B_KTTV), chấm bằng `phasegnn.evaluate`.

> **TRẠNG THÁI (24/11/2026): đã chạy lại** (15:15 – 15:20, 4,5 phút CPU; log `results/baselines_run.log`, không commit).
> Đầu vào: G5 chạy lại 24/11 (`astro_merged = true`, `astro_fit` = train 2017-01-01 → 2022-12-31) và
> `results/E1/astro_tide_by_station.csv` của `harmonic.R` mới (fit chỉ trên train). `summary*.csv`,
> `b1_coefficients.json` là của lần chạy này. Các lỗi `mae_dt` (giờ đỉnh thực đo) và `t_peak` của B2 đã sửa trong code
> trước lần chạy.

## Trước / sau (tập test 2024-07-01 → 2026-11-23, 863 ngày phát bản tin)

"Trước" = `summary.csv` ở commit 76ceb4c (triều thiên văn fit 2017–2026, gồm test; `mae_dt` tính sai).

| Mô hình | n cặp | MAE (m) trước → **sau** | RMSE | bias | NSE | MAE Δt (h) | Phủ 10–90 % |
|---|---:|---|---|---|---|---|---|
| B0 (persistence + biến thiên thiên văn) | 38 565 | 0.1635 → **0.1661** | 0.229 → 0.231 | +0.041 → +0.043 | 0.928 → 0.927 | 5.271 → 4.011 | 0.602 → 0.592 |
| B_harm (thiên văn) | 38 745 | 0.1975 → **0.2452** | 0.244 → 0.311 | +0.061 → +0.149 | 0.919 → 0.868 | 5.136 → 3.780 | 0.432 → 0.380 |
| B1 (điều hòa + hồi quy phần dư) | 38 745 | 0.1875 → **0.2165** | 0.238 → 0.272 | −0.024 → +0.047 | 0.923 → 0.899 | 1.195 → 4.872 | 0.465 → 0.424 |
| B2 (LSTM, 3 trạm, 5 seed) | 12 675 | 0.1806 → **0.1509** | 0.219 → 0.186 | −0.144 → −0.114 | 0.173 → 0.403 | 5.595 → 5.867 | 0.187 → 0.221 |
| **B_KTTV** (dự báo chính thức) | 11 050 | 0.0806 → **0.0806** | 0.117 | +0.002 | 0.769 | 4.511 → **2.590** | 0.862 |

- **B_harm, B1 tệ đi** đúng như dự kiến khi bỏ rò rỉ: mực trung bình + xu hướng fit trên 2017–2022 ngoại suy
  sang 2024–2026 bị lệch dương (bias B_harm +0.149 m). Ước lượng bằng bản port Python trước đó (B_harm 0.2451,
  B1 0.2165 m) khớp số chính thức tới 1e-4 m → đã xóa `leak_fix_estimate_20261124.csv` và
  `scripts/estimate_harmonic_leak.py`.
- B0 đổi nhẹ vì dùng biến thiên thiên văn mới. B2 đổi do G5 mới (có `astro_*`) và mẫu theo lưới ngày lịch.
- `mae_dt` cũ của B1 (1.195 h) thấp giả tạo (so với giờ đỉnh 1 thay vì giờ đỉnh cao hơn); B_KTTV/B0/B_harm
  đổi `mae_dt` cũng vì lỗi này, không phải vì dự báo đổi.
- **B_KTTV vẫn tốt nhất về MAE độ cao (0.081 m) và giờ đỉnh (2.59 h)**; mọi mô hình cơ sở khác kém hơn rõ.
  NSE của B_KTTV/B2 thấp hơn B0 vì chúng chỉ có ở 3 trạm (phương sai nhỏ hơn) — không so NSE giữa các tập khác nhau.
- Kiểm định trên cặp ghép chung: xem `results/evaluation/README.md`.

## Chạy lại

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$PWD:/work" -w /work --network spark-mongodb-gis_gis-net \
    -e PYTHONPATH=/work/packages/phasegnn/src -e MONGO_URI=mongodb://mongo:27017 phasegnn:local \
    python -X utf8 scripts/run_baselines.py --seeds 0 1 2 3 4 --out-dir results/baselines
```

`MONGO_URI` (mới, mặc định `mongodb://localhost:27017`) dùng cho B_KTTV khi chạy trong container.
