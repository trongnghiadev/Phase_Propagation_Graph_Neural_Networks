"""Gửi gói `vngis` (phần thuần Python) tới executor bằng `SparkContext.addPyFile`.

Executor của cụm không gắn repo; job nào gọi hàm `vngis` trong RDD/UDF phải gọi `ship_vngis(spark)` trước.
File zip tạo tạm từ chính thư mục gói đang import, chỉ gồm các module thuần Python `vngis.qc_rules.PURE_MODULES`
(không gửi `config`, `db`, `qc`, `locks` – cần pymongo/pydantic/pyyaml/redis mà executor không có).
"""
from __future__ import annotations

import os
import tempfile
import zipfile
from pathlib import Path

from pyspark.sql import SparkSession


def build_vngis_zip(out_dir: str | None = None) -> str:
    import vngis
    from vngis.qc_rules import PURE_MODULES
    pkg = Path(vngis.__file__).resolve().parent
    fd, path = tempfile.mkstemp(prefix="vngis_", suffix=".zip", dir=out_dir)
    os.close(fd)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        for m in PURE_MODULES:
            z.write(pkg / f"{m}.py", f"vngis/{m}.py")
    return path


def ship_vngis(spark: SparkSession) -> str:
    path = build_vngis_zip()
    spark.sparkContext.addPyFile(path)
    return path
