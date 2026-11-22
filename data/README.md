# data/ – Dữ liệu

**Chức năng:** nơi đặt dữ liệu tệp (không phải dữ liệu trong MongoDB). **Phần lớn không commit lên git.**

| Thư mục / file | Commit? | Nội dung |
|---|---|---|
| [`manifest.yaml`](manifest.yaml) | có | danh mục nguồn: URL, ngày tải, sha256, giấy phép, đường dẫn |
| [`admin_units/`](admin_units/) | có (~3 MB) | dữ liệu địa giới gốc từ provinces.open-api.vn |
| [`raw/`](raw/) | **không** | dữ liệu gốc lớn hoặc có hạn chế: PDF KTTV, MERIT, OSM, mưa vệ tinh |
| [`interim/`](interim/) | **không** | dữ liệu trung gian |
| [`processed/`](processed/) | **không** | đặc trưng Parquet (G5) cho huấn luyện |

**Quy tắc**
- Dữ liệu gốc không sửa; tải lại thì ghi bản mới vào `manifest.yaml`.
- Không đưa PDF bản tin KTTV lên GitHub (dung lượng khoảng 1,1 GB và điều khoản sử dụng của nguồn).
