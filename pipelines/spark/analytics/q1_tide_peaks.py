"""Q1 – Đỉnh triều theo trạm / năm / tháng và số ngày vượt từng cấp báo động.

    spark-submit q1_tide_peaks.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q1]
                                 [--lock-ttl-s 3600] [--dry-run] [--no-cache]

**Câu hỏi.** Trong lưu vực Đồng Nai – Sài Gòn, đỉnh triều thay đổi thế nào theo trạm, theo
năm và theo tháng? Trạm nào hay vượt báo động hơn, và vượt cấp nào?

**Nguồn.**
- `tide_extremes` (time series): chỉ lấy `qc = "ok"` và `qc_flag != "suspect"`, `kind = "high"`.
- `stations.alarm_history`: ngưỡng báo động I/II/III **THEO THỜI GIAN** (mỗi đoạn có
  `from`/`to` và 3 ngưỡng). Không dùng ngưỡng cố định — đây là điểm khác biệt quan trọng
  với các bản báo cáo cũ: ngưỡng ở Nhà Bè đổi 4 lần trong 2017–2026.

**Cách tính.**
1. Với mỗi (trạm, ngày) lấy đỉnh cao nhất (`max(value)` của các đỉnh `high` trong ngày).
2. Gắn ngưỡng của ngày đó: chọn đoạn `alarm_history` có `from ≤ day ≤ to`. Ngày nằm ngoài
   mọi đoạn → `exceed_level = ""` và cờ `threshold_known = false` (KHÔNG coi là không vượt).
3. `exceed_level` = mức cao nhất mà đỉnh đạt: `""` / `"I"` / `"II"` / `"III"`.
4. Tổng hợp theo (trạm, năm) và (trạm, năm, tháng): số ngày có đỉnh, trung vị/trung bình/max
   đỉnh (m), và số ngày vượt từng cấp.

**CẢNH BÁO CẦN BIẾT.** Nếu trạm không có `alarm_history` (Biên Hòa, Vũng Tàu) thì cột
đếm vượt báo động bằng 0 và `threshold_known = false` cho toàn bộ kỳ — đây là THIẾU DỮ LIỆU
NGƯỠNG, không phải "không vượt".

**Đầu ra** (`results/Q1/`):
- `q1_peaks_by_station_year.csv` — theo (trạm, năm).
- `q1_peaks_by_station_year_month.csv` — theo (trạm, năm, tháng).
- `q1_exceedance_summary.csv` — tổng hợp số ngày vượt từng cấp theo trạm.
- `README.md`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import Window, functions as F  # noqa: E402
from pyspark.sql.types import (DoubleType, StringType, StructField, StructType,  # noqa: E402
                               TimestampType)

from analytics.base import ResultCache, now_vn, run_job, write_csv, write_readme  # noqa: E402
from common.session import SINGLE_PARTITIONER, read_mongo  # noqa: E402

JOB = "q1_tide_peaks"

TIDE = StructType([
    StructField("ts", TimestampType()),
    StructField("station", StringType()),
    StructField("kind", StringType()),
    StructField("value", DoubleType()),
    StructField("qc", StringType()),
    StructField("qc_flag", StringType()),
])
TIDE_PIPELINE = [{"$project": {"_id": 0, "ts": 1, "station": "$meta.station", "kind": "$meta.kind",
                                "value": 1, "qc": 1, "qc_flag": 1}}]

LEVEL_ORDER = {"": 0, "I": 1, "II": 2, "III": 3}


def _alarm_rows(st_rows: list[dict]) -> list[tuple]:
    """Flatten `stations.alarm_history` thành các dòng (trạm, từ, đến, l1, l2, l3).

    `alarm_history` là mảng document lồng; connector trả về `Row` nên phải `.asDict()`.
    """
    out = []
    for s in st_rows:
        hist = s.get("alarm_history") or []
        for h in hist:
            if not isinstance(h, dict):
                h = h.asDict(recursive=True)
            f = h.get("from")
            t = h.get("to")
            if f is None or t is None:
                continue
            out.append((s["code"], str(f)[:10], str(t)[:10],
                        h.get("level_1_m"), h.get("level_2_m"), h.get("level_3_m")))
    return out


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run

    # ---- Đọc đỉnh triều --------------------------------------------------
    run.hold()
    tide = (read_mongo(spark, "tide_extremes", TIDE, TIDE_PIPELINE, ctx.db,
                       {"partitioner": SINGLE_PARTITIONER})
            .filter((F.col("qc") == "ok")
                    & (F.coalesce(F.col("qc_flag"), F.lit("")) != "suspect"))
            .filter(F.col("kind") == "high")
            .withColumn("day", F.date_format("ts", "yyyy-MM-dd"))
            .withColumn("year", F.substring("day", 1, 4).cast("int"))
            .withColumn("month", F.substring("day", 6, 2).cast("int")))
    n_raw = tide.count()
    print(f"tide_extremes (high, qc=ok): {n_raw} phần tử", flush=True)

    # ---- Đỉnh cao nhất mỗi (trạm, ngày) ---------------------------------
    run.hold()
    daily = (tide.groupBy("station", "day", "year", "month")
             .agg(F.max("value").alias("h_peak")))
    n_days = daily.count()
    print(f"Số (trạm, ngày): {n_days}", flush=True)

    # ---- Ngưỡng báo động theo thời gian -----------------------------------
    run.hold()
    st_rows = [r.asDict() for r in read_mongo(spark, "stations", None, db=ctx.db).collect()]
    alarms = _alarm_rows(st_rows)
    n_alarm_stations = len({a[0] for a in alarms})
    print(f"Trạm có alarm_history: {n_alarm_stations} / {len(st_rows)}", flush=True)
    if alarms:
        al = spark.createDataFrame(
            alarms, "station string, from_day string, to_day string, l1 double, l2 double, l3 double")
        # mỗi (trạm, ngày) → đoạn ngưỡng hiệu lực: join theo điều kiện khoảng ngày.
        # Đổi tên cột khoá để tránh "ambiguous reference" khi join hai cột cùng tên.
        d2 = daily.withColumnRenamed("station", "st")
        a2 = al.withColumnRenamed("station", "al_st")
        joined = (d2.join(a2, d2["st"] == a2["al_st"])
                  .filter((F.col("day") >= F.col("from_day")) & (F.col("day") <= F.col("to_day")))
                  .withColumn("station", F.col("st")))
        # nếu có nhiều đoạn khớp (không xảy ra vì các đoạn không chồng lấn) → giữ đoạn đầu
        w = Window.partitionBy("station", "day").orderBy("from_day")
        joined = joined.withColumn("rn", F.row_number().over(w)).filter(F.col("rn") == 1)
        joined = joined.drop("rn", "from_day", "to_day", "st", "al_st")
        peaks = joined.withColumn(
            "exceed_level",
            F.when(F.col("h_peak").isNull(), F.lit(""))
            .when(F.col("l3").isNotNull() & (F.col("h_peak") >= F.col("l3")), F.lit("III"))
            .when(F.col("l2").isNotNull() & (F.col("h_peak") >= F.col("l2")), F.lit("II"))
            .when(F.col("l1").isNotNull() & (F.col("h_peak") >= F.col("l1")), F.lit("I"))
            .otherwise(F.lit(""))).withColumn("threshold_known", F.lit(True))
    else:
        peaks = daily.withColumn("exceed_level", F.lit("")).withColumn("threshold_known", F.lit(False))

    # các (trạm, ngày) KHÔNG có đoạn ngưỡng nào hiệu lực → threshold_known = false
    known_stations = {a[0] for a in alarms}
    if known_stations:
        peaks = peaks.withColumn("threshold_known",
                                 F.col("threshold_known") & F.col("station").isin(list(known_stations)))
    else:
        peaks = peaks.withColumn("threshold_known", F.lit(False))

    peaks = peaks.cache()
    n_peaks = peaks.count()
    print(f"Số (trạm, ngày) có ngưỡng: {n_peaks}", flush=True)

    # ---- Tổng hợp theo (trạm, năm) --------------------------------------
    run.hold()
    by_year = (peaks.groupBy("station", "year")
               .agg(F.count(F.lit(1)).alias("n_days"),
                    F.count(F.col("h_peak")).alias("n_days_with_value"),
                    F.round(F.avg("h_peak"), 4).alias("mean_peak_m"),
                    F.round(F.expr("percentile_approx(h_peak, 0.5)"), 4).alias("median_peak_m"),
                    F.round(F.max("h_peak"), 4).alias("max_peak_m"),
                    F.sum(F.when(F.col("exceed_level") == "I", 1).otherwise(0)).alias("n_exceed_I"),
                    F.sum(F.when(F.col("exceed_level") == "II", 1).otherwise(0)).alias("n_exceed_II"),
                    F.sum(F.when(F.col("exceed_level") == "III", 1).otherwise(0)).alias("n_exceed_III"),
                    F.sum(F.when(F.col("threshold_known"), 1).otherwise(0)).alias("n_days_with_threshold"))
               .orderBy("station", "year"))
    rows_year = [[r["station"], r["year"], r["n_days"], r["n_days_with_value"], r["mean_peak_m"],
                  r["median_peak_m"], r["max_peak_m"], r["n_exceed_I"], r["n_exceed_II"],
                  r["n_exceed_III"], r["n_days_with_threshold"]] for r in by_year.collect()]

    # ---- Tổng hợp theo (trạm, năm, tháng) --------------------------------
    run.hold()
    by_month = (peaks.groupBy("station", "year", "month")
                .agg(F.count(F.lit(1)).alias("n_days"),
                     F.round(F.avg("h_peak"), 4).alias("mean_peak_m"),
                     F.round(F.expr("percentile_approx(h_peak, 0.5)"), 4).alias("median_peak_m"),
                     F.round(F.max("h_peak"), 4).alias("max_peak_m"),
                     F.sum(F.when(F.col("exceed_level") == "I", 1).otherwise(0)).alias("n_exceed_I"),
                     F.sum(F.when(F.col("exceed_level") == "II", 1).otherwise(0)).alias("n_exceed_II"),
                     F.sum(F.when(F.col("exceed_level") == "III", 1).otherwise(0)).alias("n_exceed_III"))
                .orderBy("station", "year", "month"))
    rows_month = [[r["station"], r["year"], r["month"], r["n_days"], r["mean_peak_m"],
                   r["median_peak_m"], r["max_peak_m"], r["n_exceed_I"], r["n_exceed_II"],
                   r["n_exceed_III"]] for r in by_month.collect()]

    # ---- Tổng hợp vượt báo động theo trạm --------------------------------
    run.hold()
    by_station = (peaks.groupBy("station")
                  .agg(F.count(F.lit(1)).alias("n_days"),
                       F.round(F.avg("h_peak"), 4).alias("mean_peak_m"),
                       F.round(F.max("h_peak"), 4).alias("max_peak_m"),
                       F.sum(F.when(F.col("exceed_level") == "I", 1).otherwise(0)).alias("n_exceed_I"),
                       F.sum(F.when(F.col("exceed_level") == "II", 1).otherwise(0)).alias("n_exceed_II"),
                       F.sum(F.when(F.col("exceed_level") == "III", 1).otherwise(0)).alias("n_exceed_III"),
                       F.sum(F.when(F.col("threshold_known"), 1).otherwise(0)).alias("n_days_with_threshold"))
                  .orderBy(F.desc("n_exceed_III"), F.desc("n_exceed_II"), F.desc("n_exceed_I")))
    rows_station = [[r["station"], r["n_days"], r["mean_peak_m"], r["max_peak_m"], r["n_exceed_I"],
                     r["n_exceed_II"], r["n_exceed_III"], r["n_days_with_threshold"]]
                    for r in by_station.collect()]

    # ---- Ghi kết quả ------------------------------------------------------
    outputs = []
    findings = [
        f"`tide_extremes` (high, qc=ok, không suspect): {n_raw} phần tử → {n_days} (trạm, ngày) → "
        f"{n_peaks} (trạm, ngày) có ngưỡng báo động hiệu lực.",
        f"Trạm có `alarm_history`: {n_alarm_stations} / {len(st_rows)}; "
        f"{len(st_rows) - n_alarm_stations} trạm KHÔNG có ngưỡng (cột đếm vượt báo động = 0 "
        f"và `threshold_known = false` — đây là thiếu dữ liệu ngưỡng, không phải không vượt).",
    ]
    for r in rows_station[:6]:
        findings.append(
            f"Trạm {r[0]}: {r[1]} ngày có đỉnh, TB {r[2]} m, max {r[3]} m; "
            f"vượt I = {r[4]}, II = {r[5]}, III = {r[6]}; ngày có ngưỡng = {r[7]}.")

    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        hdr_year = ["station", "year", "n_days", "n_days_with_value", "mean_peak_m", "median_peak_m",
                    "max_peak_m", "n_exceed_I", "n_exceed_II", "n_exceed_III", "n_days_with_threshold"]
        hdr_month = ["station", "year", "month", "n_days", "mean_peak_m", "median_peak_m", "max_peak_m",
                     "n_exceed_I", "n_exceed_II", "n_exceed_III"]
        hdr_st = ["station", "n_days", "mean_peak_m", "max_peak_m", "n_exceed_I", "n_exceed_II",
                  "n_exceed_III", "n_days_with_threshold"]
        write_csv(ctx.path("q1_peaks_by_station_year.csv"), hdr_year, rows_year)
        write_csv(ctx.path("q1_peaks_by_station_year_month.csv"), hdr_month, rows_month)
        write_csv(ctx.path("q1_exceedance_summary.csv"), hdr_st, rows_station)
        write_readme(
            ctx.path("README.md"), "Q1 – Đỉnh triều và vượt báo động theo trạm/năm/tháng",
            "Đỉnh triều cao nhất mỗi ngày thay đổi thế nào theo trạm, năm, tháng? Trạm nào hay "
            "vượt báo động (I/II/III) và vượt cấp nào?",
            {"Nguồn đỉnh": "`tide_extremes` (kind=high), qc=ok, qc_flag≠suspect",
             "Nguồn ngưỡng": "`stations.alarm_history` (theo thời gian)",
             "Số phần tử đỉnh đọc": n_raw,
             "Số (trạm, ngày)": n_days,
             "Ngưỡng cố định": "KHÔNG dùng — luôn tra `alarm_history` theo ngày"},
            ["q1_peaks_by_station_year.csv", "q1_peaks_by_station_year_month.csv",
             "q1_exceedance_summary.csv"],
            findings,
            ["Trạm không có `alarm_history` không có số ngày vượt báo động — phải đọc cột "
             "`n_days_with_threshold` để biết phần nào có ngưỡng.",
             "Ngưỡng lấy theo mốc `from`/`to` của đoạn `alarm_history` (so sánh theo NGÀY, bỏ giờ).",
             "Đỉnh `high` có thể có nhiều bản ghi trong một ngày (bản tin khác nhau) → lấy `max`."],
            now_vn())
        outputs = ["q1_peaks_by_station_year.csv", "q1_peaks_by_station_year_month.csv",
                   "q1_exceedance_summary.csv", "README.md"]

    # ---- Cache Redis (tuỳ chọn) ------------------------------------------
    cache = ResultCache("q1_tide_peaks", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    if cache.enabled and not ctx.args.dry_run:
        import json  # noqa: PLC0415
        cache.set("by_station_year", json.dumps(rows_year, ensure_ascii=False))
        cache.set("by_station", json.dumps(rows_station, ensure_ascii=False))

    return findings, outputs


if __name__ == "__main__":
    sys.exit(run_job(JOB, "Q1", body))
