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
| `--no-mlflow` / `--no-register` | tắt ghi MLflow / bản ghi MongoDB (khi không có dịch vụ) |

Mỗi lần chạy ghi `checkpoint_seed<N>.pt`, `train_log_seed<N>.json`,
`model_record_seed<N>.json` vào `--out-dir`.

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
