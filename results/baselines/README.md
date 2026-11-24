# results/baselines – Mô hình cơ sở trên tập test (GĐ5)

Sinh bởi `scripts/run_baselines.py` (B0, B_harm, B1, B2, B_KTTV), chấm bằng `phasegnn.evaluate`.

> **TRẠNG THÁI (24/11/2026): B_harm và B1 – fit trên cả test – cần chạy lại.**
> `summary*.csv` hiện có dùng `results/E1/astro_tide_by_station.csv` của bản cũ `harmonic.R` (fit 2017–2026,
> gồm tập test). Ngoài ra `mae_dt` trong `summary*.csv` cũ tính với giờ đỉnh thực đo sai (luôn lấy giờ đỉnh 1
> kể cả khi đỉnh 2 cao hơn) và B2 có `t_peak` sai định dạng (`T41:04:00Z`, giờ VN < 7) do hàm `_hours_to_iso`
> chép lỗi trong `scripts/run_baselines.py`. Cả ba lỗi đã sửa trong code.

## Trước / sau (tập test 2024-07-01 → 2026-11-23)

Nguồn: `leak_fix_estimate_20261124.csv` (`scripts/estimate_harmonic_leak.py`). "Trước" = `summary.csv` hiện có.
"Sau – chấm lại" = cùng file dự báo, chấm bằng `phasegnn.evaluate` đã sửa (MAE độ cao không đổi; chỉ `mae_dt`
đổi). "Sau – fit train" = B_harm/B1 dựng lại với triều thiên văn fit **chỉ trên train** bằng bản port Python
của `harmonic.R` (bản port tái lập bảng R hiện có tới ~1e-5 m) — **ước lượng**, số chính thức cần chạy lại R.

| Mô hình | MAE trước (m) | MAE sau – fit train (m) | RMSE trước → sau | bias trước → sau | NSE trước → sau | MAE Δt trước → chấm lại (h) |
|---|---|---|---|---|---|---|
| B_harm | 0.1975 | **0.2451** | 0.244 → 0.311 | +0.061 → +0.149 | 0.919 → 0.868 | 5.136 → 3.708 (fit train: 3.780) |
| B1 | 0.1875 | **0.2165** | 0.238 → 0.272 | −0.024 → +0.047 | 0.923 → 0.899 | 1.195 → 4.764 (fit train: 4.872) |
| B0 (persistence) | 0.1635 | 0.1635 (không dùng điều hòa) | — | — | — | 5.271 → 3.942 |
| B2 (LSTM) | 0.1806 | 0.1806 (cần chạy lại vì `t_peak` sai) | — | — | — | 5.595 → 5.902 |
| B_KTTV | 0.0806 | 0.0806 (không đổi) | — | — | — | 4.511 → 2.590 |

`mae_dt` cũ của B1 (1.195 h) thấp giả tạo: so với giờ đỉnh 1 thay vì giờ đỉnh cao hơn.
B2 "chấm lại" gồm 6 169 cặp có `t_pred` = NaN (chuỗi giờ sai) — số đúng chỉ có sau khi chạy lại.

## Chạy lại (sau khi chạy lại `pipelines/r/harmonic.R`)

```bash
docker run --rm -v "$PWD:/work" -w /work --network spark-mongodb-gis_gis-net phasegnn:local \
    python -X utf8 scripts/run_baselines.py --seeds 0 1 2 3 4 --out-dir results/baselines
```

(B_KTTV đọc `mongodb://localhost:27017` trong script; trong container cần sửa URI hoặc dùng `--no-kttv`.)
