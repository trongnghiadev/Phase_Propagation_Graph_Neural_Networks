# data/processed/ – Dữ liệu đã xử lý (không commit)

**Chức năng:** dữ liệu sẵn sàng cho mô hình và thống kê, chủ yếu Parquet do Spark sinh (G5), phân vùng theo năm.

- Mỗi bộ dữ liệu có `_meta.json` ghi `data_snapshot` (phiên bản các collection nguồn) và commit git của pipeline.
- Tạo lại được hoàn toàn từ `pipelines/`. Bị gitignore.
