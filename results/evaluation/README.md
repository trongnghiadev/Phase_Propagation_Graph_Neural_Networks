# results/evaluation – Đánh giá chung (`pipelines/r/evaluation.R`)

> **Đổi tên 25/11/2026:** thư mục này trước là `results/E5/` (`git mv`, giữ lịch sử). `results/E5/` nay dành
> cho thực nghiệm E5 "Tổng quát hóa" của đề cương (`results/E5/README.md`).

> **TRẠNG THÁI (25/11/2026, lượt 2): chạy lại trên tập test** (2024-07-01 → 2026-11-23), gộp thêm 5 mô hình ablation
> mẫu `spread` 3 seed (`*-spread`, dự báo trung bình qua seed; `results/E2/README.md`), 09:44 – 11:30 (1 h 46; log
> `_eval_log.txt`). 23 mô hình ⇒ Holm trên **253** cặp (lượt 1 sáng 25/11: 18 mô hình, 153 cặp; 24/11: 13 mô hình,
> 78 cặp). Số của các mô hình đã có lệch rất ít so với lượt 1 (vd. B_KTTV n 4 150 → 4 155, MAE 0.1352 → 0.1347;
> phasegnn-long 0.1715 → 0.1717) — chưa tìm nguyên nhân; không đổi thứ hạng hay kết luận. Không còn rò rỉ: B_harm dùng
> triều thiên văn fit chỉ trên train; B0 climatology chỉ lấy trung bình trên train; kiểm định ghép cặp theo `(station,
> target_date, peak_idx, issued_date, lead_day)`, Wilcoxon trên `|e|`, DM trên `|e_a| − |e_b|`, hiệu chỉnh Holm.

> **LƯỢT 4 (25/11/2026, 19:25 – 20:11, 46 phút) — CSV/hình hiện tại là lượt này.** Chỉ các mô hình chính: B_KTTV,
> B0 (climatology), B_harm (tự dựng), B0_py, B1, B2, phasegnn-spread-v2 và 5 mô hình v3 (`*-v3`, đồ thị con v3 + τ
> `phase`, TB 3 seed; `results/E2/README.md`) ⇒ 12 mô hình, Holm trên **66** cặp. Mọi mô hình học máy huấn luyện trên CPU.

| Mô hình | n | MAE (m) | RMSE | bias | MAE giờ (h) | NSE | KGE |
|---|---:|---|---|---|---|---|---|
| **B_KTTV** | 4 155 | **0.1347** | 0.200 | +0.000 | 5.05 | 0.609 | 0.789 |
| **phasegnn-spread-v2** | 49 825 | **0.1602** | 0.216 | −0.015 | 2.32 | 0.938 | 0.966 |
| B0_py (persistence) | 58 210 | 0.1704 | 0.241 | −0.024 | 2.18 | 0.917 | 0.948 |
| B5a-v3 | 47 733 | 0.1723 | 0.233 | +0.005 | 2.40 | 0.926 | 0.933 |
| B4-v3 | 44 193 | 0.1731 | 0.235 | −0.010 | 2.33 | 0.916 | 0.932 |
| phasegnn-v3 | 45 760 | 0.1770 | 0.239 | −0.004 | 2.37 | 0.914 | 0.927 |
| B3-v3 | 52 343 | 0.1820 | 0.243 | −0.005 | 2.50 | 0.920 | 0.958 |
| B5b-v3 | 44 234 | 0.1886 | 0.252 | +0.009 | 2.51 | 0.899 | 0.941 |
| B0 (climatology) | 14 386 | 0.1962 | 0.253 | −0.071 | — | 0.913 | 0.932 |
| B1 | 34 394 | 0.2153 | 0.279 | +0.113 | 0.79 | 0.892 | 0.901 |
| B2 | 60 293 | 0.2188 | 0.264 | −0.186 | 3.47 | 0.355 | 0.726 |
| B_harm | 3 900 | 0.2848 | 0.352 | +0.154 | 4.79 | 0.840 | 0.859 |

Kiểm định phasegnn-v3 (`mean_loss` = TB `|e_A| − |e_B|`, A vs phasegnn-v3; > 0 ⇒ v3 tốt hơn): phasegnn-spread-v2
−0.017 (DM p_Holm = 4e-202, **v2 tốt hơn**); B5a-v3 −0.003 (5e-47) và B4-v3 −0.002 (7e-43) **tốt hơn** phasegnn-v3;
B5b-v3 +0.010 (3e-36), B3-v3 +0.005 (9e-11); B0_py persistence +0.009 (p_Holm = 0.005; trung vị |e| persistence 0.115
vs 0.131 — v3 chỉ hơn về |e| TB); B_KTTV −0.078 (1e-24). **Kết luận:** v3 kém v2 rõ; trong v3, thêm thông điệp hồ chứa
(phasegnn so với B5a) và τ học (so với B4) đều không giúp.

*Dẫn chiếu chéo (v3 vs persistence):* `results/E2/README.md` (mục v3, kết luận 2) báo phasegnn-v3 **không còn** hơn
persistence (p_Holm = 0.35, `daypeak_tests_v3.csv`). Khác vì cách ghép: ở đây ghép theo **từng đỉnh** `(station,
target_date, peak_idx, issued_date, lead_day)`, Holm trên 66 cặp; E2 ghép theo **đỉnh ngày** quan trắc `(station,
issued_date, target_date, lead_day)`, DM trên chuỗi chênh lệch TB theo ngày phát hành, Holm trên 6. Cùng dấu (v3 nhỉnh
hơn persistence về |e| TB), khác mức ý nghĩa ⇒ lợi thế này mong manh, phụ thuộc cách ghép.

> **LƯỢT 3 (25/11/2026, 13:21 – 14:09, 48 phút) (CSV lượt 3 trong lịch sử git).** Gộp các mô hình đồ thị con v2
> (`*-spread-v2`, `results/E2/README.md`). Để giữ thời gian < 1 h, **bỏ khỏi lượt này**: 5 mô hình `d12`
> (`phasegnn-v1`, `B3-v1`, `B4-v1`, `B5a-v1`, `B5b-v1`), `B3-long`, `B4-abl`, `B5a-abl`, `B5b-abl`, `B3-abl`, và
> `B4-spread-v2`, `B5b-spread-v2` (dự báo **trùng từng bit** với `B4-spread`, `B5b-spread`). Còn 16 mô hình ⇒ Holm
> trên **120** cặp. Số của các mô hình bỏ ra: bảng lượt 2 dưới đây (CSV lượt 2 trong lịch sử git). Lệnh: như dưới,
> danh sách `--pred` = B0/B1/B2 (`results/baselines`), `phasegnn_long/test_full`, `phasegnn_abl/test_full_seedmean`,
> `{phasegnn,B4,B5a,B5b,B3}_spread/test_full_seedmean`, `{phasegnn,B5a,B3}_spread_v2/test_full_seedmean`.

**Lượt 3 — chỉ số** (`evaluation_overall.csv`, ghép ±6 h):

| Mô hình | n | MAE (m) | RMSE | bias | MAE giờ (h) | NSE | KGE |
|---|---:|---|---|---|---|---|---|
| **B_KTTV** | 4 155 | **0.1347** | 0.200 | +0.000 | 5.05 | 0.609 | 0.789 |
| B4-spread (= B4-spread-v2) | 44 838 | 0.1592 | 0.217 | −0.019 | 2.26 | 0.929 | 0.960 |
| B5a-spread-v2 | 48 227 | 0.1602 | 0.216 | −0.009 | 2.27 | 0.933 | 0.966 |
| **phasegnn-spread-v2** | 49 825 | **0.1602** | 0.216 | −0.015 | 2.32 | 0.938 | 0.966 |
| phasegnn-spread (v1) | 49 340 | 0.1604 | 0.216 | −0.010 | 2.34 | 0.937 | 0.967 |
| B5a-spread (v1) | 48 647 | 0.1606 | 0.217 | −0.015 | 2.29 | 0.934 | 0.965 |
| B0_py (persistence) | 58 210 | 0.1704 | 0.241 | −0.024 | 2.18 | 0.917 | 0.948 |
| phasegnn-long | 39 671 | 0.1717 | 0.233 | −0.013 | 2.97 | 0.922 | 0.933 |
| B3-spread-v2 | 55 697 | 0.1779 | 0.234 | −0.011 | 2.34 | 0.928 | 0.953 |
| B3-spread (v1) | 55 676 | 0.1780 | 0.235 | −0.009 | 2.35 | 0.928 | 0.952 |
| phasegnn-abl | 40 501 | 0.1799 | 0.237 | −0.089 | 2.89 | 0.917 | 0.927 |
| B5b-spread (= B5b-spread-v2) | 48 158 | 0.1863 | 0.247 | −0.004 | 2.43 | 0.912 | 0.944 |
| B0 (climatology) | 14 386 | 0.1962 | 0.253 | −0.071 | — | 0.913 | 0.932 |
| B1 | 34 394 | 0.2153 | 0.279 | +0.113 | 0.79 | 0.892 | 0.901 |
| B2 | 60 293 | 0.2188 | 0.264 | −0.186 | 3.47 | 0.355 | 0.726 |
| B_harm | 3 900 | 0.2848 | 0.352 | +0.154 | 4.79 | 0.840 | 0.859 |

(Các mô hình có mặt ở lượt 2 cho số giống hệt lượt 2.)

**Lượt 3 — kiểm định phasegnn-spread-v2** (`mean_loss` = TB `|e_A| − |e_B|`; > 0 ⇒ phasegnn-spread-v2 tốt hơn):

| A vs phasegnn-spread-v2 | n cặp | trung vị \|e\| A / v2 | `mean_loss` | DM t | DM p_Holm |
|---|---:|---|---|---|---|
| B_KTTV | 2 550 | 0.090 / 0.144 | −0.058 | −9.23 | 1e-18 (**B_KTTV tốt hơn**) |
| B0_py persistence | 37 397 | 0.117 / 0.122 | +0.024 | +9.60 | 3e-20 (v2 tốt hơn về \|e\| TB) |
| phasegnn-spread (v1) | 48 719 | 0.128 / 0.127 | +0.0005 | +3.19 | 0.028 (v2 tốt hơn 0.5 mm — vô nghĩa thực tế) |
| B4-spread (τ cố định) | 43 000 | 0.126 / 0.125 | +0.0004 | +2.86 | 0.071 |
| B5a-spread-v2 (chỉ up) | 46 924 | 0.128 / 0.127 | +0.0003 | +1.23 | 1.0 |
| B5b-spread (không thông điệp đồ thị) | 45 098 | 0.148 / 0.127 | +0.026 | +30.4 | 3e-200 |
| B3-spread-v2 (GraphSAGE) | 47 964 | 0.140 / 0.127 | +0.017 | +24.2 | 4e-127 |
| phasegnn-abl / phasegnn-long (`head`) | 28 909 / 27 619 | 0.148 / 0.123; 0.129 / 0.124 | +0.023 / +0.011 | +20.5 / +13.1 | < 1e-36 |

**Kết luận lượt 3:** v2 ≈ v1 (khác 0.5 mm); thứ tự và kết luận của lượt 2 giữ nguyên: PhaseGNN-spread tốt hơn
persistence và mọi mô hình học máy khác trừ biến thể τ cố định / chỉ `up` (tương đương), kém B_KTTV; τ học được và
cạnh `down` không đóng góp (trạm không nhận cạnh `down` nào trong đồ thị con — `results/E2/README.md`).

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
  results/E2/B3_abl/test_full_seedmean/predictions_test.csv `
  results/E2/phasegnn_spread/test_full_seedmean/predictions_test.csv results/E2/B4_spread/test_full_seedmean/predictions_test.csv `
  results/E2/B5a_spread/test_full_seedmean/predictions_test.csv results/E2/B5b_spread/test_full_seedmean/predictions_test.csv `
  results/E2/B3_spread/test_full_seedmean/predictions_test.csv
& "C:\Program Files\R\R-4.6.2\bin\Rscript.exe" pipelines/r/tests/test_evaluation.R   # 114 PASS / 0 FAIL
```

`B0`, `B_harm`, `B_KTTV` do `evaluation.R` tự dựng (B0 = **climatology**). B0 persistence của
`scripts/run_baselines.py` trùng tên nên được đổi thành **`B0_py`** (sửa 24/11/2026 trong `evaluation.R`: trước đây
hai mô hình khác nhau bị gộp chung một nhóm "B0"). B_harm/B_KTTV của Python không đưa vào (trùng với bản R).

## Chỉ số lượt 2 (23 mô hình; CSV hiện tại là lượt 4 ở trên)

Ghép mỗi đỉnh dự báo với đỉnh thực đo trong ±6 h (khác cách ghép theo ngày của `phasegnn.evaluate`, nên n và
MAE khác `results/baselines/summary.csv`). Độ phủ phân vị 10–90 % không có trong `evaluation.R` — xem
`results/baselines/summary.csv` và `results/E2/summary_test_full.csv`.

| Mô hình | n | MAE (m) | RMSE | bias | MAE giờ (h) | NSE | KGE |
|---|---:|---|---|---|---|---|---|
| **B_KTTV** | 4 155 | **0.1347** | 0.200 | +0.000 | 5.05 | 0.609 | 0.789 |
| B4-spread (τ cố định, TB 3 seed) | 44 838 | 0.1592 | 0.217 | −0.019 | 2.26 | 0.929 | 0.960 |
| **phasegnn-spread (TB 3 seed)** | 49 340 | **0.1604** | 0.216 | −0.010 | 2.34 | 0.937 | 0.967 |
| B5a-spread (chỉ up, TB 3 seed) | 48 647 | 0.1606 | 0.217 | −0.015 | 2.29 | 0.934 | 0.965 |
| B0_py (persistence) | 58 210 | 0.1704 | 0.241 | −0.024 | 2.18 | 0.917 | 0.948 |
| phasegnn-long | 39 671 | 0.1717 | 0.233 | −0.013 | 2.97 | 0.922 | 0.933 |
| B3-spread (GraphSAGE, TB 3 seed) | 55 676 | 0.1780 | 0.235 | −0.009 | 2.35 | 0.928 | 0.952 |
| phasegnn-abl (TB 3 seed) | 40 501 | 0.1799 | 0.237 | −0.089 | 2.89 | 0.917 | 0.927 |
| B5b-spread (chỉ down, TB 3 seed) | 48 158 | 0.1863 | 0.247 | −0.004 | 2.43 | 0.912 | 0.944 |
| B4-abl (τ cố định, TB 3 seed) | 40 819 | 0.1877 | 0.245 | −0.106 | 2.76 | 0.912 | 0.918 |
| B3-abl (GraphSAGE, TB 3 seed) | 46 412 | 0.1909 | 0.252 | −0.062 | 2.59 | 0.884 | 0.930 |
| B5a-abl (chỉ up, TB 3 seed) | 53 975 | 0.1923 | 0.248 | −0.080 | 2.68 | 0.908 | 0.930 |
| B0 (climatology, train) | 14 386 | 0.1962 | 0.253 | −0.071 | — | 0.913 | 0.932 |
| B3-long | 48 690 | 0.1979 | 0.266 | +0.044 | 2.74 | 0.874 | 0.930 |
| B5b-abl (chỉ down, TB 3 seed) | 50 550 | 0.2059 | 0.265 | −0.053 | 2.64 | 0.911 | 0.942 |
| B5b-v1 (d12) | 41 093 | 0.2148 | 0.279 | −0.064 | 3.17 | 0.891 | 0.919 |
| B1 | 34 394 | 0.2153 | 0.279 | +0.113 | 0.79 | 0.892 | 0.901 |
| B3-v1 (d12) | 39 722 | 0.2172 | 0.282 | −0.075 | 3.21 | 0.889 | 0.915 |
| B2 | 60 293 | 0.2188 | 0.264 | −0.186 | 3.47 | 0.355 | 0.726 |
| phasegnn-v1 (d12) | 42 271 | 0.2212 | 0.286 | −0.082 | 3.12 | 0.883 | 0.912 |
| B4-v1 (d12) | 42 271 | 0.2212 | 0.286 | −0.082 | 3.12 | 0.883 | 0.912 |
| B5a-v1 (d12) | 44 216 | 0.2249 | 0.289 | −0.089 | 3.14 | 0.878 | 0.909 |
| B_harm | 3 900 | 0.2848 | 0.352 | +0.154 | 4.79 | 0.840 | 0.859 |

**So với số cũ** (bản cũ đánh giá toàn giai đoạn 2017–2026, B_harm/B0 rò rỉ): B_harm 0.2285 → **0.2842 m**;
B0 climatology 0.1776 → **0.1960 m**; B_KTTV 0.1271 → **0.1352 m** (chỉ còn tập test). Bản cũ chưa có
PhaseGNN/B1/B2.

MAE theo chân trời (`evaluation_by_lead.csv`): B_KTTV 0.108 → 0.132 m (lead 1 → 5); B0_py 0.089 → 0.237 m;
phasegnn-long 0.167 → 0.178 m, phasegnn-abl 0.174 → 0.186 m (gần như phẳng). Ở lead 1, MAE của persistence thấp hơn B_KTTV (mỗi mô hình trên tập cặp riêng của nó,
không phải cặp ghép chung).

## Kiểm định (`evaluation_wilcoxon.csv`, `evaluation_diebold_mariano.csv`, Holm)

> Bảng đầu tiên dưới đây là **lượt 1** (18 mô hình, Holm trên 153 cặp); CSV hiện tại là lượt 2 (253 cặp) nên p_Holm
> trong CSV lớn hơn một chút, chiều và kết luận các cặp cũ không đổi. Kết luận 2 ("phasegnn-long là mô hình học máy tốt
> nhất") **đã bị thay** bởi kết luận 5 (`*-spread`).

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

**Mô hình `spread` (lượt 2, Holm trên 253 cặp):**

| Cặp (A vs B) | n cặp | trung vị \|e\| A / B (m) | Wilcoxon p_Holm | `mean_loss` | DM t | DM p_Holm | Kết luận |
|---|---:|---|---|---|---|---|---|
| B_KTTV vs phasegnn-spread | 2 524 | 0.090 / 0.143 | 4e-66 | −0.056 | −8.72 | 1.9e-16 | **B_KTTV tốt hơn** |
| B0_py persistence vs phasegnn-spread | 36 897 | 0.117 / 0.122 | ≈ 0 | +0.025 | +9.77 | **1.2e-20** | **phasegnn-spread tốt hơn** về \|e\| TB; trung vị persistence vẫn thấp hơn |
| B0 climatology vs phasegnn-spread | 45 445 | 0.148 / 0.126 | ≈ 0 | +0.023 | +16.67 | 4e-60 | phasegnn-spread tốt hơn |
| B4-spread vs phasegnn-spread (τ cố định) | 43 390 | 0.125 / 0.126 | 0.42 | −0.000 | −0.05 | **1.0** | **không khác biệt** |
| B5a-spread vs phasegnn-spread (chỉ up) | 47 555 | 0.128 / 0.127 | 6e-04 | +0.000 | +1.26 | **1.0** | không khác biệt |
| B5b-spread vs phasegnn-spread (chỉ down) | 44 945 | 0.149 / 0.127 | ≈ 0 | +0.026 | +31.8 | 1e-219 | phasegnn-spread tốt hơn |
| B3-spread vs phasegnn-spread (GraphSAGE) | 47 613 | 0.140 / 0.127 | ≈ 0 | +0.017 | +24.0 | 3e-125 | phasegnn-spread tốt hơn |
| phasegnn-abl vs phasegnn-spread (`head` vs `spread`) | 28 881 | 0.149 / 0.123 | ≈ 0 | +0.023 | +20.7 | 6e-93 | spread tốt hơn |
| phasegnn-long vs phasegnn-spread | 27 656 | 0.129 / 0.124 | ≈ 0 | +0.011 | +14.0 | 2e-42 | spread tốt hơn |
| B1 / B_harm vs phasegnn-spread | 26 548 / 13 775 | 0.166 / 0.120; 0.251 / 0.139 | ≈ 0 | +0.055 / +0.109 | +21.8 / +25.6 | < 1e-100 | phasegnn-spread tốt hơn |
| B0_py vs B4-spread / B5a-spread | 34 148 / 36 383 | 0.116 / 0.121; 0.117 / 0.122 | ≈ 0 | +0.025 | +9.4 / +9.6 | < 1e-18 | B4/B5a-spread cũng tốt hơn persistence |

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
5. **(lượt 2) Huấn luyện trên mẫu `spread` (9 trạm) thay đổi kết luận về persistence:** phasegnn-spread (TB 3 seed)
   **tốt hơn persistence** trên cặp ghép ±6 h (mean_loss 0.025 m, DM p_Holm = 1e-20; cùng chiều với kiểm định ghép
   đỉnh ngày ở `results/E2/README.md`, p_Holm = 6e-04), dù trung vị |e| của persistence vẫn thấp hơn một chút (0.117 vs
   0.122 m) — persistence có nhiều sai số nhỏ nhưng đuôi sai số dài hơn. MAE giờ đỉnh của phasegnn-spread (2.34 h) vẫn
   kém persistence (2.18 h) ở cách ghép ±6 h. **Vẫn kém B_KTTV** (DM p_Holm = 2e-16).
6. Trong nhóm `spread`: phasegnn ≡ B4 ≡ B5a (DM p_Holm = 1.0) ⇒ τ học được và cạnh `down` **không** đóng góp (đồ thị
   con không có cạnh `down` — lỗi ghi ở `results/E2/README.md`; δ ≈ 0); B5b (không thông điệp đồ thị) và B3 (GraphSAGE)
   kém rõ ⇒ thông điệp lan truyền từ biển có trễ pha là phần có ích.
