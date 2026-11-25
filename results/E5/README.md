# results/E5 – Tổng quát hóa (đề cương mục 7.2, RQ1)

> **TRẠNG THÁI (25/11/2026): bản nhẹ, sơ bộ** — 1 seed, cấu hình gần `long` của E2. Phần (a) chạy lại 25/11/2026
> sau khi sửa rò thống kê nhãn của trạm giữ riêng (xem dưới). Không đủ để kết luận
> chắc chắn; đủ để thấy chiều của hiệu ứng.
> Thư mục này trước 25/11/2026 chứa **đánh giá chung** của `pipelines/r/evaluation.R`; phần đó đã chuyển sang
> `results/evaluation/` (`git mv`, giữ lịch sử).

Đề cương: *"E5 – Tổng quát hóa: giữ riêng trạm; giữ riêng năm triều cao nhất; chỉ số trên tập giữ riêng"*.

## Phát hiện quan trọng về cấu hình `long` của E2

`long` = `--max-train-samples 1200 --max-val-samples 300`. `train.py` cũ lấy **N mẫu đầu**, mà mẫu xếp theo
trạm → ngày, nên **toàn bộ 1 200 mẫu train là của BEN_LUC (2017-01-15 → 2020-04-28)** và 300 mẫu val cũng chỉ
của BEN_LUC. Nghĩa là mọi mô hình `*_long` / `*_abl` của E2 chỉ học nhãn của **một** trạm rồi dự báo cho 9 trạm
— tức E2 trên thực tế đã là một thí nghiệm "giữ riêng 8/9 trạm". Để E5 có nghĩa, ta thêm tùy chọn
`--subsample spread` (lấy N mẫu cách đều trên mọi trạm/giai đoạn); mặc định vẫn là `head` để chạy lại được số cũ.

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

Chung: seed 0, `hidden_dim` 64, `delta_h` 12, 12 epoch, patience 4, val 300 mẫu (cùng cách lấy mẫu, cùng trạm
bị loại). Best val MAE: holdout 0.163 m (bản cũ 0.162), all 0.150 m.

**Kết quả trên tập test tại PHU_AN + TAN_AN** (`holdout_station_metrics.csv`, `phasegnn.evaluate`, ghép đỉnh ngày):

| Mô hình | n | MAE (m) | RMSE | bias | NSE | KGE | MAE Δt (h) | Phủ 10–90 % |
|---|---:|---|---|---|---|---|---|---|
| phasegnn-holdout | 8 450 | 0.168 | 0.209 | −0.109 | 0.27 | 0.45 | 3.77 | 0.65 |
| phasegnn-all | 8 450 | 0.149 | 0.188 | −0.068 | 0.40 | 0.52 | 3.56 | 0.69 |
| phasegnn-long (chỉ BEN_LUC) | 8 450 | 0.148 | 0.186 | −0.061 | 0.42 | 0.48 | 4.52 | 0.58 |
| B0 persistence | 8 575 | 0.155 | 0.213 | +0.046 | 0.24 | 0.65 | 3.85 | 0.62 |
| B_harm | 8 610 | 0.200 | 0.243 | +0.039 | 0.02 | 0.25 | 3.32 | 0.41 |
| B_KTTV (chỉ PHU_AN) | 4 304 | 0.081 | 0.117 | +0.008 | 0.76 | 0.87 | 2.54 | 0.86 |

Bản cũ (μ/σ rò từ train của trạm giữ riêng): holdout MAE 0.169, RMSE 0.211, bias −0.112, NSE 0.25, KGE 0.47.

Theo trạm (MAE m): PHU_AN — holdout 0.138 (cũ 0.136), all 0.124, long 0.125, B0 0.149, B_harm 0.196, B_KTTV 0.081;
TAN_AN — holdout 0.197 (cũ 0.203), all 0.173, long 0.170, B0 0.160, B_harm 0.205.

**Kiểm định ghép cặp** (`holdout_station_tests.csv`; tham chiếu `phasegnn-holdout`; ghép theo
`(station, target_date, lead_day)`; Wilcoxon trên `|e_a| − |e_b|`; DM trên chuỗi trung bình theo ngày phát bản
tin, Newey–West lag 4; Holm trên 5 so sánh; `mean_diff` > 0 ⇒ holdout kém hơn):

| holdout vs | n cặp | MAE holdout / kia | mean_diff | Wilcoxon p_Holm | DM t | DM p_Holm |
|---|---:|---|---|---|---|---|
| phasegnn-all | 8 415 | 0.168 / 0.149 | +0.019 | 5e-225 | 7.74 | 4.1e-14 |
| phasegnn-long | 8 415 | 0.168 / 0.148 | +0.020 | 5e-178 | 6.94 | 1.2e-11 |
| B0 persistence | 8 381 | 0.167 / 0.155 | +0.012 | 3e-24 | 1.59 | **0.111** |
| B_harm | 8 415 | 0.168 / 0.199 | −0.031 | 1e-57 | −3.43 | 0.0012 |
| B_KTTV (PHU_AN) | 4 215 | 0.138 / 0.081 | +0.057 | 1e-185 | 10.83 | 1.3e-26 |

(Bản cũ: mean_diff so với all/long/B0/B_harm/B_KTTV = +0.020/+0.022/+0.014/−0.029/+0.055; DM p_Holm
1.7e-17/3.8e-13/0.068/0.0026/6.3e-26.)

**Đọc kết quả (trung thực):**

1. Che nhãn PHU_AN/TAN_AN làm MAE tại hai trạm đó tăng **~0.019 m (≈ +13 %)** so với cùng cấu hình có nhãn
   (DM p_Holm = 4.1e-14); mức suy giảm lớn hơn ở TAN_AN (0.173 → 0.197 m) — trạm Vàm Cỏ Tây không có "trạm anh
   em" trong tập học. Sai lệch âm (dự báo thấp) tăng rõ: −0.068 → −0.109 m. Bỏ rò thống kê chỉ đổi số rất ít
   (MAE gộp 0.169 → 0.168; PHU_AN xấu đi 0.002, TAN_AN tốt lên 0.006) — kết luận không đổi.
2. Mô hình giữ riêng vẫn **tốt hơn B_harm** (p_Holm = 0.001) nhưng **không tốt hơn persistence** (MAE kém hơn
   0.012 m, DM p_Holm = 0.111 — không có ý nghĩa ở 5 %) và kém xa B_KTTV ở PHU_AN.
3. Điều bất ngờ: `phasegnn-long` (chỉ học BEN_LUC) **ngang** `phasegnn-all` tại hai trạm này (0.148 vs 0.149 m).
   Tức là ở quy mô dữ liệu này, việc thấy nhãn của *nhiều* trạm không giúp bao nhiêu, nhưng việc thấy nhãn của
   *chính* trạm (hoặc của một trạm tương tự) thì giúp. **Cần nhiều seed để chắc chắn:** độ lệch chuẩn
   MAE qua 3 seed của phasegnn ở E2 là 0.021 m (`results/E2/ablation_mean_sd.csv`) — lớn hơn mọi khác biệt ở
   bảng trên, nên kết luận (1) chỉ là sơ bộ (1 seed, cùng seed cho cả hai mô hình).

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
# tổng hợp + kiểm định
$D python -X utf8 scripts/generalization_summary.py --out-dir results/E5 --stations PHU_AN,TAN_AN \
   --ref phasegnn-holdout --pred phasegnn-holdout=results/E5/phasegnn_spread_holdout/test_holdout_stations/predictions_test.csv \
   --pred phasegnn-all=results/E5/phasegnn_spread_all/test_holdout_stations/predictions_test.csv \
   --pred phasegnn-long=results/E2/phasegnn_long/test_full/predictions_test.csv \
   --pred B0=results/baselines/B0/predictions_test.csv --pred B_harm=results/baselines/B_harm/predictions_test.csv \
   --pred B_KTTV=results/baselines/B_KTTV/predictions_test.csv
# (b) năm 2025 (lượt riêng, ghi holdout_year_2025_metrics.csv)
$D python -X utf8 scripts/generalization_summary.py --out-dir results/E5 --year 2025    --pred phasegnn-abl=results/E2/phasegnn_abl/test_full_seedmean/predictions_test.csv    --pred phasegnn-long=results/E2/phasegnn_long/test_full/predictions_test.csv    --pred B3-abl=results/E2/B3_abl/test_full_seedmean/predictions_test.csv    --pred B0=results/baselines/B0/predictions_test.csv --pred B_harm=results/baselines/B_harm/predictions_test.csv    --pred B_KTTV=results/baselines/B_KTTV/predictions_test.csv
```

## File

`holdout_station_metrics.csv`, `holdout_station_tests.csv`, `holdout_year_2025_metrics.csv`;
`phasegnn_spread_{all,holdout}/` (`model_record_seed0.json`, `train_log_seed0.json`,
`test_holdout_stations/metrics_test*.csv|json`). `*.pt`, `predictions*.csv`, `logs/` không commit.
