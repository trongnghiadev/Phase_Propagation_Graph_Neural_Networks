# Q6 – Đường đi từ mỗi trạm tới cửa sản

## Câu hỏi trả lời

Mỗi trạm nằm ở đâu trong cây lưu vực? Đường đi tới cửa sản dài bao nhiêu? Các trạm có trên cùng một nhánh không?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| GraphFrames | KHÔNG có trong image → BFS bằng Spark thuần |
| Nguồn đồ thị | `graph_edges` (G1) |
| Nguồn MERIT | `merit_river_reaches` |
| Gốc lưu vực | 44045960 |
| Đường đi xuôi | theo `NextDownID` (duy nhất cho mỗi trạm) |
| Cùng nhánh | chia sẻ ít nhất một reach trên đường đi xuôi |

## Kết quả

- GraphFrames KHÔNG có trong image `gis-spark:local` (đã kiểm tra `/opt/spark/jars` và module Python) → BFS bằng Spark thuần.
- Cây lưu vực 44045960: 1073 reach; `graph_edges`: 1291 cạnh.
- Trạm có COMID trong cây: 12 / 14.
- Cặp trạm trên cùng nhánh (chia sẻ đường đi xuôi): 66.
- Trạm RES_THAC_MO (Thác Mơ) COMID 44043222: 50 reach tới cửa sản, 414.8298 km, lũy kế độ dốc 0.01702673, 50 điểm hội tụ phía trên.
- Trạm RES_SR_P_MIENG (Sr.P.Miêng) COMID 44045216: 39 reach tới cửa sản, 302.1781 km, lũy kế độ dốc 0.01227969, 38 điểm hội tụ phía trên.
- Trạm DAU_TIENG (Dầu Tiếng) COMID 44043239: 26 reach tới cửa sản, 200.3361 km, lũy kế độ dốc 0.00417034, 26 điểm hội tụ phía trên.
- Trạm RES_DAU_TIENG (Dầu Tiếng) COMID 44043239: 26 reach tới cửa sản, 200.3361 km, lũy kế độ dốc 0.00417034, 26 điểm hội tụ phía trên.
- Trạm RES_TRI_AN (Trị An) COMID 44042950: 22 reach tới cửa sản, 171.2755 km, lũy kế độ dốc 0.00647382, 22 điểm hội tụ phía trên.
- Trạm GO_DAU_HA (Gò Dầu Hạ) COMID 44043119: 19 reach tới cửa sản, 157.5744 km, lũy kế độ dốc 0.00248341, 19 điểm hội tụ phía trên.
- Trạm THU_DAU_MOT (Thủ Dầu Một) COMID 44043137: 18 reach tới cửa sản, 105.4694 km, lũy kế độ dốc 0.00135235, 18 điểm hội tụ phía trên.
- Trạm BIEN_HOA (Biên Hòa) COMID 44042941: 16 reach tới cửa sản, 110.3503 km, lũy kế độ dốc 0.00192205, 16 điểm hội tụ phía trên.

## File sinh ra

- `q6_station_paths.csv`
- `q6_station_paths_detail.csv`
- `q6_shared_branches.csv`

## Giới hạn

- Do MERIT có hội tụ, từ cửa sản có NHIỀU đường đi `up` tới một trạm; cột `max_tau_up_h` là τ lớn nhất trên đường đi xuôi, KHÔNG phải tổng τ mọi nhánh.
- Độ dốc lũy kế là TỔNG `slope` của các reach trên đường đi (đơn vị m/m) — trạm thấp hơn có tổng lớn hơn.

**Ngày chạy:** 2026-11-23 20:34 (UTC+7)
