# results/qc/manual/ – Kiểm tra tay K1–K6 (GĐ2.3)

**Chức năng:** kết quả kiểm tra tay các mục K1–K6 trong checklist GĐ2.3 (`docs/proposal/checklist_de_cuong.md`) và các quyết định QC.

**Trạng thái: đã áp dụng (2026-11-23)** theo quyết định của người dùng, ghi qua CLI `pipelines/qc/qc_decide.py` (actor `manual_review`, mỗi thay đổi có `audit_logs`): K1 `rejected` (không `corrected`); K5 hai mục xác nhận (36 cờ thủ công `confirmed_ok` cho bản 2025/12/27, một `review_confirmed` cho báo cáo mặn); K6 đủ 9 thay đổi `source.is_primary`; K2 chỉ sửa `bulletin_date`, `weekday` và thêm `qc_note` (số bản tin 312 và âm lịch 19/9 là suy luận, không ghi đè). Sau đó đã chạy lại G0 và job QC. Đề xuất `alternative` của K1 và các thay đổi `bulletin_no`/`bulletin_seq`/`lunar_date` của K2 KHÔNG được áp dụng.

| File | Nội dung |
|---|---|
| `k1_k6_findings.md` | Bảng phát hiện từng mục: định danh, phát hiện, bằng chứng ngắn, mức chắc chắn, quyết định đề xuất |
| `proposed_decisions.json` | Cùng các quyết định ở dạng máy đọc được (`qc_flags` cho K1/K5; thay đổi metadata cho K2/K6) |

## Nguồn đối chiếu

- MongoDB `vietnam_gis` (chỉ đọc, ngày kiểm tra 2026-11-23): `hydromet_bulletins` (`raw` từng ô, `full_text`, `parse.warnings`, `bulletin_no`, `weekday`, `lunar_date`, `observation_date`, `forecast_water_levels[].date`, `rainfall.period`, `source.sha256`), `hydromet_bulletin_files` (PDF gốc trong `content`), `tide_extremes` (lần chạy G0 ghi trong mục 8 của `docs/data/vietnam_gis_schema.md`).
- PDF gốc: xuất từ `hydromet_bulletin_files.content` ra thư mục tạm ngoài repo; trích chữ bằng PyMuPDF (`page.get_text()`, `get_text("words")` để lấy tọa độ ô); với PDF không có lớp chữ thì kết xuất trang thành ảnh (170–200 dpi) và đọc bằng mắt. Không lưu PDF hay ảnh vào repo.

## Phương pháp từng mục

| Mục | Cách kiểm tra |
|---|---|
| K1 | Tìm mọi ô Biên Hòa có mực nước > 3 m; so `raw`, `full_text`, chữ PDF (kèm tọa độ ô) và chuỗi Biên Hòa / Thủ Dầu Một / Phú An ±7 ngày |
| K2 | Với mỗi bản tin có `bulletin_date_differs_from_filename:*`: so thứ trong tuần (tính lại từ ngày), ngày âm lịch, số bản tin, ngày dự báo đầu tiên, khoảng đo mưa với bản tin cùng ngày nội dung và bản tin liền kề theo tên file; so `sha256` và số liệu thực đo |
| K3 | Chọn ngẫu nhiên 15/73 bản bằng `random.seed(20261123)`; `random.sample(range(73), 15)` trên danh sách sắp theo `source_file`. Mỗi bản kiểm 6 điều kiện: năm = năm trong số bản tin; ngày = ngày trên tên file; thứ trong tuần khớp; `bulletin_seq` = thứ tự ngày trong năm; ngày dự báo đầu = ngày bản tin; `observation_date` = ngày bản tin − 1; kèm số bản tin liền trước/sau. Ngoài mẫu, chạy cùng 6 điều kiện cho cả 73 bản |
| K4 | So cấp báo động suy ra với bản tin liền trước/sau; xem chữ PDF và ảnh vùng ghi chú |
| K5 | Kết xuất ảnh trang PDF, chép lại từng ô bằng mắt và so tự động với số liệu trong DB (bản tin triều: 208 ô; báo cáo mặn: 126 ô bảng + các giá trị tổng hợp) |
| K6 | Nhóm `hydromet_bulletin_files` theo `sha256`; phân loại theo `source.is_primary`; với mỗi ngày từ 01/01/2017 đến 11/11/2026 kiểm tra có bản chính (sau khi bỏ 4 bản trùng) và có bản `khac/` thay thế không; đếm `tide_extremes` theo ngày quan trắc |

## Quy ước

- Thời điểm `ts` ghi theo UTC (như `tide_extremes`); ngày bản tin/ngày quan trắc ghi theo giờ Việt Nam.
- `status` dùng bộ mã của đề cương: `ok`, `suspect`, `rejected`, `corrected`.
- Repo công khai: không ghi tên người, email, số điện thoại; chỉ trích số liệu và mã định danh cần thiết.
