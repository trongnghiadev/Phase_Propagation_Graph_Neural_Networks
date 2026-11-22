"""
Nạp file PDF gốc trong KTTV/ vào collection vietnam_gis.hydromet_bulletin_files,
mỗi PDF là 1 document, nội dung lưu dạng BinData (file lớn nhất < 1 MB, xa giới hạn 16 MB).
Liên kết với hydromet_bulletins qua source_file (unique ở cả hai collection).

    python kttv_load_files.py KTTV
"""
import sys, os, glob, hashlib, datetime as dt
from bson import Binary, ObjectId
from pymongo import MongoClient, ASCENDING

MONGO_URI = "mongodb://localhost:27017"
DB, COLL = "vietnam_gis", "hydromet_bulletin_files"
MAX_BYTES = 15 * 1024 * 1024    # chừa chỗ cho metadata trong giới hạn 16 MB của BSON
BATCH_BYTES = 32 * 1024 * 1024  # mỗi lần insert_many tối đa ~32 MB

root = sys.argv[1] if len(sys.argv) > 1 else "KTTV"
files = sorted(glob.glob(os.path.join(root, "**", "*.pdf"), recursive=True))

db = MongoClient(MONGO_URI)[DB]
if COLL in db.list_collection_names():
    db[COLL].drop()
db.create_collection(COLL, validator={"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "source_file", "file_name", "content_type", "size_bytes", "sha256", "content"],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "source_file": {"bsonType": "string"},
        "content_type": {"enum": ["application/pdf"]},
        "size_bytes": {"bsonType": ["int", "long"]},
        "sha256": {"bsonType": "string", "pattern": "^[0-9a-f]{64}$"},
        "content": {"bsonType": "binData"},
    }}})
col = db[COLL]

now = dt.datetime.now(dt.timezone.utc)
batch, batch_bytes, total, skipped = [], 0, 0, []
for f in files:
    rel = os.path.relpath(f, root).replace("\\", "/")
    data = open(f, "rb").read()
    if len(data) > MAX_BYTES:
        skipped.append(rel)
        continue
    batch.append({
        "_id": ObjectId(),
        "source_file": rel,
        "file_name": os.path.basename(f),
        "content_type": "application/pdf",
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_dataset": "KTTV Nam Bo - HCMC_TVHN",
        "uploaded_at": now,
        "content": Binary(data),
    })
    batch_bytes += len(data)
    if batch_bytes >= BATCH_BYTES:
        total += len(col.insert_many(batch, ordered=True).inserted_ids)
        batch, batch_bytes = [], 0
if batch:
    total += len(col.insert_many(batch, ordered=True).inserted_ids)

col.create_index([("source_file", ASCENDING)], unique=True, name="source_file_unique")
col.create_index([("sha256", ASCENDING)], name="sha256_idx")
print(f"Đã nạp {total}/{len(files)} PDF vào {DB}.{COLL}")
if skipped:
    print("Bỏ qua (>15 MB, cần GridFS):", skipped)
