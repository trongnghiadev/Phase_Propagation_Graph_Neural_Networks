"""Tiện ích dùng chung cho các job PySpark (SparkSession, đọc MongoDB, lệnh quản trị MongoDB).

Import gói này bảo đảm import được `vngis` (phần thuần Python: `vngis.text`, `vngis.admin`,
`vngis.province_labels`): nếu `vngis` chưa có (không gửi qua `--py-files`), thêm `<gốc repo>/packages/vngis/src`
vào `sys.path` (repo được gắn vào container, ví dụ `/work`). Image Spark không có pymongo/pydantic/pyyaml nên job
KHÔNG import `vngis.config`, `vngis.db`.
"""
import sys
from pathlib import Path

try:
    import vngis  # noqa: F401
except ImportError:
    _src = Path(__file__).resolve().parents[3] / "packages" / "vngis" / "src"
    if _src.is_dir():
        sys.path.insert(0, str(_src))
