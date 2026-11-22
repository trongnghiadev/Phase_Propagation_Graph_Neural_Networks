# Phase-Propagation Graph Neural Networks

**Hệ thống quản lý dữ liệu phân tán tích hợp dự báo đỉnh mực nước trên mạng lưới sông Việt Nam bằng mạng nơ-ron đồ thị lan truyền pha**
*Thử nghiệm tại hệ thống sông Đồng Nai – Sài Gòn – Vàm Cỏ*

Đề tài gồm ba phần chạy trên cùng một database MongoDB `vietnam_gis`:

1. **Dữ liệu:** bản tin thủy văn – thủy triều TP.HCM 2017–2026, mạng sông MERIT-Basins và OpenStreetMap, danh mục trạm, địa giới hành chính trước/sau 01/07/2025. Xử lý bằng PySpark và R.
2. **Mô hình PhaseGNN:** GNN lan truyền pha hai chiều (sóng triều đi ngược, dòng xả hồ đi xuôi) dự báo độ cao và thời điểm đỉnh mực nước 1–5 ngày, so sánh với dự báo nghiệp vụ của Đài KTTV Nam Bộ.
3. **Hệ thống phân tán:** API + worker + web với phân quyền Bitmask, cache Redis, khóa phân tán (Redlock, fencing token), MongoDB replica set / sharding, giả lập bằng Docker.

Repo này chỉ chứa **code và cấu hình**. **Toàn bộ tài liệu** (schema database, ADR, đề cương, góp ý, nhật ký, báo cáo, slide) nằm ở repo private, gắn vào tại `docs/` bằng git submodule. Không có quyền vào repo private thì `docs/` để trống và các link vào `docs/` không mở được; code vẫn chạy bình thường theo README trong từng thư mục.

---

## Cấu trúc thư mục

```
Phase_Propagation_Graph_Neural_Networks/
├── config/              Cấu hình dùng chung (YAML), tách khỏi code
│   └── env/             Ghi đè theo môi trường: dev, test, cluster
├── schemas/             Hợp đồng dữ liệu giữa các khối
│   ├── mongo/           Validator $jsonSchema của từng collection, có phiên bản
│   └── prediction/      Định dạng dự báo chung cho mọi mô hình
├── migrations/          Nâng cấp database có đánh số, chạy lại và đảo ngược được
│   └── mongo/           Các bước migration + trình chạy
├── packages/            Thư viện Python dùng chung
│   ├── vngis/           Lõi nhẹ: cấu hình, kết nối, tên tiếng Việt, địa giới, khóa, cache
│   └── phasegnn/        Mô hình PhaseGNN và các mô hình cơ sở (torch, PyG)
├── apps/                Dịch vụ chạy liên tục, mỗi thư mục một image Docker
│   ├── api/             REST API (FastAPI), 3 instance sau Nginx
│   ├── worker/          Scheduler và job nền: nạp bản tin, dự báo, làm nóng cache
│   └── web/             Giao diện Next.js + Leaflet
├── pipelines/           Xử lý hàng loạt
│   ├── ingest/          Nạp dữ liệu nguồn: bản tin KTTV, địa giới, danh mục trạm
│   ├── spark/           Job PySpark: làm phẳng (G0), truy vấn Q1–Q7, đặc trưng G1–G5
│   └── r/               Thống kê, phân tích điều hòa, độ trễ pha, hàm đánh giá
├── research/            Nghiên cứu và thực nghiệm
│   ├── experiments/     E01–E22, mỗi thực nghiệm một thư mục có cấu hình
│   ├── benchmarks/      Kịch bản tải Locust / k6
│   ├── chaos/           Giả lập sự cố trên cụm Docker
│   └── notebooks/       Khám phá dữ liệu
├── infra/               Hạ tầng
│   ├── compose/         docker-compose (profile ha, spark, shard), MongoDB, Redis, Nginx
│   └── scripts/         Chuyển dữ liệu, sao lưu
├── data/                Dữ liệu tệp (phần lớn KHÔNG commit)
│   ├── admin_units/     Địa giới gốc từ provinces.open-api.vn (commit)
│   ├── raw/             PDF KTTV, MERIT, OSM, mưa vệ tinh (gitignore)
│   ├── interim/         Dữ liệu trung gian (gitignore)
│   └── processed/       Parquet cho huấn luyện (gitignore)
├── results/             Kết quả thực nghiệm: summary + hình commit, từng lần chạy gitignore
├── docs/                SUBMODULE → repo PRIVATE: toàn bộ tài liệu
│   ├── data/            Schema database, mô tả dữ liệu KTTV và địa giới
│   ├── decisions/       ADR: quyết định thiết kế đánh số
│   ├── api/, models/    Ảnh chụp OpenAPI, model card
│   └── proposal/, feedback/, journal/, report/, slides/
├── pyproject.toml       Gốc uv workspace; phiên bản DUY NHẤT của hệ thống
├── CHANGELOG.md         Lịch sử thay đổi theo phiên bản
├── .env.example         Mẫu biến môi trường (không commit .env)
├── .gitignore
└── .gitattributes       Ép LF cho *.sh, *.py, *.yaml; CRLF cho *.ps1
```

Mỗi thư mục cấp 1 và cấp 2 có `README.md` mô tả chức năng, nội dung dự kiến và quy tắc riêng.

### Đối chiếu với đề cương

| Đề cương | Thư mục |
|---|---|
| Chương 2 – MongoDB | `pipelines/ingest/`, `schemas/mongo/`, `migrations/mongo/` |
| Chương 3 – PySpark | `pipelines/spark/` |
| Chương 4 – R | `pipelines/r/` |
| Chương 5.2 – PhaseGNN và mô hình cơ sở | `packages/phasegnn/` |
| Chương 5.3–5.8 – Web, Bitmask, cache, khóa | `apps/`, `packages/vngis/` |
| Chương 5.9 – Phân tán | `infra/`, `research/chaos/` |
| Chương 6 – Mã nguồn, kiểm thử | `*/tests/` trong từng package và app |
| Chương 7 – Thực nghiệm | `research/experiments/`, `research/benchmarks/`, `results/` |
| Đề cương, báo cáo, slide | `docs/` (repo private) |

---

## Luật phụ thuộc

```
apps/web     ──HTTP──►  apps/api     ──►  packages/vngis
                        apps/worker  ──►  packages/vngis, packages/phasegnn
pipelines/*  ─────────────────────────►  packages/vngis   (phasegnn chỉ ở spark/features)
packages/phasegnn ────────────────────►  packages/vngis
packages/vngis    ────────────────────►  (không phụ thuộc khối nào trong repo)
```

- `packages/` không import từ `apps/`, `pipelines/`, `research/`.
- `apps/api` không import `phasegnn` (image API không chứa torch).
- Lý do: ADR 0001 (cấu trúc monorepo), 0002 (tách API và worker) trong `docs/decisions/` (private).

---

## Quản lý phiên bản

| Thứ thay đổi | Cách đánh phiên bản | Ở đâu |
|---|---|---|
| Hệ thống | SemVer chung, tag `vX.Y.Z`; image Docker cùng tag | `pyproject.toml`, `CHANGELOG.md` |
| API | Tiền tố `/api/v1`; thay đổi phá vỡ → `/api/v2` | `apps/api/`, `docs/api/` |
| Schema MongoDB | `schema_version` trong document; validator `v1.json`, `v2.json`; migration đánh số | `schemas/mongo/`, `migrations/mongo/` |
| Dữ liệu nguồn | Không sửa dữ liệu gốc; mỗi lần tải ghi ngày và sha256 | `data/manifest.yaml` |
| Dữ liệu dẫn xuất | `data_snapshot` (phiên bản các collection nguồn) | `data/processed/*/_meta.json`, `results/` |
| Mô hình | `phasegnn-vN` + commit git + `data_snapshot` + hash cấu hình | MLflow, collection `models`, `docs/models/` |
| Cấu hình | `config_version`, kiểm tra bằng pydantic | `config/` |
| Thư viện | `uv.lock`, `pipelines/r/renv.lock`, `apps/web/package-lock.json` | các file lock |
| Thực nghiệm | Mỗi lần chạy một thư mục `<ngày>_<commit>` có `meta.json`, không ghi đè | `results/` |
| Quyết định | ADR đánh số, không sửa, chỉ thay thế | `docs/decisions/` |

Commit theo [Conventional Commits](https://www.conventionalcommits.org/vi/v1.0.0/): `feat(api): …`, `fix(locks): …`, `feat!: …` (thay đổi phá vỡ).

---

## Hiện trạng

| Phần | Đã có trong repo |
|---|---|
| Nạp dữ liệu | `pipelines/ingest/`: parser bản tin KTTV, địa giới cũ/mới, danh mục trạm |
| Dữ liệu tệp | `data/admin_units/` (địa giới gốc), `data/manifest.yaml` |
| Thư viện chung | `packages/vngis`: `locks` (Redlock, fencing token), `text` (chuẩn hóa tên) |
| API | `apps/api`: bản tối giản `/health`, `/stations`, `/lock/{resource}` |
| Hạ tầng | `infra/compose/` (profile `ha`, `spark`, `shard`), `infra/scripts/migrate-data.ps1` |
| Tài liệu | submodule `docs/` (private): schema, dữ liệu KTTV, ADR 0001–0005, đề cương, checklist |

Chưa có: Spark, R, PhaseGNN, worker, web, migration, schema JSON, thực nghiệm. PDF bản tin KTTV (khoảng 1,1 GB) để ngoài repo, đặt vào `data/raw/kttv/` khi cần chạy lại parser (không commit).

Database `vietnam_gis` hiện có 12 collection dữ liệu: `hydromet_bulletins`, `hydromet_bulletin_files`, `merit_river_reaches`, `waterways_segments`, `stations`, `admin_units`, `admin_unit_mappings`, `province_timeline` và 4 collection do job G0 (`pipelines/spark/flatten/`) sinh ra từ bản tin: `tide_extremes` (time series), `official_forecasts`, `reservoir_daily`, `rainfall_daily`. Thêm 2 collection vận hành của job: `job_locks` (khóa chống chạy chồng), `jobs` (nhật ký lần chạy). Chi tiết: `docs/data/vietnam_gis_schema.md` (private).

---

## Bắt đầu (dự kiến)

1. Cài [uv](https://docs.astral.sh/uv/), Docker Desktop, R 4.x, Node.js.
2. `uv sync` để cài môi trường Python cho toàn workspace.
3. Chép `.env.example` thành `.env`, chỉnh URI.
4. Dựng cụm: xem `infra/compose/README.md`.
5. Nạp dữ liệu: xem `pipelines/ingest/README.md`.

## Giấy phép

Chưa chọn giấy phép cho code. Dữ liệu có giấy phép riêng theo từng nguồn (xem `data/manifest.yaml`); **không** công bố PDF bản tin KTTV khi chưa xác nhận điều khoản sử dụng.
