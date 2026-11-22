"""Tạo / cập nhật collection `stations` trong MongoDB `vietnam_gis`.

Danh mục trạm thủy văn, trạm mưa và hồ chứa xuất hiện trong `hydromet_bulletins`,
kèm TỌA ĐỘ ƯỚC LƯỢNG (chưa phải tọa độ chính thức), reach MERIT được gán,
khoảng cách dọc sông tới cửa xả và lịch sử cấp báo động lấy từ bản tin.

Chạy lại nhiều lần được (upsert theo `code`). Bản ghi đã được người dùng xác nhận
(`location.method != "estimated"`) sẽ không bị ghi đè tọa độ.

    python -X utf8 pipelines/ingest/stations/create_stations.py [mongodb://localhost:27017]
"""
import sys
import unicodedata
from collections import defaultdict
from datetime import datetime, timezone

from pymongo import ASCENDING, GEOSPHERE, MongoClient

URI = sys.argv[1] if len(sys.argv) > 1 else "mongodb://localhost:27017"
db = MongoClient(URI)["vietnam_gis"]
B, M, S = db.hydromet_bulletins, db.merit_river_reaches, "stations"
PRIMARY = {"source.is_primary": True, "bulletin_type": "tide_forecast_5day"}

# --------------------------------------------------------------------------
# Tọa độ ƯỚC LƯỢNG [lon, lat]. confidence: medium ≈ sai số vài km; low ≈ chưa chắc vị trí.
# Cần thay bằng tọa độ chính thức khi có.
# --------------------------------------------------------------------------
WATER_LEVEL = {  # tên chuẩn: (lon, lat, confidence)
    "Vũng Tàu":    (107.070, 10.340, "medium"),
    "Nhà Bè":      (106.735, 10.665, "medium"),
    "Phú An":      (106.722, 10.775, "medium"),
    "Thủ Dầu Một": (106.650, 10.980, "medium"),
    "Biên Hòa":    (106.820, 10.950, "medium"),
    "Dầu Tiếng":   (106.360, 11.300, "medium"),
    "Tân An":      (106.410, 10.540, "medium"),
    "Bến Lức":     (106.490, 10.640, "medium"),
    "Gò Dầu Hạ":   (106.260, 11.080, "medium"),
}
RESERVOIRS = {  # vị trí đập, ước lượng
    "Trị An":     (107.050, 11.100, "medium"),
    "Thác Mơ":    (106.980, 11.840, "medium"),
    "Sr.P.Miêng": (106.820, 11.580, "low"),
    "Dầu Tiếng":  (106.360, 11.330, "medium"),
    "Sông Ray":   (107.360, 10.730, "low"),
    "Đá Đen":     (107.260, 10.550, "low"),
    "Sông Hỏa":   None,  # chưa xác định được vị trí
}
RAIN = {
    "Tân Sơn Hòa":  (106.665, 10.800, "medium"),
    "Nhà Bè":       (106.735, 10.665, "medium"),
    "Mạc Đĩnh Chi": (106.700, 10.787, "medium"),
    "Vũng Tàu":     (107.070, 10.340, "medium"),
    "Thủ Dầu Một":  (106.650, 10.980, "medium"),
    "Củ Chi":       (106.500, 10.970, "low"),
    "Hóc Môn":      (106.590, 10.890, "low"),
    "Cần Giờ":      (106.950, 10.410, "low"),
    "Bình Chánh":   (106.600, 10.690, "low"),
    "Phú An":       (106.722, 10.775, "medium"),
}
SNAP_RADIUS_M = 3000


def slug(name: str) -> str:
    s = name.replace("Đ", "D").replace("đ", "d")
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    return "".join(c if c.isalnum() else "_" for c in s).strip("_").upper()


def point(c):
    return None if c is None else {"type": "Point", "coordinates": [c[0], c[1]]}


# ---------------- tên gốc, sông, số bản tin có số liệu ----------------
def collect(path, name_f, extra_f):
    info = {}
    for d in B.find(PRIMARY, {path: 1}):
        arr = d
        for p in path.split("."):
            arr = arr.get(p, {}) if isinstance(arr, dict) else {}
        for r in arr or []:
            n = r[name_f]
            e = info.setdefault(n, {"aliases": set(), "count": 0, "extra": {}})
            e["count"] += 1
            for k in ("station_raw", "reservoir_raw"):
                if r.get(k):
                    e["aliases"].add(r[k])
            for k in extra_f:
                if r.get(k):
                    e["extra"].setdefault(k, set()).add(r[k])
            if r.get("river_raw"):
                e["extra"].setdefault("river_raw", set()).add(r["river_raw"])
    return info


wl_info = collect("observed_water_levels", "station", ["river"])
res_info = collect("reservoirs", "reservoir", ["river", "province"])
rain_info = collect("rainfall.stations", "station", [])

# ---------------- lịch sử cấp báo động ----------------
alarm_runs = {}
for d in B.find(PRIMARY, {"alarm_levels": 1, "bulletin_date": 1}).sort("bulletin_date", 1):
    a = d.get("alarm_levels") or {}
    lv = (a.get("level_1_m"), a.get("level_2_m"), a.get("level_3_m"))
    for st in a.get("stations", []):
        runs = alarm_runs.setdefault(st, [])
        if runs and runs[-1]["levels"] == lv:
            runs[-1]["to"] = d["bulletin_date"]
            runs[-1]["n_bulletins"] += 1
        else:
            runs.append({"from": d["bulletin_date"], "to": d["bulletin_date"], "levels": lv, "n_bulletins": 1})

# ---------------- đồ thị MERIT: hạ lưu, khoảng cách tới cửa xả ----------------
reach = {r["COMID"]: r for r in M.find({}, {"COMID": 1, "NextDownID": 1, "lengthkm": 1, "uparea": 1, "order": 1})}


def downstream_info(comid):
    km, c = 0.0, comid
    while True:
        km += reach[c]["lengthkm"]
        nd = reach[c]["NextDownID"]
        if nd not in reach:
            return c, km, nd == 0
        c = nd


def snap(coord):
    """Chọn reach có uparea lớn nhất trong bán kính (ưu tiên dòng chính, tránh nhánh nhỏ)."""
    if coord is None:
        return None
    pt = {"type": "Point", "coordinates": [coord[0], coord[1]]}
    cands = list(M.aggregate([
        {"$geoNear": {"near": pt, "distanceField": "dist_m", "maxDistance": SNAP_RADIUS_M, "spherical": True}},
        {"$project": {"COMID": 1, "uparea": 1, "order": 1, "dist_m": 1}},
        {"$limit": 50}]))
    if not cands:
        return {"COMID": None, "method": "none_within_radius", "radius_m": SNAP_RADIUS_M, "candidates": 0}
    nearest = cands[0]
    best = max(cands, key=lambda r: r["uparea"])
    root, km, is_outlet = downstream_info(best["COMID"])
    return {
        "COMID": best["COMID"], "uparea_km2": round(best["uparea"], 1), "order": best["order"],
        "distance_m": round(best["dist_m"]), "method": "max_uparea_within_radius",
        "radius_m": SNAP_RADIUS_M, "candidates": len(cands),
        "nearest_COMID": nearest["COMID"], "nearest_uparea_km2": round(nearest["uparea"], 1),
        "basin_root_COMID": root, "root_is_outlet": is_outlet,
        "dist_to_root_km": round(km, 1),
        "verified_by": None,
    }


# ---------------- tạo collection, validator, index ----------------
validator = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["code", "name", "kind", "location", "location_meta", "updated_at"],
    "properties": {
        "code": {"bsonType": "string", "pattern": "^[A-Z0-9_]+$"},
        "name": {"bsonType": "string"},
        "kind": {"enum": ["water_level", "sea_level", "rain", "reservoir"]},
        "location": {"bsonType": ["object", "null"]},
        "location_meta": {"bsonType": "object", "required": ["method", "confidence"],
                          "properties": {"method": {"enum": ["estimated", "official", "verified"]},
                                         "confidence": {"enum": ["high", "medium", "low", "unknown"]}}},
        "merit": {"bsonType": ["object", "null"]},
    }}}
if S not in db.list_collection_names():
    db.create_collection(S, validator=validator, validationLevel="moderate")
else:
    db.command("collMod", S, validator=validator, validationLevel="moderate")
col = db[S]
col.create_index([("code", ASCENDING)], unique=True, name="code_unique")
col.create_index([("location", GEOSPHERE)], name="location_2dsphere")
col.create_index([("kind", ASCENDING), ("name", ASCENDING)], name="kind_name_idx")
col.create_index([("merit.COMID", ASCENDING)], name="merit_comid_idx")

now = datetime.now(timezone.utc)
docs = []
for name, c in WATER_LEVEL.items():
    i = wl_info.get(name, {"aliases": set(), "count": 0, "extra": {}})
    river = sorted(i["extra"].get("river", {""}))[0]
    kind = "sea_level" if name == "Vũng Tàu" else "water_level"
    docs.append({
        "code": slug(name), "name": name, "kind": kind, "river": river,
        "aliases": sorted({name} | i["aliases"]),
        "river_aliases": sorted(i["extra"].get("river_raw", set())),
        "coord": c, "bulletin_rows": i["count"],
        "alarm_history": [{"from": r["from"], "to": r["to"], "level_1_m": r["levels"][0],
                           "level_2_m": r["levels"][1], "level_3_m": r["levels"][2],
                           "n_bulletins": r["n_bulletins"]} for r in alarm_runs.get(name, [])],
        "datum": "0 Hải đồ" if name == "Vũng Tàu" else None,
        "has_official_forecast": name in ("Phú An", "Nhà Bè", "Thủ Dầu Một"),
    })
for name, c in RESERVOIRS.items():
    i = res_info.get(name, {"aliases": set(), "count": 0, "extra": {}})
    docs.append({
        "code": "RES_" + slug(name), "name": name, "kind": "reservoir",
        "river": sorted(i["extra"].get("river", {""}))[0],
        "province_history": sorted(i["extra"].get("province", set())),
        "aliases": sorted({name} | i["aliases"]), "coord": c, "bulletin_rows": i["count"],
    })
for name, c in RAIN.items():
    i = rain_info.get(name, {"aliases": set(), "count": 0, "extra": {}})
    docs.append({
        "code": "RAIN_" + slug(name), "name": name, "kind": "rain",
        "aliases": sorted({name} | i["aliases"]), "coord": c, "bulletin_rows": i["count"],
    })

# ---------------- địa giới hành chính: tỉnh CŨ (trước 01/07/2025) và MỚI ----------------
# Cần collection admin_units (pipelines/ingest/admin_units/create_admin_units.py). Chưa có ranh giới đa giác nên chỉ gán
# đến cấp TỈNH: trạm theo vị trí đã biết, hồ chứa theo nhãn tỉnh ghi trong bản tin.
OLD_ERA, NEW_ERA = "before_2025_07", "from_2025_07"
EFFECTIVE_UTC = datetime(2025, 6, 30, 17, 0)     # 01/07/2025 00:00 giờ Việt Nam, lưu UTC (naive)
STATION_PROVINCE = {  # tên trạm: (tỉnh trước 01/07/2025, tỉnh từ 01/07/2025)
    "Vũng Tàu": ("Bà Rịa - Vũng Tàu", "Hồ Chí Minh"),
    "Nhà Bè": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Phú An": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Thủ Dầu Một": ("Bình Dương", "Hồ Chí Minh"),
    "Biên Hòa": ("Đồng Nai", "Đồng Nai"),
    "Dầu Tiếng": ("Bình Dương", "Hồ Chí Minh"),
    "Tân An": ("Long An", "Tây Ninh"),
    "Bến Lức": ("Long An", "Tây Ninh"),
    "Gò Dầu Hạ": ("Tây Ninh", "Tây Ninh"),
    "Tân Sơn Hòa": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Mạc Đĩnh Chi": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Củ Chi": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Hóc Môn": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Cần Giờ": ("Hồ Chí Minh", "Hồ Chí Minh"),
    "Bình Chánh": ("Hồ Chí Minh", "Hồ Chí Minh"),
}
has_admin = "admin_units" in db.list_collection_names()


def norm_name(s):
    s = " ".join(s.strip().lower().split())
    for p in ["thành phố trung ương", "thành phố", "tỉnh"]:
        if s.startswith(p + " "):
            s = s[len(p) + 1:]
            break
    s = unicodedata.normalize("NFKD", s.replace("đ", "d")).encode("ascii", "ignore").decode()
    return s.replace("-", " ").replace("  ", " ").strip()


def find_province(era, label):
    if not has_admin or not label:
        return None
    n = norm_name(label)
    u = db.admin_units.find_one({"era": era, "level": "province", "$or": [{"name_norm": n}, {"aliases_norm": n}]},
                                {"_id": 0, "code": 1, "name": 1})
    return {"code": u["code"], "name": u["name"]} if u else None


# Nhãn tỉnh của hồ chứa trong bản tin, chia theo đoạn liên tiếp và theo thời kỳ
res_runs = defaultdict(list)
for b in B.find(PRIMARY, {"reservoirs.reservoir": 1, "reservoirs.province": 1, "bulletin_date": 1}).sort("bulletin_date", 1):
    era = NEW_ERA if b["bulletin_date"] >= EFFECTIVE_UTC else OLD_ERA
    for r in b.get("reservoirs", []):
        runs = res_runs[r["reservoir"]]
        label = r.get("province")
        if runs and runs[-1]["label"] == label and runs[-1]["era"] == era:
            runs[-1]["to"] = b["bulletin_date"]
            runs[-1]["n_bulletins"] += 1
        else:
            runs.append({"label": label, "era": era, "from": b["bulletin_date"], "to": b["bulletin_date"],
                         "n_bulletins": 1})


def reservoir_admin(name):
    hist, before, after = [], None, None
    for r in res_runs.get(name, []):
        p = find_province(r["era"], r["label"])
        note = None
        if p is None and r["era"] == NEW_ERA:
            p = find_province(OLD_ERA, r["label"])
            if p:
                note = "bản tin còn dùng tên tỉnh cũ sau ngày hiệu lực 01/07/2025"
        hist.append({**r, "province_code": p["code"] if p else None, "province_name": p["name"] if p else None,
                     "matched_era": (r["era"] if not note else OLD_ERA) if p else None, "note": note})
        if p and r["era"] == OLD_ERA:
            before = p
        if p and r["era"] == NEW_ERA and note is None:
            after = p
    return hist, before, after


def station_admin(d):
    if not has_admin:
        return None
    if d["kind"] == "reservoir":
        hist, before, after = reservoir_admin(d["name"])
        d["admin_history"] = hist
        return {OLD_ERA: before, NEW_ERA: after, "level": "province",
                "method": "nhãn tỉnh trong hydromet_bulletins.reservoirs.province",
                "note": None if before else "bản tin không ghi tỉnh trước 01/07/2025"}
    names = STATION_PROVINCE.get(d["name"])
    if not names:
        return None
    return {OLD_ERA: find_province(OLD_ERA, names[0]), NEW_ERA: find_province(NEW_ERA, names[1]),
            "level": "province", "method": "theo vị trí đã biết của trạm (chưa có ranh giới đa giác)"}


for d in docs:
    d["admin"] = station_admin(d)
    c = d.pop("coord")
    existing = col.find_one({"code": d["code"]}, {"location_meta": 1})
    keep_location = existing and existing.get("location_meta", {}).get("method") != "estimated"
    setf = {**d, "updated_at": now, "source": "hydromet_bulletins (tên, sông, cấp báo động); tọa độ ước lượng"}
    if not keep_location:
        setf["location"] = point(c)
        setf["location_meta"] = {"method": "estimated", "confidence": c[2] if c else "unknown",
                                 "note": "Tọa độ ước lượng, cần thay bằng tọa độ chính thức"}
        m = snap(c) if d["kind"] in ("water_level", "reservoir") else None
        reasons = ["tọa độ ước lượng"]
        if m and m.get("uparea_km2") is not None:
            if (d["kind"] == "reservoir" and m["uparea_km2"] < 500) or \
               (d["kind"] == "water_level" and m["uparea_km2"] < 1000):
                reasons.append("reach được gán có uparea nhỏ bất thường")
            if not m["root_is_outlet"] or m["basin_root_COMID"] != 44045960:
                reasons.append("không thuộc cây Đồng Nai – Sài Gòn (44045960)")
        setf["merit"] = m
        setf["needs_review"] = True
        setf["review_reasons"] = reasons
    col.update_one({"code": d["code"]}, {"$set": setf, "$setOnInsert": {"created_at": now, "version": 1}},
                   upsert=True)

print("stations:", col.count_documents({}))
for d in col.find({}, {"_id": 0, "code": 1, "kind": 1, "location.coordinates": 1, "location_meta.confidence": 1,
                       "merit.COMID": 1, "merit.uparea_km2": 1, "merit.nearest_uparea_km2": 1,
                       "merit.basin_root_COMID": 1, "merit.dist_to_root_km": 1, "bulletin_rows": 1}).sort("code"):
    print(d)
