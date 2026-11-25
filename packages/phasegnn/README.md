# packages/phasegnn/ – Mô hình PhaseGNN

**Chức năng:** mô hình GNN lan truyền pha hai chiều (đề cương 5.2) và các mô hình so sánh.

| Module | Nội dung |
|---|---|
| `src/phasegnn/data.py` | đọc Parquet G5 (`data/processed/features/g5/year=*/part-*.parquet`) và cạnh đồ thị; dựng đồ thị hai chiều; tách tập train/val/test theo **`config/splits.json`** (nguồn duy nhất, dùng chung với Spark G5 và R; `PGNN_SPLITS_FILE` để ghi đè); cửa sổ mẫu trên **lưới ngày lịch liên tục** (ngày thiếu = NaN); `check_leakage`; `check_astro_provenance` (từ chối triều thiên văn fit trên ngày ≥ mốc test hoặc thiếu `astro_tide_meta.json`) |
| `src/phasegnn/layers.py` | `PhaseShift` (dịch pha khả vi), `PhaseMessage` (down/up theo τ vật lý), `TopoGRU` hai lượt, `topo_order`, `split_directions` |
| `src/phasegnn/model.py` | `PhaseGNN`; biến thể B3 (GraphSAGE+GRU), B4 (τ cố định), B5a/B5b (một chiều) |
| `src/phasegnn/losses.py` | pinball (q10/q50/q90), `L_peak` (soft-max độ cao đỉnh), `L_timing` (khoảng cách vòng 24 h), `L_τ`, `L_mono`; chỉ số MAE/NSE/độ phủ phân vị |
| `src/phasegnn/baselines.py` | B0 persistence, B_harm, B_KTTV, B1 (hồi quy phần dư có trễ hồ chứa), B2 (LSTM trạm); ghi/kiểm tra theo `schemas/prediction/v1.schema.json` |
| `src/phasegnn/train.py` | vòng huấn luyện, dừng sớm, checkpoint; `StationSubgraph` (đồ thị con biển ↔ trạm) |
| `src/phasegnn/forecast.py` | sinh dự báo theo schema; chế độ job hằng ngày (`--job-day`) |
| `src/phasegnn/registry.py`, `job.py` | ghi MLflow / bản ghi mô hình; job dự báo hằng ngày có khoá Redlock |
| `scripts/` | `evaluate_baselines.py` (chấm B0/B_harm/B1), `evaluate_checkpoint.py` (chấm checkpoint), `scoring.py` (quy ước chấm điểm dùng chung) |

> **Không phụ thuộc `torch_geometric` hay `torch_scatter`.** Toàn bộ phép gom theo đồ thị
> (scatter-sum) được hiện thực bằng `torch.index_add_` nên package chạy được trên
> `python:3.12-slim` + torch CPU. Chỉ phụ thuộc `vngis` (logic dùng chung) và torch.

## Kiểm thử

Checklist 6.5 yêu cầu: dịch xung đúng τ, lan truyền topo hai chiều đúng Σ τ, học lại τ
trên dữ liệu tổng hợp (Muskingum) với sai số tương đối < 20 %, overfit được một lô
nhỏ, forward shape đúng, và các mô hình cơ sở chạy được.

```bash
# Docker (khuyến nghị — môi trường giống hệt huấn luyện)
docker build -f packages/phasegnn/Dockerfile -t phasegnn:local .
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    pytest packages/phasegnn/tests -q

# Hoặc trực tiếp (đã có torch CPU)
PYTHONPATH=packages/phasegnn/src python -m pytest packages/phasegnn/tests -q
```

## Huấn luyện

```bash
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python -m phasegnn.train --variant phasegnn --epochs 20 --delta-h 12 \
        --out-dir results/E2/phasegnn
```

Tham số quan trọng:

| Tham số | Ý nghĩa |
|---|---|
| `--variant` | `phasegnn`, `B3`, `B4`, `B5a`, `B5b` |
| `--delta-h` | bước lưới giờ (mặc định 1). `12` = nửa chu kỳ M2 (2 mốc/ngày) → nhanh gấp ~12× trên CPU; `1` = lưới giờ đầy đủ (336 bước cho 14 ngày) |
| `--max-train-samples` / `--max-val-samples` | giới hạn số mẫu (chạy nhanh để thử) |
| `--subsample head\|spread` | cách lấy N mẫu: `head` = N mẫu đầu (mặc định cũ; với N nhỏ chỉ có BEN_LUC), `spread` = cách đều mọi trạm |
| `--subgraph-version 1\|2\|3` | cách dựng đồ thị con (`build_station_subgraph`): `2` là **mặc định lượt mới** (đi cùng `--tau-mode phase`; ablation 25/11/2026: v2+phase tốt nhất, v3 kém v2 ~0.016 m). `3` = **rút gọn đường đi**: chỉ giữ trạm, nút biển và các hồ chứa ở thượng nguồn, nối thẳng vào trạm với τ = tổng τ dọc đường (cạnh `down` hồ → trạm, `up` biển → trạm); nút biển nhận `astro_*`, nút hồ nhận `res_*` (tổng các hồ — G5 chưa có số liệu từng hồ). `2` = đường biển → trạm có cạnh `down`; `1` = hành vi trước 25/11/2026 (thiếu cạnh `down`). Ghi trong checkpoint; checkpoint cũ không có trường này được nạp với `1` |
| `--tau-mode phase\|grid` | τ tác động lên thông điệp: `phase` (mặc định lượt mới) = dịch trên lưới Δ **cộng** nhúng pha liên tục `P·[sin,cos](2πτ/T_M2), [sin,cos](2πτ/24 h)`; `grid` = chỉ dịch trên lưới Δ (hành vi cũ; với τ ≪ Δ thì δ không học). Checkpoint cũ ⇒ `grid` |
| `--no-mlflow` / `--no-register` | tắt ghi MLflow / bản ghi MongoDB (khi không có dịch vụ) |
| `--device auto\|cpu\|cuda\|cuda:N` | thiết bị (`phasegnn.device.resolve_device`). **Mặc định `cpu`** để chạy lại đúng số liệu thực nghiệm cũ (mọi kết quả `results/E2` trước 25/11/2026 huấn luyện trên CPU); `auto` = `cuda` nếu torch thấy GPU, không thì `cpu`; `cuda` mà không có GPU ⇒ dừng với lỗi rõ |
| `--deterministic` | bật `torch.use_deterministic_algorithms` + `cudnn.deterministic` (chậm hơn; tái lập trên GPU) |

Mỗi lần chạy ghi `checkpoint_seed<N>.pt`, `train_log_seed<N>.json`,
`model_record_seed<N>.json` vào `--out-dir`. Thiết bị thực dùng ghi trong `config.device` +
`config.device_info` (`train_log`) và `train_config.device`/`train_config.device_info` (`model_record`).
Checkpoint luôn nạp lên CPU (`forecast.load_checkpoint`, `map_location="cpu"`) nên checkpoint huấn luyện
trên GPU dùng được trên máy không có GPU.

## Chạy trên GPU

Image `phasegnn:local` là torch **CPU**. GPU dùng image riêng `phasegnn:cuda`
(`Dockerfile.cuda`, cùng mã nguồn, `torch==2.11.0+cu126`; wheel này có sẵn mã cho sm_75 —
Turing, ví dụ RTX 2060).

1. **Cập nhật driver NVIDIA trên Windows** (WSL2 dùng driver của Windows; không cài driver trong
   WSL hay container). Cần ≥ 560.76 (CUDA 12.6) — tối thiểu lý thuyết ≥ 528.33 nhờ tương thích phiên
   bản phụ CUDA 12.x; driver 46x (CUDA 11.x) **không** chạy được. Sau đó `wsl --update`
   (kernel WSL ≥ 5.10.43), bật lại Docker Desktop (backend WSL2). Kiểm tra trên Windows: `nvidia-smi`.
2. **Build và kiểm tra GPU trong container:**

   ```bash
   docker build -f packages/phasegnn/Dockerfile.cuda -t phasegnn:cuda .
   docker run --rm --gpus all phasegnn:cuda python -c "import torch;print(torch.cuda.is_available())"
   docker run --rm --gpus all phasegnn:cuda python -c "import torch;print(torch.cuda.get_device_name(0), torch.cuda.get_arch_list())"
   ```

   Kỳ vọng `True` và danh sách arch có `sm_75`.
3. **Huấn luyện trên GPU:**

   ```bash
   docker run --rm --gpus all -v "$PWD:/work" -w /work phasegnn:cuda \
       python -X utf8 -m phasegnn.train --variant phasegnn --epochs 20 --device cuda \
           --no-mlflow --out-dir results/E2/phasegnn_cuda
   ```

   Thêm `--deterministic` nếu cần tái lập trên GPU. Số trên GPU có thể lệch nhẹ so với CPU (thứ tự
   phép cộng dấu phẩy động khác) — so sánh với số cũ thì chạy lại trên CPU.
4. **So sánh tốc độ** (1 epoch nhỏ, cùng dữ liệu/seed, in giây/epoch và chênh lệch val MAE):

   ```bash
   docker run --rm --gpus all -v "$PWD:/work" -w /work phasegnn:cuda \
       python -X utf8 scripts/bench_device.py --epochs 1 --max-train-samples 600 --max-val-samples 200
   ```

5. **Worker GPU** (tùy chọn): dịch vụ `worker-gpu` (profile `gpu`) trong
   `infra/compose/docker-compose.yml`, image `phasegnn:cuda`. Việc `train` nhận tham số `device`
   (`auto|cpu|cuda`); bỏ trống → biến `PGNN_TRAIN_DEVICE` (mặc định `auto`). Dự báo trong worker dùng
   `PGNN_FORECAST_DEVICE` (mặc định `cpu`).

**Ghi chú trung thực về tốc độ.** Mỗi mẫu là một đồ thị con nhỏ (vài chục nút) và vòng huấn luyện xử
lý từng trạm/từng lô nhỏ, đánh giá còn đọc từng giá trị về CPU (`float(...)`) ⇒ phần lớn thời gian là
chi phí khởi chạy kernel và đồng bộ CPU↔GPU, không phải tính toán. GPU **có thể không nhanh hơn nhiều**
(thậm chí chậm hơn với `--delta-h 12`) so với CPU 1 luồng; đo bằng `scripts/bench_device.py` trước khi
chuyển hẳn. Đo ngày 25/11/2026 (RTX 2060, `--delta-h 1` mặc định, 300 mẫu train/100 val `spread`, 1 epoch,
seed 0, CPU 1 luồng trong container `--cpus 2` khi máy đang chạy việc khác): CPU 117,6 s/epoch, CUDA
28,6 s/epoch (nhanh hơn ~4,1×), val MAE đỉnh bằng nhau tới 5 chữ số (0,1774 m). Muốn tăng tốc thật cần **gộp nhiều đồ thị con thành một lô** (block-diagonal, kiểu
`torch_geometric.data.Batch`) và gom phép đánh giá thành phép tensor — **việc tồn**, chưa làm.

## Dự báo và đánh giá

```bash
# Xuất dự báo (theo schemas/prediction/v1.schema.json)
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python -m phasegnn.forecast --checkpoint results/E2/phasegnn/checkpoint_seed0.pt \
        --split test --out-dir results/E2/phasegnn

# Chấm điểm checkpoint trên tập test
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python packages/phasegnn/scripts/evaluate_checkpoint.py \
        --checkpoint results/E2/phasegnn/checkpoint_seed0.pt \
        --split test --max-days 300 --out-dir results/E2/phasegnn

# Chấm điểm các mô hình cơ sở trên **cùng** tập ngày
docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
    python packages/phasegnn/scripts/evaluate_baselines.py \
        --split test --max-days 300 --out-dir results/E2/baselines
```

**Quy ước chấm điểm** (dùng chung cho mọi mô hình, xem `scripts/scoring.py`):

* MAE đỉnh (m) — trung bình |`h_q50` − độ cao đỉnh quan trọc thực đo|;
* MAE giờ (h) — trung bình sai số giờ đỉnh **quanh vòng 24 h**:
  `d = 0.5·|((Δ + 12) mod 24) − 12|` (đỉnh 23:30 và 00:15 chỉ cách nhau 0.75 h).

> **Lưu ý khi chấm.** Bảng quan trọc thực đo phải phủ **mọi** ngày mà dự báo nhắm
> tới (`target_date = issued_date + lead_day − 1`), không chỉ các ngày phát bản tin
> đã chọn — nếu không, các bản ghi `lead_day ≥ 2` sẽ bị bỏ sót và MAE bị lạc quan.

Phụ thuộc: `vngis`, torch, pandas, numpy, pyarrow. Chỉ `apps/worker` và
`pipelines/spark/features` dùng package này.
