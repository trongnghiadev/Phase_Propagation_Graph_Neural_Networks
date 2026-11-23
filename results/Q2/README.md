# Q2 – Lưu lượng xả hồ chứa theo mùa và số ngày xả tràn

## Câu hỏi trả lời

Các hồ xả nước (qua máy, qua đập tràn) ra sao theo mùa? Bao nhiêu ngày xả tràn?

## Cấu hình chạy

| Tham số | Giá trị |
|---|---|
| Nguồn | `reservoir_daily` |
| Mùa mưa | tháng 5–10 |
| Mùa khô | tháng 11–4 |
| Định nghĩa xả tràn | `spillway_discharge_m3s > 0` |
| Số bản ghi | 15313 |
| Số hồ | 7 |

## Kết quả

- `reservoir_daily`: 15313 bản ghi, 7 hồ.
- Tổng số ngày xả tràn (mọi hồ, mọi năm): 2725 / 15313 ngày-dữ liệu.
- Hồ RES_DAU_TIENG năm 2025: 320 ngày xả tràn, 0 ngày qua máy; TB xả 43.011 m³/s, max 200.0 m³/s, thể tích 1.3564 tỷ m³.
- Hồ RES_DA_DEN năm 2026: 291 ngày xả tràn, 303 ngày qua máy; TB xả 6.899 m³/s, max 770.0 m³/s, thể tích 0.1854 tỷ m³.
- Hồ RES_DAU_TIENG năm 2026: 234 ngày xả tràn, 0 ngày qua máy; TB xả 32.535 m³/s, max 65.9 m³/s, thể tích 0.8827 tỷ m³.
- Hồ RES_DAU_TIENG năm 2023: 222 ngày xả tràn, 0 ngày qua máy; TB xả 32.726 m³/s, max 200.0 m³/s, thể tích 1.0264 tỷ m³.
- Hồ RES_DAU_TIENG năm 2024: 206 ngày xả tràn, 0 ngày qua máy; TB xả 29.837 m³/s, max 200.0 m³/s, thể tích 0.9435 tỷ m³.

## File sinh ra

- `q2_reservoir_by_season.csv`
- `q2_reservoir_by_month.csv`
- `q2_reservoir_yearly.csv`

## Giới hạn

- `reservoir_daily` chỉ ghi nhận các hồ có trong bản tin KTTV của TP.HCM (Trị An, Thác Mơ, Dầu Tiếng, Sr. P. Miêng…) — không phải toàn bộ hồ lớn của lưu vực.
- `n_days_turbine` và `n_days_spillway` có thể cùng = 1 (xả cả hai kênh trong ngày).

**Ngày chạy:** 2026-11-23 17:24 (UTC+7)
