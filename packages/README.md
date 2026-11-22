# packages/ – Thư viện Python dùng chung

**Chức năng:** code dùng ở nhiều nơi được viết **một lần** ở đây, rồi `apps/`, `pipelines/`, `research/` import vào.

| Package | Phụ thuộc | Nội dung |
|---|---|---|
| [`vngis/`](vngis/) | nhẹ: pymongo, redis, pydantic, pyyaml | cấu hình, kết nối, chuẩn hóa tên tiếng Việt, địa giới theo thời kỳ, khóa phân tán, cache |
| [`phasegnn/`](phasegnn/) | nặng: torch, torch_geometric | mô hình PhaseGNN, các mô hình cơ sở, huấn luyện |

**Luật phụ thuộc (kiểm tra bằng import-linter)**
- `packages/*` không import từ `apps/`, `pipelines/`, `research/`.
- `vngis` không import `phasegnn`, torch hay pyspark.
- Chỉ dùng những gì package khai báo trong `__init__.py`; hàm sắp bỏ đánh dấu `DeprecationWarning` và giữ thêm một phiên bản minor.
