# Q1 – Đỉnh triều và vượt báo động theo trạm/năm/tháng

## Câu hỏi trả lời

Đỉnh triều cao nhất mỗi ngày thay đổi thế nào theo trạm, năm, tháng? Trạm nào hay vượt báo động (I/II/III) và vượt cấp nào?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Nguồn đỉnh | `tide_extremes` (kind=high), qc=ok, qc_flag≠suspect |
| Nguồn ngưỡng | `stations.alarm_history` (theo thời gian) |
| Số phần tử đỉnh đọc | 60591 |
| Số (trạm, ngày) | 32214 |
| Ngưỡng cố định | KHÔNG dùng — luôn tra `alarm_history` theo ngày |

## Kết quả

- `tide_extremes` (high, qc=ok, không suspect): 60591 phần tử → 32214 (trạm, ngày) → 6465 (trạm, ngày) có ngưỡng báo động hiệu lực.
- Trạm có `alarm_history`: 3 / 26; 23 trạm KHÔNG có ngưỡng (cột đếm vượt báo động = 0 và `threshold_known = false` — đây là thiếu dữ liệu ngưỡng, không phải không vượt).
- Trạm PHU_AN: 3585 ngày có đỉnh, TB 1.2289 m, max 1.78 m; vượt I = 496, II = 362, III = 212; ngày có ngưỡng = 3585.
- Trạm NHA_BE: 2406 ngày có đỉnh, TB 1.238 m, max 1.78 m; vượt I = 320, II = 191, III = 123; ngày có ngưỡng = 2406.
- Trạm THU_DAU_MOT: 474 ngày có đỉnh, TB 1.3863 m, max 1.88 m; vượt I = 70, II = 66, III = 96; ngày có ngưỡng = 474.

## File sinh ra

- `q1_peaks_by_station_year.csv`
- `q1_peaks_by_station_year_month.csv`
- `q1_exceedance_summary.csv`

## Giới hạn

- Trạm không có `alarm_history` không có số ngày vượt báo động — phải đọc cột `n_days_with_threshold` để biết phần nào có ngưỡng.
- Ngưỡng lấy theo mốc `from`/`to` của đoạn `alarm_history` (so sánh theo NGÀY, bỏ giờ).
- Đỉnh `high` có thể có nhiều bản ghi trong một ngày (bản tin khác nhau) → lấy `max`.

**Ngày chạy:** 2026-11-23 17:39 (UTC+7)
