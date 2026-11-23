"""SparkSession, đọc MongoDB có đẩy lọc xuống server, lệnh quản trị MongoDB qua driver Java, khóa job.

Image Spark (`gis-spark:local`) không có pymongo. Lệnh quản trị (tạo collection, index,
`renameCollection`, aggregation kiểm tra, khóa `job_locks`, bản ghi `jobs`) chạy qua driver Java đã nằm sẵn trong
MongoDB Spark Connector (`mongo-spark-connector_2.12-10.4.1-all.jar`), gọi bằng py4j từ driver Python.
Nhờ vậy job không cần cài thêm gói nào.

URI/DB: tham số dòng lệnh, nếu không có thì biến môi trường `MONGO_URI`, `MONGO_DB` (cùng tên với `vngis.config`;
`MONGODB_URI`/`MONGODB_DB` – tên cũ, infra/compose đã đổi sang MONGO_* – vẫn được nhận làm dự phòng).
Mọi lệnh ghi (connector, `$out`, `renameCollection`, khóa) dùng `writeConcern: {w: "majority"}`.
"""
from __future__ import annotations

import json
import os
import re
import socket
from datetime import datetime, timedelta, timezone
from typing import Any

from pyspark.sql import DataFrame, SparkSession
from pyspark.sql.types import StructType

DEFAULT_URI = "mongodb://mongo:27017"          # tên host MongoDB trong mạng Docker của cụm Spark
DEFAULT_DB = "vietnam_gis"
VN_TZ = "Asia/Ho_Chi_Minh"
WRITE_CONCERN = {"w": "majority"}


def _env(*names: str) -> str:
    return next((v.strip() for v in (os.environ.get(n) for n in names) if v and v.strip()), "")


def env_uri() -> str:
    """`MONGO_URI` (như vngis.config); `MONGODB_URI` là tên cũ, chỉ còn làm dự phòng."""
    return _env("MONGO_URI", "MONGODB_URI") or DEFAULT_URI


def env_db() -> str:
    return _env("MONGO_DB", "MONGODB_DB") or DEFAULT_DB


def build_session(app_name: str, uri: str, db: str = DEFAULT_DB) -> SparkSession:
    """SparkSession đọc/ghi MongoDB; múi giờ phiên là giờ Việt Nam để `date_format` ra ngày VN.

    Giá trị timestamp vẫn là thời điểm tuyệt đối, ghi xuống MongoDB thành BSON Date UTC.
    """
    return (SparkSession.builder
            .appName(app_name)
            .config("spark.mongodb.read.connection.uri", uri)
            .config("spark.mongodb.read.database", db)
            .config("spark.mongodb.write.connection.uri", uri)
            .config("spark.mongodb.write.database", db)
            .config("spark.mongodb.write.writeConcern.w", WRITE_CONCERN["w"])
            .config("spark.sql.session.timeZone", VN_TZ)
            .getOrCreate())


SINGLE_PARTITIONER = "com.mongodb.spark.sql.connector.read.partitioner.SinglePartitionPartitioner"


def read_mongo(spark: SparkSession, collection: str, schema: StructType | None = None,
               pipeline: list[dict] | None = None, db: str = DEFAULT_DB,
               options: dict[str, str] | None = None) -> DataFrame:
    """Đọc một collection; `pipeline` ($match/$project) được đẩy xuống MongoDB; `options`: tùy chọn connector
    (ví dụ `{"partitioner": SINGLE_PARTITIONER}` cho time series, vốn là view)."""
    r = spark.read.format("mongodb").option("database", db).option("collection", collection)
    for k, v in (options or {}).items():
        r = r.option(k, v)
    if pipeline:
        r = r.option("aggregation.pipeline", json.dumps(pipeline, ensure_ascii=False))
    if schema is not None:
        r = r.schema(schema)
    return r.load()


def write_mongo(df: DataFrame, collection: str, db: str = DEFAULT_DB) -> None:
    """Ghi thêm (append) vào collection đã tạo sẵn (giữ nguyên validator, kiểu time series), w = majority.

    Không dùng mode `overwrite` vì connector sẽ drop collection và mất cấu hình.
    """
    (df.write.format("mongodb").mode("append")
       .option("database", db).option("collection", collection)
       .option("ordered", "false")
       .option("writeConcern.w", WRITE_CONCERN["w"])
       .save())


def bson_date(dt: datetime) -> dict:
    """Datetime aware → JSON mở rộng dạng canonical cho `Document.parse`."""
    return {"$date": {"$numberLong": str(int(dt.astimezone(timezone.utc).timestamp() * 1000))}}


def _parse_iso(s: str) -> datetime:
    """Parse ISO-8601 do MongoDB sinh ra (Python 3.10 `fromisoformat` thiếu hỗ trợ).

    Driver Java có thể ghi `2026-11-23T13:30:46.21+00:00` (2 chữ số phần thập phân) —
    `datetime.fromisoformat` của Python 3.10 chỉ chấp nhận đúng 3 hoặc 6 chữ số.
    """
    txt = s.strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    # Chuẩn hoá phần thập phân về đúng 6 chữ số (micro giây).
    m = re.search(r"\.(\d+)", txt)
    if m and len(m.group(1)) != 6:
        frac = m.group(1)[:6].ljust(6, "0")
        txt = txt[:m.start(1)] + frac + txt[m.end(1):]
    try:
        return datetime.fromisoformat(txt)
    except ValueError:
        return datetime.fromtimestamp(0, tz=timezone.utc)


def decode_extjson(obj):
    """Giải mã JSON mở rộng (canonical/relaxed) của MongoDB về kiểu Python.

    Driver Java `toJson()` sinh **canonical extended JSON**: BSON `int64` → `{"$numberLong": "…"}`,
    BSON `int32` → `{"$numberInt": "…"}`, BSON `double` → `{"$numberDouble": "…"}`,
    BSON `Date` → `{"$date": {"$numberLong": "…"}}` hoặc `{"$date": "ISO"}`,
    `ObjectId` → `{"$oid": "…"}`.

    Không giải mã thì COMID đọc từ MongoDB thành dict thay vì số → so khớp khoá thất bại
    (ví dụ Q6: `max_tau_up_h` luôn bằng 0 vì `str(src)` không bằng `"44042931"`).
    """
    if isinstance(obj, dict):
        if len(obj) == 1:
            if "$numberLong" in obj:
                return int(obj["$numberLong"])
            if "$numberInt" in obj:
                return int(obj["$numberInt"])
            if "$numberDouble" in obj:
                return float(obj["$numberDouble"])
            if "$numberDecimal" in obj:
                return float(obj["$numberDecimal"])
            if "$oid" in obj:
                return str(obj["$oid"])
            if "$date" in obj:
                d = obj["$date"]
                if isinstance(d, dict) and "$numberLong" in d:
                    return datetime.fromtimestamp(int(d["$numberLong"]) / 1000, tz=timezone.utc)
                if isinstance(d, str):
                    return _parse_iso(d)
                return d
        return {k: decode_extjson(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [decode_extjson(v) for v in obj]
    return obj


class JobLockError(RuntimeError):
    """Job đang chạy ở nơi khác (khóa trong `job_locks` còn hạn)."""


class LockLostError(RuntimeError):
    """Khóa không còn thuộc lần chạy này (hết hạn và bị lần chạy khác lấy, hoặc bị xóa)."""


class MongoAdmin:
    """Gọi lệnh MongoDB (JSON mở rộng) qua driver Java của connector."""

    def __init__(self, spark: SparkSession, uri: str, db: str = DEFAULT_DB):
        self._jvm = spark.sparkContext._jvm
        self._client = self._jvm.com.mongodb.client.MongoClients.create(uri)
        self.db_name = db
        self._db = self._client.getDatabase(db)

    def close(self) -> None:
        self._client.close()

    # ---- lệnh thô -------------------------------------------------------
    def command(self, cmd: dict, admin: bool = False) -> dict:
        doc = self._jvm.org.bson.Document.parse(json.dumps(cmd, ensure_ascii=False))
        target = self._client.getDatabase("admin") if admin else self._db
        # `toJson()` của driver Java trả về canonical extended JSON → phải giải mã
        # để COMID/`$numberLong` thành số thật, nếu không các phép so khớp khoá sẽ hỏng.
        return decode_extjson(json.loads(target.runCommand(doc).toJson()))

    def aggregate(self, collection: str, pipeline: list[dict], write: bool = False,
                  bypass_validation: bool = False) -> list[dict]:
        """Aggregation trả về toàn bộ kết quả (dùng cho kết quả nhỏ: đếm, mẫu). `write` = có `$out` (w majority);
        `bypass_validation` = `bypassDocumentValidation: true` (chép bản sao lưu nguyên trạng)."""
        cmd = {"aggregate": collection, "pipeline": pipeline, "cursor": {"batchSize": 100000}, "allowDiskUse": True}
        if write:
            cmd["writeConcern"] = WRITE_CONCERN
        if bypass_validation:
            cmd["bypassDocumentValidation"] = True
        res = self.command(cmd)
        cur = res["cursor"]
        docs = list(cur["firstBatch"])
        while cur.get("id") not in (0, None, {"$numberLong": "0"}):
            res = self.command({"getMore": cur["id"], "collection": collection, "batchSize": 100000})
            cur = res["cursor"]
            docs.extend(cur["nextBatch"])
        return docs

    # ---- collection -----------------------------------------------------
    def collection_names(self) -> list[str]:
        res = self.command({"listCollections": 1, "nameOnly": True})
        return [c["name"] for c in res["cursor"]["firstBatch"]]

    def collection_options(self, name: str) -> dict:
        res = self.command({"listCollections": 1, "filter": {"name": name}})
        batch = res["cursor"]["firstBatch"]
        return batch[0].get("options", {}) if batch else {}

    def list_indexes(self, name: str) -> list[dict]:
        return list(self.command({"listIndexes": name})["cursor"]["firstBatch"])

    def exists(self, name: str) -> bool:
        return name in self.collection_names()

    def drop(self, name: str) -> None:
        if self.exists(name):
            self.command({"drop": name, "writeConcern": WRITE_CONCERN})

    def create(self, name: str, **options: Any) -> None:
        self.command({"create": name, **options, "writeConcern": WRITE_CONCERN})

    def create_indexes(self, name: str, indexes: list[dict]) -> None:
        if indexes:
            self.command({"createIndexes": name, "indexes": indexes, "writeConcern": WRITE_CONCERN})

    def count(self, name: str, query: dict | None = None) -> int:
        out = self.aggregate(name, [{"$match": query or {}}, {"$count": "n"}])
        return int(out[0]["n"]) if out else 0

    def rename(self, src: str, dst: str, drop_target: bool = True) -> None:
        self.command({"renameCollection": f"{self.db_name}.{src}", "to": f"{self.db_name}.{dst}",
                      "dropTarget": drop_target, "writeConcern": WRITE_CONCERN}, admin=True)

    def find_one(self, name: str, query: dict, sort: dict | None = None) -> dict | None:
        cmd = {"find": name, "filter": query, "limit": 1}
        if sort:
            cmd["sort"] = sort
        batch = self.command(cmd)["cursor"]["firstBatch"]
        return batch[0] if batch else None

    def find_many(self, name: str, query: dict, projection: dict | None = None, limit: int = 10000) -> list[dict]:
        cmd = {"find": name, "filter": query, "limit": limit, "batchSize": limit}
        if projection:
            cmd["projection"] = projection
        return list(self.command(cmd)["cursor"]["firstBatch"])

    def upsert(self, name: str, query: dict, update: dict) -> None:
        self.command({"update": name, "updates": [{"q": query, "u": update, "upsert": True}],
                      "writeConcern": WRITE_CONCERN})

    # ---- khóa chống chạy chồng ------------------------------------------
    def acquire_lock(self, job: str, run_id: str, ttl_s: int, collection: str = "job_locks") -> None:
        """Giữ khóa `{_id: job}` trong `job_locks`; khóa có `expires_at` (index TTL tự xóa khóa bị bỏ rơi).

        Lấy nguyên tử bằng `findAndModify` + upsert: chỉ khớp khi khóa cũ đã hết hạn; khóa còn hạn → upsert trùng
        `_id` → lỗi duplicate key → `JobLockError`.
        """
        self.create_indexes(collection, [{"key": {"expires_at": 1}, "name": "expires_at_ttl",
                                          "expireAfterSeconds": 0}])
        now = datetime.now(timezone.utc)
        doc = {"_id": job, "run_id": run_id, "host": socket.gethostname(), "acquired_at": bson_date(now),
               "expires_at": bson_date(now + timedelta(seconds=ttl_s))}
        try:
            self.command({"findAndModify": collection, "query": {"_id": job, "expires_at": {"$lt": bson_date(now)}},
                          "update": doc, "upsert": True, "new": True, "writeConcern": WRITE_CONCERN})
        except Exception as e:  # py4j bọc MongoCommandException
            if "E11000" not in str(e) and "11000" not in str(e):
                # lỗi khác (mạng, timeout…): lệnh có thể đã ghi khóa → thử nhả theo run_id rồi báo lỗi
                try:
                    self.release_lock(job, run_id, collection)
                except Exception as e2:  # noqa: BLE001
                    print(f"[KHÓA] không nhả được khóa sau lỗi lấy khóa: {e2}", flush=True)
                raise
            holder = self.find_one(collection, {"_id": job})
            raise JobLockError(f"job '{job}' đang chạy (khóa {collection}: {holder})") from None

    def renew_lock(self, job: str, run_id: str, ttl_s: int, collection: str = "job_locks") -> None:
        """Gia hạn khóa VÀ xác nhận vẫn giữ: `findAndModify {_id, run_id}`; không khớp → `LockLostError`."""
        exp = datetime.now(timezone.utc) + timedelta(seconds=ttl_s)
        res = self.command({"findAndModify": collection, "query": {"_id": job, "run_id": run_id},
                            "update": {"$set": {"expires_at": bson_date(exp)}}, "new": True,
                            "writeConcern": WRITE_CONCERN})
        if not res.get("value"):
            holder = self.find_one(collection, {"_id": job})
            raise LockLostError(f"mất khóa '{job}' (run_id {run_id}); khóa hiện tại: {holder}")

    def release_lock(self, job: str, run_id: str, collection: str = "job_locks") -> None:
        self.command({"delete": collection, "deletes": [{"q": {"_id": job, "run_id": run_id}, "limit": 1}],
                      "writeConcern": WRITE_CONCERN})
