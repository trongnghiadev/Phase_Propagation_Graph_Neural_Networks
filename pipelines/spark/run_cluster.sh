"""Chạy job PySpark trên cụm `spark://spark-master:7077` (Windows/Git Bash).

    bash pipelines/spark/run_cluster.sh features/graph_build.py --min-uparea-km2 100

Cách dùng: script gọi `docker run` với driver trong container `gis-spark:local`, gắn:
- `<repo>:/work:ro`            – mã nguồn (đọc-only)
- `<repo>/data:/work/data`     – dữ liệu đã xử lý (ghi được)
- `<repo>/results:/out`        – kết quả/báo cáo (ghi được)

Biến môi trường: `REPO` (mặc định thư mục gốc repo), `NETWORK` (mặc định `spark-mongodb-gis_gis-net`),
`MASTER` (mặc định `spark://spark-master:7077`), `EXECUTOR_MEM` (mặc định `1g`), `DRIVER_NAME`.

LƯU Ý: các job `features/` (G1, G3, G4, G5) ghi Parquet vào volume Windows gắn vào container nên
Hadoop `ChecksumFileSystem` không đổi tên được thư mục `_temporary` khi chạy trên cụm
(`Mkdirs failed to create .../_temporary/0/_temporary`). Phải chạy chúng với driver trong container:

    MASTER='local[*]' bash pipelines/spark/run_cluster.sh features/graph_build.py --min-uparea-km2 100

Các job `analytics/` chỉ ghi CSV nên chạy trên cụm `spark://spark-master:7077` như bình thường.
"""
set -euo pipefail

REPO="${REPO:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
NETWORK="${NETWORK:-spark-mongodb-gis_gis-net}"
MASTER="${MASTER:-spark://spark-master:7077}"
EXECUTOR_MEM="${EXECUTOR_MEM:-1g}"
DRIVER_NAME="${DRIVER_NAME:-feat-driver}"

if [ "$#" -lt 1 ]; then
  echo "Dùng: bash pipelines/spark/run_cluster.sh <job.py> [tham số job...]" >&2
  exit 2
fi
JOB="$1"; shift

mkdir -p "${REPO}/data/processed" "${REPO}/results"

MSYS_NO_PATHCONV=1 docker run --rm --name "${DRIVER_NAME}" --hostname "${DRIVER_NAME}" \
  --network "${NETWORK}" \
  -e PYTHONIOENCODING=utf-8 -e PYSPARK_PYTHON=/usr/bin/python3 \
  -e MONGO_URI="${MONGO_URI:-mongodb://mongo:27017}" -e MONGO_DB="${MONGO_DB:-vietnam_gis}" \
  -v "${REPO}:/work:ro" \
  -v "${REPO}/data:/work/data" \
  -v "${REPO}/results:/out" \
  gis-spark:local \
  /opt/spark/bin/spark-submit --master "${MASTER}" \
  --conf "spark.driver.host=${DRIVER_NAME}" --conf "spark.executor.memory=${EXECUTOR_MEM}" \
  "/work/${JOB}" "$@"
