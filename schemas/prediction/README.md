# schemas/prediction/ – Định dạng dự báo chung

**Chức năng:** JSON Schema của bản ghi dự báo mà **mọi** mô hình phải xuất ra, để một hàm đánh giá (`pipelines/r/evaluation.R`) dùng được cho tất cả (đề cương 4.5, checklist GĐ4.5).

Trường dự kiến: `model`, `station`, `issued_date`, `target_date`, `lead_day` (ngày phát bản tin = 1), `peak_idx`, `h_q10`, `h_q50`, `h_q90`, `t_peak`.

File: `v1.schema.json` (tạo khi chốt định dạng ở GĐ4).
