# results/E2 – PhaseGNN và biến thể (B3, B4, B5a, B5b)

> **TRẠNG THÁI (24/11/2026): cần chạy lại.**
>
> - `baselines/` (B_harm, B1): dùng triều thiên văn của bản cũ `harmonic.R` — **fit trên cả test – cần chạy lại**.
> - PhaseGNN/B3/B4/B5: G5 lần chạy 2026-11-23 có `astro_*` = null (`astro_merged = false`), nên mô hình
>   **không** dùng triều thiên văn bị rò rỉ. Nhưng tập mẫu đã đổi: `phasegnn.data.build_dataset` trước đây dựng
>   cửa sổ theo vị trí phần tử (bỏ qua ngày thiếu) → 848 / 19 509 mẫu train (4,3 %) và 324 / 4 743 mẫu val
>   (6,8 %) lệch ngày lịch; tập test không có ngày thiếu (0 mẫu lệch). Nay dùng lưới ngày lịch liên tục
>   (train 19 557, val 4 761, test 7 605 mẫu).
> - `scripts/run_experiments.py` (B2 trong E2) sinh `t_peak` sai định dạng khi giờ VN < 7 — đã sửa.
> - Chấm lại `phasegnn_d12/predictions_test.csv` bằng `phasegnn.evaluate` đã sửa: MAE 0.6956 m (không đổi),
>   MAE Δt 5.295 → 5.212 h; `B3_d12`: MAE 0.6632 m, MAE Δt 5.123 → 5.200 h. (Các checkpoint `*_d12` là bản trước
>   khi sửa `HeightScale`.)

## Chạy lại (thứ tự)

1. `pipelines/r/harmonic.R` → (Spark G5 nếu muốn ghép `astro_*`; G5 sẽ dừng mã 1 nếu triều chưa fit lại).
2. Huấn luyện lại (CPU, lâu):

```bash
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python -m phasegnn.train --variant phasegnn --epochs 20 --delta-h 12 --out-dir results/E2/phasegnn
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python packages/phasegnn/scripts/evaluate_checkpoint.py --checkpoint results/E2/phasegnn/checkpoint_seed0.pt \
        --split test --out-dir results/E2/phasegnn
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python packages/phasegnn/scripts/evaluate_baselines.py --split test --out-dir results/E2/baselines
```

(Lặp lại cho `--variant B3|B4|B5a|B5b`.)
