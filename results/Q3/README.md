# Q3 – Thống kê MERIT theo `order` và OSM theo `fclass`

## Câu hỏi trả lời

Cấu trúc lưu vực theo MERIT (order, chiều dài, độ dốc, diện tích) và phân bố lớp OSM?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Nguồn MERIT | `merit_river_reaches` |
| Gốc lưu vực | 44045960 |
| Nguồn OSM | `waterways_segments` |
| Số reach trong cây | 1073 |
| Số đoạn OSM | 38877 |

## Kết quả

- MERIT: cây lưu vực 44045960 có 1073 reach (toàn bộ CSDL có 20435 reach ngoài cây).
- Trong cây: tổng chiều dài 9320.301 km, median chiều dài 6.7712 km, median độ dốc 0.00067666, 103 reach có `slope ≤ 0`.
- OSM `waterways_segments`: 38877 đoạn.
- MERIT order 1: 538 reach, tổng 4504.798 km, median 5.7709 km, median slope 0.00134947, slope=0: 39.
- MERIT order 2: 252 reach, tổng 2233.979 km, median 7.4845 km, median slope 0.00064038, slope=0: 26.
- MERIT order 3: 103 reach, tổng 1039.988 km, median 8.8471 km, median slope 0.00046341, slope=0: 12.
- MERIT order 4: 149 reach, tổng 1305.822 km, median 6.9315 km, median slope 0.00032217, slope=0: 17.
- MERIT order 5: 29 reach, tổng 220.278 km, median 5.2977 km, median slope 2.899e-05, slope=0: 9.
- MERIT order 6: 2 reach, tổng 15.437 km, median 5.1203 km, median slope 1.96e-05, slope=0: 0.
- OSM fclass stream: 15552 đoạn (có tên: 1719).
- OSM fclass canal: 13918 đoạn (có tên: 1026).
- OSM fclass river: 7643 đoạn (có tên: 2889).
- OSM fclass drain: 1764 đoạn (có tên: 44).

## File sinh ra

- `q3_merit_by_order.csv`
- `q3_merit_summary.csv`
- `q3_osm_by_fclass.csv`
- `q3_osm_summary.csv`

## Giới hạn

- Cây lưu vực được dựng bằng đúng quy tắc của G1 (đi ngược `up1..up4` từ gốc).
- `waterways_segments` là toàn bộ CSDL OSM của khu vực, KHÔNG lọc theo lưu vực (chưa có spatial join — xem Q5).

**Ngày chạy:** 2026-11-23 17:40 (UTC+7)
