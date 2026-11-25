# results/evaluation – Đánh giá chung (`pipelines/r/evaluation.R`)

> **Đổi tên 25/11/2026:** thư mục này trước là `results/E5/` (`git mv`, giữ lịch sử). `results/E5/` nay dành
> cho thực nghiệm E5 "Tổng quát hóa" của đề cương (`results/E5/README.md`).

> **TRẠNG THÁI (25/11/2026): chạy lại trên tập test** (2024-07-01 → 2026-11-23), gộp thêm 5 mô hình ablation
> 3 seed (`*-abl`, dự báo trung bình qua seed; `results/E2/README.md`), 04:00 – 04:43 (43 phút; log
> `_eval_log.txt`). 18 mô hình ⇒ Holm trên **153** cặp (lần 24/11: 13 mô hình, 78 cặp). Không còn rò rỉ: B_harm dùng triều thiên văn fit chỉ trên train; B0 climatology chỉ lấy
> trung bình trên train; kiểm định ghép cặp theo `(station, target_date, peak_idx, issued_date, lead_day)`,
> Wilcoxon trên `|e|`, DM trên `|e_a| − |e_b|`, hiệu chỉnh Holm trên 153 cặp.

## Lệnh

```powershell
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/evaluation.R --split test --pred `
  results/baselines/B0/predictions_test.csv results/baselines/B1/predictions_test.csv `
  results/baselines/B2/predictions_test.csv `
  results/E2/phasegnn_d12/test_full/predictions_test.csv results/E2/B3_d12/test_full/predictions_test.csv `
  results/E2/B4_d12/test_full/predictions_test.csv results/E2/B5a_d12/test_full/predictions_test.csv `
  results/E2/B5b_d12/test_full/predictions_test.csv results/E2/phasegnn_long/test_full/predictions_test.csv `
  results/E2/B3_long/test_full/predictions_test.csv `
  results/E2/phasegnn_abl/test_full_seedmean/predictions_test.csv results/E2/B4_abl/test_full_seedmean/predictions_test.csv `
  results/E2/B5a_abl/test_full_seedmean/predictions_test.csv results/E2/B5b_abl/test_full_seedmean/predictions_test.csv `
  results/E2/B3_abl/test_full_seedmean/predictions_test.csv
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/tests/test_evaluation.R   # 114 PASS / 0 FAIL
```

`B0`, `B_harm`, `B_KTTV` do `evaluation.R` tự dựng (B0 = **climatology**). B0 persistence của
`scripts/run_baselines.py` trùng tên nên được đổi thành **`B0_py`** (sửa 24/11/2026 trong `evaluation.R`: trước đây
hai mô hình khác nhau bị gộp chung một nhóm "B0"). B_harm/B_KTTV của Python không đưa vào (trùng với bản R).

## Chỉ số (`evaluation_overall.csv`)

Ghép mỗi đỉnh dự báo với đỉnh thực đo trong ±6 h (khác cách ghép theo ngày của `phasegnn.evaluate`, nên n và
MAE khác `results/baselines/summary.csv`). Độ phủ phân vị 10–90 % không có trong `evaluation.R` — xem
`results/baselines/summary.csv` và `results/E2/summary_test_full.csv`.

| Mô hình | n | MAE (m) | RMSE | bias | MAE giờ (h) | NSE | KGE |
|---|---:|---|---|---|---|---|---|
| **B_KTTV** | 4 150 | **0.1352** | 0.201 | −0.002 | 5.05 | 0.604 | 0.787 |
| B0_py (persistence) | 58 183 | 0.1702 | 0.240 | −0.024 | 2.18 | 0.917 | 0.948 |
| **phasegnn-long** | 39 695 | **0.1715** | 0.233 | −0.012 | 2.97 | 0.922 | 0.933 |
| phasegnn-abl (TB 3 seed) | 40 512 | 0.1794 | 0.236 | −0.089 | 2.89 | 0.918 | 0.928 |
| B4-abl (τ cố định, TB 3 seed) | 40 833 | 0.1872 | 0.244 | −0.106 | 2.76 | 0.913 | 0.918 |
| B3-abl (GraphSAGE, TB 3 seed) | 46 353 | 0.1904 | 0.251 | −0.062 | 2.59 | 0.886 | 0.930 |
| B5a-abl (chỉ up, TB 3 seed) | 53 925 | 0.1921 | 0.248 | −0.080 | 2.68 | 0.909 | 0.930 |
| B0 (climatology, train) | 14 364 | 0.1960 | 0.253 | −0.071 | — | 0.914 | 0.932 |
| B3-long | 48 650 | 0.1973 | 0.265 | +0.044 | 2.74 | 0.875 | 0.930 |
| B5b-abl (chỉ down, TB 3 seed) | 50 498 | 0.2054 | 0.264 | −0.053 | 2.64 | 0.912 | 0.942 |
| B5b-v1 (d12) | 41 058 | 0.2142 | 0.278 | −0.064 | 3.17 | 0.892 | 0.920 |
| B1 | 34 389 | 0.2152 | 0.279 | +0.113 | 0.79 | 0.892 | 0.901 |
| B3-v1 (d12) | 39 692 | 0.2165 | 0.280 | −0.075 | 3.21 | 0.890 | 0.915 |
| B2 | 60 368 | 0.2192 | 0.265 | −0.187 | 3.47 | 0.351 | 0.726 |
| phasegnn-v1 (d12) | 42 231 | 0.2205 | 0.284 | −0.082 | 3.12 | 0.884 | 0.913 |
| B4-v1 (d12) | 42 231 | 0.2205 | 0.284 | −0.082 | 3.12 | 0.884 | 0.913 |
| B5a-v1 (d12) | 44 176 | 0.2244 | 0.288 | −0.089 | 3.14 | 0.879 | 0.909 |
| B_harm | 3 905 | 0.2842 | 0.352 | +0.153 | 4.79 | 0.840 | 0.860 |

**So với số cũ** (bản cũ đánh giá toàn giai đoạn 2017–2026, B_harm/B0 rò rỉ): B_harm 0.2285 → **0.2842 m**;
B0 climatology 0.1776 → **0.1960 m**; B_KTTV 0.1271 → **0.1352 m** (chỉ còn tập test). Bản cũ chưa có
PhaseGNN/B1/B2.

MAE theo chân trời (`evaluation_by_lead.csv`): B_KTTV 0.108 → 0.132 m (lead 1 → 5); B0_py 0.089 → 0.237 m;
phasegnn-long 0.167 → 0.178 m, phasegnn-abl 0.174 → 0.186 m (gần như phẳng). Ở lead 1, MAE của persistence thấp hơn B_KTTV (mỗi mô hình trên tập cặp riêng của nó,
không phải cặp ghép chung).

## Kiểm định (`evaluation_wilcoxon.csv`, `evaluation_diebold_mariano.csv`, Holm)

`mean_loss` = trung bình `|e_a| − |e_b|` (âm ⇒ A chính xác hơn).

| Cặp (A vs B) | n cặp | trung vị \|e\| A / B (m) | Wilcoxon p_Holm | DM t | DM p_Holm | Kết luận |
|---|---:|---|---|---|---|---|
| B_KTTV vs phasegnn-long | 1 729 | 0.080 / 0.134 | 8.5e-40 | −6.64 | 1.1e-09 | **B_KTTV tốt hơn** |
| B_KTTV vs phasegnn-abl | 1 716 | 0.080 / 0.153 | 4.0e-51 | −6.52 | 2.4e-09 | B_KTTV tốt hơn |
| B_KTTV vs phasegnn-v1 | 2 580 | 0.100 / 0.178 | 8.7e-94 | −9.13 | 3.9e-18 | B_KTTV tốt hơn |
| B_KTTV vs B3-long | 2 596 | 0.090 / 0.192 | 4.0e-151 | −12.54 | 4.4e-34 | B_KTTV tốt hơn |
| B_KTTV vs B0_py / B1 / B2 | 2 323 / 1 848 / 1 746 | 0.07 / 0.156 – 0.175 | < 1e-67 | −12.1 / −7.3 / −8.0 | < 1e-11 | B_KTTV tốt hơn |
| phasegnn-long vs B3-long | 25 848 | 0.125 / 0.142 | ≈ 0 | −12.72 | 4.2e-35 | phasegnn-long tốt hơn |
| phasegnn-long vs B0 climatology | 36 537 | 0.130 / 0.150 | ≈ 0 | −6.73 | 6.3e-10 | phasegnn-long tốt hơn |
| phasegnn-long vs B0_py persistence | 30 593 | 0.126 / 0.113 | 6.3e-07 | −0.07 | **1.0** | **không khác biệt** về \|e\| TB; trung vị persistence thấp hơn |
| phasegnn-long vs B1 / B_harm | 24 725 / 8 984 | 0.128 / 0.166; 0.145 / 0.216 | ≈ 0 | −14.8 / −16.3 | < 1e-46 | phasegnn-long tốt hơn |
| phasegnn-v1 vs B4-v1 | 42 231 | 0.1814 / 0.1814 | ≈ 0 | −16.2 | 7.9e-57 | khác nhau ~3e-7 m: **có ý nghĩa thống kê nhưng vô nghĩa thực tế** (hai mô hình gần như trùng) |
| **phasegnn-abl vs B4-abl** (τ học vs τ cố định) | 39 069 | 0.150 / 0.159 | ≈ 0 | −27.4 | 2.7e-163 | phasegnn tốt hơn, chênh MAE ~0.008 m |
| phasegnn-abl vs B5a-abl (chỉ up) | 36 692 | 0.149 / 0.159 | ≈ 0 | −16.9 | 5.8e-62 | phasegnn tốt hơn (0.013 m) |
| phasegnn-abl vs B5b-abl (chỉ down) | 31 094 | 0.148 / 0.164 | ≈ 0 | −16.7 | 2.0e-60 | phasegnn tốt hơn (0.021 m) |
| phasegnn-abl vs B3-abl (GraphSAGE) | 29 723 | 0.146 / 0.153 | ≈ 0 | −11.2 | 4.3e-27 | phasegnn tốt hơn (0.011 m) |
| phasegnn-abl vs B0_py persistence | 31 089 | 0.145 / 0.115 | 1.7e-42 | +2.58 | **0.11** | không khác biệt về \|e\| TB (persistence nhỉnh hơn 0.007 m); trung vị persistence thấp hơn |
| phasegnn-abl vs B0 climatology | 37 122 | 0.150 / 0.148 | 0.39 | +0.04 | 1.0 | không khác biệt |
| phasegnn-abl vs phasegnn-long | 34 582 | 0.151 / 0.129 | ≈ 0 | +9.9 | 2.0e-21 | seed 0 đơn lẻ tốt hơn trung bình 3 seed |

**Kết luận mới:**

1. **Không mô hình nào thắng B_KTTV.** B_KTTV chính xác hơn mọi mô hình khác trên cặp ghép chung (mọi p_Holm < 1e-9).
2. **PhaseGNN (long)** là mô hình học máy tốt nhất: tốt hơn GraphSAGE (B3-long), climatology, B1, B_harm,
   B2 và các biến thể d12 — nhưng **không tốt hơn persistence (B0_py)** theo DM (p_Holm = 1.0 với 153 cặp; p = 0.94 trước hiệu chỉnh).
3. Các biến thể d12 (3 epoch, 300 mẫu) gần như chưa học; so sánh biến thể nay dùng **ablation 3 seed `*-abl`**
   (cấu hình `long`): trên cặp ghép ±6 h, phasegnn-abl tốt hơn B4/B5a/B5b/B3-abl (mọi p_Holm < 1e-26) — cùng thứ
   tự với kiểm định theo ngày của `results/E2/ablation_tests.csv`: bỏ cạnh `up` (B5b) hại nhất, τ học được (so với
   B4) chỉ giúp ~0.004–0.008 m. Nhưng trung bình 3 seed của phasegnn **không** tốt hơn persistence (DM p_Holm =
   0.11) và cũng không tốt hơn climatology. Lưu ý mọi mô hình `long`/`abl` chỉ học nhãn của BEN_LUC
   (`results/E5/README.md`).
4. Kết luận cũ "cả 3 cặp khác biệt có ý nghĩa" (B_harm/B_KTTV/B0) được thay bằng bảng trên; hướng của các
   khác biệt không đổi (B_KTTV < B0 < B_harm về MAE).
