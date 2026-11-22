"""
Gộp JSON đã parse + dữ liệu chép tay, ghi lại file JSON hoàn chỉnh và nạp vào MongoDB.
Chạy từ gốc repo (xem docs/data/du_lieu_kttv.md, mục 1.11):
    python -X utf8 pipelines/ingest/kttv/kttv_parser.py data/raw/kttv data/interim/kttv/kttv_ban_tin_parsed.json
    python -X utf8 pipelines/ingest/kttv/kttv_load_mongo.py data/interim/kttv/kttv_ban_tin_parsed.json data/interim/kttv/hydromet_bulletins.json data/raw/kttv
"""
import sys, os, json, hashlib, datetime as dt
from bson import json_util
from pymongo import MongoClient, ASCENDING, DESCENDING
from kttv_manual import MANUAL_DOCS
from kttv_parser import to_json
from kttv_english import to_english
from bson import ObjectId

MONGO_URI = "mongodb://localhost:27017"
DB, COLL = "vietnam_gis", "hydromet_bulletins"
OLD_COLL = "kttv_ban_tin_thuy_van"   # tên cũ (tiếng Việt) -> xóa khi nạp lại
VN = dt.timezone(dt.timedelta(hours=7))

path = sys.argv[1] if len(sys.argv) > 1 else "KTTV_json/kttv_ban_tin_parsed.json"
out_path = sys.argv[2] if len(sys.argv) > 2 else "KTTV_json/hydromet_bulletins.json"
pdf_root = sys.argv[3] if len(sys.argv) > 3 else "KTTV"
with open(path, encoding="utf-8") as fh:
    # {"$date": "...+07:00"} -> datetime tz-aware (mặc định json_util trả naive UTC -> lệch 7h)
    docs = json_util.loads(fh.read(), json_options=json_util.JSONOptions(tz_aware=True, tzinfo=dt.timezone.utc))

# json_util trả datetime UTC-aware; chuyển về naive giờ VN để ghép với dữ liệu chép tay
def to_vn_naive(o):
    if isinstance(o, dt.datetime):
        return o.astimezone(VN).replace(tzinfo=None) if o.tzinfo else o
    if isinstance(o, dict):
        return {k: to_vn_naive(v) for k, v in o.items()}
    if isinstance(o, list):
        return [to_vn_naive(v) for v in o]
    return o
docs = [to_vn_naive(d) for d in docs]

for d in docs:
    if d["_id"] in MANUAL_DOCS:
        keep = {k: d[k] for k in ("_id", "nguon", "khu_vuc")}
        d.clear()
        d.update(keep)
        d.update(MANUAL_DOCS[keep["_id"]]())
    d["nguon"]["la_ban_chinh"] = not d["nguon"].get("thu_muc_khac", False)
    # để đối chiếu với file gốc lưu trong hydromet_bulletin_files (nối qua source_file)
    pdf = os.path.join(pdf_root, d["_id"])
    if os.path.exists(pdf):
        data = open(pdf, "rb").read()
        d["nguon"]["size_bytes"] = len(data)
        d["nguon"]["sha256"] = hashlib.sha256(data).hexdigest()
    if d.get("ngay_ban_tin"):
        d["nam"], d["thang"] = d["ngay_ban_tin"].year, d["ngay_ban_tin"].month

# ---- chuyển sang schema tiếng Anh + _id ObjectId ----
docs = [to_english(d) for d in docs]
docs.sort(key=lambda d: d["source_file"])
for d in docs:
    d["_id"] = ObjectId()
docs = [{"_id": d.pop("_id"), **d} for d in docs]

def to_json_en(o):
    if isinstance(o, ObjectId):
        return {"$oid": str(o)}
    return to_json(o)

# ghi file JSON hoàn chỉnh (Extended JSON: $oid, $date ISO +07:00) -> dùng được với mongoimport --jsonArray
with open(out_path, "w", encoding="utf-8") as fh:
    json.dump(docs, fh, default=to_json_en, ensure_ascii=False, indent=1)

# datetime naive = giờ VN -> gắn tz để Mongo lưu đúng thời điểm (UTC)
def to_aware(o):
    if isinstance(o, dt.datetime):
        return o.replace(tzinfo=VN) if o.tzinfo is None else o
    if isinstance(o, dict):
        return {k: to_aware(v) for k, v in o.items()}
    if isinstance(o, list):
        return [to_aware(v) for v in o]
    return o

client = MongoClient(MONGO_URI, tz_aware=True)
db = client[DB]
for old in (COLL, OLD_COLL):
    if old in db.list_collection_names():
        db[old].drop()
db.create_collection(COLL, validator={"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "source_file", "source", "bulletin_type", "parse"],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "source_file": {"bsonType": "string"},
        "bulletin_type": {"enum": ["tide_forecast_5day", "spring_tide_warning", "salinity_intrusion_survey",
                                   "no_text_layer", "other", "error"]},
        "bulletin_date": {"bsonType": ["date", "null"]},
    }}})
col = db[COLL]
res = col.insert_many([to_aware(d) for d in docs], ordered=False)
col.create_index([("source_file", ASCENDING)], unique=True, name="source_file_unique")
col.create_index([("bulletin_date", DESCENDING)], name="bulletin_date_idx")
col.create_index([("bulletin_type", ASCENDING), ("bulletin_date", ASCENDING)], name="bulletin_type_date_idx")
col.create_index([("year", ASCENDING), ("month", ASCENDING)], name="year_month_idx")
col.create_index([("bulletin_no", ASCENDING)], name="bulletin_no_idx")
col.create_index([("forecast_water_levels.station", ASCENDING), ("forecast_water_levels.date", ASCENDING)], name="forecast_station_date_idx")
col.create_index([("observed_water_levels.station", ASCENDING), ("observation_date", ASCENDING)], name="observed_station_date_idx")
col.create_index([("reservoirs.reservoir", ASCENDING), ("observation_date", ASCENDING)], name="reservoir_date_idx")
col.create_index([("rainfall.stations.station", ASCENDING), ("observation_date", ASCENDING)], name="rainfall_station_date_idx")
print(f"Đã nạp {len(res.inserted_ids)} document vào {DB}.{COLL}; count = {col.count_documents({})}")
