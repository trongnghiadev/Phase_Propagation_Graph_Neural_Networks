# G3 – Thời gian truyền vật lý theo cạnh (`tau_phys_h`)

## Câu hỏi trả lời

Mỗi cạnh của đồ thị hai chiều (G1) cần một thời gian truyền sóng để dựng bước
lan truyền triều. Câu hỏi: với mô hình Manning (sóng lũ) và sóng thuỷ tĩnh
(sóng triều), τ là bao nhiêu, và tổng τ từ Vũng Tàu tới mỗi trạm là bao nhiêu?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Số cạnh xử lý | 1291 |
| Reach có `slope = 0` được thay | 150 |
| `n` (hệ số nhám Manning) | proxy tài liệu hoá trong docstring `features/tau_phys.py` |
| `R` (bán kính thuỷ lược) | proxy từ `uparea` và chiều dài reach |
| Kẹp vận tốc | giá trị hợp lý vật lý, xem docstring |

## Kết quả

- Hướng `down`: 645 cạnh; τ min 0.0051 h, trung vị 0.4609 h, max 12.7526 h; celerity trung vị 4.940 m/s.
- Hướng `up`: 646 cạnh; τ min 0.0717 h, trung vị 2.3019 h, max 50.5791 h; celerity trung vị 0.555 m/s.

Tổng τ từ Vũng Tàu (hướng `up`) tới các trạm/hồ:

| Trạm | Tên | COMID | Loại | τ từ biển (h) |
|---|---|---|---|---|
| NHA_BE | Nhà Bè | 44042931 | water_level | 3.8161 |
| BEN_LUC | Bến Lức | 44043086 | water_level | 5.3537 |
| TAN_AN | Tân An | 44042875 | water_level | 6.1273 |
| PHU_AN | Phú An | 44043114 | water_level | 6.1787 |
| THU_DAU_MOT | Thủ Dầu Một | 44043137 | water_level | 9.7893 |
| BIEN_HOA | Biên Hòa | 44042941 | water_level | 10.2680 |
| GO_DAU_HA | Gò Dầu Hạ | 44043119 | water_level | 19.3233 |
| RES_TRI_AN | Trị An | 44042950 | reservoir | 29.0343 |
| DAU_TIENG | Dầu Tiếng | 44043239 | water_level | 35.4406 |
| RES_DAU_TIENG | Dầu Tiếng | 44043239 | reservoir | 35.4406 |
| RES_SR_P_MIENG | Sr.P.Miêng | 44045216 | reservoir | 55.8908 |
| RES_THAC_MO | Thác Mơ | 44043222 | reservoir | 103.7624 |

## File sinh ra

- `tau_phys_summary.csv` – theo chiều: đếm, min/trung vị/max τ, tổng τ, celerity.
- `tau_from_sea_by_station.csv` – tổng τ từ Vũng Tàu tới từng trạm/hồ.

## Giới hạn

- `n` và `R` là **proxy suy ra từ `uparea`/chiều dài reach**, KHÔNG phải đo đạc
  thực tế của kênh; τ vì vậy là ước lượng cấp độ, dùng để định hình cấu trúc
  lan truyền (độ trễ tương đối giữa các trạm) chứ không phải dự báo thời gian tuyệt đối.
- Sóng triều bị **giảm tốc độ** bởi vận tốc dòng chảy dọc tuyến (sóng đi ngược dòng);
  vận tốc dòng chảy ước lượng từ `uparea`/slope nên có sai số.
- Reach có `slope = 0` dùng trung vị của nhóm `order` cùng cấp; giá trị gốc giữ ở `slope_raw`.

**Ngày chạy:** 2026-11-23 13:44 (run `20261123T134439Z_94b5c1`)
