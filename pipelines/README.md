# pipelines/ – Xử lý hàng loạt

**Chức năng:** các bước xử lý dữ liệu chạy theo lô (một lần hoặc định kỳ), tạo dữ liệu cho mô hình và thống kê.

| Thư mục | Ngôn ngữ | Nội dung | Đề cương |
|---|---|---|---|
| [`ingest/`](ingest/) | Python | nạp dữ liệu nguồn vào MongoDB: bản tin KTTV, địa giới, danh mục trạm | Chương 2 |
| [`spark/`](spark/) | PySpark | làm phẳng bản tin (G0), truy vấn Q1–Q7, đồ thị và đặc trưng (G1–G5) | Chương 3 |
| [`qc/`](qc/) | Python | CLI ghi quyết định QC (`qc_flags` + `audit_logs`) qua `vngis.qc` | Chương 2 (2.7), 5.1 |
| [`r/`](r/) | R | thống kê, phân tích điều hòa, độ trễ pha, hàm đánh giá | Chương 4 |

**Thứ tự chạy:** `ingest` → `spark/flatten` (G0) → `spark/qc` → quyết định QC (`qc/`) → `spark/flatten` lại → `spark/analytics`, `r/` → `spark/features` → huấn luyện (`packages/phasegnn`).

Mọi bước ghi lại phiên bản dữ liệu đầu vào (`data_snapshot`) để truy ngược kết quả.
