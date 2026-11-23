# Q4 – Toàn vẹn dữ liệu và topology

## Câu hỏi trả lời

Dữ liệu bản tin đầy đủ không (thiếu/trùng ngày)? QC ra sao? Đồ thị MERIT có giữ được toàn vẹn topology không?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Bản tin | `hydromet_bulletins` |
| Triều | `tide_extremes` |
| MERIT | `merit_river_reaches` |
| Gốc lưu vực | 44045960 |
| Đồ thị đã dựng | `graph_edges` (G1) |

## Kết quả

- Bản tin có ngày quan sát: 32706 bản tin; 63 ngày thiếu trên tổng 32418 ngày lịch trong khoảng quan sát.
- Ngày trùng (nhiều bản tin cùng trạm + ngày): 351 cặp.
- `tide_extremes`: 129420 phần tử; 121337 đạt QC ok (không cờ), 378 đạt QC ok nhưng có cờ.
- Topology cây 44045960: 1073 reach, 535 điểm hội tụ, 0 lỗi; `graph_edges` có 1291 cạnh / 646 nút.
- Trạm Biên Hòa: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Bến Lức: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Dầu Tiếng: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Gò Dầu Hạ: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Nhà Bè: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Phú An: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Thủ Dầu Một: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Tân An: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- Trạm Vũng Tàu: thiếu 7 ngày trong 2016-12-31→2026-11-10 (7/3602 ngày).
- QC `ok` / `(null)`: 121337 phần tử.
- QC `not_occurred` / `(null)`: 4105 phần tử.
- QC `missing` / `(null)`: 3391 phần tử.
- QC `ok` / `suspect`: 343 phần tử.
- QC `no_data` / `(null)`: 86 phần tử.
- QC `no_value` / `(null)`: 75 phần tử.
- QC `ok` / `confirmed_ok`: 35 phần tử.
- QC `bad_time` / `(null)`: 22 phần tử.

## File sinh ra

- `q4_missing_days.csv`
- `q4_duplicate_bulletins.csv`
- `q4_qc_distribution.csv`
- `q4_topology_checks.csv`

## Giới hạn

- Ngày thiếu được tính trong khoảng [min_day, max_day] của từng trạm — trạm mới tham gia giữa chừng sẽ có 'ngày thiếu' trước thời điểm bắt đầu.
- Ngày trùng là do nhiều bản tin phát cho cùng một trạm trong một ngày (bản tin 05h và bản tin 17h), KHÔNG phải lỗi dữ liệu.
- Điểm hội tụ (nhiều reach đổ về cùng một hạ nguồn) là đặc tính hợp lệ của lưu vực thật, không phải lỗi topology.

**Ngày chạy:** 2026-11-23 20:36 (UTC+7)
