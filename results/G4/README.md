# G4 – Mưa theo nút đồ thị (fallback dựa trạm)

## Câu hỏi trả lời

Mỗi nút trên đồ thị cần chuỗi mưa ngày để làm đặc trưng đầu vào cho mô hình.
Repo **chưa có sản phẩm mưa vệ tinh** (IMERG/CHIRPS) nên job dùng mưa quan trắc
tại trạm (`rainfall_daily`) và gán theo quy tắc xác định, tái lập được.

## Quy tắc (tài liệu hoá)

1. Mỗi **trạm mưa** được gán cho các nút đồ thị **trong bán kính 40 km** (khoảng cách đường thẳng trên mặt cầu Trái Đất).
2. Trọng số nút `n` – trạm `s`:
   `w(n,s) = [uparea(n) / A(s)] / Σ_{s'∈S(n)} [uparea(n) / A(s')]`
   với `A(s)` = diện tích lưu vực của trạm (`uparea` của reach gắn với trạm).
3. **Chuẩn hoá mỗi nút: tổng trọng số = 1** (job assert).
4. Ma trận trọng số được ghi ra `rain_weights.csv` để kiểm chứng.

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Bán kính gán | 40 km |
| Số nút đồ thị | 646 |
| Nút có ít nhất một trạm mưa | 155 |
| Nút không có trạm mưa | 491 |
| Số trạm mưa | 10 |
| Số dòng nút–ngày | 2322370 |

## Kết quả theo năm

| Năm | Số dòng | Dòng có mưa | TB ngày (mm) | Max ngày (mm) |
|---|---|---|---|---|
| 2016 | 646 | 0 |  |  |
| 2017 | 234498 | 27626 | 10.6390 | 178.1 |
| 2018 | 235144 | 15983 | 9.7228 | 401.0 |
| 2019 | 235790 | 15531 | 8.4611 | 87.0 |
| 2020 | 236436 | 16002 | 10.0966 | 212.0 |
| 2021 | 235144 | 15821 | 9.1163 | 109.0 |
| 2022 | 235144 | 18875 | 8.6412 | 96.2 |
| 2023 | 234498 | 16244 | 9.0513 | 98.9 |
| 2024 | 236436 | 14919 | 11.5173 | 126.3 |
| 2025 | 235790 | 20614 | 9.8117 | 112.0 |
| 2026 | 202844 | 22079 | 10.9835 | 258.0 |

## File sinh ra

- `rain_weights.csv` – ma trận trọng số (nút × trạm mưa) + khoảng cách (km).
- `rain_by_node_summary.csv` – thống kê theo năm.
- `data/processed/features/rain_by_node/year=<YYYY>/part-*.parquet` – mưa ngày theo nút
  (cột `rain_mm_d1`, `rain_mm_d3`, `rain_mm_d7`).

## Giới hạn

- Đây là **fallback dựa trạm**: độ phân giải không đồng đều theo không gian, nút xa trạm
  nhận giá trị của trạm gần nhất nên không mô tả tốt phân bố mưa cục bộ trong lưu vực.
- `A(s)` dùng `uparea` của reach gắn trạm — cần bảng `catchment_area` riêng cho trạm mưa
  khi có.
- Khi có IMERG/CHIRPS: **chỉ thay bảng nguồn đầu vào**, toàn bộ logic trọng số và tích lũy
  giữ nguyên (và có thể bỏ bán kính 40 km vì lưới phủ toàn lưu vực).

**Ngày chạy:** 2026-11-23 13:52 (run `20261123T135130Z_6f995d`)
