# pipelines/spark/ – Job PySpark

**Chức năng:** xử lý phân tán trên Spark cluster (đề cương Chương 3).

| Thư mục (dự kiến) | Nội dung |
|---|---|
| `common/` | SparkSession, schema tường minh, đẩy `$match`/`$project` xuống MongoDB |
| `flatten/` | G0: bản tin → `tide_extremes`, `official_forecasts`, `reservoir_daily`, `rainfall_daily` |
| `analytics/` | Q1–Q7: thống kê triều, hồ, MERIT/OSM, toàn vẹn dữ liệu, ghép OSM ↔ MERIT, đồ thị, độ chính xác KTTV |
| `matching/` | G2: gán trạm vào reach, ghép OSM ↔ MERIT (Sedona) |
| `repair/` | QC, sửa `slope = 0`, gắn cờ nút biên |
| `features/` | G1 đồ thị hai chiều, G3 τ vật lý, G4 mưa theo nút, G5 mẫu huấn luyện (Parquet) |

`vngis` được đóng gói `.zip` và gửi qua `--py-files` khi `spark-submit`.
