# data/raw/ – Dữ liệu gốc (không commit)

**Chức năng:** dữ liệu nguồn dạng tệp, lớn hoặc có hạn chế chia sẻ. Thư mục này bị gitignore, chỉ README được commit.

| Thư mục (dự kiến) | Nội dung |
|---|---|
| `kttv/` | PDF bản tin KTTV Nam Bộ 2017–2026 (hiện ở `datamongo/KTTV/`) |
| `merit/` | shapefile MERIT-Basins (`riv_pfaf_*`, `cat_pfaf_44`) |
| `osm/` | snapshot đường thủy OSM |
| `precip/` | mưa vệ tinh (IMERG / CHIRPS / ERA5) |

Mỗi nguồn phải có một mục trong `data/manifest.yaml`.
