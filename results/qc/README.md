# results/qc/ – Báo cáo QC số liệu bản tin (GĐ2.3)

Chỉ chứa số tổng hợp, không tên người, không chép nội dung bản tin.

| File | Nội dung | Commit |
|---|---|---|
| `summary.csv` | trạm × năm × quy tắc → số cờ theo `status` | có |
| `exceedance.csv` | số ngày đỉnh triều vượt cấp báo động I/II/III theo trạm × năm (K9) | có |
| `runs/<run_id>/` | từng lần chạy: `flags.csv` (danh sách cờ), `run.json` (tham số, thống kê) | không (gitignore) |
| `manual/` | kết quả kiểm tra tay K1–K6 (do người kiểm tra ghi; job không đụng tới); đã áp dụng 23/11/2026 qua CLI, có `audit_logs` | theo người kiểm tra |

## Cách tạo

Job Spark `pipelines/spark/qc/qc_tide_extremes.py` (xem `pipelines/spark/README.md`, mục QC) với `--report-dir`
trỏ vào thư mục này: chạy quy tắc R1–R3 trên `tide_extremes`, gộp vào `qc_flags`, rồi ghi lại `summary.csv`,
`exceedance.csv` (ghi đè; nội dung chỉ phụ thuộc dữ liệu, `qc_flags` và tham số) và một thư mục `runs/<run_id>/`.
Quyết định của người ghi bằng CLI `pipelines/qc/qc_decide.py` (kèm `audit_logs`); chạy lại job để cập nhật báo cáo.

Thứ tự khuyến nghị: G0 → QC → ghi quyết định (CLI) → G0 (áp quyết định vào `tide_extremes`) → QC (báo cáo).

## `summary.csv`

`station, year, rule, suspect, confirmed_ok, rejected, corrected, total, auto_cleared`

- `year`: năm theo giờ Việt Nam của thời điểm phần tử (`ts`).
- `rule`: `R1` (ngoài khoảng hợp lý của trạm), `R2` (đỉnh thấp nhất thấp hơn chân cao nhất cùng ngày; chỉ gắn cặp cực trị), `R3` (giờ đỉnh lệch so với
  Vũng Tàu + độ trễ điển hình), `manual` (cờ thủ công không có quy tắc nào), `any` (mỗi cờ đếm một lần).
  Cờ có nhiều quy tắc được đếm ở mỗi quy tắc, nên tổng các dòng R1–R3 có thể lớn hơn dòng `any`.
- Chỉ đếm cờ có hiệu lực (`vngis.qc_rules.is_active`): không `retired`, và có quyết định (`confirmed_ok`/`rejected`/
  `corrected`), hoặc cờ thủ công, hoặc `suspect` mà quy tắc vẫn kích hoạt. `auto_cleared` = số cờ đã có quyết định nhưng quy tắc không còn kích hoạt (dữ liệu hoặc tham số đổi).
- Tham số quy tắc: `config/default.yaml` mục `qc` (`rules_version`).

## `exceedance.csv`

`station, year, days_with_levels, days_with_peak, ge_level_1, ge_level_2, ge_level_3, days_level_3_undefined`

- Một ngày = một ngày quan trắc (một bản tin chính). Đỉnh của ngày = đỉnh triều thực đo cao nhất trong ngày, dùng giá
  trị sau QC: `rejected` bị bỏ, `corrected` dùng giá trị sửa, `suspect`/`confirmed_ok` giữ nguyên.
- Cấp báo động lấy từ **chính bản tin của ngày đó** (`hydromet_bulletins.alarm_levels`, cùng nguồn với
  `stations.alarm_history`), không dùng ngưỡng cố định: Phú An 1,3/1,4/1,5 m đến 18/03/2020, sau đó 1,4/1,5/1,6 m;
  Nhà Bè từ 19/03/2020; Thủ Dầu Một từ 25/07/2025. Chỉ trạm và ngày có cấp báo động mới được tính.
- `days_with_levels`: số ngày bản tin có cấp báo động cho trạm; `days_with_peak`: trong đó, số ngày có đỉnh;
  `ge_level_k`: số ngày đỉnh ≥ cấp k (bằng cấp báo động cũng tính là vượt);
  `days_level_3_undefined`: số ngày bản tin không ghi cấp III (ngày quan trắc 31/12/2024–30/01/2025), không tính ở `ge_level_3`.
