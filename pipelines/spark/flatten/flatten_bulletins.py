"""G0 – Làm phẳng bản tin KTTV (`hydromet_bulletins`) thành 4 collection.

    spark-submit flatten_bulletins.py [mongodb_uri] [--db vietnam_gis] [--max-lag-days 31] [--lock-ttl-s 3600]
                                      [--dry-run] [--keep-staging] [--ack-rollback RUN_ID]

URI/DB mặc định lấy từ biến `MONGO_URI`/`MONGO_DB` (như `vngis.config`), rồi `mongodb://mongo:27017`/`vietnam_gis`.
Đầu vào: bản tin chính (`source.is_primary = true`, `bulletin_type = tide_forecast_5day`),
lọc và bỏ `full_text` ngay trên MongoDB. Đầu ra:

| Collection           | Một document là                         |
|----------------------|-----------------------------------------|
| `tide_extremes`      | một đỉnh/chân triều thực đo (time series) |
| `official_forecasts` | (bản tin, trạm, ngày dự báo)            |
| `reservoir_daily`    | (ngày quan trắc, hồ)                    |
| `rainfall_daily`     | (ngày quan trắc, trạm mưa)              |

Quy trình: giữ khóa `job_locks` → ghi `<tên>__staging_<run_id>` → kiểm tra (gồm số lượng so với kỳ vọng) →
sao lưu bản chính thành `<tên>__prev` → thay bản chính (`renameCollection(dropTarget)`; time series dùng `$out`
vì không đổi tên được trên MongoDB 8.0) → xóa `__prev`. Kiểm tra nào hỏng thì dừng, giữ nguyên bản chính.
Mỗi lần chạy ghi một bản ghi `jobs` (`_id = run_id`, `generated_at`, trạng thái, số lượng, collection đã thay).
Khóa được gia hạn và xác nhận (`findAndModify {_id, run_id}`) trước mỗi pha dài và trước MỖI lần thay.
Mã thoát: 0 ok (hoặc --ack-rollback thành công), 1 kiểm tra hỏng (hoặc --ack-rollback sai run_id), 2 tên không
tra được mã, 3 job đang chạy nơi khác, 4 còn `__prev` của lần chạy hỏng giữa chừng (lùi lại rồi --ack-rollback),
5 mất khóa giữa chừng, 6 lỗi không lường trước. Trạng thái `jobs.status`: running → ok / failed / dry_run / blocked
(mã 4); rolled_back (sau --ack-rollback). Trước MỖI lần thay ghi `replacing = <tên>`; ngay sau lệnh thay thành
công ghi `replaced` (trước khi đếm lại), để process chết giữa chừng vẫn để lại dấu vết.
"""
from __future__ import annotations

import argparse
import os
import secrets
import sys
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import DataFrame, functions as F  # noqa: E402
from pyspark.sql.window import Window  # noqa: E402

from common.session import (JobLockError, LockLostError, MongoAdmin, bson_date, build_session, env_db, env_uri,  # noqa: E402
                            read_mongo, write_mongo)
from flatten import rules  # noqa: E402
from flatten.schemas import BULLETIN, PROVINCE, STATION  # noqa: E402

JOB = "g0_flatten_bulletins"
NAMES = ("tide_extremes", "official_forecasts", "reservoir_daily", "rainfall_daily")
PRIMARY_MATCH = {"source.is_primary": True, "bulletin_type": "tide_forecast_5day"}
SOURCE_PIPELINE = [{"$match": PRIMARY_MATCH}, {"$project": {"full_text": 0}}]

# metaField chỉ gồm khóa nhận dạng chuỗi (trạm, loại đỉnh/chân): mỗi bucket gom nhiều bản tin.
TS_OPTIONS = {"timeField": "ts", "metaField": "meta", "granularity": "hours"}
STAGING = "__staging"
PREV = "__prev"

# Mã chất lượng của một đỉnh/chân: cờ của bản tin nếu có; `missing` = ô trống không cờ;
# `bad_time` = có số nhưng giờ không đọc được (giữ số, `ts` là giờ giữ chỗ).
QC_SQL = """CASE WHEN {e}.flag IS NOT NULL THEN {e}.flag
                 WHEN {e}.water_level_m IS NULL THEN 'missing'
                 WHEN {e}.datetime IS NULL THEN 'bad_time'
                 ELSE 'ok' END"""

# ---------------------------------------------------------------------------
# Validator / index của 4 collection
# ---------------------------------------------------------------------------
NUM_OR_NULL = {"bsonType": ["double", "int", "long", "null"]}
STR_OR_NULL = {"bsonType": ["string", "null"]}
DAY = {"bsonType": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
EXTREME_ITEM = {"bsonType": "object", "required": ["ts", "value", "qc"],
                "properties": {"ts": {"bsonType": ["date", "null"]}, "value": NUM_OR_NULL,
                               "qc": {"bsonType": "string"}}}

SPECS = {
    "official_forecasts": {
        "validator": {"$jsonSchema": {
            "bsonType": "object",
            "required": ["station", "issued_date", "target_date", "lead_day", "high", "low", "source_file"],
            "properties": {
                "station": {"bsonType": "string"},
                "issued_date": DAY,
                "target_date": DAY,
                "lead_day": {"bsonType": "int", "minimum": 1, "maximum": 5},
                "high": {"bsonType": "array", "items": EXTREME_ITEM},
                "low": {"bsonType": "array", "items": EXTREME_ITEM},
                "source_file": {"bsonType": "string"},
                "issued_date_note": STR_OR_NULL,
            }}},
        "indexes": [
            {"key": {"station": 1, "issued_date": 1, "target_date": 1}, "name": "station_issued_target_unique",
             "unique": True},
            {"key": {"station": 1, "target_date": 1, "lead_day": 1}, "name": "station_target_lead_idx"},
            {"key": {"source_file": 1}, "name": "source_file_idx"},
        ],
    },
    "reservoir_daily": {
        "validator": {"$jsonSchema": {
            "bsonType": "object",
            "required": ["date", "day", "issued_date", "reservoir", "province", "province_flag", "flags",
                         "source_file"],
            "properties": {
                "date": {"bsonType": "date"},
                "day": DAY,
                "issued_date": DAY,
                "issued_date_note": STR_OR_NULL,
                "reservoir": {"bsonType": "string"},
                "upstream_water_level_m": NUM_OR_NULL, "inflow_m3s": NUM_OR_NULL,
                "turbine_discharge_m3s": NUM_OR_NULL, "spillway_discharge_m3s": NUM_OR_NULL,
                "rainfall_mm": NUM_OR_NULL,
                "flags": {"bsonType": "object"},
                # null khi bản tin không ghi tỉnh (nhãn rỗng), kèm province_flag = "empty_label"
                "province": {"bsonType": ["object", "null"], "required": ["era", "code"],
                             "properties": {"era": {"enum": [rules.ERA_OLD, rules.ERA_NEW]},
                                            "code": {"bsonType": "int"}, "note": STR_OR_NULL,
                                            "provisional": {"bsonType": "bool"}}},
                "province_flag": {"enum": [None, "empty_label"]},
                "source_file": {"bsonType": "string"},
            }}},
        "indexes": [
            {"key": {"reservoir": 1, "date": 1}, "name": "reservoir_date_unique", "unique": True},
            {"key": {"province.era": 1, "province.code": 1, "date": 1}, "name": "province_date_idx"},
        ],
    },
    "rainfall_daily": {
        "validator": {"$jsonSchema": {
            "bsonType": "object",
            "required": ["date", "day", "station", "rainfall_mm", "source_file"],
            "properties": {
                "date": {"bsonType": "date"},
                "day": DAY,
                "station": {"bsonType": "string"},
                "rainfall_mm": NUM_OR_NULL,
                "flag": STR_OR_NULL,
                "source_file": {"bsonType": "string"},
            }}},
        "indexes": [
            {"key": {"station": 1, "date": 1}, "name": "station_date_unique", "unique": True},
        ],
    },
}
TS_INDEXES = [{"key": {"meta.station": 1, "ts": 1}, "name": "station_ts_idx"}]


# ---------------------------------------------------------------------------
# Biến đổi
# ---------------------------------------------------------------------------
def with_issued(b: DataFrame) -> DataFrame:
    """Ngày phát của bản tin, dùng chung cho `official_forecasts` và `reservoir_daily`.

    Ngày phát = ngày bản tin; nếu ngày dự báo đầu tiên khác ngày bản tin (lỗi ngày trong nội dung PDF,
    ví dụ 2025/11/08/HCMC_TVHN_20251108.pdf) thì lấy ngày dự báo đầu tiên (quy ước lead_day 1 = ngày phát)
    và ghi `issued_date_note`.
    """
    first = F.array_min(F.col("forecast_water_levels.date"))
    differs = first.isNotNull() & (F.date_format(first, "yyyy-MM-dd") != F.date_format("bulletin_date", "yyyy-MM-dd"))
    return (b.withColumn("issued_ts", F.when(differs, first).otherwise(F.col("bulletin_date")))
             .withColumn("issued_date_note",
                         F.when(differs, F.concat(F.lit("ngày bản tin trong nội dung PDF là "),
                                                  F.date_format("bulletin_date", "yyyy-MM-dd"),
                                                  F.lit("; lấy ngày dự báo đầu tiên làm ngày phát")))))


def attach_code(df: DataFrame, lookup: DataFrame, group: str, name_col: str, out_col: str) -> DataFrame:
    lk = lookup.filter(F.col("grp") == group).select(F.col("label").alias("_lbl"), F.col("code").alias(out_col))
    return df.join(F.broadcast(lk), df[name_col] == lk["_lbl"], "left").drop("_lbl")


def build_tide_extremes(b: DataFrame, lookup: DataFrame) -> DataFrame:
    obs = b.select("source_file", "observation_date", F.explode("observed_water_levels").alias("o"))
    parts = []
    for kind, arr in (("high", "high_tides"), ("low", "low_tides")):
        parts.append(obs.select("source_file", "observation_date", F.col("o.station").alias("station_name"),
                                F.lit(kind).alias("kind"), F.posexplode(f"o.{arr}").alias("slot", "e")))
    x = parts[0].unionByName(parts[1])
    # Đỉnh/chân không có giờ: ts giữ chỗ = 00:00:0(slot+1) giờ VN của ngày quan trắc (không trùng giờ thật,
    # vì giờ thật luôn tròn phút), ghi `ts_placeholder = true`.
    placeholder = (F.col("observation_date").cast("long") + F.col("slot") + 1).cast("timestamp")
    # Bản tin ghi 2 đỉnh (hoặc 2 chân) cùng một giờ (lỗi chép trong PDF):
    # - cùng giá trị: phần tử đầu giữ `ok`, phần tử sau là bản lặp;
    # - khác giá trị: không biết giờ nào đúng, cả hai bị đánh dấu.
    # Phần tử bị đánh dấu có qc `duplicate_time`; phần tử không phải đầu nhóm chuyển sang ts giữ chỗ.
    grp = Window.partitionBy(F.col("source_file"), F.col("station_name"), F.col("kind"), F.col("e.datetime"))
    x = (x.withColumn("_n", F.count("*").over(grp))
          .withColumn("_nv", F.size(F.collect_set("e.water_level_m").over(grp)))
          .withColumn("_first", F.col("slot") == F.min("slot").over(grp)))
    dup = F.col("e.datetime").isNotNull() & (F.col("_n") > 1)
    moved = dup & ~F.col("_first")
    base_qc = F.expr(QC_SQL.format(e="e"))
    qc = F.when(dup & (base_qc == "ok") & ~(F.col("_first") & (F.col("_nv") == 1)), F.lit("duplicate_time")) \
          .otherwise(base_qc)
    x = x.select(
        "station_name", "kind", "source_file",
        F.when(moved, placeholder).otherwise(F.coalesce(F.col("e.datetime"), placeholder)).alias("ts"),
        (F.col("e.datetime").isNull() | moved).alias("ts_placeholder"),
        F.col("e.water_level_m").alias("value"),
        qc.alias("qc"),
    )
    return attach_code(x, lookup, "tide", "station_name", "station")


def to_tide_docs(x: DataFrame) -> DataFrame:
    # meta chỉ {station, kind}; source_file, qc, ts_placeholder là field thường (ít bucket hơn)
    return x.select("ts", F.struct("station", "kind").alias("meta"), "value", "qc", "ts_placeholder", "source_file")


def build_official_forecasts(b: DataFrame, lookup: DataFrame) -> DataFrame:
    f = b.select("source_file", "issued_ts", "issued_date_note", F.explode("forecast_water_levels").alias("f"))
    item = "transform(f.{a}, e -> named_struct('ts', e.datetime, 'value', e.water_level_m, 'qc', " \
           + QC_SQL.format(e="e") + "))"
    f = f.select(
        "source_file",
        F.col("f.station").alias("station_name"),
        F.date_format("issued_ts", "yyyy-MM-dd").alias("issued_date"),
        F.date_format("f.date", "yyyy-MM-dd").alias("target_date"),
        F.expr(item.format(a="high_tides")).alias("high"),
        F.expr(item.format(a="low_tides")).alias("low"),
        "issued_date_note",
    )
    f = f.withColumn("lead_day", (F.datediff(F.to_date("target_date"), F.to_date("issued_date")) + 1).cast("int"))
    return attach_code(f, lookup, "tide", "station_name", "station")


def build_reservoir_rows(b: DataFrame, lookup: DataFrame) -> DataFrame:
    r = b.select("source_file", "issued_ts", "issued_date_note", "observation_date",
                 F.explode("reservoirs").alias("r"))
    r = r.select("source_file", "issued_ts", "issued_date_note", "observation_date",
                 F.col("r.reservoir").alias("reservoir_name"), F.col("r.province").alias("province_label"),
                 "r.river", "r.upstream_water_level_m", "r.inflow_m3s", "r.turbine_discharge_m3s",
                 "r.spillway_discharge_m3s", "r.rainfall_mm", "r.notes",
                 F.coalesce(F.col("r.flags"), F.create_map().cast("map<string,string>")).alias("flags"))
    return attach_code(r, lookup, "reservoir", "reservoir_name", "reservoir")


def attach_province(spark, r: DataFrame, timeline: list[dict], max_lag_days: int):
    """Tỉnh của hồ theo NGÀY QUAN TRẮC bằng `vngis.province_labels` (quy tắc dùng chung với `stations`)."""
    obs_rows = (r.select("reservoir_name", "province_label", F.col("observation_date").cast("long").alias("o"))
                 .distinct().collect())
    utc = lambda s: datetime.fromtimestamp(s, tz=timezone.utc)  # noqa: E731
    rows = [{"reservoir": x["reservoir_name"], "label": x["province_label"], "obs": utc(x["o"])} for x in obs_rows]
    ranges, runs, unmatched = rules.province_ranges(rows, timeline, max_lag_days)
    rdf = spark.createDataFrame(
        [(g["reservoir"], g["label"], rules.to_epoch(g["obs_from"]), rules.to_epoch(g["obs_to"]), g["era"],
          g["code"], g["note"], g["provisional"], g["empty"]) for g in ranges],
        "p_res string, p_label string, p_from long, p_to long, p_era string, p_code int, p_note string, "
        "p_prov boolean, p_empty boolean")
    od = F.col("observation_date").cast("long")
    cond = ((r["reservoir_name"] == rdf["p_res"]) & r["province_label"].eqNullSafe(rdf["p_label"])
            & (od >= rdf["p_from"]) & (od <= rdf["p_to"]))
    out = r.join(F.broadcast(rdf), cond, "left")
    return out, runs, unmatched


def to_reservoir_docs(r: DataFrame) -> DataFrame:
    return r.select(
        F.col("observation_date").alias("date"),
        F.date_format("observation_date", "yyyy-MM-dd").alias("day"),
        F.date_format("issued_ts", "yyyy-MM-dd").alias("issued_date"),
        "issued_date_note",
        "reservoir", "river", "upstream_water_level_m", "inflow_m3s", "turbine_discharge_m3s",
        "spillway_discharge_m3s", "rainfall_mm", "notes", "flags",
        F.when(F.col("p_code").isNotNull(),
               F.struct(F.col("p_era").alias("era"), F.col("p_code").alias("code"), F.col("p_note").alias("note"),
                        F.col("p_prov").alias("provisional"))).alias("province"),
        F.when(F.col("p_empty"), F.lit("empty_label")).alias("province_flag"),
        "source_file")


def build_rainfall(b: DataFrame, lookup: DataFrame) -> DataFrame:
    x = b.select("source_file", "observation_date", F.col("rainfall.period").alias("period"),
                 F.explode("rainfall.stations").alias("s"))
    x = x.select("source_file", "observation_date", "period", F.col("s.station").alias("station_name"),
                 F.col("s.rainfall_mm").alias("rainfall_mm"), F.col("s.flag").alias("flag"))
    return attach_code(x, lookup, "rain", "station_name", "station")


def to_rain_docs(x: DataFrame) -> DataFrame:
    return x.select(F.col("observation_date").alias("date"),
                    F.date_format("observation_date", "yyyy-MM-dd").alias("day"),
                    "station", "rainfall_mm", "flag", "period", "source_file")


# ---------------------------------------------------------------------------
# Kiểm tra
# ---------------------------------------------------------------------------
class Report:
    def __init__(self):
        self.items: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.items.append((name, ok, detail))
        print(f"[{'OK ' if ok else 'LỖI'}] {name}: {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.items)


def _iso(v) -> datetime:
    s = v["$date"] if isinstance(v, dict) else v
    if isinstance(s, dict):  # canonical {"$numberLong": ...}
        return datetime.fromtimestamp(int(s["$numberLong"]) / 1000, tz=timezone.utc)
    return rules.parse_iso_utc(s)


def check_tide_counts(adm: MongoAdmin, rep: Report, staging: str, excluded: list[str]) -> int:
    """Trả về số document kỳ vọng của tide_extremes (= tổng ô đỉnh/chân trong bản tin chính đã bỏ trùng)."""
    src_match = {**PRIMARY_MATCH, "source_file": {"$nin": excluded}}
    elements = [
        {"$match": src_match},
        {"$unwind": "$observed_water_levels"},
        {"$project": {"e": {"$concatArrays": [{"$ifNull": ["$observed_water_levels.high_tides", []]},
                                              {"$ifNull": ["$observed_water_levels.low_tides", []]}]}}},
        {"$unwind": "$e"},
        {"$group": {"_id": None, "total": {"$sum": 1},
                    "num": {"$sum": {"$cond": [{"$isNumber": "$e.water_level_m"}, 1, 0]}},
                    "num_time": {"$sum": {"$cond": [{"$and": [{"$isNumber": "$e.water_level_m"},
                                                              {"$eq": [{"$type": "$e.datetime"}, "date"]}]}, 1, 0]}}}},
    ]
    src = adm.aggregate("hydromet_bulletins", elements)[0]
    raw_all = adm.aggregate("hydromet_bulletins", [{"$match": PRIMARY_MATCH}] + elements[1:])[0]
    got_total = adm.count(staging)
    got_num = adm.count(staging, {"value": {"$ne": None}})
    got_ok = adm.count(staging, {"qc": {"$in": ["ok", "duplicate_time"]}})
    rep.add("tide_extremes: số bản ghi value != null = số đỉnh/chân có số (aggregation độc lập)",
            got_num == src["num"],
            f"tide_extremes={got_num}, bản tin={src['num']} (toàn bộ bản chính trước khi bỏ bản trùng: "
            f"{raw_all['num']}); có số và có giờ: {src['num_time']} = qc ok + duplicate_time {got_ok}")
    rep.add("tide_extremes: tổng số phần tử", got_total == src["total"] and got_ok == src["num_time"],
            f"tide_extremes={got_total}, bản tin={src['total']}")
    return int(src["total"])


def check_forecasts(adm: MongoAdmin, rep: Report, staging: str, excluded: list[str]) -> int:
    """Trả về số document kỳ vọng của official_forecasts (= số phần tử forecast_water_levels)."""
    expected_files = adm.aggregate("hydromet_bulletins", [
        {"$match": {**PRIMARY_MATCH, "source_file": {"$nin": excluded}}},
        {"$project": {"_id": 0, "source_file": 1,
                      "n": {"$size": {"$ifNull": ["$forecast_water_levels", []]}},
                      "st": {"$size": {"$setUnion": [{"$ifNull": ["$forecast_water_levels.station", []]}, []]}}}},
    ])
    exp = {d["source_file"]: d for d in expected_files}
    got = adm.aggregate(staging, [
        {"$group": {"_id": {"f": "$source_file", "s": "$station"}, "n": {"$sum": 1},
                    "leads": {"$addToSet": "$lead_day"}}},
        {"$group": {"_id": "$_id.f", "docs": {"$sum": "$n"}, "stations": {"$sum": 1},
                    "bad_leads": {"$sum": {"$cond": [{"$setEquals": ["$leads", [1, 2, 3, 4, 5]]}, 0, 1]}}}},
    ])
    gotm = {g["_id"]: g for g in got}
    bad = []
    for f, e in exp.items():
        g = gotm.get(f)
        if g is None or g["docs"] != 5 * e["st"] or g["docs"] != e["n"] or g["stations"] != e["st"] \
                or g["bad_leads"]:
            bad.append((f, e, g))
    extra = set(gotm) - set(exp)
    dist: dict[int, int] = {}
    for e in exp.values():
        dist[e["st"]] = dist.get(e["st"], 0) + 1
    leads = adm.aggregate(staging, [{"$group": {"_id": "$lead_day", "n": {"$sum": 1}}}, {"$sort": {"_id": 1}}])
    rep.add("official_forecasts: mỗi bản tin chính = 5 ngày × số trạm dự báo, lead_day 1..5",
            not bad and not extra,
            f"{len(exp)} bản tin; phân bố số trạm {dict(sorted(dist.items()))}; sai {len(bad)}"
            + (f", ví dụ {bad[:3]}" if bad else "") + (f"; thừa {sorted(extra)[:3]}" if extra else "")
            + f"; theo lead_day {[(x['_id'], x['n']) for x in leads]}")
    return sum(int(e["n"]) for e in exp.values())


def check_tide_duplicates(adm: MongoAdmin, rep: Report, staging: str) -> None:
    dups = adm.aggregate(staging, [
        {"$group": {"_id": {"s": "$meta.station", "k": "$meta.kind", "ts": "$ts"}, "n": {"$sum": 1}}},
        {"$match": {"n": {"$gt": 1}}}, {"$limit": 20}])
    rep.add("tide_extremes: không trùng (station, kind, ts)", not dups,
            f"{len(dups)} khóa trùng" + (f": {dups[:5]}" if dups else ""))


def check_sample_times(adm: MongoAdmin, rep: Report, staging: str, code_to_name: dict[str, str]) -> None:
    sample = adm.aggregate(staging, [{"$match": {"qc": "ok"}}, {"$sample": {"size": 5}}])
    lines, ok_all = [], True
    for s in sample:
        ts = _iso(s["ts"])
        local = (ts + timedelta(hours=7)).strftime("%H:%M")
        m = s["meta"]
        arr = "high_tides" if m["kind"] == "high" else "low_tides"
        els = adm.aggregate("hydromet_bulletins", [
            {"$match": {"source_file": s["source_file"]}},
            {"$unwind": "$observed_water_levels"},
            {"$match": {"observed_water_levels.station": code_to_name[m["station"]]}},
            {"$unwind": f"$observed_water_levels.{arr}"},
            {"$project": {"_id": 0, "e": f"$observed_water_levels.{arr}"}}])
        cand = [e["e"] for e in els if e["e"].get("water_level_m") == s["value"]]
        hit = any(e.get("time") == local for e in cand)
        ok_all &= hit
        lines.append(f"{m['station']} {m['kind']} {ts.isoformat()} +7h={local} value={s['value']} "
                     f"PDF time={[e.get('time') for e in cand]} ({s['source_file']}) "
                     f"{'khớp' if hit else 'KHÔNG KHỚP'}")
    rep.add("tide_extremes: 5 bản ghi ngẫu nhiên, giờ UTC+7 khớp HH:MM gốc", ok_all and len(sample) == 5,
            "\n    " + "\n    ".join(lines))


# ---------------------------------------------------------------------------
# Nạp nguyên tử
# ---------------------------------------------------------------------------
def create_like(adm: MongoAdmin, name: str, target: str) -> None:
    """Tạo `target` cùng kiểu/validator/index với collection `name` (theo SPECS)."""
    if name == "tide_extremes":
        adm.create(target, timeseries=TS_OPTIONS)
    else:
        adm.create(target, validator=SPECS[name]["validator"], validationLevel="strict", validationAction="error")


def indexes_of(name: str) -> list[dict]:
    return TS_INDEXES if name == "tide_extremes" else SPECS[name]["indexes"]


def out_stage(adm: MongoAdmin, name: str, target: str) -> dict:
    if name == "tide_extremes":
        return {"$out": {"db": adm.db_name, "coll": target, "timeseries": TS_OPTIONS}}
    return {"$out": {"db": adm.db_name, "coll": target}}


KEEP_OPTIONS = ("validator", "validationLevel", "validationAction", "timeseries")


def load_jobs(adm: MongoAdmin) -> list[dict]:
    """Bản ghi `jobs` của job, `generated_at` đổi sang datetime (cho các hàm quyết định thuần Python)."""
    out = []
    for j in adm.find_many("jobs", {"job": JOB},
                           {"status": 1, "replaced": 1, "replacing": 1, "generated_at": 1}):
        j["generated_at"] = _iso(j["generated_at"]) if j.get("generated_at") else None
        out.append(j)
    return out


def drop_stale_staging(adm: MongoAdmin, run_id: str, ttl_s: int) -> None:
    """Dọn staging cũ theo `rules.stale_staging`: xóa staging của run đã kết thúc (`jobs.status` ∈ ok / failed /
    dry_run / blocked / rolled_back), hoặc không có trong `jobs` / vẫn `running` mà cũ hơn hạn khóa; giữ staging
    trẻ của run chưa kết thúc. Chỉ gọi khi đang giữ khóa."""
    jobs = {j["_id"]: j for j in load_jobs(adm)}
    drop, keep = rules.stale_staging(adm.collection_names(), NAMES, run_id, jobs, datetime.now(timezone.utc), ttl_s)
    for c, why in drop:
        adm.drop(c)
        print(f"[DỌN] xóa staging cũ {c} ({why})", flush=True)
    for c, why in keep:
        print(f"[DỌN] giữ {c}: {why}", flush=True)


class PrevPendingError(RuntimeError):
    """Còn `__prev` của một lần chạy hỏng giữa chừng: cần lùi lại rồi --ack-rollback trước khi chạy tiếp."""


def rollback_help(bad: dict, prevs: list[str]) -> str:
    done = list(bad.get("replaced") or [])
    if bad.get("replacing") and bad["replacing"] not in done:
        done.append(f"{bad['replacing']} (có thể đã thay)")
    return (f"lần chạy {bad['_id']} (status {bad.get('status')}) hỏng giữa chừng: đã thay {done}; còn {prevs}.\n"
            f"  Gỡ chặn: (a) lùi từng collection đã thay từ bản __prev: collection thường "
            f"renameCollection('<db>.<tên>{PREV}', '<db>.<tên>', dropTarget=true); tide_extremes: aggregate trên "
            f"tide_extremes{PREV} với $out {{coll: 'tide_extremes', timeseries: {TS_OPTIONS}}}; "
            f"(b) chạy lại job với --ack-rollback {bad['_id']} (đặt jobs.status = rolled_back, xóa các {PREV} còn lại).")


def check_prev_safe(adm: MongoAdmin, run_id: str) -> None:
    """`rules.pending_rollback` trên `jobs`: còn `__prev` và có run hỏng giữa chừng (failed/running có
    replaced/replacing, sau lần ok cuối) → `PrevPendingError` (không xóa `__prev`)."""
    prevs = [n + PREV for n in NAMES if adm.exists(n + PREV)]
    bad = rules.pending_rollback(load_jobs(adm), run_id, bool(prevs))
    if bad:
        raise PrevPendingError(rollback_help(bad, prevs))


def ack_rollback(adm: MongoAdmin, target: str) -> int:
    """Gỡ chặn sau khi đã lùi lại tay: kiểm tra `target` đúng là run hỏng giữa chừng đang chặn, in và xóa mọi
    `<tên>__prev`, đặt `jobs[target].status = rolled_back`. 0 thành công, 1 nếu `target` không phải run đang chặn."""
    prevs = [n + PREV for n in NAMES if adm.exists(n + PREV)]
    jobs = load_jobs(adm)
    bad = rules.pending_rollback(jobs, "", True)
    if bad is None or bad["_id"] != target:
        print(f"--ack-rollback {target}: không phải lần chạy đang chặn (đang chặn: {bad['_id'] if bad else 'không có'})"
              f"; không thay đổi gì.", flush=True)
        return 1
    print(f"[ACK] {target}: xóa {prevs or 'không có __prev'}; jobs.status = rolled_back", flush=True)
    for p in prevs:
        adm.drop(p)
    adm.upsert("jobs", {"_id": target}, {"$set": {"status": "rolled_back",
                                                  "rolled_back_at": bson_date(datetime.now(timezone.utc))}})
    return 0


def backup(adm: MongoAdmin, name: str) -> bool:
    """Sao bản chính thành `<tên>__prev` với ĐÚNG tùy chọn (validator, time series) và index của bản chính hiện tại
    (không phải SPECS mới: bản cũ có thể theo validator cũ), để lùi lại được. False nếu chưa có bản chính."""
    prev = name + PREV
    adm.drop(prev)
    if not adm.exists(name):
        return False
    opts = {k: v for k, v in adm.collection_options(name).items() if k in KEEP_OPTIONS}
    if "timeseries" in opts:
        opts["timeseries"] = {k: v for k, v in opts["timeseries"].items() if k in TS_OPTIONS}
    adm.create(prev, **opts)
    adm.create_indexes(prev, [{k: v for k, v in ix.items() if k not in ("v", "ns")}
                              for ix in adm.list_indexes(name) if ix["name"] not in ("_id_", "meta_1_ts_1")])
    stage = ({"$out": {"db": adm.db_name, "coll": prev, "timeseries": opts["timeseries"]}} if "timeseries" in opts
             else {"$out": {"db": adm.db_name, "coll": prev}})
    # bản cũ có thể không thỏa validator mới/cũ: chép nguyên trạng, không kiểm tra lại
    adm.aggregate(name, [stage], write=True, bypass_validation=True)
    n, m = adm.count(name), adm.count(prev)
    if n != m:
        raise RuntimeError(f"sao lưu {name} → {prev}: {m} / {n} document")
    print(f"[SAO LƯU] {name} → {prev}: {m} document", flush=True)
    return True


def swap(adm: MongoAdmin, name: str, stg: str, expected: int, keep_staging: bool, on_replaced) -> int:
    """Thay `name` bằng `stg`; `on_replaced()` được gọi NGAY sau khi lệnh thay thành công (trước khi đếm lại)."""
    n = adm.count(stg)
    if n != expected:                                  # kiểm tra lần cuối ngay trước khi thay
        raise RuntimeError(f"{stg}: {n} document, kỳ vọng {expected}; không thay {name}")
    if name == "tide_extremes":
        # renameCollection không áp dụng cho time series trên MongoDB 8.0 ("cannot rename view").
        # $out với tùy chọn timeseries (MongoDB >= 7.0.3) ghi vào collection tạm rồi thay nguyên tử
        # collection đích; index của collection đích được giữ lại.
        if not adm.exists(name):
            adm.create(name, timeseries=TS_OPTIONS)
            adm.create_indexes(name, TS_INDEXES)
        adm.aggregate(stg, [out_stage(adm, name, name)], write=True)
        on_replaced()
        adm.create_indexes(name, TS_INDEXES)
        if not keep_staging:
            adm.drop(stg)
    else:
        adm.rename(stg, name, drop_target=True)
        on_replaced()
    got = adm.count(name)
    if got != expected:
        raise RuntimeError(f"{name}: sau khi thay có {got} document, kỳ vọng {expected}")
    print(f"[NẠP] {name}: {got} document", flush=True)
    return got


def replace_all(adm: MongoAdmin, staging: dict[str, str], expected: dict[str, int], keep_staging: bool,
                job_update, hold_lock, run_id: str) -> dict[str, int]:
    """Sao lưu cả 4 bản chính, thay lần lượt; lỗi giữa chừng → in rõ collection nào đã thay và cách lùi lại.

    `hold_lock()` gia hạn và xác nhận vẫn giữ khóa (LockLostError nếu mất) – gọi trước sao lưu và trước MỖI swap.
    """
    check_prev_safe(adm, run_id)
    hold_lock()
    backed = [n for n in NAMES if backup(adm, n)]
    replaced: list[str] = []
    counts: dict[str, int] = {}
    try:
        for name in NAMES:
            hold_lock()
            job_update({"replacing": name})                 # ghi TRƯỚC khi thay: process chết giữa chừng vẫn biết

            def done(name=name):
                replaced.append(name)
                job_update({"replaced": list(replaced), "replacing": None})

            counts[name] = swap(adm, name, staging[name], expected[name], keep_staging, done)
    except Exception:
        left = [n for n in NAMES if n not in replaced]
        print(f"[LỖI GIỮA CHỪNG] đã thay: {replaced or 'không có'}; CHƯA thay: {left}.", flush=True)
        print("  " + rollback_help({"_id": run_id, "status": "failed", "replaced": replaced},
                                   [n + PREV for n in backed]), flush=True)
        raise
    for n in backed:
        adm.drop(n + PREV)
    return counts


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("uri", nargs="?", default=env_uri(), help="URI MongoDB (mặc định biến MONGO_URI)")
    ap.add_argument("--db", default=env_db(), help="database (mặc định biến MONGO_DB hoặc vietnam_gis)")
    ap.add_argument("--max-lag-days", type=int, default=rules.DEFAULT_MAX_LAG_DAYS,
                    help="giới hạn 'đổi tên chậm' của nhãn tỉnh; khớp admin.max_label_lag_days trong config")
    ap.add_argument("--lock-ttl-s", type=int, default=3600, help="hạn của khóa job_locks (giây)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ ghi staging và kiểm tra, không thay bản chính")
    ap.add_argument("--keep-staging", action="store_true", help="giữ staging của tide_extremes sau khi nạp")
    ap.add_argument("--ack-rollback", metavar="RUN_ID",
                    help="đã lùi lại tay các collection của RUN_ID: đặt jobs.status = rolled_back, xóa mọi __prev, "
                         "rồi thoát (không chạy G0)")
    args = ap.parse_args(argv)

    generated_at = datetime.now(timezone.utc)
    run_id = generated_at.strftime("%Y%m%dT%H%M%SZ") + "_" + secrets.token_hex(3)
    staging = {n: f"{n}{STAGING}_{run_id}" for n in NAMES}
    print(f"run_id={run_id} db={args.db}", flush=True)

    spark = build_session(JOB, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    rep = Report()
    status, locked = "failed", False

    def job_update(fields: dict) -> None:
        adm.upsert("jobs", {"_id": run_id}, {"$set": fields})

    def hold_lock() -> None:
        """Gia hạn khóa trước mỗi pha dài và xác nhận vẫn giữ (mất khóa → LockLostError, dừng không thay)."""
        adm.renew_lock(JOB, run_id, args.lock_ttl_s)

    try:
        try:
            adm.acquire_lock(JOB, run_id, args.lock_ttl_s)
            locked = True
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        if args.ack_rollback:
            status = "ack"
            return ack_rollback(adm, args.ack_rollback)
        job_update({"job": JOB, "run_id": run_id, "generated_at": bson_date(generated_at), "status": "running",
                    "db": args.db, "dry_run": args.dry_run, "max_lag_days": args.max_lag_days,
                    "staging": staging, "replaced": []})
        if not args.dry_run:
            check_prev_safe(adm, run_id)              # dừng sớm, trước khi làm việc nặng
        drop_stale_staging(adm, run_id, args.lock_ttl_s)

        b_all = read_mongo(spark, "hydromet_bulletins", BULLETIN, SOURCE_PIPELINE, args.db).cache()
        meta = b_all.select("source_file", "source.sha256",
                            F.col("source.date_from_filename").cast("long").alias("dff"),
                            F.col("bulletin_date").cast("long").alias("bd")).collect()
        excl = rules.duplicate_exclusions([{"source_file": m["source_file"], "sha256": m["sha256"],
                                            "date_from_filename": m["dff"], "bulletin_date": m["bd"]} for m in meta])
        excluded = [e["source_file"] for e in excl]
        print(f"Bản tin chính: {len(meta)}; bỏ {len(excluded)} bản trùng nội dung (sha256):", flush=True)
        for e in excl:
            print(f"    bỏ {e['source_file']}  (giữ {e['kept']})", flush=True)
        b = b_all.filter(~F.col("source_file").isin(excluded)) if excluded else b_all
        b = with_issued(b)

        st_rows = [r.asDict() for r in read_mongo(spark, "stations", STATION, db=args.db).collect()]
        lookup = spark.createDataFrame(rules.station_lookup_rows(st_rows), "grp string, label string, code string")
        code_to_name = {s["code"]: s["name"] for s in st_rows}
        timeline = [r.asDict() for r in read_mongo(spark, "province_timeline", PROVINCE, db=args.db).collect()]

        tide = build_tide_extremes(b, lookup).cache()
        fc = build_official_forecasts(b, lookup).cache()
        res_rows = build_reservoir_rows(b, lookup)
        res, runs, p_unmatched = attach_province(spark, res_rows, timeline, args.max_lag_days)
        res = res.cache()
        rain = build_rainfall(b, lookup).cache()

        # ---- Kiểm tra tên → mã ------------------------------------------------
        for label, df, col in (("trạm mực nước (observed)", tide, "station"), ("trạm dự báo", fc, "station"),
                               ("hồ chứa", res, "reservoir"), ("trạm mưa", rain, "station")):
            bad = sorted(r[0] for r in df.filter(F.col(col).isNull()).select("station_name" if col == "station"
                                                                            else "reservoir_name")
                         .distinct().collect())
            rep.add(f"tên {label} đều có mã trong stations", not bad, "không có tên lạc" if not bad else f"{bad}")
        n_res = res.count()
        n_res_rows = res_rows.count()
        # nhãn rỗng (bản tin không ghi tỉnh) không chặn job: province = null, province_flag = "empty_label"
        bad_unmatched = [u for u in p_unmatched if not u["empty"]]
        n_empty = res.filter(F.coalesce(F.col("p_empty"), F.lit(False))).count()
        no_prov = res.filter(F.col("p_code").isNull() & ~F.coalesce(F.col("p_empty"), F.lit(False))).count()
        n_runs = sum(len(v) for v in runs.values())
        rep.add("hồ chứa: tên tỉnh → {era, code} (vngis.province_labels, theo ngày quan trắc)",
                not bad_unmatched and no_prov == 0 and n_res == n_res_rows,
                f"{n_runs} đoạn nhãn; không tra được {bad_unmatched or 0}; bản ghi thiếu mã {no_prov}; "
                f"nhãn rỗng (province = null) {n_empty}; số dòng sau join {n_res} / trước {n_res_rows}")
        vn = lambda t: (t + timedelta(hours=7)).date()  # noqa: E731
        for res_name, rs in sorted(runs.items()):
            for p in rs:
                print(f"    {res_name:<12} '{p.label}' quan trắc [{vn(p.first)}, {vn(p.last)}] ({p.n} ngày, "
                      f"{p.obs_era}) -> {p.era}:{p.code}" + (f"  ({p.note})" if p.note else "")
                      + ("  [provisional: đoạn cuối dữ liệu, còn trong hạn đổi tên chậm]" if p.provisional else "")
                      + (f"  KHÔNG KHỚP: {p.reason}" if not p.matched else ""), flush=True)
        if not rep.ok:
            print("Dừng: có tên không tra được mã.", flush=True)
            return 2

        # ---- Ghi staging ----------------------------------------------------
        hold_lock()
        for name, docs in (("tide_extremes", to_tide_docs(tide)),
                           ("official_forecasts", fc.drop("station_name")),
                           ("reservoir_daily", to_reservoir_docs(res)),
                           ("rainfall_daily", to_rain_docs(rain))):
            stg = staging[name]
            create_like(adm, name, stg)
            write_mongo(docs, stg, args.db)
            adm.create_indexes(stg, indexes_of(name))       # index unique cũng là một bước kiểm tra trùng khóa
            print(f"[STAGING] {stg}: {adm.count(stg)} document", flush=True)

        # ---- Kiểm tra bắt buộc (gồm số lượng so với kỳ vọng) ----------------
        hold_lock()
        expected = {
            "tide_extremes": check_tide_counts(adm, rep, staging["tide_extremes"], excluded),
            "official_forecasts": check_forecasts(adm, rep, staging["official_forecasts"], excluded),
            "reservoir_daily": n_res_rows,
            "rainfall_daily": rain.count(),
        }
        check_tide_duplicates(adm, rep, staging["tide_extremes"])
        check_sample_times(adm, rep, staging["tide_extremes"], code_to_name)
        for name in NAMES:
            got = adm.count(staging[name])
            rep.add(f"{name}: số document staging = số kỳ vọng", got == expected[name], f"{got} / {expected[name]}")

        qc = adm.aggregate(staging["tide_extremes"], [{"$group": {"_id": "$qc", "n": {"$sum": 1}}},
                                                      {"$sort": {"n": -1}}])
        print("tide_extremes theo qc:", {q["_id"]: q["n"] for q in qc}, flush=True)
        notes = adm.aggregate(staging["reservoir_daily"], [
            {"$match": {"province.note": {"$ne": None}}},
            {"$group": {"_id": {"r": "$reservoir", "era": "$province.era", "code": "$province.code"},
                        "n": {"$sum": 1}, "from": {"$min": "$day"}, "to": {"$max": "$day"}}}])
        print("reservoir_daily có ghi chú tỉnh (theo ngày quan trắc):", notes, flush=True)
        for name in ("official_forecasts", "reservoir_daily"):
            fnotes = adm.aggregate(staging[name], [
                {"$match": {"issued_date_note": {"$ne": None}}},
                {"$group": {"_id": {"f": "$source_file", "issued": "$issued_date"}, "n": {"$sum": 1}}}])
            print(f"{name} có ghi chú ngày phát:", fnotes, flush=True)
        job_update({"expected": expected, "checks": [{"name": n, "ok": ok} for n, ok, _ in rep.items]})

        if not rep.ok:
            print(f"Dừng: kiểm tra hỏng, giữ nguyên bản chính. Staging để xem: {list(staging.values())} "
                  f"(bị dọn ở lần chạy sau, vì run này đã kết thúc với status failed).", flush=True)
            return 1
        if args.dry_run:
            print(f"--dry-run: không thay bản chính. Staging để xem: {list(staging.values())} "
                  f"(bị dọn ở lần chạy sau, vì run này đã kết thúc với status dry_run).", flush=True)
            status = "dry_run"
            return 0
        counts = replace_all(adm, staging, expected, args.keep_staging, job_update, hold_lock, run_id)
        status = "ok"
        job_update({"counts": counts})
        return 0
    except PrevPendingError as e:
        status = "blocked"
        print(f"Dừng: {e}", flush=True)
        return 4
    except LockLostError as e:
        print(f"Dừng: {e}; không thay (thêm) collection nào.", flush=True)
        return 5
    except Exception:  # noqa: BLE001  – lỗi không lường trước: mã riêng, jobs.status = failed (ở finally)
        status = "failed"
        traceback.print_exc()
        print("Dừng: lỗi không lường trước (mã thoát 6); xem traceback ở trên và bản ghi jobs.", flush=True)
        return 6
    finally:
        # Nhả khóa trong try/finally riêng: ghi jobs lỗi chỉ log, không chặn nhả khóa.
        try:
            if locked:
                if status != "ack":                     # --ack-rollback không tạo bản ghi jobs cho chính nó
                    try:
                        job_update({"status": status, "finished_at": bson_date(datetime.now(timezone.utc))})
                    except Exception as e:  # noqa: BLE001
                        print(f"[JOBS] không ghi được trạng thái cuối ({status}): {e}", flush=True)
                try:
                    adm.release_lock(JOB, run_id)
                except Exception as e:  # noqa: BLE001
                    print(f"[KHÓA] không nhả được khóa {JOB}/{run_id} (tự hết hạn theo TTL): {e}", flush=True)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()


if __name__ == "__main__":
    sys.exit(main())
