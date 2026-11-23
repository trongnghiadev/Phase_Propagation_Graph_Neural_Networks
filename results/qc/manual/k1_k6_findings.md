# Kiểm tra tay K1–K6 – phát hiện và quyết định đề xuất

Ngày kiểm tra: 2026-11-23. Phương pháp, seed và nguồn: xem `README.md`. Mọi quyết định dưới đây là **đề xuất**, chưa ghi vào MongoDB. Bản máy đọc: `proposed_decisions.json`.

## Tóm tắt

| Mục | Kết luận | Mức chắc chắn | Quyết định đề xuất |
|---|---|---|---|
| K1 | 11,18 m có thật trong PDF gốc (lỗi của bản tin, không phải lỗi parse) | cao | `rejected` (1 cờ); giá trị đúng có lẽ là 1,18 m nhưng PDF không thể hiện |
| K2 | 12/12 bản đã xác định được ngày thật. 10 bản: ngày theo nội dung là đúng; 1 bản chính (`2025/11/08`) có tiêu đề chép lại của hôm trước, ngày thật là 08/11/2025; 1 bản `khac/` là bản duy nhất của ngày 30/08/2026 | cao | sửa `bulletin_date` của `2025/11/08`; xem K6 cho phần còn lại |
| K3 | 15/15 mẫu đúng năm; cả 73 bản đều qua 6 điều kiện | cao | không cần sửa |
| K4 | 6/6 bản: 1,30 / 1,40 / 1,50 m tại Phú An, khớp bản tin liền kề và PDF | cao | không cần sửa |
| K5 | 2 bản chép tay (1 là bản scan) khớp 100 % với ảnh PDF | cao | `ok`, không cần sửa |
| K6 | 38 nhóm trùng: 4 cặp bản chính + 34 nhóm bản chính trùng bản `khac/`. Bản đúng của 4 ngày bị mất nằm trong `khac/`; 5 ngày quan trắc đang thiếu trong `tide_extremes` có thể khôi phục | cao | sửa `source.is_primary` (đổi 9 bản), sau đó bỏ quy tắc loại khi dùng hoặc giữ lại làm kiểm tra |

---

## K1 – Biên Hòa đỉnh triều 11,18 m

| Field | Giá trị |
|---|---|
| source_file | `2021/04/18/HCMC_TVHN_20210418.pdf` (bản chính, số 108/2021, `parse.status = ok`, không có cảnh báo) |
| Trạm / loại | `BIEN_HOA` / `high` (đỉnh triều thứ 2) |
| Ngày quan trắc (VN) | 17/04/2021, giờ 09:00 |
| ts UTC | `2021-04-17T02:00:00Z` |
| Giá trị trong DB | `water_level_m = 11.18`, `raw = ["11.18", "9.00"]`; `tide_extremes.qc = "ok"` |

**Bằng chứng**

- `full_text` và chữ PDF đều ghi 11.18 ở ô mực nước đỉnh thứ 2 của dòng Biên Hòa (giờ 9:00; đỉnh thứ 1 là 1,31 m). Trong PDF, "11.18" nằm đúng cột "Mực nước" của đỉnh thứ 2 (x ≈ 268–290, cùng hàng với 1.31 / 19.30 / 9.00). Chuỗi "11.18" chỉ xuất hiện 1 lần trong cả PDF. → Parser đọc đúng; **lỗi nằm trong bản tin gốc** (nhập sai ở bảng Excel nguồn).
- Không có bản `khac/` nào cho ngày này để đối chiếu.
- Chuỗi đỉnh thứ 2 tại Biên Hòa (ngày quan trắc 13→19/04): 1,34 · 1,36 · 1,34 · **11,18** · 1,11 · 1,06 · 1,05. Cùng ngày: Thủ Dầu Một 1,16, Phú An 1,01. Đỉnh thứ 1 cùng ngày 1,31. Phân vị 99 % đỉnh ngày Biên Hòa là 2,01 m.
- Giá trị hợp lý khoảng 1,1–1,25 m; giả thuyết gõ thừa chữ "1" (→ 1,18 m) rất hợp, nhưng PDF không thể hiện giá trị đúng nên không đủ căn cứ để `corrected`.

**Quyết định đề xuất**

| collection | station | kind | ts (UTC) | status | corrected_value | reason |
|---|---|---|---|---|---|---|
| `tide_extremes` | `BIEN_HOA` | `high` | `2021-04-17T02:00:00Z` | `rejected` | `null` | PDF gốc in "11.18" (không phải lỗi parse); vượt xa phân vị 99 % (2,01 m) và chuỗi liền kề (1,05–1,36 m); nhiều khả năng gõ thừa "1" (1,18 m) nhưng PDF không thể hiện giá trị đúng |

Phương án thay thế (người dùng chọn): `corrected`, `corrected_value = 1.18`, mức chắc chắn trung bình (suy luận, không có trong nguồn).

---

## K2 – 12 bản tin `bulletin_date_differs_from_filename`

Cột "Ngày thật" xác định bằng: thứ trong tuần in trong PDF, ngày âm lịch, số bản tin, ngày dự báo đầu tiên, khoảng đo mưa, và `sha256`/số liệu thực đo so với bản tin cùng ngày nội dung.

| # | source_file | Chính? | Ngày tên file | Ngày nội dung (DB) | Bằng chứng | Ngày thật | Tác động tới dữ liệu đang dùng |
|---|---|---|---|---|---|---|---|
| 1 | `2017/02/09/khac/…_Bantinngay09022017pdf.pdf` | không | 2017-02-09 | 2018-02-09 | số 040/2018; "Thứ Sáu" (09/02/2018 là thứ Sáu, 09/02/2017 là thứ Năm); âm lịch 24/12 Đinh Dậu; cùng `sha256` với `2018/02/09/…20180209.pdf` | 2018-02-09 | không (bản `khac/`, là bản sao của bản chính 2018-02-09) |
| 2 | `2018/01/13/khac/…_Bantinngay13012018pdf.pdf` | không | 2018-01-13 | 2019-01-13 | số 013/2019; "Chủ Nhật"; âm lịch 08/12 Mậu Tuất; trùng `sha256` với `2019/01/13/…` | 2019-01-13 | không |
| 3 | `2018/08/12/khac/…_Bantinngay1282018pdf.pdf` | không | 2018-08-12 | 2019-08-12 | số 224/2019; "Thứ Hai"; âm lịch 12/7 Kỷ Hợi; trùng `sha256` với `2019/08/12/…` | 2019-08-12 | không |
| 4 | `2018/12/10/khac/…_BTinPCTT101218pdf.pdf` | không | 2018-12-10 | 2017-12-10 | số 344/2017; "Chủ Nhật"; âm lịch 23/10 Đinh Dậu; trùng `sha256` với `2017/12/10/…` | 2017-12-10 | không |
| 5 | `2019/01/10/HCMC_TVHN_20190110.pdf` | **có** | 2019-01-10 | 2020-01-10 | số 010/2020; "Thứ Sáu"; âm lịch 16/12 Kỷ Hợi; trùng `sha256` với `2020/01/10/…` | 2020-01-10 | đã bị loại bởi `duplicate_exclusions`; nhưng **bản thật của 10/01/2019** (`2019/01/10/khac/…_Bantinngay10012019pdf.pdf`, số 010/2019) không được dùng → ngày quan trắc 2019-01-09 thiếu trong `tide_extremes` (xem K6) |
| 6 | `2019/02/11/HCMC_TVHN_20190211.pdf` | **có** | 2019-02-11 | 2020-02-11 | số 042/2020; "Thứ Ba"; âm lịch 18/1 Canh Tý; trùng `sha256` với `2020/02/11/…` | 2020-02-11 | như #5; bản thật nằm ở `2019/02/11/khac/…` (042/2019); thiếu ngày quan trắc 2019-02-10 |
| 7 | `2020/02/07/HCMC_TVHN_20200207.pdf` | **có** | 2020-02-07 | 2021-02-07 | số 038/2021; "Chủ Nhật"; âm lịch 26/12 Canh Tý; trùng `sha256` với `2021/02/07/…` | 2021-02-07 | như #5; bản thật ở `2020/02/07/khac/…` (038/2020); thiếu ngày quan trắc 2020-02-06 |
| 8 | `2020/07/05/HCMC_TVHN_20200705.pdf` | **có** | 2020-07-05 | 2020-07-06 | số 188/2020; "Thứ Hai"; âm lịch 16/5; trùng `sha256` với `2020/07/06/…` | 2020-07-06 | như #5; bản thật ở `2020/07/05/khac/…_HOCMTVHN202007051030pdf.pdf` (187/2020); thiếu ngày quan trắc 2020-07-04 |
| 9 | `2021/09/22/khac/…_Bntinngy2292021pdf.pdf` | không | 2021-09-22 | 2021-09-21 | số 264/2021; "Thứ Ba"; âm lịch 15/8; trùng `sha256` với `2021/09/21/…` | 2021-09-21 | không (bản chính 2021-09-22 số 265/2021 có đủ) |
| 10 | `2022/02/03/khac/…_Bntinngy03022022pdf.pdf` | không | 2022-02-03 | 2023-02-03 | số 034/2023; "Thứ Sáu"; âm lịch 13/1 Quý Mão; trùng `sha256` với `2023/02/03/…` | 2023-02-03 | không |
| 11 | `2025/08/30/khac/…_Bntinngy3082025pdf.pdf` | không | 2025-08-30 | 2026-08-30 | số 242/2026 (liền giữa 241/2026 và 243/2026); "Chủ Nhật" (30/08/2026); âm lịch 18/7 Bính Ngọ (17/7 và 19/7 ở hai bản liền kề); `sha256` **không trùng** file nào | 2026-08-30 | **không có bản chính `2026/08/30/`** → đây là bản duy nhất của ngày 30/08/2026; ngày quan trắc 2026-08-29 đang thiếu trong `tide_extremes` (0 document) |
| 12 | `2025/11/08/HCMC_TVHN_20251108.pdf` | **có** | 2025-11-08 | 2025-11-07 | Tiêu đề in số 311/2025, "Thứ Sáu ngày 07/11", âm lịch 18/9 – giống hệt bản 2025-11-07. Nhưng số liệu thuộc ngày 08/11: bảng thực đo ghi ngày 07/11/2025 (bản 07/11 ghi 06/11/2025), mưa "từ 7h 07/11 đến 7h 08/11", dự báo 08/11→12/11; số liệu thực đo khác bản 07/11 (Vũng Tàu đỉnh 4,20/4,25 so với 4,30/4,29); bản kế tiếp là 313/2025 → số 312/2025 bị thiếu | 2025-11-08 (số đúng là 312/2025, suy luận) | `tide_extremes` đúng (theo `observation_date` 07/11, không trùng khóa); `official_forecasts`, `reservoir_daily` đã sửa nhờ `issued_date_note`. **Còn sai:** `hydromet_bulletins.bulletin_date` = 2025-11-07 → hai bản chính cùng `bulletin_date` 07/11, không bản nào ngày 08/11 |

**Quyết định đề xuất K2**

- #12: sửa trong `hydromet_bulletins` (`source_file = 2025/11/08/HCMC_TVHN_20251108.pdf`): `bulletin_date` → 2025-11-08 (VN, = `2025-11-07T17:00:00Z`), `weekday` → "Thứ Bảy", `bulletin_seq` → 312, `bulletin_no` → "312/2025", `lunar_date.day` → 19; giữ giá trị gốc trong ghi chú/`audit_logs` (bản tin in sai tiêu đề). Mức chắc chắn: ngày – cao; số 312 – trung bình (suy từ chuỗi 311 → ? → 313); âm lịch 19/9 – suy từ 18/9 của ngày trước.
- #5–#8: xử lý ở K6.
- #11: xử lý ở K6.
- #1–#4, #9, #10: không đổi gì (bản `khac/`, ngày theo nội dung đã đúng, là bản sao của bản chính).

---

## K3 – 73 bản tin `year_truncated_taken_from_bulletin_no`

73 bản (72 bản chính): năm 2017: 35, 2020: 9, 2021: 10, 2022: 9, 2023: 10. Dòng ngày trong PDF bị cắt, ví dụ "… tháng 3 năm 20" hoặc "… năm 202".

Mẫu ngẫu nhiên, `seed = 20261123` (`random.sample(range(73), 15)` trên danh sách sắp theo `source_file`). Cột "6 điều kiện": năm = năm trong số bản tin; ngày = ngày tên file; thứ khớp ngày; `bulletin_seq` = thứ tự ngày trong năm; dự báo bắt đầu đúng ngày bản tin; `observation_date` = ngày bản tin − 1.

| # | source_file | Số bản tin | Thứ (PDF) | Âm lịch (PDF) | Số liền trước / sau | 6 điều kiện |
|---|---|---|---|---|---|---|
| 1 | `2017/03/18/HCMC_TVHN_20170318.pdf` | 077/2017 | Thứ Bảy | 21/2 | 076 / 078 | đạt |
| 2 | `2017/03/26/HCMC_TVHN_20170326.pdf` | 085/2017 | Chủ Nhật | 29/2 | 084 / 086 | đạt |
| 3 | `2017/04/02/HCMC_TVHN_20170402.pdf` | 092/2017 | Chủ Nhật | 6/3 | 091 / 093 | đạt |
| 4 | `2017/04/09/HCMC_TVHN_20170409.pdf` | 099/2017 | Chủ Nhật | 13/3 | 098 / 100 | đạt |
| 5 | `2017/04/15/HCMC_TVHN_20170415.pdf` | 105/2017 | Thứ Bảy | 19/3 | 104 / 106 | đạt |
| 6 | `2017/04/30/HCMC_TVHN_20170430.pdf` | 120/2017 | Chủ Nhật | 5/4 | 119 / 121 | đạt |
| 7 | `2017/05/13/HCMC_TVHN_20170513.pdf` | 133/2017 | Thứ Bảy | 18/4 | 132 / 134 | đạt |
| 8 | `2017/05/20/HCMC_TVHN_20170520.pdf` | 140/2017 | Thứ Bảy | 25/4 | 139 / 141 | đạt |
| 9 | `2017/05/21/HCMC_TVHN_20170521.pdf` | 141/2017 | Chủ Nhật | 26/4 | 140 / 142 | đạt |
| 10 | `2017/06/04/HCMC_TVHN_20170604.pdf` | 155/2017 | Chủ Nhật | 10/5 | 154 / 156 | đạt |
| 11 | `2020/12/13/HCMC_TVHN_20201213.pdf` | 348/2020 | Chủ Nhật | 29/10 | 347 / 349 | đạt |
| 12 | `2021/10/24/HCMC_TVHN_20211024.pdf` | 297/2021 | Chủ Nhật | 19/9 | 296 / 298 | đạt |
| 13 | `2021/11/14/HCMC_TVHN_20211114.pdf` | 318/2021 | Chủ Nhật | 10/10 | 317 / 319 | đạt |
| 14 | `2021/11/21/HCMC_TVHN_20211121.pdf` | 325/2021 | Chủ Nhật | 17/10 | 324 / 326 | đạt |
| 15 | `2023/10/15/HCMC_TVHN_20231015.pdf` | 288/2023 | Chủ Nhật | 1/9 | 287 / 289 | đạt |

Kiểm tra thứ trong tuần là phép thử độc lập mạnh nhất: nếu năm sai thì thứ in trong PDF không khớp (ví dụ 18/03/2017 là thứ Bảy, 18/03/2018 là Chủ Nhật). Ngoài mẫu: **73/73** bản đạt cả 6 điều kiện.

**Quyết định đề xuất:** không sửa. Có thể giữ cảnh báo trong `parse.warnings` (thông tin), đánh dấu K3 đã kiểm. Mức chắc chắn: cao.

---

## K4 – 6 bản tin `alarm_levels_inferred_from_values`

| source_file | Giá trị trong DB (trạm; BĐ I/II/III) | Chữ PDF | Bản liền trước / sau | Kết luận |
|---|---|---|---|---|
| `2017/02/24/HCMC_TVHN_20170224.pdf` | Phú An; 1,30 / 1,40 / 1,50 | có "BĐ II : 1.40", "BĐ III : 1.50"; dòng đầu chỉ có ": 1.30 m", **nhãn "BĐ I" không có trong PDF** (đã xem ảnh vùng ghi chú) | 1,30/1,40/1,50 cả hai phía | đúng |
| `2017/02/25/HCMC_TVHN_20170225.pdf` | như trên | như 24/02 (cùng mẫu) | như trên | đúng |
| `2017/02/26/HCMC_TVHN_20170226.pdf` | như trên | như 24/02 | như trên | đúng |
| `2017/02/27/HCMC_TVHN_20170227.pdf` | như trên | như 24/02 | như trên | đúng |
| `2017/02/28/HCMC_TVHN_20170228.pdf` | như trên | như 24/02 | như trên | đúng |
| `2017/09/06/HCMC_TVHN_20170906.pdf` | như trên | trên ảnh có đủ "BĐ I : 1.30 m"; lớp chữ trích ra mất nhãn "BĐ I" | như trên | đúng |

Bộ 1,30 / 1,40 / 1,50 m chỉ cho Phú An khớp giai đoạn 2017 (mục 2.3 schema: 1.170 bản tin). **Quyết định đề xuất:** không sửa. Mức chắc chắn: cao.

---

## K5 – 2 bản chép tay từ ảnh (1 bản là scan)

| source_file | Loại | Cách kiểm | Kết quả | Mức chắc chắn |
|---|---|---|---|---|
| `2025/12/27/HCMC_TVHN_20251227.pdf` (số 361/2025; `pdf_has_no_text_layer`, `manually_transcribed_from_image`) | bản tin triều; PDF có chữ dạng nét vẽ (2.487 đối tượng vẽ, không lớp chữ, không ảnh) | kết xuất trang 170 dpi, chép lại 208 ô (9 trạm × 8 ô thực đo; 15 dòng dự báo × 8 ô; 4 hồ × 4 ô) và so tự động; xem thêm tiêu đề, mưa, cấp báo động | **208/208 ô khớp**; ngày 27/12/2025 thứ Bảy, âm lịch 08/11 Ất Tỵ, thực đo ngày 26/12/2025, mưa 5 trạm đều "-" (không mưa), BĐ 1,40/1,50/1,60 tại Phú An, Nhà Bè, Thủ Dầu Một – đều khớp DB | cao |
| `2021/06/10/khac/HCMC_TVHN_20210610_KSTVXNMT06D110062021pdf.pdf` (`scanned_pdf`, `manually_transcribed_from_image`) | báo cáo khảo sát xâm nhập mặn, 3 trang ảnh scan | đọc ảnh trang 1 (bảng 7 vị trí × 3 đặc trưng × 6 cột) và trang 2–3 (nhận định, tổng hợp) | **126/126 ô bảng khớp**; tổng hợp khớp (pH TB 6,77, min 6,27, max 7,22; độ đục TB 55,70, min 17, max 230 NTU; EC TB 1,40, min 0,19, max 6,84 mS/cm) và khớp phép tính lại từ bảng | cao |

Ghi chú: báo cáo mặn đề ngày 14/06/2021 (`bulletin_date`), số liệu đo ngày 10/06/2021 (`observation_date`) – đúng như văn bản. Bản này không nằm trong `tide_extremes` (không phải bản tin triều).

**Quyết định đề xuất:** xác nhận `ok`, không sửa giá trị (2 mục trong `proposed_decisions.json`, phạm vi cả bản tin).

---

## K6 – 38 nhóm PDF trùng `sha256`

### Phân loại

| Loại | Số nhóm | Ghi chú |
|---|---:|---|
| Hai bản **chính** trùng nhau | 4 | một file nằm sai thư mục ngày |
| Bản chính trùng bản `khac/` cùng ngày | 28 | bản `khac/` là bản gửi lại; không ảnh hưởng |
| Bản chính trùng bản `khac/` ở thư mục ngày khác | 6 | bản `khac/` nằm sai thư mục (K2 #1–#4, #9, #10); không ảnh hưởng vì không phải bản chính |
| **Tổng** | **38** | |

### 4 cặp bản chính trùng

| Cặp | File nằm sai thư mục (đang `is_primary = true`) | Ngày thật của file đó | File đúng (giữ) | Bản thật của ngày trên thư mục sai |
|---|---|---|---|---|
| 1 | `2019/01/10/HCMC_TVHN_20190110.pdf` | 2020-01-10 (010/2020) | `2020/01/10/HCMC_TVHN_20200110.pdf` | `2019/01/10/khac/HCMC_TVHN_20190110_Bantinngay10012019pdf.pdf` – số 010/2019, thứ Năm, âm lịch 5/12, nối tiếp 009/2019 và 011/2019; đủ 9 trạm, 35 giá trị thực đo |
| 2 | `2019/02/11/HCMC_TVHN_20190211.pdf` | 2020-02-11 (042/2020) | `2020/02/11/HCMC_TVHN_20200211.pdf` | `2019/02/11/khac/HCMC_TVHN_20190211_Bantinngay11022019pdf.pdf` – 042/2019, thứ Hai, âm lịch 7/1; 35 giá trị |
| 3 | `2020/02/07/HCMC_TVHN_20200207.pdf` | 2021-02-07 (038/2021) | `2021/02/07/HCMC_TVHN_20210207.pdf` | `2020/02/07/khac/HCMC_TVHN_20200207_Bantinngay07022020pdf.pdf` – 038/2020, thứ Sáu, âm lịch 14/1; 36 giá trị |
| 4 | `2020/07/05/HCMC_TVHN_20200705.pdf` | 2020-07-06 (188/2020) | `2020/07/06/HCMC_TVHN_20200706.pdf` | `2020/07/05/khac/HCMC_TVHN_20200705_HOCMTVHN202007051030pdf.pdf` – 187/2020, Chủ Nhật, âm lịch 15/5, nối tiếp 186/2020; 32 giá trị |

Tác động hiện tại: quy tắc `duplicate_exclusions` bỏ đúng 4 file sai thư mục, nên G0 không bị trùng khóa. Nhưng 4 ngày bản tin 2019-01-10, 2019-02-11, 2020-02-07, 2020-07-05 **không có bản chính nào** → `tide_extremes` có 0 document cho các ngày quan trắc 2019-01-09, 2019-02-10, 2020-02-06, 2020-07-04 (đã đếm), dù số liệu có sẵn trong 4 bản `khac/` ở trên (cả 4 `parse.status = ok`, `sha256` không trùng file nào).

Thêm (từ K2 #11): `2025/08/30/khac/HCMC_TVHN_20250830_Bntinngy3082025pdf.pdf` là bản duy nhất của ngày 30/08/2026 (242/2026); ngày quan trắc 2026-08-29 cũng có 0 document trong `tide_extremes`.

### 34 nhóm còn lại

Cả 34 nhóm có đúng 1 bản chính, và bản chính đó có ngày nội dung = ngày trên tên file; bản còn lại nằm trong `khac/` (`is_primary = false`) → **không ảnh hưởng** dữ liệu đang dùng (G0 và `stations` chỉ đọc bản chính). Danh sách đầy đủ theo `sha256` (12 ký tự đầu):

| sha256 | Bản chính | Bản `khac/` | Sai thư mục? |
|---|---|---|---|
| 008e3b885f1d | 2017/12/10/…20171210.pdf | 2018/12/10/khac/…_BTinPCTT101218pdf.pdf | có (bản `khac/` ở năm 2018) |
| 245adabea98f | 2019/01/13/…20190113.pdf | 2018/01/13/khac/…_Bantinngay13012018pdf.pdf | có |
| 58340facd273 | 2021/09/21/…20210921.pdf | 2021/09/22/khac/…_Bntinngy2292021pdf.pdf | có |
| 702549feb247 | 2023/02/03/…20230203.pdf | 2022/02/03/khac/…_Bntinngy03022022pdf.pdf | có |
| 8b327e2a04c6 | 2018/02/09/…20180209.pdf | 2017/02/09/khac/…_Bantinngay09022017pdf.pdf | có |
| 99b80f369c57 | 2019/08/12/…20190812.pdf | 2018/08/12/khac/…_Bantinngay1282018pdf.pdf | có |
| 070d3ca27354 | 2019/09/08/…20190908.pdf | cùng thư mục `khac/` | không |
| 10fe65b6c389 | 2018/04/01/…20180401.pdf | cùng thư mục | không |
| 1871b89da4ee | 2018/05/16/…20180516.pdf | cùng thư mục | không |
| 1d5b076e519f | 2023/04/27/…20230427.pdf | cùng thư mục | không |
| 286f40c81d1c | 2018/10/21/…20181021.pdf | cùng thư mục | không |
| 2891c4ae03d2 | 2017/03/19/…20170319.pdf | cùng thư mục | không |
| 2f1acd3046e2 | 2026/05/25/…20260525.pdf | cùng thư mục | không |
| 4afe0a647b9d | 2019/10/10/…20191010.pdf | cùng thư mục | không |
| 5badcf0ff96e | 2019/05/03/…20190503.pdf | cùng thư mục | không |
| 5d556a55ce5d | 2024/04/29/…20240429.pdf | cùng thư mục | không |
| 6000e1a2ee7e | 2026/03/26/…20260326.pdf | cùng thư mục | không |
| 659ee9c4c750 | 2026/05/03/…20260503.pdf | cùng thư mục | không |
| 6f1c066b59e4 | 2020/05/03/…20200503.pdf | cùng thư mục | không |
| 70a910e905d3 | 2018/11/08/…20181108.pdf | cùng thư mục | không |
| 721bf33b1c59 | 2024/07/26/…20240726.pdf | cùng thư mục | không |
| 7366756bac0a | 2024/04/26/…20240426.pdf | cùng thư mục | không |
| 7c061ac78946 | 2019/03/27/…20190327.pdf | cùng thư mục | không |
| 800576098221 | 2019/08/23/…20190823.pdf | cùng thư mục | không |
| 8277366c92d6 | 2023/03/09/…20230309.pdf | cùng thư mục | không |
| c84547952e1a | 2025/09/14/…20250914.pdf | cùng thư mục | không |
| d0789e4ebee1 | 2019/07/06/…20190706.pdf | cùng thư mục | không |
| d2c19b4fa473 | 2017/08/14/…20170814.pdf | cùng thư mục | không |
| d57e37d5f707 | 2025/11/30/…20251130.pdf | cùng thư mục | không |
| d7def6b6dc26 | 2024/04/25/…20240425.pdf | cùng thư mục | không |
| dd87ecfc39cb | 2025/12/31/…20251231.pdf | cùng thư mục | không |
| e2f6962ae904 | 2017/06/21/…20170621.pdf | cùng thư mục | không |
| f442f46284ce | 2019/05/31/…20190531.pdf | cùng thư mục | không |
| f72b1f0f4d5a | 2026/08/04/…20260804.pdf | cùng thư mục | không |

### Quyết định đề xuất K6

**Sửa `source.is_primary`** (khuyến nghị), không chỉ giữ quy tắc loại khi dùng, vì quy tắc loại hiện tại làm mất 4 ngày có số liệu thật:

1. Đặt `source.is_primary = false` cho 4 file sai thư mục (cột 2 bảng 4 cặp); ghi lý do "trùng sha256 với bản chính ngày thật".
2. Đặt `source.is_primary = true` cho 4 bản `khac/` là bản thật của ngày (cột 5) và cho `2025/08/30/khac/HCMC_TVHN_20250830_Bntinngy3082025pdf.pdf` (bản duy nhất ngày 30/08/2026).
3. Chạy lại G0: dự kiến 3.595 bản tin (3.590 + 5), thêm 5 ngày quan trắc (2019-01-09, 2019-02-10, 2020-02-06, 2020-07-04, 2026-08-29), `tide_extremes` thêm 5 × 36 = 180 document. Sau khi sửa, `vngis.bulletins.duplicate_exclusions` không còn gì để loại; có thể giữ làm phép kiểm (khẳng định 0 cặp bản chính trùng `sha256`).
4. Ghi mọi thay đổi vào `audit_logs` (trước/sau, lý do).

Phương án tối thiểu (nếu không muốn sửa `hydromet_bulletins`): giữ quy tắc loại, chấp nhận thiếu 5 ngày quan trắc.

---

## Phát hiện phụ (ngoài K1–K6, để ghi nhận)

- 8 ngày bản tin không có bản chính lẫn bản `khac/`: 2017-04-28, 2017-09-08, 2018-04-23, 2021-02-23, 2022-04-25, 2023-06-13, 2023-07-06 (và 2025-11-08 – đã giải thích ở K2 #12, thực ra có bản nhưng sai `bulletin_date`). Là khoảng trống của nguồn.
- `2019/01/01/khac/HCMC_TVHN_20190101_TVHNNABO201901011pdf.pdf` có số bản tin "366/2019" vào ngày 01/01/2019 (bất thường, có thể là 001/2019 hoặc chép từ cuối năm 2018); bản `khac/`, không ảnh hưởng.
