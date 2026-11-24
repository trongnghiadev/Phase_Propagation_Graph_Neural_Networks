# results/E5 – Đánh giá chung (`pipelines/r/evaluation.R`)

> **TRẠNG THÁI (24/11/2026): fit trên cả test – cần chạy lại.** Các file `evaluation_*.csv/png` hiện có:
>
> - **B_harm**: triều thiên văn của bản cũ `harmonic.R` (fit 2017–2026, gồm tập test).
> - **B0** (climatology): trung bình theo ngày lịch trên **mọi** năm, kể cả năm test.
> - Đánh giá trên **toàn giai đoạn** 2017–2026 (B_harm/B0 phần lớn là trong mẫu).
> - **Kiểm định** (`evaluation_tests.csv`, `evaluation_wilcoxon.csv`, `evaluation_diebold_mariano.csv`) sai:
>   ghép cặp theo `(station, target_date, peak_idx)` không có `issued_date`/`lead_day` (B_KTTV có 5 chân trời
>   cho mỗi ngày đích → lấy bản ghi đầu tiên bất kỳ); Wilcoxon trên sai số có dấu; DM trên `|e_a − e_b|`
>   (luôn "có ý nghĩa"). Kết luận "cả 3 cặp khác biệt có ý nghĩa" **không dùng được**.
>
> B_KTTV (MAE 0.1271 m trên toàn giai đoạn) không phụ thuộc điều hòa, nhưng cũng nên chạy lại vì
> mặc định mới chỉ đánh giá tập test.

## Đã sửa trong code

`R/evaluation.R`: khóa cặp `(station, target_date, peak_idx, issued_date, lead_day)`; `horizon_free`
cho B0/B_harm; Wilcoxon trên `|e|`; DM trên `|e_a| − |e_b|`, sắp theo thời gian. `evaluation.R`:
`--split test` mặc định (biên từ `config/splits.json`), B0 chỉ dùng tập train.

## Chạy lại (thứ tự)

```powershell
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/harmonic.R
# (tuỳ chọn) chạy lại Spark G5 để ghép astro_* — xem pipelines/spark/README.md
docker run --rm -v "${PWD}:/work" -w /work phasegnn:local python -X utf8 scripts/run_baselines.py --out-dir results/baselines
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/evaluation.R --split test --pred results/baselines/predictions_all_test.csv
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/tests/test_evaluation.R
```

Ước lượng trước khi chạy lại: xem `results/baselines/README.md` (B_harm test MAE 0.198 → ~0.245 m theo
`phasegnn.evaluate`).
