# apps/ – Dịch vụ chạy liên tục

**Chức năng:** các dịch vụ của hệ thống web, mỗi thư mục build thành một image Docker riêng.

| Thư mục | Vai trò | Phụ thuộc |
|---|---|---|
| [`api/`](api/) | REST API (FastAPI), 3 instance sau Nginx | `vngis` |
| [`worker/`](worker/) | Scheduler và job nền: nạp bản tin, dự báo hằng ngày, làm nóng cache, nạp lại dữ liệu | `vngis`, `phasegnn` |
| [`web/`](web/) | Giao diện (Next.js + Leaflet) | gọi `api/` qua HTTP |

**Vì sao tách API và worker** (ADR 0002 trong `docs/decisions/`, private): API không cần chứa mô hình PyTorch nên image nhẹ; job nặng chạy ở worker. Nhiều worker cùng lập lịch, khóa phân tán đảm bảo mỗi lượt chỉ một worker chạy (thực nghiệm E14).
