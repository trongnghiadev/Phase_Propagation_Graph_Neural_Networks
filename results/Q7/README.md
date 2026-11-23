# Q7 – Độ chính xác dự báo nghiệp vụ theo chân trời

## Câu hỏi trả lời

Bản tin dự báo chính thức chính xác bao nhiêu, và độ chính xác giảm thế nào theo chân trời dự báo (lead day 1…5)?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Nguồn dự báo | `official_forecasts` |
| Nguồn quan trắc | `tide_extremes` |
| Cửa sổ ghép | ±6 giờ quanh mốc dự báo |
| Quy tắc khi nhiều quan trắc | lấy quan trắc GẦN NHẤT theo `ts` |
| Số bản tin | 38410 |
| Số dự báo | 153640 |
| Số cặp khớp | 145479 |

## Kết quả

- `official_forecasts`: 38410 bản tin → 153640 dự báo (đỉnh + chân).
- `tide_extremes` có giá trị: 121750; cặp khớp trong ±6 h: 145479 (94.7% dự báo có quan trắc đối chiếu).
- lead_day 1: 29045 cặp / 3 trạm; MAE chiều cao 0.0489 m; MAE chiều giờ đỉnh 0.3371 h; sai số lớn nhất 3.1 m.
- lead_day 2: 29139 cặp / 3 trạm; MAE chiều cao 0.0872 m; MAE chiều giờ đỉnh 0.5902 h; sai số lớn nhất 3.1 m.
- lead_day 3: 29122 cặp / 3 trạm; MAE chiều cao 0.1066 m; MAE chiều giờ đỉnh 0.7504 h; sai số lớn nhất 3.02 m.
- lead_day 4: 29090 cặp / 3 trạm; MAE chiều cao 0.1186 m; MAE chiều giờ đỉnh 0.8826 h; sai số lớn nhất 3.1 m.
- lead_day 5: 29083 cặp / 3 trạm; MAE chiều cao 0.1276 m; MAE chiều giờ đỉnh 0.9856 h; sai số lớn nhất 31.35 m.
- Trạm PHU_AN: 68263 cặp (2017–2026), MAE cao 0.0934 m, MAE giờ đỉnh 0.6971 h.
- Trạm NHA_BE: 68045 cặp (2017–2026), MAE cao 0.1011 m, MAE giờ đỉnh 0.7165 h.
- Trạm THU_DAU_MOT: 9171 cặp (2025–2026), MAE cao 0.1063 m, MAE giờ đỉnh 0.7456 h.

## File sinh ra

- `q7_by_lead_day.csv`
- `q7_by_station_lead.csv`
- `q7_error_distribution.csv`
- `q7_by_station.csv`

## Giới hạn

- MAE giờ đỉnh tính trên TOÀN BỘ cặp (đỉnh và chân), không chỉ đỉnh.
- Thủ Dầu Một chỉ có dự báo từ giữa 2025 → cột `year_first` cho thấy kỳ dữ liệu; không so sánh trực tiếp với các trạm có dữ liệu từ 2017.
- Dự báo không có quan trắc đối chiếu trong cửa sổ ±6 h bị LOẠI khỏi MAE (đếm ở `n_pairs`), không coi là sai số 0.

**Ngày chạy:** 2026-11-23 18:24 (UTC+7)
