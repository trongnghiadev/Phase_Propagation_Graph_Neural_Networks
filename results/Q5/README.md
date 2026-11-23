# Q5 – Ghép OSM ↔ MERIT theo khoảng cách không gian

## Câu hỏi trả lời

Các đoạn sông OSM có khớp reach MERIT trong lưu vực không? Ở ngưỡng nào tỉ lệ khớp cao nhất?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Sedona | KHÔNG có trong image → dùng Spark thuần (prefilter bbox + khoảng cách chính xác giữa hai đoạn thẳng) |
| Nguồn OSM | `waterways_segments` |
| Nguồn MERIT | `merit_river_reaches` |
| Gốc lưu vực | 44045960 |
| Ngưỡng | 50 m, 100 m, 250 m |
| Bán kính Trái Đất | 6371008.8 |

## Kết quả

- Sedona KHÔNG có trong image `gis-spark:local` (kiểm tra `/opt/spark/jars`, Ivy cache và module Python `sedona`) → spatial join làm bằng Spark thuần: prefilter bbox + khoảng cách chính xác giữa hai đoạn thẳng (chiếu equirectangular cục bộ).
- Reach MERIT trong cây 44045960 có hình học: 1073; đoạn OSM có hình học: 38877.
- Ứng viên sau prefilter bbox: 7741; cặp khớp trong 250.0 m: 4331.
- Ngưỡng 50 m: 3199 cặp khớp; OSM có khớp 1950/38877 (5.016%); MERIT có khớp 872/1073 (81.267%); khoảng cách trung bình 7.53 m.
- Ngưỡng 100 m: 3683 cặp khớp; OSM có khớp 2140/38877 (5.505%); MERIT có khớp 898/1073 (83.691%); khoảng cách trung bình 15.83 m.
- Ngưỡng 250 m: 4331 cặp khớp; OSM có khớp 2419/38877 (6.222%); MERIT có khớp 923/1073 (86.021%); khoảng cách trung bình 38.05 m.

## File sinh ra

- `q5_spatial_matches.csv`
- `q5_buffer_summary.csv`
- `q5_osm_unmatched.csv`

## Giới hạn

- **Sedona không có sẵn** trong `gis-spark:local` — đã kiểm tra `/opt/spark/jars`, Ivy cache và module Python. Cách thay thế được tài liệu hoá rõ, KHÔNG thay âm thầm.
- Khoảng cách dùng phép chiếu equirectangular cục bộ quanh vĩ độ của **từng cặp** (sai số < 0,1 % ở vĩ độ 10–11°), tức dưới 1 m — không ảnh hưởng ngưỡng 50/100/250 m.
- Hình học được **rút gọn Douglas–Peucker** với sai số 0,0005° (≈ 55 m) trước khi so sánh; sai số này nhỏ hơn ngưỡng khớp nhỏ nhất (50 m) nên không đổi kết luận khớp/không khớp.
- Cả hai nguồn lưu đường thẳng nối các đỉnh (không phải đường cong), gây sai số vài mét trên reach dài.
- `waterways_segments` là toàn bộ CSDL OSM khu vực, KHÔNG lọc theo lưu vực — nên tỉ lệ bao phủ thấp là bình thường.

**Ngày chạy:** 2026-11-23 19:46 (UTC+7)
