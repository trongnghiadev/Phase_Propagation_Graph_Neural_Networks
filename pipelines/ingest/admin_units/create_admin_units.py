"""Nạp đơn vị hành chính CŨ (trước 01/07/2025) và MỚI (từ 01/07/2025) vào MongoDB `vietnam_gis`.

Đầu vào: data/admin_units/ (tải bằng pipelines/ingest/admin_units/fetch_admin_units.py từ provinces.open-api.vn)
Đầu ra:
  admin_units          mỗi tỉnh / huyện / xã của cả hai thời kỳ là 1 document, khóa (era, level, code)
  admin_unit_mappings  mỗi cặp (xã cũ, xã mới) là 1 document (quan hệ nhiều – nhiều)
  province_timeline    97 tỉnh (63 cũ + 34 mới) với khoảng hiệu lực valid_from/valid_to và quan hệ
                       sáp nhập, để xem / quy đổi số liệu theo thời gian trước và sau 01/07/2025

Lưu ý: cùng một `code` có thể là hai đơn vị KHÁC NHAU ở hai thời kỳ (ví dụ tỉnh 80: Long An → Tây Ninh),
nên luôn truy vấn kèm `era`.

Tên chuẩn hóa (`name_norm`, `aliases_norm`) dùng `vngis.text.norm_name` (bản duy nhất trong repo).

Nạp an toàn: ghi vào collection tạm `*__staging`, kiểm tra, rồi `renameCollection` (dropTarget) – thay thế
nguyên tử, người đọc không thấy dữ liệu dở dang.

    python -X utf8 pipelines/ingest/admin_units/create_admin_units.py [mongodb_uri]

Kết nối đọc bằng `vngis.config` (APP_ENV, MONGO_URI, MONGO_DB; tham số dòng lệnh `mongodb_uri` ghi đè URI);
tên collection theo `mongo.collections`. Cần package vngis (pymongo, pydantic, pyyaml).
"""
import json
import os
import sys
from collections import Counter, defaultdict
from datetime import datetime, timezone

from pymongo import ASCENDING

try:                                   # cài package vngis (container/workspace) hoặc chạy thẳng từ repo
    import vngis  # noqa: F401
except ImportError:
    sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "..", "packages", "vngis", "src"))
from vngis.config import ConfigError, load_config  # noqa: E402
from vngis.db import get_db  # noqa: E402
from vngis.text import ADMIN_PREFIXES as PREFIXES, norm_name as norm  # noqa: E402

SRC = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "admin_units")
OLD, NEW = "before_2025_07", "from_2025_07"
EFFECTIVE = datetime(2025, 7, 1)                    # ngày hiệu lực (giờ Việt Nam)
LAST_OLD = datetime(2025, 6, 30)
SOURCE = "provinces.open-api.vn"

# Cách viết tắt hay gặp trong bản tin và tài liệu
EXTRA_ALIASES = {
    (NEW, "province", 79): ["TPHCM", "TP.HCM", "TP. HCM", "TP. Hồ Chí Minh", "Hồ Chí Minh", "Sài Gòn"],
    (OLD, "province", 79): ["TPHCM", "TP.HCM", "TP. HCM", "TP. Hồ Chí Minh", "Hồ Chí Minh", "Sài Gòn"],
    (OLD, "province", 77): ["BR-VT", "BRVT", "Bà Rịa Vũng Tàu"],
}


def short_name(name: str, division_type: str) -> str:
    low = name.lower()
    for p in [division_type] + PREFIXES:
        if low.startswith(p + " "):
            return name[len(p) + 1:]
    return name


def unit(era, level, src, **extra):
    d = {
        "_id": f"{era}:{level}:{src['code']}",
        "era": era, "level": level, "code": src["code"],
        "name": src["name"], "short_name": short_name(src["name"], src["division_type"]),
        "division_type": src["division_type"], "codename": src["codename"],
        "name_norm": norm(src["name"]),
        "valid_from": EFFECTIVE if era == NEW else None,
        "valid_to": LAST_OLD if era == OLD else None,
        "source": SOURCE,
    }
    if src.get("short_codename"):
        d["short_codename"] = src["short_codename"]
    if src.get("phone_code"):
        d["phone_code"] = src["phone_code"]
    d.update(extra)
    aliases = EXTRA_ALIASES.get((era, level, src["code"]), [])
    if aliases:
        d["aliases"] = aliases
        d["aliases_norm"] = sorted({norm(a) for a in aliases})
    return d


def main():
    v1 = json.load(open(os.path.join(SRC, "v1_depth3.json"), encoding="utf-8"))
    v2 = json.load(open(os.path.join(SRC, "v2_depth2.json"), encoding="utf-8"))
    leg = [json.loads(l) for l in open(os.path.join(SRC, "v2_to_legacies.jsonl"), encoding="utf-8")]
    meta = json.load(open(os.path.join(SRC, "fetch_meta.json"), encoding="utf-8")) \
        if os.path.exists(os.path.join(SRC, "fetch_meta.json")) else {}
    fetched_at = meta.get("fetched_at")

    # ---------------- documents đơn vị ----------------
    docs = []
    old_ward = {}
    for p in v1:
        docs.append(unit(OLD, "province", p, province_code=p["code"]))
        for d in p["districts"]:
            docs.append(unit(OLD, "district", d, province_code=p["code"], district_code=d["code"]))
            for w in d["wards"]:
                docs.append(unit(OLD, "ward", w, province_code=p["code"], district_code=d["code"]))
                old_ward[w["code"]] = (w, d, p)
    new_ward = {}
    for p in v2:
        docs.append(unit(NEW, "province", p, province_code=p["code"]))
        for w in p["wards"]:
            docs.append(unit(NEW, "ward", w, province_code=p["code"]))
            new_ward[w["code"]] = (w, p)

    # ---------------- ánh xạ xã cũ ↔ xã mới ----------------
    maps, missing_old = [], []
    for row in leg:
        nw, np_ = new_ward[row["code"]]
        for lw in row["legacies"]:
            if lw["code"] not in old_ward:
                missing_old.append(lw["code"])
                continue
            ow, od, op = old_ward[lw["code"]]
            maps.append({
                "_id": f"{ow['code']}->{nw['code']}",
                "old": {"ward_code": ow["code"], "ward_name": ow["name"], "district_code": od["code"],
                        "district_name": od["name"], "province_code": op["code"], "province_name": op["name"]},
                "new": {"ward_code": nw["code"], "ward_name": nw["name"],
                        "province_code": np_["code"], "province_name": np_["name"]},
                "source": SOURCE,
            })

    # Kế thừa cấp tỉnh suy ra từ ánh xạ cấp xã
    succ = defaultdict(Counter)
    pred = defaultdict(Counter)
    for m in maps:
        succ[m["old"]["province_code"]][m["new"]["province_code"]] += 1
        pred[m["new"]["province_code"]][m["old"]["province_code"]] += 1
    # và cấp xã
    w_succ, w_pred = defaultdict(set), defaultdict(set)
    for m in maps:
        w_succ[m["old"]["ward_code"]].add(m["new"]["ward_code"])
        w_pred[m["new"]["ward_code"]].add(m["old"]["ward_code"])
    pname_old = {p["code"]: p["name"] for p in v1}
    pname_new = {p["code"]: p["name"] for p in v2}
    for d in docs:
        if d["level"] == "province" and d["era"] == OLD:
            d["successors"] = [{"code": c, "name": pname_new[c], "n_ward_links": n}
                               for c, n in succ[d["code"]].most_common()]
        elif d["level"] == "province" and d["era"] == NEW:
            d["predecessors"] = [{"code": c, "name": pname_old[c], "n_ward_links": n}
                                 for c, n in pred[d["code"]].most_common()]
        elif d["level"] == "ward" and d["era"] == OLD:
            d["successor_ward_codes"] = sorted(w_succ.get(d["code"], []))
        elif d["level"] == "ward" and d["era"] == NEW:
            d["predecessor_ward_codes"] = sorted(w_pred.get(d["code"], []))

    # ---------------- province_timeline: tỉnh cũ/mới theo thời gian ----------------
    # valid_from / valid_to là mốc 00:00 giờ Việt Nam lưu dạng UTC; valid_to là mốc KẾT THÚC (không bao gồm).
    # Tỉnh hiệu lực tại thời điểm t: valid_from <= t < valid_to (null = không giới hạn phía đó).
    cut = datetime(2025, 6, 30, 17, 0)             # 01/07/2025 00:00 giờ Việt Nam
    n_wards_old = Counter(w[2]["code"] for w in old_ward.values())
    n_dist_old = {p["code"]: len(p["districts"]) for p in v1}
    n_wards_new = {p["code"]: len(p["wards"]) for p in v2}
    timeline = []
    for p in v1:
        s = succ[p["code"]]
        main_code, main_links = s.most_common(1)[0]
        group = [o for o, cnt in pred[main_code].items()]
        status = ("giữ nguyên" if len(group) == 1 and len(s) == 1 else "sáp nhập")
        timeline.append({
            "_id": f"{OLD}:{p['code']}", "era": OLD, "code": p["code"], "name": p["name"],
            "short_name": short_name(p["name"], p["division_type"]), "name_norm": norm(p["name"]),
            "division_type": p["division_type"], "codename": p["codename"], "phone_code": p.get("phone_code"),
            "valid_from": None, "valid_to": cut,
            "n_districts": n_dist_old[p["code"]], "n_wards": n_wards_old[p["code"]],
            "status": status,
            "successor": {"code": main_code, "name": pname_new[main_code],
                          "share_of_ward_links": round(main_links / sum(s.values()), 4)},
            "other_successors": [{"code": c, "name": pname_new[c], "n_ward_links": n}
                                 for c, n in s.most_common()[1:]],
            "merged_with": sorted(({"code": o, "name": pname_old[o]} for o in group if o != p["code"]),
                                  key=lambda x: x["code"]),
            "keeps_name": norm(pname_new[main_code]) == norm(p["name"]),
            "code_reused_by_successor": main_code == p["code"],
            "source": SOURCE,
        })
    for p in v2:
        pr = pred[p["code"]]
        total = sum(pr.values())
        formed = [{"code": c, "name": pname_old[c], "n_ward_links": n, "share_of_ward_links": round(n / total, 4)}
                  for c, n in pr.most_common()]
        timeline.append({
            "_id": f"{NEW}:{p['code']}", "era": NEW, "code": p["code"], "name": p["name"],
            "short_name": short_name(p["name"], p["division_type"]), "name_norm": norm(p["name"]),
            "division_type": p["division_type"], "codename": p["codename"], "phone_code": p.get("phone_code"),
            "valid_from": cut, "valid_to": None,
            "n_districts": 0, "n_wards": n_wards_new[p["code"]],
            "status": "giữ nguyên" if len(formed) == 1 else "hợp nhất",
            "formed_from": formed,
            "same_code_old_name": pname_old.get(p["code"]),   # tên tỉnh cũ từng dùng cùng mã (có thể khác!)
            "source": SOURCE,
        })
    for t in timeline:
        aliases = EXTRA_ALIASES.get((t["era"], "province", t["code"]), [])
        if aliases:
            t["aliases"] = aliases
            t["aliases_norm"] = sorted({norm(a) for a in aliases})

    # ---------------- kiểm tra trước khi nạp ----------------
    c = Counter((d["era"], d["level"]) for d in docs)
    report = {
        "units": {f"{e}/{l}": n for (e, l), n in sorted(c.items())},
        "mapping_pairs": len(maps),
        "new_wards_with_mapping": sum(1 for r in leg if r["legacies"]),
        "new_wards_without_mapping": [r["code"] for r in leg if not r["legacies"]],
        "old_wards_mapped": len(w_succ), "old_wards_total": len(old_ward),
        "old_wards_split": sum(1 for v in w_succ.values() if len(v) > 1),
        "legacy_codes_not_in_v1": sorted(set(missing_old)),
        "old_provinces_split": {pname_old[k]: [pname_new[x] for x in v] for k, v in succ.items() if len(v) > 1},
        "timeline": {"provinces": len(timeline),
                     "new_unchanged": sum(1 for t in timeline if t["era"] == NEW and t["status"] == "giữ nguyên"),
                     "new_merged": sum(1 for t in timeline if t["era"] == NEW and t["status"] == "hợp nhất"),
                     "same_code_different_name": [f"{t['code']}: {t['same_code_old_name']} → {t['name']}"
                                                  for t in timeline if t["era"] == NEW and t.get("same_code_old_name")
                                                  and norm(t["same_code_old_name"]) != t["name_norm"]]},
    }
    assert c[(OLD, "province")] == 63 and c[(NEW, "province")] == 34, c
    assert len(leg) == c[(NEW, "ward")], "Chưa tải đủ ánh xạ cho mọi xã mới"
    assert all(succ[p["code"]] for p in v1), "Có tỉnh cũ không có tỉnh kế thừa"

    # ---------------- nạp nguyên tử ----------------
    try:                                            # APP_ENV, MONGO_URI, MONGO_DB; tham số dòng lệnh ghi đè URI
        env = dict(os.environ)
        if len(sys.argv) > 1:
            env["MONGO_URI"] = sys.argv[1]
        cfg = load_config(environ=env)
    except ConfigError as e:
        sys.exit(f"Lỗi cấu hình: {e}")
    db = get_db(cfg)
    names = cfg.mongo.collections
    now = datetime.now(timezone.utc)
    for d in docs:
        d["loaded_at"] = now
        d["fetched_at"] = fetched_at
    for m in maps:
        m["loaded_at"] = now

    unit_validator = {"$jsonSchema": {
        "bsonType": "object", "required": ["_id", "era", "level", "code", "name", "province_code"],
        "properties": {"era": {"enum": [OLD, NEW]}, "level": {"enum": ["province", "district", "ward"]},
                       "code": {"bsonType": "int"}, "province_code": {"bsonType": "int"}}}}
    map_validator = {"$jsonSchema": {
        "bsonType": "object", "required": ["old", "new"],
        "properties": {"old": {"bsonType": "object", "required": ["ward_code", "province_code"]},
                       "new": {"bsonType": "object", "required": ["ward_code", "province_code"]}}}}

    def load(name, rows, validator, indexes):
        tmp = name + "__staging"
        db.drop_collection(tmp)
        db.create_collection(tmp, validator=validator)
        db[tmp].insert_many(rows, ordered=True)
        for keys, opts in indexes:
            db[tmp].create_index(keys, **opts)
        assert db[tmp].count_documents({}) == len(rows)
        db.client.admin.command("renameCollection", f"{db.name}.{tmp}", to=f"{db.name}.{name}", dropTarget=True)

    load(names.admin_units, docs, unit_validator, [
        ([("era", ASCENDING), ("level", ASCENDING), ("code", ASCENDING)], {"unique": True, "name": "era_level_code_unique"}),
        ([("era", ASCENDING), ("level", ASCENDING), ("name_norm", ASCENDING)], {"name": "era_level_name_norm_idx"}),
        ([("aliases_norm", ASCENDING)], {"name": "aliases_norm_idx", "sparse": True}),
        ([("era", ASCENDING), ("province_code", ASCENDING), ("level", ASCENDING)], {"name": "era_province_level_idx"}),
        ([("era", ASCENDING), ("district_code", ASCENDING)], {"name": "era_district_idx", "sparse": True}),
    ])
    timeline_validator = {"$jsonSchema": {
        "bsonType": "object", "required": ["_id", "era", "code", "name", "valid_from", "valid_to", "status"],
        "properties": {"era": {"enum": [OLD, NEW]}, "code": {"bsonType": "int"},
                       "valid_from": {"bsonType": ["date", "null"]}, "valid_to": {"bsonType": ["date", "null"]},
                       "status": {"enum": ["giữ nguyên", "sáp nhập", "hợp nhất"]}}}}
    for t in timeline:
        t["loaded_at"] = now
    load(names.province_timeline, timeline, timeline_validator, [
        ([("era", ASCENDING), ("code", ASCENDING)], {"unique": True, "name": "era_code_unique"}),
        ([("valid_from", ASCENDING), ("valid_to", ASCENDING)], {"name": "validity_idx"}),
        ([("name_norm", ASCENDING)], {"name": "name_norm_idx"}),
        ([("aliases_norm", ASCENDING)], {"name": "aliases_norm_idx", "sparse": True}),
        ([("successor.code", ASCENDING)], {"name": "successor_idx", "sparse": True}),
    ])
    load(names.admin_unit_mappings, maps, map_validator, [
        ([("old.ward_code", ASCENDING)], {"name": "old_ward_idx"}),
        ([("new.ward_code", ASCENDING)], {"name": "new_ward_idx"}),
        ([("old.province_code", ASCENDING), ("new.province_code", ASCENDING)], {"name": "old_new_province_idx"}),
    ])
    report["loaded_at"] = now.isoformat()
    json.dump(report, open(os.path.join(SRC, "load_report.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(report, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
