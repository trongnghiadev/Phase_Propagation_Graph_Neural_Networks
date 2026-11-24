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

# ---------------------------------------------------------------- tham số CLI ----
# Chế độ nạp (ADR 0007 — "nạp thay thế toàn bộ" bằng staging + kiểm tra + rename):
#   --staging              nạp vào collection tạm `hydromet_bulletins__staging_<run_id>`
#   --run-id <id>         định danh lượt chạy (mặc định: timestamp UTC)
#   --promote             sau khi nạp staging: kiểm tra → sao lưu `__prev` → renameCollection
#   --g0-only             chỉ dựng lại collection chính + index từ dữ liệu đang có (chạy lại G0)
#   --no-validate         bỏ qua validator khi nạp staging
def _arg(flag: str) -> bool:
    return flag in sys.argv


def _arg_value(flag: str, default: str | None = None) -> str | None:
    if flag in sys.argv:
        i = sys.argv.index(flag)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return default


STAGING = _arg("--staging")
RUN_ID = _arg_value("--run-id") or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
PROMOTE = _arg("--promote")
G0_ONLY = _arg("--g0-only")
NO_VALIDATE = _arg("--no-validate")

#: Collection đích của lượt nạp (collection tạm khi --staging).
TARGET_COLL = f"{COLL}__staging_{RUN_ID}" if STAGING else COLL
#: Collection bản chính (đích rename khi promote).
MAIN_COLL = COLL
#: Collection sao lưu bản chính trước khi thay thế.
PREV_COLL = f"{COLL}__prev"

VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "source_file", "source", "bulletin_type", "parse"],
    "properties": {
        "_id": {"bsonType": "objectId"},
        "source_file": {"bsonType": "string"},
        "bulletin_type": {"enum": ["tide_forecast_5day", "spring_tide_warning", "salinity_intrusion_survey",
                                   "no_text_layer", "other", "error"]},
        "bulletin_date": {"bsonType": ["date", "null"]},
    }}}


def _drop_if_exists(name: str) -> None:
    """Xoá collection nếu có (chỉ dùng cho collection *tạm*, không dùng cho collection chính)."""
    if name in db.list_collection_names():
        db[name].drop()


def _create_with_validator(name: str, *, validate: bool = True) -> None:
    """Tạo collection kèm validator $jsonSchema (nếu `validate`)."""
    _drop_if_exists(name)
    if validate:
        db.create_collection(name, validator=VALIDATOR)
    else:
        db.create_collection(name)


def _build_indexes(col) -> None:
    """Dựng lại toàn bộ index của collection bản tin (giữ nguyên định nghĩa G0)."""
    col.create_index([("source_file", ASCENDING)], unique=True, name="source_file_unique")
    col.create_index([("bulletin_date", DESCENDING)], name="bulletin_date_idx")
    col.create_index([("bulletin_type", ASCENDING), ("bulletin_date", ASCENDING)], name="bulletin_type_date_idx")
    col.create_index([("year", ASCENDING), ("month", ASCENDING)], name="year_month_idx")
    col.create_index([("bulletin_no", ASCENDING)], name="bulletin_no_idx")
    col.create_index([("forecast_water_levels.station", ASCENDING), ("forecast_water_levels.date", ASCENDING)], name="forecast_station_date_idx")
    col.create_index([("observed_water_levels.station", ASCENDING), ("observation_date", ASCENDING)], name="observed_station_date_idx")
    col.create_index([("reservoirs.reservoir", ASCENDING), ("observation_date", ASCENDING)], name="reservoir_date_idx")
    col.create_index([("rainfall.stations.station", ASCENDING), ("observation_date", ASCENDING)], name="rainfall_station_date_idx")


def _checksum_index() -> dict:
    """Đối chiếu SHA-256 / số bản tin / số bản chính với `hydromet_bulletin_files`."""
    files_coll = db["hydromet_bulletin_files"]
    if files_coll not in db.list_collection_names():
        return {"n_files": 0, "n_primary": 0, "n_sha": 0, "sha_by_file": {}}
    sha_by_file, n_primary = {}, 0
    for f in files_coll.find({}, {"_id": 0, "sha256": 1, "sha256_file": 1, "source_file": 1,
                                 "path": 1, "nguon.la_ban_chinh": 1, "is_primary": 1}):
        sha = f.get("sha256") or f.get("sha256_file")
        name = f.get("source_file") or f.get("path")
        if sha and name:
            sha_by_file[name] = sha
        if f.get("is_primary") or (f.get("nguon") or {}).get("la_ban_chinh"):
            n_primary += 1
    return {"n_files": len(sha_by_file), "n_primary": n_primary, "n_sha": len(sha_by_file),
            "sha_by_file": sha_by_file}


def _validate_staging(target: str) -> dict:
    """Kiểm tra collection tạm trước khi thay thế collection chính.

    - số bản tin > 0;
    - mọi bản tin có SHA-256 khớp với `hydromet_bulletin_files` (nếu có file đối chiếu);
    - số bản chính khớp với số bản chính trong `hydromet_bulletin_files`.
    """
    col = db[target]
    n_docs = col.count_documents({})
    if n_docs == 0:
        raise SystemExit(f"[LỖI] collection tạm {target} rỗng — huỷ nạp lại (exit 3)")
    info = _checksum_index()
    sha_by_file = info["sha_by_file"]
    mismatch, checked = [], 0
    n_primary_loaded = 0
    for d in col.find({}, {"_id": 0, "source_file": 1, "source.sha256": 1, "source.is_primary": 1,
                           "source.la_ban_chinh": 1}):
        src = d.get("source") or {}
        if src.get("is_primary") or src.get("la_ban_chinh"):
            n_primary_loaded += 1
        got = src.get("sha256")
        want = sha_by_file.get(d.get("source_file"))
        if want and got:
            checked += 1
            if got != want:
                mismatch.append({"source_file": d.get("source_file"), "got": got, "want": want})
    if mismatch:
        raise SystemExit(f"[LỖI] {len(mismatch)} bản tin lệch SHA-256 — huỷ nạp lại (exit 4)")
    if info["n_primary"] and n_primary_loaded != info["n_primary"]:
        raise SystemExit(f"[LỖI] số bản chính lệch: nạp {n_primary_loaded}, "
                         f"file {info['n_primary']} — huỷ nạp lại (exit 5)")
    return {"n_bulletins": n_docs, "n_primary": n_primary_loaded, "n_sha_checked": checked,
            "n_files": info["n_files"]}


def _promote(target: str) -> None:
    """Kiểm tra → sao lưu `__prev` → `renameCollection(dropTarget=True)` → tăng `ver:`.

    Đây là bước **nguyên tử**: collection chính được thay bằng một lệnh `renameCollection`
    của MongoDB, nên không có trạng thái trung gian mà collection "chính" rỗng hoặc nửa vời.
    """
    checks = _validate_staging(target)
    # 1) sao lưu bản chính hiện tại sang `__prev` (để lùi lại được)
    if MAIN_COLL in db.list_collection_names():
        _drop_if_exists(PREV_COLL)
        db[MAIN_COLL].aggregate([{"$match": {}}, {"$out": PREV_COLL}], allowDiskUse=True)
    # 2) thay thế nguyên tử
    db.command({"renameCollection": f"{DB}.{target}", "to": f"{DB}.{MAIN_COLL}", "dropTarget": True})
    print(f"[OK] đã thay thế nguyên tử {DB}.{MAIN_COLL} (checks={checks})")
    return checks


# ------------------------------------------------------------ chạy nạp ----------
if G0_ONLY:
    # Chạy lại G0: dựng lại index + validator trên collection chính (không nạp lại dữ liệu).
    _drop_if_exists(OLD_COLL)
    col = db[MAIN_COLL]
    db.command({"collMod": MAIN_COLL, "validator": VALIDATOR, "validationLevel": "moderate"})
    _build_indexes(col)
    print(f"[G0] đã dựng lại validator + {len(col.index_information()) - 1} index cho {DB}.{MAIN_COLL}; "
          f"count = {col.count_documents({})}")
    raise SystemExit(0)

# Nạp vào collection tạm (mặc định của chế độ --staging) hoặc thẳng collection chính.
if STAGING:
    # Chế độ an toàn: KHÔNG xoá collection chính; nạp vào collection tạm.
    _create_with_validator(TARGET_COLL, validate=not NO_VALIDATE)
else:
    # Chế độ cũ (G0): nạp thẳng, có xoá collection chính trước (giữ tương thích ngược).
    for old in (COLL, OLD_COLL):
        if old in db.list_collection_names():
            db[old].drop()
    _create_with_validator(COLL, validate=True)
    TARGET_COLL = COLL

col = db[TARGET_COLL]
res = col.insert_many([to_aware(d) for d in docs], ordered=False)
_build_indexes(col)
print(f"Đã nạp {len(res.inserted_ids)} document vào {DB}.{TARGET_COLL}; count = {col.count_documents({})}")

if STAGING and PROMOTE:
    checks = _promote(TARGET_COLL)
    # Dọn staging còn sót (không bắt buộc — collection chính đã là bản mới)
    raise SystemExit(0)
elif STAGING:
    print(f"[--staging] đã nạp vào collection tạm {TARGET_COLL}. "
          f"Chạy lại với --promote để kiểm tra và thay thế nguyên tử.")
    raise SystemExit(0)
