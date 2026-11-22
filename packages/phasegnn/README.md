# packages/phasegnn/ – Mô hình PhaseGNN

**Chức năng:** mô hình GNN lan truyền pha hai chiều (đề cương 5.2) và các mô hình so sánh.

| Module (dự kiến) | Nội dung |
|---|---|
| `data.py` | đọc Parquet G5 và `graph_edges`, dựng đồ thị PyG hai chiều, gom nút theo mức topo |
| `layers.py` | `PhaseShift` (dịch pha khả vi), `PhaseMessage` (down/up), `TopoGRU` hai lượt |
| `model.py` | `PhaseGNN`, các biến thể B4 (τ cố định), B5 (một chiều) |
| `losses.py` | pinball, L_peak (soft-max), L_timing (soft-argmax, khoảng cách vòng), L_τ, L_mono |
| `baselines/` | B0 persistence, B_harm, B1 hồi quy phần dư, B2 LSTM, B3 GraphSAGE + GRU |
| `train.py` | huấn luyện, Optuna, MLflow; xuất dự báo theo `schemas/prediction/` |

**Kiểm thử bắt buộc trước khi huấn luyện thật** (`tests/`): dịch xung đúng τ, lan truyền topo đúng Σ τ, học lại τ trên dữ liệu tổng hợp (sai số < 20%).

Phụ thuộc: `vngis`, torch, torch_geometric. Chỉ `apps/worker` và `pipelines/spark/features` dùng package này.
