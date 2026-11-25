# results/E2 – PhaseGNN và biến thể (B3, B4, B5a, B5b)

> **TRẠNG THÁI (24/11/2026): đã chạy lại** trên G5 mới (`astro_merged = true`, triều thiên văn fit chỉ trên train)
> và mẫu theo lưới ngày lịch (train 19 557, val 4 761, test 7 605 mẫu; rò rỉ 0).
> **Cấu hình giữ nguyên như lần trước** (đọc từ `model_record_seed0.json` cũ và lệnh `docker run` cũ):
> seed 0, `hidden_dim` 64, `delta_h` 12, `t_in` 14, `t_out` 5, batch 16, lr 1e-3;
> `*_d12`: 3 epoch, 300 mẫu train / 100 mẫu val, patience 10; `*_long`: 12 epoch, 1 200 / 300 mẫu, patience 4.
> Huấn luyện bằng `python -m phasegnn.train` (như lần trước), **không** qua `scripts/run_experiments.py`
> (mặc định của script đó khác: `hidden_dim` 32, `delta_h` 12.42, 3 seed → không so được với số cũ).

## Ablation `spread` với đồ thị con v2 (`*_spread_v2`, 25/11/2026) — có cạnh `down`

**Sửa lỗi.** `build_station_subgraph(version=2)` (mặc định cho lượt huấn luyện mới; `TrainConfig.subgraph_version`,
`--subgraph-version`, ghi trong checkpoint; checkpoint cũ không có trường này ⇒ v1, dự báo không đổi — đã kiểm tra
`phasegnn_abl` seed 1, 20 ngày: sai khác 0.0). v2 giữ cạnh `down` ngược chiều của mỗi cạnh `up` trên đường biển →
trạm: BEN_LUC 9 up / 8 down, PHU_AN 11 / 10, TAN_AN 7 / 6, DAU_TIENG 26 / 25 (cạnh rời nút biển không có cạnh
`down` ngược trong đồ thị toàn cục: 645 `down` / 646 `up`). Cấu hình huấn luyện giống hệt `*_spread`, thêm
`--subgraph-version 2`; seed 0/1/2. Thời gian: 18 lượt (15 ablation + 3 E5 holdout), 7 song song, ≈ 1 h 50
(kết thúc 13:16). E5 "all" v2 dùng lại dự báo của `phasegnn_spread_v2` (cùng cấu hình, tất định).

**Trung bình ± sd qua 3 seed** (`ablation_spread_v2_mean_sd.csv`, `ablation_spread_v2_by_seed.csv`), so với v1:

| Mô hình | MAE v2 (m) | MAE v1 (`*_spread`) | RMSE v2 | bias v2 | MAE Δt v2 (h) | Phủ 10–90 % v2 |
|---|---|---|---|---|---|---|
| phasegnn | 0.146 ± 0.002 | 0.146 ± 0.001 | 0.198 ± 0.003 | −0.044 ± 0.001 | 3.81 ± 0.04 | 0.76 |
| B4 τ cố định | 0.146 ± 0.003 | 0.146 ± 0.003 (**trùng từng bit**) | 0.198 | −0.039 | 3.82 | 0.76 |
| B5a chỉ `up` | **0.145 ± 0.004** | 0.146 ± 0.003 | 0.196 | −0.042 | 3.82 | 0.77 |
| B5b chỉ `down` | 0.172 ± 0.001 | 0.172 ± 0.001 (**trùng từng bit**) | 0.228 | −0.046 | 4.05 | 0.74 |
| B3 GraphSAGE | 0.161 ± 0.003 | 0.161 ± 0.003 | 0.214 | −0.044 | 3.83 | 0.74 |

**Kiểm định** (`ablation_spread_v2_tests.csv`, dự báo TB seed, DM Newey–West lag 4, Holm trên 4):
phasegnn vs B4 −0.0002 m (DM p_Holm = 0.64); vs B5a +0.0010 (p_Holm = 0.11; theo seed −0.006 / +0.005 / +0.004);
vs B5b −0.027 (2e-27, 3/3 seed); vs B3 −0.016 (9e-16, 3/3 seed). Thêm (ghép đỉnh ngày, 9 trạm, Holm trên 4 — v1, B0,
B_harm, B_KTTV; `daypeak_tests_spread_v2.csv`, tạo lại bằng `scripts/e2_daypeak_tests.py --set v2` từ dự báo đã có):
phasegnn-spread-v2 vs phasegnn-spread (v1) 0.1449 / 0.1453 m, DM p_Holm = 0.34; vs B0 persistence 0.144 / 0.166 m,
p_Holm = 8e-04; vs B_harm 0.145 / 0.244 m, p_Holm = 3e-29; vs B_KTTV (3 trạm) 0.127 / 0.081 m, p_Holm = 1e-25.

**τ học được v2** (`tau_summary_v2.csv`): cạnh `up` |δ| 0.0004–0.0012 (τ_eff/τ_phys ∈ [0.9985, 1.002]); cạnh `down`
**δ = 0 tuyệt đối** ở mọi mô hình; B5b δ = 0 cả hai chiều.

**Vì sao sửa lỗi gần như không đổi gì (kiểm tra trực tiếp trên đồ thị v2):** trạm đích là **nút đầu thượng nguồn**
của đồ thị con (đường đi chỉ gồm các nút từ biển tới trạm), nên mọi cạnh `down` đều **đi ra khỏi** trạm về phía biển:
số cạnh `down` đi vào trạm = 0 ở mọi trạm (trong đồ thị toàn cục trạm có 1–2 cạnh `down` đi vào, từ thượng nguồn,
nhưng các nút đó không được đưa vào đồ thị con). Mô hình chỉ đọc đầu ra ở nút trạm, và thông điệp được tính từ mã hoá
đầu vào (không từ trạng thái GRU của nút khác), nên đầu ra tại trạm **không phụ thuộc cạnh `down`**:
B4 và B5b cho dự báo trùng từng bit với v1; phasegnn/B5a chỉ đổi gián tiếp (`L_τ = mean(δ²)` nay lấy trung bình trên
nhiều cạnh hơn ⇒ đổi quỹ đạo huấn luyện). B3: seed 0 và seed 2 trùng v1 tới 6 chữ số (MAE test 0.163965 / 0.158435 m;
val MAE đỉnh, best epoch 8 / 9 như v1); seed 1 khác (0.1609 vs 0.1596 m) vì chọn best epoch khác (11 ở v2, 9 ở v1;
`model_record_seed1.json`). Chưa kiểm chứng cơ chế gây khác biệt ở seed 1.

**Kết luận (trung thực):**

1. Sửa lỗi là cần (đúng ý đồ thiết kế) nhưng **không thay đổi kết quả**: v2 ≈ v1 (phasegnn 0.1449 vs 0.1453 m, DM
   p_Holm = 0.34). Mọi kết luận của `*_spread` giữ nguyên: tốt hơn persistence, kém xa B_KTTV, τ học được không có
   tác dụng (δ ≈ 0), thông điệp lan truyền từ biển có ích (B5b kém +0.027 m), tốt hơn GraphSAGE (−0.016 m).
2. **Vai trò của chiều dòng chảy (`down`) vẫn chưa đo được**, không phải vì không có ích mà vì đồ thị con không chứa
   nút nào ở thượng nguồn trạm (không có nhánh sông/hồ chứa phía trên). Muốn kiểm định cần mở rộng đồ thị con: thêm
   đường xuôi dòng **từ thượng nguồn/hồ chứa (Trị An, Dầu Tiếng, Thác Mơ) tới trạm** — việc tiếp theo, chưa làm.
   B5a ("chỉ up") vì vậy tương đương phasegnn; B5b = mô hình không nhận thông tin đồ thị.

## Ablation 3 seed với mẫu `spread`, đồ thị con v1 (`*_spread`, 25/11/2026) — số gần như trùng v2 ở trên

**Cấu hình:** giống hệt `*_abl` (12 epoch, 1 200 mẫu train / 300 val, patience 4, `hidden_dim` 64, `delta_h` 12,
batch 16, lr 1e-3, seed 0/1/2), **chỉ đổi** `--subsample spread` (1 200 / 300 mẫu cách đều trên 9 trạm và toàn giai
đoạn train/val, thay vì 1 200 mẫu đầu = chỉ BEN_LUC). Giữ 1 200 / 300 (không tăng lên 3 000 / 600) để (i) so sánh với
`head` chỉ khác đúng cách lấy mẫu và (ii) dùng chung với E5 (`results/E5/`). Seed 0 của `phasegnn_spread` tái tạo
**đúng** `results/E5/phasegnn_spread_all` seed 0 (val MAE 0.150333 m, best epoch 10 — tất định).
Thời gian máy: 19 lần huấn luyện + chấm (15 ablation + 4 E5), 7 song song: 07:58 → 09:41 (≈ 1 h 45; ≈ 85–105 s/epoch khi
chạy song song). Chấm toàn bộ tập test (7 605 mẫu, 38 025 cặp đỉnh ngày) bằng `phasegnn.evaluate`.

**Trung bình ± độ lệch chuẩn qua 3 seed** (`ablation_spread_mean_sd.csv`; từng seed: `ablation_spread_by_seed.csv`):

| Mô hình | MAE (m) | RMSE | bias | NSE | KGE | MAE Δt (h) | Phủ 10–90 % | MAE `head` (`*_abl`) |
|---|---|---|---|---|---|---|---|---|
| **phasegnn** | **0.146 ± 0.001** | 0.198 ± 0.003 | −0.040 ± 0.003 | 0.946 ± 0.001 | 0.962 ± 0.002 | 3.84 ± 0.06 | 0.76 ± 0.00 | 0.180 ± 0.021 |
| B4 τ cố định | 0.146 ± 0.003 | 0.198 ± 0.004 | −0.039 ± 0.003 | 0.946 ± 0.002 | 0.961 ± 0.004 | **3.82 ± 0.08** | 0.76 ± 0.01 | 0.187 ± 0.024 |
| B5a chỉ `up` | 0.146 ± 0.003 | 0.198 ± 0.003 | −0.047 ± 0.007 | 0.946 ± 0.002 | 0.958 ± 0.002 | 3.83 ± 0.04 | 0.76 ± 0.02 | 0.189 ± 0.030 |
| B5b chỉ `down` (*) | 0.172 ± 0.001 | 0.228 ± 0.001 | −0.046 ± 0.005 | 0.929 ± 0.001 | 0.941 ± 0.002 | 4.05 ± 0.15 | 0.74 ± 0.02 | 0.207 ± 0.017 |
| B3 GraphSAGE | 0.161 ± 0.003 | 0.214 ± 0.003 | −0.042 ± 0.009 | 0.938 ± 0.002 | 0.947 ± 0.003 | 3.83 ± 0.11 | 0.74 ± 0.02 | 0.212 ± 0.052 |
| *Tham chiếu:* B0 persistence | 0.166 | 0.231 | +0.043 | 0.927 | 0.954 | 4.01 | 0.59 | |
| *Tham chiếu:* B_KTTV (3 trạm) | 0.081 | 0.117 | +0.002 | 0.769 | 0.880 | 2.59 | 0.86 | |

MAE từng seed (0/1/2): phasegnn 0.145/0.148/0.146; B4 0.143/0.148/0.148; B5a 0.150/0.146/0.143; B5b 0.172/0.171/0.172;
B3 0.164/0.160/0.158. (*) B5b thực chất là **mô hình không có thông điệp đồ thị** — xem "Lỗi phát hiện" dưới.

**Kiểm định ghép cặp phasegnn vs biến thể** (`ablation_spread_tests.csv`; cùng phương pháp như `*_abl`: dự báo
trung bình seed `<mô hình>_spread/test_full_seedmean/` (tên `<mô hình>-spread`), ghép `(station, target_date,
lead_day)`, Wilcoxon, DM trên 845 ngày phát bản tin, Newey–West lag 4, Holm trên 4 so sánh; `diff` < 0 ⇒ phasegnn tốt hơn):

| phasegnn vs | MAE TB-seed phasegnn / biến thể | diff | Wilcoxon p_Holm | DM t | DM p_Holm | diff seed 0 / 1 / 2 | `head`: diff, DM p_Holm |
|---|---|---|---|---|---|---|---|
| B4 τ cố định | 0.1453 / 0.1451 | +0.0002 | 1e-05 | +0.93 | **0.70** | +0.002 / −0.000 / −0.001 | −0.004, 7e-11 |
| B5a chỉ `up` | 0.1453 / 0.1454 | −0.0001 | 2e-06 | −0.18 | **0.86** | −0.005 / +0.002 / +0.003 | −0.011, 2e-06 |
| B5b chỉ `down` | 0.1453 / 0.1715 | −0.026 | < 1e-300 | −11.65 | 9e-31 | −0.027 / −0.023 / −0.026 (3/3) | −0.024, 2e-11 |
| B3 GraphSAGE | 0.1453 / 0.1604 | −0.015 | < 1e-300 | −7.91 | 8e-15 | −0.019 / −0.012 / −0.012 (3/3) | −0.006, 0.011 |

(Wilcoxon cho p rất nhỏ cả khi |diff| ≈ 1e-4 vì n = 37 845 cặp và phân phối hiệu lệch không đối xứng; dùng DM
(tính tự tương quan theo ngày) làm tiêu chí chính.)

**So với tham chiếu** (cùng cách ghép đỉnh ngày, `scripts/generalization_summary.py` với `--stations` = cả 9 trạm,
tham chiếu `phasegnn-spread` TB 3 seed; DM Newey–West lag 4, Holm trên 5 so sánh; `daypeak_tests_spread.csv`, tạo
lại bằng `scripts/e2_daypeak_tests.py --set spread` từ dự báo đã có — kiểm định chính thức ±6 h ở `results/evaluation/`):

| phasegnn-spread vs | n cặp | MAE spread / kia | diff | DM t | DM p_Holm |
|---|---:|---|---|---|---|
| phasegnn-abl (`head`, TB 3 seed) | 37 845 | 0.145 / 0.175 | −0.030 | −9.87 | 2e-22 |
| phasegnn-long (`head`, seed 0) | 37 845 | 0.145 / 0.157 | −0.012 | −6.79 | 2e-11 |
| B0 persistence | 37 672 | 0.145 / 0.166 | −0.022 | −3.45 | **6e-04** |
| B_harm | 37 845 | 0.145 / 0.244 | −0.099 | −11.35 | 4e-29 |
| B_KTTV (3 trạm) | 10 833 | 0.128 / 0.081 | +0.047 | +10.38 | 1e-24 |

**τ học được** (`tau_summary.csv`, `scripts/tau_summary.py`, 450 mẫu test cách đều, δ của mọi cạnh):
τ_eff = τ_phys · exp(δ) **gần như không rời τ_phys**: |δ| trung bình 0.0002–0.0008 (spread) và 0.0001–0.0004 (`head`),
tỉ số τ_eff/τ_phys trong [0.999, 1.0015] (p10–p90), không cạnh nào chạm biên kẹp ±1.5. τ_phys trung bình trên đường
biển → trạm là 0.86 h, tức chỉ ≈ 7 % của một bước lưới Δ = 12 h — dịch pha gần như chỉ là nội suy rất nhỏ giữa hai
mốc lưới, nên gradient theo δ rất yếu và `L_τ = mean(δ²)` kéo δ về 0. Nghĩa là ở `delta_h = 12` **cơ chế τ học được
không hoạt động**; khác biệt phasegnn/B4 chỉ là nhiễu giữa các lần huấn luyện.

**Lỗi phát hiện (đã sửa bằng đồ thị con v2 — xem mục `*_spread_v2` ở trên; mục này mô tả v1):** `phasegnn.train.build_station_subgraph` **không giữ cạnh `down`
nào**: đồ thị toàn cục có 645 cạnh `down` (đúng là chiều ngược của cạnh `up`), nhưng mọi đồ thị con của trạm chỉ có
cạnh `up` (BEN_LUC 9, PHU_AN 11, TAN_AN 7, BIEN_HOA 16 cạnh, 0 cạnh `down`). Nguyên nhân: khi truy ngược đường đi,
`par, e = prev[cur]` thì cạnh `up` `e` là `cur → par`, nên cạnh `down` cùng cặp là `par → cur`; code lại tìm cạnh
`down` `cur → par` (cùng chiều với `up`) ⇒ không bao giờ khớp. Hệ quả cho mọi kết quả E2/E5 hiện có (cả `head` lẫn
`spread`): **thông điệp `down` luôn bằng 0**; phasegnn = B5a + một lượt TopoGRU "down" không có thông điệp; **B5b = GRU
từng nút không nhận thông tin đồ thị nào**. Vì vậy "bỏ cạnh `up` hại nhất" thực ra là "bỏ hết thông điệp đồ thị hại
nhất"; ablation **chưa** đo được vai trò của chiều dòng chảy. Sửa (đảo điều kiện thành `src == par, dst == cur`) sẽ đổi
dự báo của **mọi checkpoint cũ** (đồ thị con không lưu trong checkpoint) nên cần cờ tương thích và huấn luyện lại —
đã làm: cờ `subgraph_version` (v1 cho checkpoint cũ, v2 cho lượt mới).

**Đọc kết quả (trung thực):**

1. **Cách lấy mẫu quan trọng hơn mọi lựa chọn kiến trúc đã thử.** Cùng 1 200 mẫu, `spread` (9 trạm) giảm MAE
   phasegnn từ 0.180 ± 0.021 (`head`, chỉ BEN_LUC) xuống **0.146 ± 0.001 m** (−19 %), độ lệch giữa seed giảm ~20 lần,
   MAE giờ đỉnh 4.82 → 3.84 h, bias −0.098 → −0.040 m, độ phủ 10–90 % 0.51 → 0.76. Mọi biến thể đều cải thiện tương tự.
2. **PhaseGNN (spread) tốt hơn persistence** — lần đầu trong dự án: MAE 0.145 vs 0.166 m (DM p_Holm = 6e-04, ghép
   đỉnh ngày), MAE giờ đỉnh 3.82 vs 4.01 h. Kiểm định ±6 h của `evaluation.R`: xem `results/evaluation/README.md`.
3. **Vẫn kém xa B_KTTV**: tại 3 trạm có dự báo KTTV, 0.128 vs 0.081 m (DM p_Holm = 1e-24); giờ đỉnh 3.8 vs 2.6 h.
4. **τ học được không đem lại gì** (phasegnn vs B4: +0.0002 m, DM p_Holm = 0.70; chiều đổi theo seed). Kết luận "τ
   giúp ~0.004 m" của `*_abl` không lặp lại khi huấn luyện trên dữ liệu đại diện; δ ≈ 0 (xem trên).
5. **Hướng cạnh:** phasegnn ≈ B5a (−0.0001 m, p_Holm = 0.86) — đúng như dự kiến vì đồ thị con không có cạnh `down`.
   B5b (không thông điệp đồ thị) kém rõ (+0.026 m, 3/3 seed) ⇒ **lan truyền từ biển vào có ích thật** (≈ 15 % MAE).
6. **So với GraphSAGE (B3)** phasegnn tốt hơn ổn định: −0.015 m, 3/3 seed, DM p_Holm = 8e-15 (với `head` chỉ −0.006 và
   không ổn định). B3 nằm giữa B5b và phasegnn: thông điệp đồ thị không trễ pha giúp một phần.

## Ablation 3 seed (`*_abl`, 25/11/2026) — mẫu `head` (chỉ BEN_LUC), giữ để đối chiếu

**Cấu hình:** đúng `long` (12 epoch, 1 200 mẫu train / 300 val, patience 4, `hidden_dim` 64, `delta_h` 12,
batch 16, lr 1e-3), seed **0, 1, 2**, cho phasegnn, B4 (τ cố định), B5a (chỉ cạnh `up`), B5b (chỉ cạnh `down`),
B3 (GraphSAGE). Seed 0 của `phasegnn_abl` tái tạo **đúng từng số** `phasegnn_long` (tất định). Chấm trên **toàn
bộ tập test** (863 ngày, 7 605 mẫu, 38 025 cặp đỉnh ngày) bằng `phasegnn.evaluate`.
Tổng thời gian máy: huấn luyện ≈ 4–12 phút/mô hình (6 song song), chấm ≈ 1,5 phút/mô hình khi chạy một mình
(43 phút khi 7 cùng lúc).

> **Giới hạn quan trọng (phát hiện 25/11/2026):** với `--max-train-samples 1200` kiểu cũ (`head`), 1 200 mẫu
> train và 300 mẫu val **đều là của BEN_LUC** (mẫu xếp theo trạm → ngày). Mọi mô hình `long`/`abl` chỉ học nhãn
> của một trạm (2017-01 → 2020-04) rồi dự báo cho 9 trạm. Ablation dưới đây vẫn so được giữa các biến thể (cùng
> dữ liệu), nhưng không đại diện cho mô hình huấn luyện đầy đủ. Đã chạy lại với `--subsample spread`: mục "Ablation 3 seed với mẫu
> `spread`" ở trên (kết luận về τ và hướng cạnh của mục này **không** còn đứng vững).

**Trung bình ± độ lệch chuẩn qua 3 seed** (`ablation_mean_sd.csv`; từng seed: `ablation_by_seed.csv`):

| Mô hình | MAE (m) | RMSE | bias | NSE | KGE | MAE Δt (h) | Phủ 10–90 % |
|---|---|---|---|---|---|---|---|
| **phasegnn** | **0.180 ± 0.021** | **0.236 ± 0.022** | −0.098 ± 0.066 | **0.924 ± 0.014** | 0.916 ± 0.026 | 4.82 ± 0.20 | 0.51 ± 0.05 |
| B4 τ cố định | 0.187 ± 0.024 | 0.242 ± 0.027 | −0.107 ± 0.076 | 0.919 ± 0.017 | 0.914 ± 0.036 | **4.79 ± 0.25** | 0.49 ± 0.06 |
| B5a chỉ `up` | 0.189 ± 0.030 | 0.247 ± 0.038 | −0.099 ± 0.064 | 0.915 ± 0.027 | **0.923 ± 0.038** | 5.16 ± 0.51 | 0.62 ± 0.04 |
| B5b chỉ `down` | 0.207 ± 0.017 | 0.264 ± 0.017 | −0.097 ± 0.080 | 0.904 ± 0.012 | 0.918 ± 0.038 | 5.18 ± 0.27 | 0.64 ± 0.17 |
| B3 GraphSAGE | 0.212 ± 0.052 | 0.272 ± 0.057 | −0.074 ± 0.135 | 0.897 ± 0.045 | 0.913 ± 0.069 | 4.86 ± 0.12 | 0.54 ± 0.18 |
| *Tham chiếu:* B0 persistence | 0.166 | 0.231 | +0.043 | 0.927 | 0.954 | 4.01 | 0.59 |
| *Tham chiếu:* B_KTTV (3 trạm) | 0.081 | 0.117 | +0.002 | 0.769 | 0.880 | 2.59 | 0.86 |

MAE từng seed (0/1/2): phasegnn 0.157/0.197/0.187; B4 0.159/0.201/0.202; B5a 0.165/0.223/0.180;
B5b 0.188/0.222/0.209; B3 0.176/0.188/0.272.

**Kiểm định ghép cặp phasegnn vs biến thể** (`ablation_tests.csv`, `scripts/ablation_summary.py`).
Chọn **dự báo trung bình qua seed** làm kiểm định chính (q10/q50/q90 trung bình cộng, giờ đỉnh trung bình vòng
24 h; file `<mô hình>_abl/test_full_seedmean/`), và lặp lại trên từng seed để xem chiều có ổn định không.
Ghép theo `(station, target_date, lead_day)`; Wilcoxon ký hạng trên `|e_phasegnn| − |e_biến thể|`; DM trên chuỗi
trung bình theo ngày phát bản tin (845 ngày), Newey–West lag 4; Holm trên 4 so sánh. `diff` < 0 ⇒ phasegnn tốt hơn.

| phasegnn vs | MAE TB-seed phasegnn / biến thể | diff | Wilcoxon p_Holm | DM t | DM p_Holm | diff seed 0 / 1 / 2 |
|---|---|---|---|---|---|---|
| B4 τ cố định | 0.175 / 0.179 | −0.004 | < 1e-300 | −6.69 | 6.7e-11 | −0.002 / −0.004 / −0.015 (3/3 tốt hơn) |
| B5a chỉ `up` | 0.175 / 0.186 | −0.011 | 2e-272 | −4.92 | 1.7e-06 | −0.008 / −0.026 / **+0.007** (2/3) |
| B5b chỉ `down` | 0.175 / 0.199 | −0.024 | < 1e-300 | −6.93 | 1.6e-11 | −0.031 / −0.026 / −0.022 (3/3) |
| B3 GraphSAGE | 0.175 / 0.181 | −0.006 | 5e-32 | −2.53 | 0.011 | −0.019 / **+0.009** (DM p = 0.18) / −0.085 (2/3) |

(Trung bình 3 seed của phasegnn — MAE 0.175 — kém seed 0 đơn lẻ (0.157) vì hai seed còn lại kém hơn; trung bình
dự báo không tự động tốt hơn seed tốt nhất.)

**Đọc kết quả (trung thực, sơ bộ):** *(cập nhật 25/11/2026: điểm 1 và 2 không còn đứng vững — đồ thị con không có cạnh `down` nên B5b = không thông điệp đồ thị, và với mẫu `spread` τ học được không giúp; xem mục `spread` ở trên.)*

1. **Bỏ cạnh `up` (B5b, chỉ lan truyền xuôi dòng) hại rõ nhất**: +0.024 m MAE, ổn định cả 3 seed. Phù hợp vật lý:
   sóng triều đi từ biển vào là tín hiệu chính. Bỏ cạnh `down` (B5a) hại ít hơn và không ổn định (1 seed B5a tốt hơn).
2. **τ học được (phasegnn vs B4) giúp rất ít**: −0.004 m MAE (≈ 2 %), cùng chiều cả 3 seed và có ý nghĩa thống kê,
   nhưng nhỏ hơn nhiều so với độ lệch giữa các seed (0.021 m); MAE giờ đỉnh của B4 còn hơi tốt hơn (4.79 vs 4.82 h).
   Kết luận: ở cấu hình này τ học được **chưa** đem lại cải thiện có ý nghĩa thực tế.
3. **So với GraphSAGE (B3)** phasegnn tốt hơn trung bình (−0.006 m, DM p_Holm = 0.011) nhưng không ổn định theo seed
   (seed 1 B3 tốt hơn); B3 dao động mạnh nhất giữa các seed (sd 0.052 m).
4. **Không biến thể nào tốt hơn persistence (B0, 0.166 m) khi tính trung bình qua seed**; chỉ seed 0 của
   phasegnn/B4 gần bằng B0. Khoảng cách tới B_KTTV (0.081 m) không đổi. Kiểm định ±6 h trong
   `results/evaluation/` (tên mô hình `phasegnn-abl`, `B4-abl`, …).

## Sửa lỗi trong lần chạy này

`phasegnn/train.py::evaluate` gọi `pinball_loss(...)` (trả về **vô hướng**) rồi lấy `pin_all[bi]` → mọi lệnh
huấn luyện dừng ở epoch 0 với `IndexError: invalid index of a 0-dim tensor`. Đã sửa: tính pinball cho từng mẫu.
117 test `packages/phasegnn/tests` đạt.

## Thời gian chạy (CPU, 1 luồng/mô hình, chạy song song trong Docker)

| Việc | Thời gian |
|---|---|
| Huấn luyện `*_d12` (mỗi mô hình) | 45 – 58 s (≈ 7 – 16 s/epoch) |
| Huấn luyện `phasegnn_long` / `B3_long` | 5 phút 35 s / 3 phút 56 s (dừng sớm, best epoch 5 / 2) |
| `evaluate_checkpoint.py --max-days 300` (mỗi mô hình) | 1,2 phút (chạy 2 cùng lúc) – 9 phút (7 cùng lúc) |
| `evaluate_checkpoint.py` toàn bộ test (863 ngày, 7 605 mẫu) | 22 – 32 phút (7 cùng lúc) |

## Kết quả trên toàn bộ tập test (`summary_test_full.csv`)

Chấm bằng `phasegnn.evaluate` — **cùng hàm chấm với `results/baselines/summary.csv`**
(`scripts/summarize_predictions.py`), nên so được trực tiếp với mô hình cơ sở. 9 trạm, 38 025 cặp.

| Mô hình | MAE (m) | RMSE | bias | NSE | KGE | MAE Δt (h) | Phủ 10–90 % |
|---|---|---|---|---|---|---|---|
| phasegnn (d12) | 0.2374 | 0.303 | −0.159 | 0.875 | 0.880 | 6.25 | 0.405 |
| B3 GraphSAGE (d12) | 0.2391 | 0.305 | −0.165 | 0.873 | 0.875 | 6.20 | 0.345 |
| B4 τ cố định (d12) | 0.2374 | 0.303 | −0.159 | 0.875 | 0.880 | 6.25 | 0.405 |
| B5a chỉ `up` (d12) | 0.2371 | 0.303 | −0.158 | 0.875 | 0.880 | 6.26 | 0.423 |
| B5b chỉ `down` (d12) | 0.2359 | 0.301 | −0.156 | 0.876 | 0.882 | 6.11 | 0.719 |
| **phasegnn (long)** | **0.1573** | **0.212** | −0.021 | **0.939** | 0.946 | 4.80 | 0.558 |
| B3 GraphSAGE (long) | 0.1763 | 0.233 | −0.002 | 0.926 | 0.955 | 4.92 | 0.740 |
| *Tham chiếu:* B0 persistence | 0.1661 | 0.231 | +0.043 | 0.927 | 0.954 | 4.01 | 0.592 |
| *Tham chiếu:* **B_KTTV** (3 trạm, 11 050 cặp) | **0.0806** | 0.117 | +0.002 | 0.769 | 0.880 | **2.59** | 0.862 |

**Đọc kết quả (trung thực):**

- **Không mô hình nào trong E2 thắng B_KTTV.** PhaseGNN tốt nhất (`long`) có MAE 0.157 m, gần gấp đôi B_KTTV
  (0.081 m), và MAE giờ đỉnh 4.8 h so với 2.6 h. Kiểm định trên cùng cặp ghép: `results/evaluation/README.md`.
- `phasegnn_long` có MAE thấp hơn B3_long (0.176 m). So với B0 persistence (0.166 m) thì **không khác biệt có ý nghĩa** (DM p = 0.94 trước hiệu chỉnh, p_Holm = 1.0, `results/evaluation/`; ghép ±6 h MAE B0_py còn thấp hơn: 0.170 so với 0.172 m), và giờ đỉnh kém B0 (4.80 h so với 4.01 h).
- Các mô hình `d12` gần như **chưa học** (best epoch 0 ở 4/5 mô hình, MAE gần như không đổi theo chân trời: phasegnn_d12
  0.2697 → 0.2703 m từ lead 1 → 5, chấm 300 ngày) và kém cả B0 persistence. So sánh biến thể ở cấu hình này **không có ý nghĩa**.
- **phasegnn và B4 cho kết quả có MAE/RMSE trùng nhau tới 1e-6** (độ phủ 10–90 % lệch ~3e-5) (cả lần cũ lẫn lần này): với 3 epoch, `learn_tau` không làm
  đổi dự báo. Đã chạy ablation 3 seed ở cấu hình `long`: xem mục "Ablation 3 seed" ở trên (thư mục `d12`/`long` cũ giữ nguyên để đối chiếu).

## So với số cũ (`--max-days 300`, `phasegnn/scripts/scoring.py`, giống lần chạy trước)

| Mô hình | MAE đỉnh cũ (m) | MAE đỉnh cũ – chấm lại (m) | **MAE đỉnh mới (m)** | MAE giờ cũ – chấm lại (h) | **MAE giờ mới (h)** |
|---|---|---|---|---|---|
| phasegnn_d12 | 0.6993 | 0.6956 | **0.2701** | 5.212 | **5.072** |
| B3_d12 | 0.6822 | 0.6632 | **0.2702** | 5.200 | **5.077** |
| B4_d12 | — | — | 0.2701 | — | 5.072 |
| B5a_d12 | — | — | 0.2701 | — | 5.038 |
| B5b_d12 | — | — | 0.2613 | — | 4.961 |
| phasegnn_long | — (chưa chấm) | — | **0.1828** | — | 1.281 |
| B3_long | — (chưa chấm) | — | 0.1958 | — | 3.781 |

MAE đỉnh d12 giảm 0.70 → 0.27 m chủ yếu vì checkpoint cũ là bản **trước** khi sửa `HeightScale`, không phải do
`astro_*`. "MAE giờ" của `scoring.py` khác định nghĩa `mae_dt` của `phasegnn.evaluate` (bảng trên) — không trộn hai cột.
Val (best): phasegnn_d12 0.308 → 0.299 m; B3_d12 0.268 → 0.301; phasegnn_long 0.180 → 0.211; B3_long 0.176 → 0.190 m.

## File

`<mô hình>_spread/` : như `_abl` (`test_full_seed*/`, `test_full_seedmean/` tên `<mô hình>-spread`); `ablation_spread_*.csv`,
`tau_summary.csv` (τ học được, `scripts/tau_summary.py`).
`<mô hình>_spread_v2/` : như `_spread`, đồ thị con v2 (tên `<mô hình>-spread-v2`); `ablation_spread_v2_*.csv`,
`tau_summary_v2.csv`; `daypeak_tests_spread.csv`, `daypeak_tests_spread_v2.csv` (ghép đỉnh ngày 9 trạm,
`scripts/e2_daypeak_tests.py`).
`<mô hình>_abl/` : `checkpoint_seed{0,1,2}.pt`, `model_record_seed*.json`, `train_log_seed*.json`, `test_full_seed*/` (chấm từng seed), `test_full_seedmean/` (dự báo trung bình seed).
`<mô hình>/` : `checkpoint_seed0.pt`, `model_record_seed0.json`, `train_log_seed0.json`, `metrics_test*.csv/json`
(300 ngày); `<mô hình>/test_full/` : chấm toàn bộ test. `predictions_test.csv`, `*.pt`, `logs/` không commit.
Lưu ý: `phasegnn_long/predictions_test.csv` (300 ngày) mang tên `phasegnn-v1`; bản `test_full/` mang tên
`phasegnn-long` / `B3-long` để không trùng với `d12` khi đưa vào `evaluation.R`.

## Chạy lại

```bash
R="$(pwd -W)"   # Git Bash; cần MSYS_NO_PATHCONV=1
MSYS_NO_PATHCONV=1 docker run --rm -v "$R:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
  python -X utf8 -m phasegnn.train --variant phasegnn --epochs 3 --delta-h 12 --max-train-samples 300 \
    --max-val-samples 100 --out-dir results/E2/phasegnn_d12 --no-mlflow --no-register
# long: --epochs 12 --max-train-samples 1200 --max-val-samples 300 --patience 4 --out-dir results/E2/phasegnn_long
MSYS_NO_PATHCONV=1 docker run --rm -v "$R:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
  python -X utf8 packages/phasegnn/scripts/evaluate_checkpoint.py --checkpoint results/E2/phasegnn_d12/checkpoint_seed0.pt \
    --split test --out-dir results/E2/phasegnn_d12/test_full          # thêm --max-days 300 để so với số cũ
MSYS_NO_PATHCONV=1 docker run --rm -v "$R:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
  python -X utf8 scripts/summarize_predictions.py --out results/E2/summary_test_full.csv results/E2/*/test_full/predictions_test.csv
```

(Lặp lại cho `--variant B3|B4|B5a|B5b`; với `long` thêm `--model-name phasegnn-long|B3-long` khi chấm.)

**Ablation 3 seed (`*_abl`).** Huấn luyện như `long` với `--seed 0|1|2 --out-dir results/E2/<mô hình>_abl`;
chấm `evaluate_checkpoint.py --checkpoint results/E2/<mô hình>_abl/checkpoint_seed<k>.pt --split test
--model-name <mô hình>-abl-s<k> --out-dir results/E2/<mô hình>_abl/test_full_seed<k>`; rồi tổng hợp:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$R:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local   python -X utf8 scripts/ablation_summary.py --root results/E2 --out-dir results/E2     --models phasegnn B4 B5a B5b B3 --seeds 0 1 2
```

Ghi `ablation_by_seed.csv`, `ablation_mean_sd.csv`, `ablation_tests.csv` và
`<mô hình>_abl/test_full_seedmean/predictions_test.csv` (tên `<mô hình>-abl`, đưa vào `evaluation.R`).

**Ablation `spread` (`*_spread`).** Như trên, thêm `--subsample spread`, `--out-dir results/E2/<mô hình>_spread`,
chấm với `--model-name <mô hình>-spread-s<k> --out-dir results/E2/<mô hình>_spread/test_full_seed<k>`, rồi:

```bash
MSYS_NO_PATHCONV=1 docker run --rm -v "$R:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
  python -X utf8 scripts/ablation_summary.py --root results/E2 --out-dir results/E2 \
    --models phasegnn B4 B5a B5b B3 --seeds 0 1 2 --suffix _spread --out-prefix ablation_spread
MSYS_NO_PATHCONV=1 docker run --rm -v "$R:/work" -w /work -e PYTHONPATH=/work/packages/phasegnn/src phasegnn:local \
  python -X utf8 scripts/tau_summary.py --n-samples 450 --out results/E2/tau_summary.csv \
    results/E2/phasegnn_spread/checkpoint_seed{0,1,2}.pt results/E2/B5a_spread/checkpoint_seed{0,1,2}.pt \
    results/E2/phasegnn_abl/checkpoint_seed{0,1,2}.pt
```

**Đồ thị con v2 (`*_spread_v2`).** Như `*_spread`, thêm `--subgraph-version 2` (mặc định), `--out-dir
results/E2/<mô hình>_spread_v2`, `--model-name <mô hình>-spread-v2-s<k>`; tổng hợp với `--suffix _spread_v2
--out-prefix ablation_spread_v2`; τ: `scripts/tau_summary.py --out results/E2/tau_summary_v2.csv
results/E2/{phasegnn,B5a,B5b}_spread_v2/checkpoint_seed{0,1,2}.pt`.
