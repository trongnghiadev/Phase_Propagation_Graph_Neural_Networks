"""G5 – Dựng tập huấn luyện (Parquet) cho mô hình dự báo đỉnh triều.

    spark-submit build_training_set.py [mongodb_uri] [--db vietnam_gis] [--out-dir DIR]
                                      [--astro-csv PATH] [--lock-ttl-s 3600] [--dry-run]

**Nguồn dữ liệu.**
- `tide_extremes` (time series): đỉnh/chân triều thực đo; chỉ lấy `qc = "ok"` và
  `qc_flag != "suspect"`. Đọc bằng `SINGLE_PARTITIONER` (time series là view).
- `stations.alarm_history`: ngưỡng báo động I/II/III **theo thời gian** (mỗi đoạn có `from`/`to`).
- `reservoir_daily`: lưu lượng vào/xả tràn/qua máy của các hồ.
- `rainfall_daily`: mưa tại các trạm mưa.
- `hydromet_bulletins.lunar_date`: ngày âm lịch (nguồn `day` của bản tin = ngày quan trắc).

**Quy tắc ngày của đỉnh (`peak*_day_offset`).** Bản tin KTTV ghi đỉnh/chân theo **ngày quan sát**
của bản tin; một đỉnh xuất hiện sau nửa đêm vẫn được tính thuộc ngày quan sát trước đó. Kiểm tra
trên toàn bộ `tide_extremes` (129.420 phần tử): 129.416 phần tử có `ts` thuộc đúng ngày quan sát,
4 phần tử lệch sang ngày kế tiếp. Vì vậy:
- `day` của một dòng = **ngày quan sát** của bản tin (không phải ngày lịch của `ts`);
- `peakN_day_offset` = 0 nếu đỉnh thuộc ngày quan sát, 1 nếu đỉnh thực sự sang ngày kế tiếp.
Quy tắc này PHẢI khớp với `pipelines/r/` (dùng chung hàm `vngis`-style: `features.rules.peak_day_offset`).

**Giá trị thiếu.** Đỉnh không quan trắc được → cột giá trị `null` và `missing_peakN = true`
(không dùng 0). Cột `h_peak1`/`h_peak2` là đỉnh THỨ NHẤT/THỨ HAI **theo giờ tăng dần** trong ngày.

**Báo động.** `exceed_level` = mức cao nhất mà đỉnh đạt tới, theo ngưỡng `alarm_history` đang hiệu lực
trong ngày đó: `""` (không vượt), `"I"`, `"II"`, `"III"`. Không dùng ngưỡng cố định.
Với đỉnh có `null` → `exceed_level = ""` và `exceed_level_known = false`.

**Rò rỉ (checklist GĐ3.6).** Mẫu dự báo cho ngày `d` chỉ dùng quan trắc tới hết ngày `d−1`.
Cột `max_obs_day` = ngày quan trắc LỚN NHẤT thực sự dùng cho dòng đó. Kiểm tra bắt buộc:
`max_obs_day < day` cho MỌI dòng (hỏng → dừng, mã thoát 1).

**Cột `astro_*`.** `pipelines/r/harmonic.R` ghi `data/processed/features/astro_tide.csv`
(cột `day,h1,h2,t1_h,t2_h`). Nếu có file → ghép vào; nếu không → các cột `astro_*` = null và
in cảnh báo `[CẢNH BÁO] thiếu data/processed/features/astro_tide.csv – cột astro_* để null`.

**Chia tập** (đã chốt, không đổi): train 2017-01-01→2022-12-31, val 2023-01-01→2024-06-30,
test 2024-07-01→2026-11-23.

**Đầu ra.** `data/processed/features/g5/year=<YYYY>/part-*.parquet` và
`data/processed/features/g5/_meta.json` (đếm theo collection, mốc thời gian lớn nhất, ngày chạy,
phiên bản job, `data_snapshot` để ghi vào bản ghi mô hình, và biên chia tập).

Mã thoát: 0 ok, 1 kiểm tra hỏng (kể cả rò rỉ), 2 không tra được dữ liệu, 3 job đang chạy ở nơi khác,
5 mất khóa, 6 lỗi không lường trước.
"""
from __future__ import annotations

import argparse
import bisect
import json
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (BooleanType, DoubleType, StringType, StructField, StructType,  # noqa: E402
                               TimestampType)
from pyspark.sql.window import Window  # noqa: E402

from common.jobrun import JobRun  # noqa: E402
from common.session import (SINGLE_PARTITIONER, JobLockError, LockLostError, MongoAdmin, build_session,  # noqa: E402
                            env_db, env_uri, read_mongo)

JOB = "g5_build_training_set"
JOB_VERSION = "g5-v1"

TIDE = StructType([
    StructField("ts", TimestampType()),
    StructField("station", StringType()),
    StructField("kind", StringType()),
    StructField("value", DoubleType()),
    StructField("qc", StringType()),
    StructField("qc_flag", StringType()),
    StructField("source_file", StringType()),
])
# Đọc giá trị GỐC? Không: G5 dùng giá trị sau QC (`value`, `qc`) vì đã bỏ `rejected`.
TIDE_PIPELINE = [{"$project": {
    "_id": 0, "ts": 1, "station": "$meta.station", "kind": "$meta.kind", "value": 1,
    "qc": 1, "qc_flag": 1, "source_file": 1,
}}]

# Biên chia tập (đã chốt ở `schemas/features/v1.md`).
SPLITS = {
    "train": ("2017-01-01", "2022-12-31"),
    "val": ("2023-01-01", "2024-06-30"),
    "test": ("2024-07-01", "2026-11-23"),
}


# ---------------------------------------------------------------------------
# Quy tắc thuần Python (dùng chung / test được)
# ---------------------------------------------------------------------------
def alarm_for_day(history: list[dict], day: str) -> dict | None:
    """Đoạn `alarm_history` có hiệu lực trong ngày `day` (YYYY-MM-DD), hoặc None.

    Mỗi đoạn có `from`/`to` (chuỗi giờ Việt Nam, ví dụ `2020-03-19 17:00:00`) và 3 ngưỡng.
    So sánh theo NGÀY (bỏ phần giờ) vì `day` của mẫu là ngày lịch.
    """
    if not history:
        return None
    for h in history:
        f = str(h.get("from") or "")[:10]
        t = str(h.get("to") or "")[:10]
        if f and t and f <= day <= t:
            return h
    return None


def exceed_level(peak_m: float | None, levels: dict | None) -> tuple[str, bool]:
    """Mức báo động cao nhất mà đỉnh đạt: ("", "I", "II", "III") và cờ biết/không biết.

    Đỉnh `None` (không quan trắc) → ("", False).
    """
    if peak_m is None or not levels:
        return "", False
    l1 = levels.get("level_1_m")
    l2 = levels.get("level_2_m")
    l3 = levels.get("level_3_m")
    best, known = "", True
    if l3 is not None and peak_m >= float(l3):
        return "III", known
    if l2 is not None and peak_m >= float(l2):
        best = "II"
    elif l1 is not None and peak_m >= float(l1):
        best = "I"
    else:
        known = False
    return best, known


def split_of(day: str) -> str | None:
    """Tập chia theo `day`: train / val / test, hoặc None nếu ngoài các khoảng."""
    for name, (a, b) in SPLITS.items():
        if a <= day <= b:
            return name
    return None


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("uri", nargs="?", default=env_uri(), help="URI MongoDB (mặc định biến MONGO_URI)")
    ap.add_argument("--db", default=env_db())
    ap.add_argument("--out-dir", default="/work/data/processed/features/g5",
                    help="thư mục ghi Parquet (mặc định /work/data/processed/features/g5)")
    ap.add_argument("--astro-csv", default="/work/data/processed/features/astro_tide.csv",
                    help="file CSV của R (mặc định /work/data/processed/features/astro_tide.csv)")
    ap.add_argument("--lock-ttl-s", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true", help="tính và kiểm tra, không ghi Parquet")
    args = ap.parse_args(argv)

    spark = build_session(JOB, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    run = JobRun(adm, JOB, args.lock_ttl_s)
    print(f"run_id={run.run_id} db={args.db}", flush=True)
    status = "failed"
    try:
        try:
            run.acquire()
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        run.start(db=args.db, splits=SPLITS, job_version=JOB_VERSION)
        if not adm.exists("tide_extremes"):
            print("Dừng: chưa có `tide_extremes`; chạy G0 trước.", flush=True)
            return 2

        # ---- Đọc bản tin chính: ngày quan sát + ngày âm lịch ---------------
        run.hold()
        bulletins = read_mongo(spark, "hydromet_bulletins", None, db=args.db,
                               pipeline=[{"$match": {"source.is_primary": True,
                                                     "bulletin_type": "tide_forecast_5day"}},
                                         {"$project": {"_id": 0, "source_file": 1, "observation_date": 1,
                                                       "bulletin_date": 1, "lunar_date.day": 1}}]).cache()
        binfo = {r["source_file"]: r.asDict() for r in bulletins.collect()}
        print(f"Bản tin chính: {len(binfo)}", flush=True)

        # ---- Đọc đỉnh/chân triều ------------------------------------------
        run.hold()
        tide = (read_mongo(spark, "tide_extremes", TIDE, TIDE_PIPELINE, args.db,
                           {"partitioner": SINGLE_PARTITIONER})
                .filter((F.col("qc") == "ok") & (F.coalesce(F.col("qc_flag"), F.lit("")) != "suspect"))
                .cache())
        n_tide = tide.count()
        n_coll = adm.count("tide_extremes")
        print(f"tide_extremes: {n_coll} phần tử; giữ {n_tide} (qc=ok, không suspect)", flush=True)
        if n_tide == 0:
            print("Dừng: không có phần tử triều nào đạt điều kiện QC.", flush=True)
            return 1

        # ngày quan trắc = ngày VN của `observation_date` của bản tin nguồn
        obs_map = {sf: _vn_date(v.get("observation_date")) for sf, v in binfo.items()}
        # `lunar_date` là document lồng → connector trả về `Row`, không phải dict.
        lunar_map = {}
        for sf, v in binfo.items():
            ld = v.get("lunar_date")
            if ld is None:
                lunar_map[sf] = None
                continue
            if not isinstance(ld, dict):
                as_dict = getattr(ld, "asDict", None)
                ld = as_dict(recursive=True) if callable(as_dict) else {}
            lunar_map[sf] = ld.get("day")
        obs_bc = spark.createDataFrame(
            [(sf, obs_map[sf], lunar_map.get(sf)) for sf in obs_map if obs_map[sf]],
            "source_file string, obs_day string, lunar_day int")

        run.hold()
        t = (tide.join(F.broadcast(obs_bc), "source_file", "left")
             .withColumn("ts_day", F.date_format("ts", "yyyy-MM-dd"))
             .withColumn("obs_day", F.coalesce("obs_day", "ts_day")))

        # ---- Quay 2 đỉnh + 2 chân theo (trạm, ngày, loại) -------------------
        run.hold()
        # Đánh số thứ tự theo giờ tăng dần trong (trạm, ngày quan sát): đỉnh/chân thứ
        # nhất trong ngày = `rn = 1`, thứ hai = `rn = 2`.
        w_peak = Window.partitionBy("station", "obs_day").orderBy(F.col("ts").asc())
        peaks = t.filter(F.col("kind") == "high").withColumn("rn", F.row_number().over(w_peak))
        w_low = Window.partitionBy("station", "obs_day").orderBy(F.col("ts").asc())
        lows = t.filter(F.col("kind") == "low").withColumn("rn", F.row_number().over(w_low))

        def pivot_extremes(df: DataFrame, prefix: str) -> DataFrame:
            p = df.filter(F.col("rn") <= 2).select(
                "station", "obs_day",
                F.when(F.col("rn") == 1, F.col("value")).alias(f"{prefix}1"),
                F.when(F.col("rn") == 2, F.col("value")).alias(f"{prefix}2"),
                F.when(F.col("rn") == 1, F.col("ts")).alias(f"{prefix}ts1"),
                F.when(F.col("rn") == 2, F.col("ts")).alias(f"{prefix}ts2"),
                F.when(F.col("rn") == 1, F.col("ts_day")).alias(f"{prefix}day1"),
                F.when(F.col("rn") == 2, F.col("ts_day")).alias(f"{prefix}day2"),
            )
            return p.groupBy("station", "obs_day").agg(
                F.max(f"{prefix}1").alias(f"h_{prefix}1"),
                F.max(f"{prefix}2").alias(f"h_{prefix}2"),
                F.max(f"{prefix}ts1").alias(f"t_{prefix}1_ts"),
                F.max(f"{prefix}ts2").alias(f"t_{prefix}2_ts"),
                F.max(f"{prefix}day1").alias(f"{prefix}1_day"),
                F.max(f"{prefix}day2").alias(f"{prefix}2_day"),
            )

        g = (pivot_extremes(peaks, "peak").join(pivot_extremes(lows, "low"), ["station", "obs_day"], "full")
             .withColumnRenamed("obs_day", "day"))
        print("Bảng (trạm, ngày) đã quay:", flush=True)

        # ---- Giờ trong ngày (giờ Việt Nam) --------------------------------
        g = (g.withColumn("t_peak1_h", _hour_of_day("t_peak1_ts"))
              .withColumn("t_peak2_h", _hour_of_day("t_peak2_ts"))
              .withColumn("t_low1_h", _hour_of_day("t_low1_ts"))
              .withColumn("t_low2_h", _hour_of_day("t_low2_ts")))

        # ---- day_offset (quy tắc đã thống nhất với R) ---------------------
        g = (g.withColumn("peak1_day_offset",
                          F.when(F.col("peak1_day") <= F.col("day"), F.lit(0)).otherwise(F.lit(1)))
              .withColumn("peak2_day_offset",
                          F.when(F.col("peak2_day") <= F.col("day"), F.lit(0)).otherwise(F.lit(1)))
              .withColumn("missing_peak1", F.col("h_peak1").isNull())
              .withColumn("missing_peak2", F.col("h_peak2").isNull()))

        # ---- Ngày âm lịch ---------------------------------------------------
        run.hold()
        lunar = (obs_bc.groupBy("obs_day").agg(F.max("lunar_day").alias("lunar_day"))
                 .withColumnRenamed("obs_day", "day"))
        g = g.join(lunar, "day", "left")

        # ---- Ngưỡng báo động theo thời gian --------------------------------
        st_rows = [r.asDict() for r in read_mongo(spark, "stations", None, db=args.db).collect()]
        alarm_map: dict[str, list[dict]] = {}
        for s in st_rows:
            hist = list(s.get("alarm_history") or [])
            # connector trả phần tử lồng của `alarm_history` thành `Row` → chuyển về dict
            alarm_map[s["code"]] = [
                (h.asDict(recursive=True) if not isinstance(h, dict) and
                 callable(getattr(h, "asDict", None)) else h)
                for h in hist
            ]
        print(f"Trạm: {len(alarm_map)}; có `alarm_history`: "
              f"{sum(1 for v in alarm_map.values() if v)}", flush=True)

        # ---- Hồ chứa: tổng theo ngày ---------------------------------------
        run.hold()
        res = read_mongo(spark, "reservoir_daily", None, db=args.db,
                         pipeline=[{"$project": {"_id": 0, "day": 1, "reservoir": 1, "inflow_m3s": 1,
                                                 "spillway_discharge_m3s": 1, "turbine_discharge_m3s": 1}}])
        res_day = (res.groupBy("day").agg(
            F.sum(F.col("inflow_m3s")).alias("res_inflow_m3s"),
            F.sum(F.col("spillway_discharge_m3s")).alias("res_spill_m3s"),
            F.sum(F.col("turbine_discharge_m3s")).alias("res_turbine_m3s"),
            F.countDistinct("reservoir").alias("n_reservoirs")))
        print("reservoir_daily theo ngày:", flush=True)

        # ---- Mưa theo ngày --------------------------------------------------
        rain = read_mongo(spark, "rainfall_daily", None, db=args.db,
                          pipeline=[{"$project": {"_id": 0, "day": 1, "station": 1, "rainfall_mm": 1}}])
        rain_day = (rain.groupBy("day").agg(
            F.sum(F.col("rainfall_mm")).alias("rain_mm"),
            F.countDistinct("station").alias("n_rain_stations")))
        print("rainfall_daily theo ngày:", flush=True)

        # ---- Ghép tất cả ---------------------------------------------------
        run.hold()
        g = (g.join(res_day, "day", "left").join(rain_day, "day", "left")
              .withColumn("year", F.substring("day", 1, 4).cast("int")))
        g = g.cache()
        n_rows = g.count()
        print(f"Số dòng (trạm, ngày): {n_rows}", flush=True)

        # ---- Báo động (tính trên driver: cần `alarm_history` theo thời gian) --
        days_by_station = [(r["station"], r["day"]) for r in g.select("station", "day").distinct().collect()]
        lvl_rows = []
        for st, day in days_by_station:
            h = alarm_for_day(alarm_map.get(st, []), day)
            if h is None:
                lvl_rows.append((st, day, None, None, None))
            else:
                lvl_rows.append((st, day, h.get("level_1_m"), h.get("level_2_m"), h.get("level_3_m")))
        lv = spark.createDataFrame(lvl_rows, "station string, day string, l1 double, l2 double, l3 double")
        g = g.join(F.broadcast(lv), ["station", "day"], "left")

        # `exceed_level` theo đỉnh lớn nhất trong ngày (đỉnh 1 và đỉnh 2)
        exc_rows = []
        for r in g.select("station", "day", "h_peak1", "h_peak2", "l1", "l2", "l3").collect():
            best, known = "", False
            for pk in (r["h_peak1"], r["h_peak2"]):
                if pk is None:
                    continue
                lv_, kv = exceed_level(float(pk), {"level_1_m": r["l1"], "level_2_m": r["l2"],
                                                    "level_3_m": r["l3"]})
                if kv and lv_ not in ("I", "II", "III"):
                    pass
                if kv:
                    order = {"": 0, "I": 1, "II": 2, "III": 3}
                    if order[lv_] > order[best]:
                        best = lv_
                    known = True
            exc_rows.append((r["station"], r["day"], best, known))
        exc = spark.createDataFrame(exc_rows, "station string, day string, exceed_level string, "
                                             "exceed_level_known boolean")
        g = g.join(exc, ["station", "day"], "left")

        # ---- astro_* (CSV của R) --------------------------------------------
        has_astro = os.path.exists(args.astro_csv)
        if has_astro:
            astro = spark.read.option("header", "true").option("inferSchema", "true").csv(args.astro_csv)
            cols = {c.lower(): c for c in astro.columns}
            need = {"day", "h1", "h2", "t1_h", "t2_h"}
            if not need.issubset(set(cols)):
                print(f"[CẢNH BÁO] {args.astro_csv} thiếu cột {sorted(need - set(cols))}; "
                      f"cột astro_* để null", flush=True)
                has_astro = False
            else:
                astro = (astro.select(F.col(cols["day"]).cast("string").alias("day"),
                                      F.col(cols["h1"]).cast("double").alias("astro_h1"),
                                      F.col(cols["h2"]).cast("double").alias("astro_h2"),
                                      F.col(cols["t1_h"]).cast("double").alias("astro_t1_h"),
                                      F.col(cols["t2_h"]).cast("double").alias("astro_t2_h")))
                g = g.join(F.broadcast(astro), "day", "left")
                print(f"Đã ghép {args.astro_csv}", flush=True)
        if not has_astro:
            print("[CẢNH BÁO] thiếu data/processed/features/astro_tide.csv – cột astro_* để null", flush=True)
            for c in ("astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h"):
                g = g.withColumn(c, F.lit(None).cast("double"))

        # ---- max_obs_day + kiểm tra rò rỉ ----------------------------------
        # Mẫu dự báo cho ngày `day` dùng: mưa + lưu lượng hồ của ngày `day−1` (đã qua) và
        # đỉnh triều của ngày `day` là NhãN (không phải đầu vào). Đầu vào quan trắc gần nhất
        # là ngày `day−1` (nếu có mưa hoặc lưu lượng hồ ghi nhận trong ngày đó).
        run.hold()
        obs_set = set(r["day"] for r in g.select("day").distinct().collect())
        rain_days = set(r["day"] for r in rain_day.select("day").distinct().collect())
        res_days = set(r["day"] for r in res_day.select("day").distinct().collect())
        obs_union = rain_days | res_days
        # Ngày quan trắc đầu vào gần nhất TRƯỚC `day`: dùng `bisect` trên danh sách ngày
        # đã sắp xếp (O(n log n) thay vì O(n²)).
        sorted_obs = sorted(obs_union)
        max_obs: dict[str, str | None] = {}
        for d in sorted(obs_set):
            i = bisect.bisect_left(sorted_obs, d) - 1
            max_obs[d] = sorted_obs[i] if i >= 0 else None
        mo = spark.createDataFrame([(d, v) for d, v in max_obs.items()],
                                   "day string, max_obs_day string")
        g = g.join(mo, "day", "left")

        leak = g.filter(F.col("max_obs_day").isNotNull() & (F.col("max_obs_day") >= F.col("day")))
        n_leak = leak.count()
        n_with_obs = g.filter(F.col("max_obs_day").isNotNull()).count()
        print(f"[{'OK ' if n_leak == 0 else 'LỖI'}] kiểm tra rò rỉ: max_obs_day < day cho mọi dòng — "
              f"{n_with_obs} dòng có quan trắc đầu vào, {n_leak} dòng vi phạm", flush=True)
        if n_leak:
            for r in leak.select("station", "day", "max_obs_day").limit(10).collect():
                print(f"    rò rỉ: {r['station']} day={r['day']} max_obs_day={r['max_obs_day']}", flush=True)
            return 1

        # ---- Chọn cột theo hợp đồng -----------------------------------------
        cols = [
            "station", "day", "year",
            "h_peak1", "h_peak2", "t_peak1_h", "t_peak2_h",
            "h_low1", "h_low2", "t_low1_h", "t_low2_h",
            "peak1_day_offset", "peak2_day_offset", "missing_peak1", "missing_peak2",
            "exceed_level", "exceed_level_known",
            "res_inflow_m3s", "res_spill_m3s", "res_turbine_m3s", "rain_mm",
            "lunar_day", "moon_phase",
            "astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h",
            "max_obs_day", "n_reservoirs", "n_rain_stations",
        ]
        g = g.select(*[c for c in cols if c in g.columns])
        # `moon_phase` có thể chưa tồn tại (chưa có bảng tra) → thêm cột null trước khi
        # chốt thứ tự cột theo hợp đồng.
        if "moon_phase" not in g.columns:
            g = g.withColumn("moon_phase", F.lit(None).cast("double"))
        g = g.select(*cols)

        # ---- Thống kê -------------------------------------------------------
        per_year = g.groupBy("year").agg(F.count("*").alias("n")).orderBy("year").collect()
        print("Số dòng theo năm:", {int(r["year"]): int(r["n"]) for r in per_year}, flush=True)
        per_station = g.groupBy("station").agg(F.count("*").alias("n")).orderBy("station").collect()
        print("Số dòng theo trạm:", {r["station"]: int(r["n"]) for r in per_station}, flush=True)
        n_miss1 = g.filter(F.col("missing_peak1")).count()
        n_miss2 = g.filter(F.col("missing_peak2")).count()
        print(f"Thiếu đỉnh 1: {n_miss1}; thiếu đỉnh 2: {n_miss2}", flush=True)
        exc_counts = g.groupBy("exceed_level").agg(F.count("*").alias("n")).collect()
        print("Phân bố `exceed_level`:", {r["exceed_level"] or "(rỗng)": int(r["n"]) for r in exc_counts},
              flush=True)
        split_counts = {k: 0 for k in SPLITS}
        for r in g.select("day").distinct().collect():
            s = split_of(r["day"])
            if s:
                split_counts[s] += 1
        print(f"Số ngày theo tập: {split_counts}", flush=True)

        # ---- data_snapshot --------------------------------------------------
        snap = {
            "run_date": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            "job_version": JOB_VERSION,
            "collections": {
                "tide_extremes": adm.count("tide_extremes"),
                "official_forecasts": adm.count("official_forecasts"),
                "reservoir_daily": adm.count("reservoir_daily"),
                "rainfall_daily": adm.count("rainfall_daily"),
                "hydromet_bulletins": adm.count("hydromet_bulletins"),
                "stations": adm.count("stations"),
                "merit_river_reaches": adm.count("merit_river_reaches"),
                "qc_flags": adm.count("qc_flags") if adm.exists("qc_flags") else 0,
            },
            "max_timestamps": {
                "tide_extremes_ts": _max_ts(spark, "tide_extremes"),
                "reservoir_daily_date": _max_ts(spark, "reservoir_daily", "date"),
                "rainfall_daily_date": _max_ts(spark, "rainfall_daily", "date"),
                "official_forecasts_target_date": _max_ts(spark, "official_forecasts", "target_date"),
            },
            "day_range": [g.select(F.min("day")).first()[0], g.select(F.max("day")).first()[0]],
            "rows": n_rows,
            "rows_per_year": {str(int(r["year"])): int(r["n"]) for r in per_year},
            "rows_per_station": {r["station"]: int(r["n"]) for r in per_station},
            "astro_merged": has_astro,
            "leak_check": {"rows_with_input_obs": n_with_obs, "violations": n_leak},
        }
        run.update({"stats": snap})

        if args.dry_run:
            print("--dry-run: không ghi Parquet.", flush=True)
            status = "dry_run"
            return 0

        # ---- Ghi Parquet -----------------------------------------------------
        run.hold()
        if os.path.exists(args.out_dir):
            for name in os.listdir(args.out_dir):
                if name.startswith("year="):
                    import shutil
                    shutil.rmtree(os.path.join(args.out_dir, name), ignore_errors=True)
        os.makedirs(args.out_dir, exist_ok=True)
        g.write.mode("overwrite").partitionBy("year").parquet(args.out_dir)
        print(f"[PARQUET] {args.out_dir}/year=<YYYY>/ ({n_rows} dòng)", flush=True)

        meta = {
            "job": JOB,
            "job_version": JOB_VERSION,
            "run_id": run.run_id,
            "run_date": snap["run_date"],
            "data_snapshot": snap,
            "splits": {k: {"from": v[0], "to": v[1]} for k, v in SPLITS.items()},
            "columns": cols,
            "peak_day_offset_rule": (
                "day = ngày quan sát của bản tin; peakN_day_offset = 0 nếu đỉnh thuộc ngày quan sát, "
                "1 nếu sang ngày kế tiếp (quy tắc dùng chung với pipelines/r/)"),
        }
        with open(os.path.join(args.out_dir, "_meta.json"), "w", encoding="utf-8", newline="\n") as fh:
            json.dump(meta, fh, ensure_ascii=False, indent=1, default=str)
            fh.write("\n")
        print(f"[META] {args.out_dir}/_meta.json", flush=True)
        status = "ok"
        return 0
    except LockLostError as e:
        print(f"Dừng: {e}", flush=True)
        return 5
    except Exception:  # noqa: BLE001
        status = "failed"
        traceback.print_exc()
        print("Dừng: lỗi không lường trước (mã thoát 6).", flush=True)
        return 6
    finally:
        try:
            run.finish(status)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()


# ---------------------------------------------------------------------------
def _vn_date(v) -> str | None:
    """Ngày Việt Nam (YYYY-MM-DD) của một mốc thời gian."""
    if v is None:
        return None
    if isinstance(v, dict):
        s = v.get("$date")
        if isinstance(s, dict):
            v = datetime.fromtimestamp(int(s["$numberLong"]) / 1000, tz=timezone.utc)
        else:
            v = s
    if isinstance(v, datetime):
        return (v + __import__("datetime").timedelta(hours=7)).strftime("%Y-%m-%d")
    return str(v)[:10]


def _prev_day(day: str) -> str:
    """Ngày liền trước của `day` (YYYY-MM-DD)."""
    from datetime import date, timedelta
    y, m, d = (int(x) for x in day.split("-"))
    return (date(y, m, d) - timedelta(days=1)).strftime("%Y-%m-%d")


def _hour_of_day(col: str):
    """Giờ trong ngày (0..24) của một cột timestamp; null nếu null."""
    return F.when(F.col(col).isNull(), F.lit(None).cast("double")).otherwise(
        (F.hour(F.col(col)) + F.minute(F.col(col)) / 60.0
         + F.second(F.col(col)) / 3600.0).cast("double"))


def _max_ts(spark, coll: str, field: str = "ts") -> str | None:
    """Mốc thời gian lớn nhất của một collection (đọc bằng aggregation trên server)."""
    if not spark.sparkContext._jvm:  # pragma: no cover
        return None
    out = adm_max(spark, coll, field)
    return out


def adm_max(spark, coll: str, field: str) -> str | None:
    """$max của một trường, đọc bằng aggregation MongoDB qua driver Java."""
    jvm = spark.sparkContext._jvm
    client = jvm.com.mongodb.client.MongoClients.create(
        spark.conf.get("spark.mongodb.read.connection.uri"))
    try:
        db = client.getDatabase(spark.conf.get("spark.mongodb.read.database"))
        # LƯU Ý: phải dùng dạng $group + $max (accumulator trên cả luồng).
        # Dạng $project + $max chỉ lấy giá trị của tài liệu ĐẦU TIÊN → sai.
        doc = jvm.org.bson.Document.parse(json.dumps(
            {"aggregate": coll, "pipeline": [{"$group": {"_id": None, "m": {"$max": f"${field}"}}},
                                            {"$limit": 1}], "cursor": {}}))
        res = db.runCommand(doc).toJson()
        batch = json.loads(res)["cursor"]["firstBatch"]
        if not batch:
            return None
        m = batch[0].get("m")
        if m is None:
            return None
        if isinstance(m, dict):
            m = m.get("$date")
        if isinstance(m, dict):
            ms = int(m["$numberLong"])
            return datetime.fromtimestamp(ms / 1000, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        return str(m)
    finally:
        client.close()


if __name__ == "__main__":
    sys.exit(main())
