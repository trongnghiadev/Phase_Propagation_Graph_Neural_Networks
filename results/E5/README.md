# results/E5 – Tổng quát hóa (đề cương mục 7.2, RQ1)

> **TRẠNG THÁI (25/11/2026): bản nhẹ** — cấu hình gần `long` của E2 với mẫu `spread`. Phần (a) nay có **3 seed**
> (0, 1, 2) cho cả mô hình giữ riêng và đối chứng, sau khi sửa rò thống kê nhãn của trạm giữ riêng (xem dưới).
> Phần (b) chưa có kiểm định ghép cặp.
> Thư mục này trước 25/11/2026 chứa **đánh giá chung** của `pipelines/r/evaluation.R`; phần đó đã chuyển sang
> `results/evaluation/` (`git mv`, giữ lịch sử).

Đề cương: *"E5 – Tổng quát hóa: giữ riêng trạm; giữ riêng năm triều cao nhất; chỉ số trên tập giữ riêng"*.

## Phát hiện quan trọng về cấu hình `long` của E2

`long` = `--max-train-samples 1200 --max-val-samples 300`. `train.py` cũ lấy **N mẫu đầu**, mà mẫu xếp theo
trạm → ngày, nên **toàn bộ 1 200 mẫu train là của BEN_LUC (2017-01-15 → 2020-04-28)** và 300 mẫu val cũng chỉ
của BEN_LUC. Nghĩa là mọi mô hình `*_long` / `*_abl` của E2 chỉ học nhãn của **một** trạm rồi dự báo cho 9 trạm
— tức E2 trên thực tế đã là một thí nghiệm "giữ riêng 8/9 trạm". Để E5 có nghĩa, ta thêm tùy chọn
`--subsample spread` (lấy N mẫu cách đều trên mọi trạm/giai đoạn); mặc định vẫn là `head` để chạy lại được số cũ. Ablation E2 đã chạy lại với `spread` (`results/E2/README.md`, mục `*_spread`): MAE phasegnn 0.180 ± 0.021 → 0.146 ± 0.001 m.

## (a) Giữ riêng trạm

**Thiết kế.** Trạm giữ riêng: **PHU_AN** (trạm chính, sông Sài Gòn, có dự báo KTTV) và **TAN_AN** (sông Vàm Cỏ Tây,
không có trạm cùng sông trong tập học). `--holdout-stations PHU_AN,TAN_AN` bỏ **mọi mẫu** của hai trạm khỏi train
và val; khi dự báo, trạm vẫn dùng quan trắc quá khứ của chính nó làm đầu vào (như mọi trạm khác) — "che nhãn",
không che đầu vào.

**Thống kê chuẩn hoá của trạm giữ riêng (sửa 25/11/2026).** Mô hình dự báo trên thang chuẩn hoá theo trạm
(`h_norm = (h − μ)/σ`, `phasegnn.data.HeightScale`). Bản chạy trước fit μ/σ trên **toàn bộ train, kể cả trạm
giữ riêng** — tức dùng thống kê nhãn train của chính PHU_AN/TAN_AN (rò thống kê nhãn). Nay
(`build_datasets(holdout_stations=...)`): μ/σ của trạm giữ riêng lấy từ **quan trắc đỉnh của trạm trong giai đoạn
val** (2023-01-01 → 2024-06-30, trước mốc test; trạm đã bị loại khỏi val nên các giá trị này không dùng làm nhãn
hay dừng sớm) — tương ứng tình huống vận hành: trạm mới có ~1,5 năm quan trắc nhưng chưa từng được huấn luyện.
Không có quan trắc val → dự phòng bằng trung bình μ/σ của các trạm còn lại. Bộ chuẩn hoá đặc trưng gộp cũng bỏ
qua trạm giữ riêng. Mô hình `phasegnn-all` (không giữ riêng) không đổi. Số dưới đây là bản chạy lại.

| Mô hình | Huấn luyện trên | Thư mục |
|---|---|---|
| `phasegnn-holdout` | 7 trạm (trừ PHU_AN, TAN_AN), 1 200 mẫu `spread` | `phasegnn_spread_holdout/` |
| `phasegnn-all` | 9 trạm, 1 200 mẫu `spread` (đối chứng cùng cấu hình) | `phasegnn_spread_all/` |
| `phasegnn-long` | chỉ BEN_LUC (cấu hình `long` cũ, E2) | `results/E2/phasegnn_long/` |
| B0 (persistence), B_harm, B_KTTV | — | `results/baselines/` |

Chung: seed **0, 1, 2**, `hidden_dim` 64, `delta_h` 12, 12 epoch, patience 4, val 300 mẫu (cùng cách lấy mẫu, cùng trạm
bị loại). Best val MAE (seed 0/1/2): holdout 0.163/0.160/0.160 m; all 0.150/0.152/0.150 m.
`phasegnn_spread_all` seed k trùng tuyệt đối với `results/E2/phasegnn_spread` seed k (cùng cấu hình, tất định).

**Kết quả trên tập test tại PHU_AN + TAN_AN — trung bình ± độ lệch chuẩn qua 3 seed**
(`holdout_station_mean_sd.csv`; từng seed: `holdout_station_by_seed.csv`; `phasegnn.evaluate`, ghép đỉnh ngày, 8 450 cặp):

| Mô hình | MAE (m) | RMSE | bias | NSE | KGE | MAE Δt (h) | Phủ 10–90 % |
|---|---|---|---|---|---|---|---|
| phasegnn-holdout | 0.168 ± 0.005 | 0.209 ± 0.007 | −0.102 ± 0.006 | 0.27 ± 0.05 | 0.41 | 4.04 ± 0.33 | 0.65 |
| phasegnn-all | 0.148 ± 0.001 | 0.187 ± 0.001 | −0.065 ± 0.004 | 0.41 ± 0.01 | 0.50 | 3.57 ± 0.01 | 0.72 |

MAE từng seed (0/1/2): holdout 0.168/0.162/0.173 (PHU_AN 0.138/0.136/0.149; TAN_AN 0.197/0.189/0.197);
all 0.149/0.148/0.146 (PHU_AN 0.124/0.124/0.124; TAN_AN 0.173/0.173/0.169). Theo trạm (TB ± sd): PHU_AN holdout
0.141 ± 0.007 vs all 0.124 ± 0.000; TAN_AN 0.194 ± 0.005 vs 0.172 ± 0.003.

**Dự báo trung bình seed và tham chiếu** (`holdout_station_metrics.csv`):

| Mô hình | n | MAE (m) | RMSE | bias | NSE | KGE | MAE Δt (h) | Phủ 10–90 % |
|---|---:|---|---|---|---|---|---|---|
| phasegnn-holdout (TB 3 seed) | 8 450 | 0.167 | 0.208 | −0.110 | 0.27 | 0.43 | 4.02 | 0.65 |
| phasegnn-all (TB 3 seed) | 8 450 | 0.147 | 0.187 | −0.068 | 0.41 | 0.51 | 3.53 | 0.72 |
| phasegnn-long (`head`, chỉ BEN_LUC, seed 0) | 8 450 | 0.148 | 0.186 | −0.061 | 0.42 | 0.48 | 4.52 | 0.58 |
| phasegnn-abl (`head`, chỉ BEN_LUC, TB 3 seed) | 8 450 | 0.202 | 0.245 | −0.168 | −0.02 | 0.42 | 3.96 | 0.45 |
| B0 persistence | 8 575 | 0.155 | 0.213 | +0.046 | 0.24 | 0.65 | 3.85 | 0.62 |
| B_harm | 8 610 | 0.200 | 0.242 | +0.039 | 0.02 | 0.25 | 3.32 | 0.41 |
| B_KTTV (chỉ PHU_AN) | 4 304 | 0.081 | 0.117 | +0.008 | 0.76 | 0.87 | 2.54 | 0.86 |

**Kiểm định ghép cặp** (`holdout_station_tests.csv`; tham chiếu `phasegnn-holdout` = **dự báo trung bình 3 seed**
(`pairing = seedmean`), ghép theo `(station, target_date, lead_day)`; Wilcoxon trên `|e_a| − |e_b|`; DM trên chuỗi
trung bình theo ngày phát bản tin, Newey–West lag 4; Holm trên 6 so sánh; `mean_diff` > 0 ⇒ holdout kém hơn):

| holdout (TB seed) vs | n cặp | MAE holdout / kia | mean_diff | Wilcoxon p_Holm | DM t | DM p_Holm |
|---|---:|---|---|---|---|---|
| phasegnn-all (TB seed) | 8 415 | 0.167 / 0.147 | +0.020 | 4e-251 | 8.19 | 1.1e-15 |
| phasegnn-long | 8 415 | 0.167 / 0.148 | +0.019 | 1e-199 | 7.26 | 1.1e-12 |
| phasegnn-abl | 8 415 | 0.167 / 0.202 | −0.035 | ≈ 0 | −12.57 | 1.8e-35 |
| B0 persistence | 8 381 | 0.167 / 0.155 | +0.012 | 2e-23 | 1.51 | **0.130** |
| B_harm | 8 415 | 0.167 / 0.199 | −0.032 | 1e-54 | −3.42 | 0.0013 |
| B_KTTV (PHU_AN) | 4 215 | 0.138 / 0.081 | +0.057 | 2e-178 | 10.59 | 1.7e-25 |

Theo từng seed (holdout seed k vs all seed k, `pairing = seed<k>`): mean_diff +0.019 / +0.014 / +0.027 m,
DM p 1.0e-14 / 7.9e-09 / 3.7e-18 — **cùng chiều cả 3 seed**.

Bản 1 seed trước (seed 0): holdout 0.168, all 0.149; mean_diff so với all/long/B0/B_harm/B_KTTV =
+0.019/+0.020/+0.012/−0.031/+0.057, DM p_Holm 4.1e-14/1.2e-11/0.111/0.0012/1.3e-26.

**Đọc kết quả (trung thực):**

1. Che nhãn PHU_AN/TAN_AN làm MAE tại hai trạm đó tăng **0.020 m (≈ +14 %)** so với cùng cấu hình có nhãn
   (0.168 ± 0.005 vs 0.148 ± 0.001 m; DM p_Holm = 1e-15 trên dự báo TB seed; cùng chiều ở cả 3 seed). Hiệu ứng lớn hơn
   độ lệch giữa seed khoảng 4 lần ⇒ kết luận (1) của bản 1 seed **đứng vững**. Suy giảm ở cả hai trạm (PHU_AN +0.017,
   TAN_AN +0.022 m); sai lệch âm (dự báo thấp) tăng −0.065 → −0.102 m; giờ đỉnh kém hơn 3.57 → 4.04 h. Mô hình giữ
   riêng cũng kém ổn định hơn theo seed (sd 0.005 vs 0.001 m).
2. Mô hình giữ riêng vẫn **tốt hơn B_harm** (p_Holm = 0.001) và tốt hơn hẳn `phasegnn-abl` (mô hình chỉ học BEN_LUC, TB
   3 seed: 0.202 m), nhưng **không tốt hơn persistence** tại hai trạm này (kém hơn 0.012 m, DM p_Holm = 0.13) và kém xa
   B_KTTV ở PHU_AN (0.138 vs 0.081 m).
3. Điểm "bất ngờ" của bản 1 seed — `phasegnn-long` (chỉ BEN_LUC) ngang `phasegnn-all` — **là may mắn của seed 0**:
   trung bình 3 seed của cùng cấu hình `head` (`phasegnn-abl`) có MAE 0.202 m tại hai trạm, kém cả mô hình giữ riêng.
   Tức là thấy nhãn của nhiều trạm (kể cả không phải trạm đích) giúp rõ: holdout 7 trạm (0.168) < chỉ BEN_LUC (0.202);
   thấy nhãn của chính trạm giúp thêm (0.148).
4. Mọi mô hình ở đây dùng đồ thị con chưa có cạnh `down` (lỗi `build_station_subgraph`, `results/E2/README.md`) — áp
   dụng như nhau cho holdout và all nên so sánh (1) vẫn công bằng. Bản chạy lại với đồ thị con v2: mục (a′) dưới — kết luận không đổi.

### (a′) Đồ thị con v2 (có cạnh `down`, 25/11/2026) — `subgraph_v2/`

Cùng thiết kế và cấu hình, `--subgraph-version 2` (`results/E2/README.md`, mục `*_spread_v2`), seed 0/1/2.
`phasegnn-holdout-v2`: `phasegnn_spread_v2_holdout/`; `phasegnn-all-v2` = `results/E2/phasegnn_spread_v2`
(cùng cấu hình, dự báo toàn tập test lọc về hai trạm). Kết quả trong `subgraph_v2/` (`holdout_station_*.csv`;
tham chiếu `phasegnn-holdout-v2`, Holm trên 6 so sánh; bảng v1 ở trên giữ nguyên).

| Mô hình (PHU_AN + TAN_AN) | MAE TB ± sd 3 seed (m) | MAE TB-seed | bias | MAE Δt (h) | MAE PHU_AN / TAN_AN |
|---|---|---|---|---|---|
| phasegnn-holdout-v2 | 0.165 ± 0.010 | 0.165 | −0.106 | 4.27 | 0.140 / 0.191 |
| phasegnn-all-v2 | 0.147 ± 0.001 | 0.146 | −0.072 | 3.49 | 0.123 / 0.171 |
| *(v1)* phasegnn-holdout | 0.168 ± 0.005 | 0.167 | −0.110 | 4.02 | 0.141 / 0.194 |
| *(v1)* phasegnn-all | 0.148 ± 0.001 | 0.147 | −0.068 | 3.53 | 0.124 / 0.172 |

MAE holdout-v2 từng seed: 0.174 / 0.154 / 0.169 (dao động giữa seed lớn gấp đôi v1).

| holdout-v2 (TB seed) vs | MAE holdout-v2 / kia | mean_diff | DM t | DM p_Holm |
|---|---|---|---|---|
| phasegnn-all-v2 | 0.165 / 0.146 | +0.018 | 8.38 | 2.7e-16 (theo seed +0.026 / +0.006 / +0.023, 3/3 cùng chiều) |
| phasegnn-holdout (v1) | 0.165 / 0.167 | −0.002 | −3.56 | 7e-04 (theo seed: +0.006 / −0.009 / −0.004 — không ổn định) |
| B0 persistence | 0.165 / 0.155 | +0.010 | 1.26 | **0.21** |
| B_harm | 0.165 / 0.199 | −0.034 | −3.72 | 6e-04 |
| B_KTTV (PHU_AN) | 0.137 / 0.081 | +0.056 | 10.65 | 1e-25 |

**Đọc:** kết luận của (a) không đổi với v2 — che nhãn hai trạm làm MAE tăng ≈ 0.018 m (+13 %), cùng chiều cả 3
seed; không tốt hơn persistence; kém xa B_KTTV. Holdout-v2 nhỉnh hơn holdout-v1 trên dự báo TB seed (−0.002 m)
nhưng chiều đổi theo seed nên không coi là cải thiện (đầu ra tại trạm không phụ thuộc cạnh `down`, xem
`results/E2/README.md`).

## (b) Giữ riêng năm triều cao nhất

**Chọn năm.** Với mỗi trạm lấy đỉnh cao nhất năm, chuẩn hóa z theo trạm rồi lấy trung bình 9 trạm:
2025 cao nhất (z̄ = +0.92; 2024 +0.64, 2023 +0.45; các năm 2017–2022 trong khoảng −0.16…+0.20).
**2025 nằm trọn trong tập test** (2024-07-01 → 2026-11-23), tức đã giữ riêng sẵn: mọi mô hình chỉ huấn luyện
trên 2017–2022 (val 2023 → 2024-06), nên không cần huấn luyện lại — chỉ chấm trên các ngày đích thuộc 2025.

`holdout_year_2025_metrics.csv` (mọi trạm; `phasegnn-abl`, `B3-abl` = dự báo trung bình 3 seed của E2):

| Mô hình | n | MAE 2025 (m) | MAE cả tập test (m) | RMSE | bias | NSE | KGE | MAE Δt (h) |
|---|---:|---|---|---|---|---|---|---|
| phasegnn-abl (TB 3 seed) | 16 425 | 0.180 | 0.175 | 0.240 | −0.126 | 0.922 | 0.913 | 4.58 |
| phasegnn-long (seed 0) | 16 425 | 0.156 | 0.157 | 0.215 | −0.036 | 0.938 | 0.948 | 4.84 |
| B3-abl (TB 3 seed) | 16 425 | 0.185 | 0.181 | 0.247 | −0.096 | 0.918 | 0.923 | 4.56 |
| B0 persistence | 16 365 | 0.170 | 0.166 | 0.238 | +0.041 | 0.924 | 0.953 | 3.99 |
| B_harm | 16 425 | 0.235 | 0.245 | 0.298 | +0.129 | 0.880 | 0.892 | 3.73 |
| B_KTTV (3 trạm) | 4 525 | 0.084 | 0.081 | 0.125 | −0.007 | 0.742 | 0.866 | 2.68 |

**Đọc kết quả:** năm triều cao nhất **không** làm thứ hạng thay đổi. So với cả tập test, MAE 2025 thay đổi
nhỏ và không cùng chiều: phasegnn-abl +0.005 m, B3-abl +0.004, B0 +0.004, B_KTTV +0.003; phasegnn-long gần như
không đổi (0.157 → 0.156, −0.001); B_harm giảm 0.010 (0.245 → 0.235). Không có dấu hiệu PhaseGNN sụp đổ ở năm
cực trị. So với persistence thì tùy cấu hình: **phasegnn-long tốt hơn B0 về MAE** (0.156 vs 0.170 m; cũng tốt
hơn về RMSE 0.215 vs 0.238 và bias −0.036 vs +0.041) nhưng **kém B0 về giờ đỉnh** (MAE Δt 4.84 vs 3.99 h);
phasegnn-abl và B3-abl (TB 3 seed) **kém B0** cả về MAE (0.180, 0.185 vs 0.170) lẫn giờ đỉnh (4.58, 4.56 vs
3.99 h). Mọi mô hình học máy kém xa B_KTTV (0.084 m, trên 3 trạm có dự báo KTTV). Bảng này chưa có kiểm định
ghép cặp (chỉ là chỉ số điểm, 1 seed với phasegnn-long). Lưu ý: đây là chỉ số trên *mọi ngày* của 2025, không
riêng các đỉnh cực trị; chưa tách top-k đỉnh.

## Chạy lại

```bash
R="$(pwd -W)"   # Git Bash; cần MSYS_NO_PATHCONV=1
D="MSYS_NO_PATHCONV=1 docker run --rm -v $R:/work -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local"
# huấn luyện (≈ 12 phút mỗi mô hình, CPU 1 luồng)
$D python -X utf8 -m phasegnn.train --variant phasegnn --seed 0 --epochs 12 --delta-h 12 \
   --max-train-samples 1200 --max-val-samples 300 --patience 4 --subsample spread \
   --holdout-stations PHU_AN,TAN_AN --out-dir results/E5/phasegnn_spread_holdout --no-mlflow --no-register
#   (bỏ --holdout-stations và đổi --out-dir results/E5/phasegnn_spread_all cho mô hình đối chứng)
# chấm tại trạm giữ riêng (≈ 8 phút)
$D python -X utf8 packages/phasegnn/scripts/evaluate_checkpoint.py \
   --checkpoint results/E5/phasegnn_spread_holdout/checkpoint_seed0.pt --split test --stations PHU_AN,TAN_AN \
   --model-name phasegnn-spread-holdout --out-dir results/E5/phasegnn_spread_holdout/test_holdout_stations
#   (lặp lại với --seed 1|2; chấm seed k ghi vào .../test_holdout_stations_seed<k>, seed 0 ở .../test_holdout_stations)
# tổng hợp + kiểm định (3 seed: nhiều đường dẫn cách nhau dấu phẩy ⇒ dự báo TB seed + holdout_station_by_seed/mean_sd.csv)
H=results/E5/phasegnn_spread_holdout; A=results/E5/phasegnn_spread_all
$D python -X utf8 scripts/generalization_summary.py --out-dir results/E5 --stations PHU_AN,TAN_AN \
   --ref phasegnn-holdout \
   --pred phasegnn-holdout=$H/test_holdout_stations/predictions_test.csv,$H/test_holdout_stations_seed1/predictions_test.csv,$H/test_holdout_stations_seed2/predictions_test.csv \
   --pred phasegnn-all=$A/test_holdout_stations/predictions_test.csv,$A/test_holdout_stations_seed1/predictions_test.csv,$A/test_holdout_stations_seed2/predictions_test.csv \
   --pred phasegnn-long=results/E2/phasegnn_long/test_full/predictions_test.csv \
   --pred phasegnn-abl=results/E2/phasegnn_abl/test_full_seedmean/predictions_test.csv \
   --pred B0=results/baselines/B0/predictions_test.csv --pred B_harm=results/baselines/B_harm/predictions_test.csv \
   --pred B_KTTV=results/baselines/B_KTTV/predictions_test.csv
# (b) năm 2025 (lượt riêng, ghi holdout_year_2025_metrics.csv)
$D python -X utf8 scripts/generalization_summary.py --out-dir results/E5 --year 2025    --pred phasegnn-abl=results/E2/phasegnn_abl/test_full_seedmean/predictions_test.csv    --pred phasegnn-long=results/E2/phasegnn_long/test_full/predictions_test.csv    --pred B3-abl=results/E2/B3_abl/test_full_seedmean/predictions_test.csv    --pred B0=results/baselines/B0/predictions_test.csv --pred B_harm=results/baselines/B_harm/predictions_test.csv    --pred B_KTTV=results/baselines/B_KTTV/predictions_test.csv
```

## File

`holdout_station_metrics.csv`, `holdout_station_tests.csv`, `holdout_station_by_seed.csv`,
`holdout_station_mean_sd.csv`, `holdout_year_2025_metrics.csv`;
`subgraph_v2/holdout_station_*.csv` (đồ thị con v2); `phasegnn_spread_v2_holdout/` (seed 0/1/2, `test_holdout_stations_seed{0,1,2}/`);
`phasegnn_spread_{all,holdout}/` (`model_record_seed{0,1,2}.json`, `train_log_seed{0,1,2}.json`,
`test_holdout_stations/` (seed 0), `test_holdout_stations_seed{1,2}/` — `metrics_test*.csv|json`). `*.pt`, `predictions*.csv`, `logs/` không commit.
