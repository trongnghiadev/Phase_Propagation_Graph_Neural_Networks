"""Q7 – Độ chính xác dự báo nghiệp vụ theo chân trời (lead time).

    spark-submit q7_forecast_accuracy.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q7]
                                         [--tolerance-h 6] [--lock-ttl-s 3600]
                                         [--dry-run] [--no-cache]

**Câu hỏi.** Bản tin dự báo triều chính thức của KTTV TP.HCM có chính xác không, và độ chính
xác giảm thế nào theo chân trời dự báo (lead day 1…5)? Trạm nào dự báo tốt/hơn?

**Nguồn.**
- `official_forecasts` (38.410 bản tin): `station`, `issued_date`, `target_date`, `lead_day`,
  `high[]`, `low[]` — mỗi phần tử trong `high`/`low` có `ts` (mốc thời gian dự báo), `value`,
  `qc`.
- `tide_extremes` (129.420 phần tử): đỉnh/chân **thực đo**.

**Cách ghép (đã chốt).**
1. Với mỗi bản tin và mỗi dự báo đỉnh (`high`) / chân (`low`), lấy `value` và `ts`.
2. Ghép với quan trắc: tìm quan trắc cùng `station`, cùng `kind`, có `ts` **trong khoảng
   ±`--tolerance-h` giờ** quanh `ts` của dự báo (mặc định 6 h). Dùng `join` theo khoảng
   thời gian trên cùng `station` + `kind`.
   - Nếu có nhiều quan trắc trong cửa sổ → lấy quan trắc **GẦN NHẤT** theo `ts`.
   - Không có quan trắc nào trong cửa sổ → cặp đó không được tính vào MAE (đếm riêng).
3. `lead_day` = `target_date − issued_date` (tính lại từ ngày để tránh lỗi bản tin), kiểm tra
   khớp với trường `lead_day` của bản tin.
4. MAE chiều cao (m) và MAE chiều giờ đỉnh (h) theo (trạm, lead_day) và theo lead_day tổng.

**THỦ DẦU MỘT.** Tách riêng `THU_DAU_MOT` (chỉ có dự báo từ giữa 2025) — nếu gộp vào số
liệu chung sẽ làm sai lệch so sánh giữa các giai đoạn. Báo cáo riêng trong
`q7_by_station.csv` và nêu rõ trong `README.md`.

**Đầu ra** (`results/Q7/`):
- `q7_by_lead_day.csv` — theo lead_day: MAE cao, MAE giờ đỉnh, số cặp, số trạm.
- `q7_by_station_lead.csv` — theo (trạm, lead_day).
- `q7_error_distribution.csv` — phân bố sai số (theo khoảng).
- `q7_by_station.csv` — tổng hợp theo trạm.
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

JOB = "q7_forecast_accuracy"
TOLERANCE_H_DEFAULT = 6.0

TIDE = StructType([
    StructField("ts", TimestampType()),
    StructField("station", StringType()),
    StructField("kind", StringType()),
    StructField("value", DoubleType()),
    StructField("qc", StringType()),
])
TIDE_PIPELINE = [{"$project": {"_id": 0, "ts": 1, "station": "$meta.station", "kind": "$meta.kind",
                                "value": 1, "qc": 1}}]


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run
    tol_h = float(getattr(ctx.args, "tolerance_h", TOLERANCE_H_DEFAULT))

    # ---- 1. Dự báo: mỗi phần tử high/low thành một dòng -------------------
    run.hold()
    fc = read_mongo(spark, "official_forecasts", None, db=ctx.db,
                    pipeline=[{"$project": {"_id": 0, "station": 1, "issued_date": 1, "target_date": 1,
                                            "lead_day": 1, "high": 1, "low": 1}}])
    n_fc = fc.count()
    print(f"official_forecasts: {n_fc} bản tin", flush=True)
    if n_fc == 0:
        raise SystemExit("Dừng: `official_forecasts` rỗng.")

    run.hold()
    # `explode` mảng `high`/`low` lồng nhau: `posexplode` hai mảng rồi ghép theo chỉ số.
    parts = []
    for kind in ("high", "low"):
        p = (fc.select(F.explode(F.col(kind)).alias("e"), "station", "issued_date", "target_date",
                       "lead_day")
             .select(F.col("e.ts").alias("ts"), F.col("e.value").alias("fc_value"),
                     F.col("e.qc").alias("fc_qc"), "station", "issued_date", "target_date", "lead_day")
             .withColumn("kind", F.lit(kind)))
        parts.append(p)
    preds = parts[0].unionByName(parts[1])
    n_pred = preds.count()
    print(f"Số dự báo (đỉnh/chân): {n_pred}", flush=True)

    # ---- 2. Quan trắc ------------------------------------------------------
    run.hold()
    obs = (read_mongo(spark, "tide_extremes", TIDE, TIDE_PIPELINE, ctx.db,
                      {"partitioner": SINGLE_PARTITIONER})
           .filter(F.col("value").isNotNull())
           .select(F.col("ts"), F.col("station"), F.col("kind"), F.col("value").alias("obs_value"))
           .cache())
    n_obs = obs.count()
    print(f"tide_extremes có giá trị: {n_obs}", flush=True)

    # ---- 3. Ghép trong cửa sổ ±tol giờ -------------------------------------
    run.hold()
    tol_s = tol_h * 3600.0
    # Ghép theo `station` + `kind` + khoảng thời gian. Vì chuỗi thời gian là UTC nhưng
    # đều đặt theo cùng lưới thời gian Việt Nam nên chênh lệch tuyệt đối là hợp lệ.
    # Đổi tên cột TRƯỚC khi join để tránh `ts` bị trùng (AMBIGUOUS_REFERENCE).
    joined = (preds.withColumnRenamed("ts", "fc_ts")
              .join(obs.withColumnRenamed("ts", "obs_ts"), ["station", "kind"])
              .filter(F.abs(F.unix_timestamp(F.col("fc_ts")) - F.unix_timestamp(F.col("obs_ts")))
                      <= tol_s))

    # lấy quan trắc GẦN NHẤT
    w = Window.partitionBy("station", "kind", "fc_ts", "issued_date").orderBy(
        F.abs(F.unix_timestamp(F.col("fc_ts")) - F.unix_timestamp(F.col("obs_ts"))).asc())
    matched = (joined.withColumn("rn", F.row_number().over(w))
               .filter(F.col("rn") == 1)
               .drop("rn"))
    n_matched = matched.count()
    print(f"Cặp dự báo–quan trắc khớp trong ±{tol_h:.0f} h: {n_matched} / {n_pred}", flush=True)

    # ---- 4. Sai số ----------------------------------------------------------
    run.hold()
    df = (matched.withColumn("err_h", F.col("fc_value") - F.col("obs_value"))
          .withColumn("abs_err_h", F.abs(F.col("err_h")))
          .withColumn("err_time_h",
                      F.abs((F.unix_timestamp(F.col("fc_ts"))
                             - F.unix_timestamp(F.col("obs_ts"))) ) / 3600.0)
          .withColumn("lead_day_calc",
                      F.datediff(F.to_date(F.col("target_date"), "yyyy-MM-dd"),
                                 F.to_date(F.col("issued_date"), "yyyy-MM-dd")))
          .withColumn("year", F.substring(F.col("issued_date"), 1, 4).cast("int"))
          .cache())
    n_df = df.count()
    print(f"Số dòng đánh giá được: {n_df}", flush=True)

    # ---- 5. Tổng hợp theo lead_day -----------------------------------------
    run.hold()
    by_lead = (df.groupBy("lead_day")
               .agg(F.count(F.lit(1)).alias("n_pairs"),
                    F.countDistinct("station").alias("n_stations"),
                    F.round(F.avg("abs_err_h"), 4).alias("mae_height_m"),
                    F.round(F.expr("percentile_approx(abs_err_h, 0.5)"), 4).alias("median_abs_err_h"),
                    F.round(F.max("abs_err_h"), 4).alias("max_abs_err_h"),
                    F.round(F.avg("err_time_h"), 4).alias("mae_peak_hour_h"))
               .orderBy("lead_day"))
    rows_lead = [[r["lead_day"], r["n_pairs"], r["n_stations"], r["mae_height_m"],
                  r["median_abs_err_h"], r["max_abs_err_h"], r["mae_peak_hour_h"]]
                 for r in by_lead.collect()]

    # ---- 6. Theo (trạm, lead_day) ------------------------------------------
    run.hold()
    by_st_lead = (df.groupBy("station", "lead_day")
                  .agg(F.count(F.lit(1)).alias("n_pairs"),
                       F.round(F.avg("abs_err_h"), 4).alias("mae_height_m"),
                       F.round(F.expr("percentile_approx(abs_err_h, 0.5)"), 4)
                       .alias("median_abs_err_h"),
                       F.round(F.avg("err_time_h"), 4).alias("mae_peak_hour_h"))
                  .orderBy("station", "lead_day"))
    rows_st_lead = [[r["station"], r["lead_day"], r["n_pairs"], r["mae_height_m"],
                     r["median_abs_err_h"], r["mae_peak_hour_h"]] for r in by_st_lead.collect()]

    # ---- 7. Phân bố sai số --------------------------------------------------
    run.hold()
    bins = [(-9.99, -1.0), (-1.0, -0.5), (-0.5, -0.2), (-0.2, 0.2), (0.2, 0.5), (0.5, 1.0), (1.0, 9.99)]
    rows_dist = []
    for lo, hi in bins:
        n = df.filter((F.col("err_h") >= lo) & (F.col("err_h") < hi)).count()
        rows_dist.append([f"[{lo}, {hi})", n, round(100.0 * n / n_df, 3) if n_df else 0.0])

    # ---- 8. Tổng hợp theo trạm ---------------------------------------------
    run.hold()
    by_st = (df.groupBy("station")
             .agg(F.count(F.lit(1)).alias("n_pairs"),
                  F.round(F.avg("abs_err_h"), 4).alias("mae_height_m"),
                  F.round(F.max("abs_err_h"), 4).alias("max_abs_err_h"),
                  F.round(F.avg("err_time_h"), 4).alias("mae_peak_hour_h"),
                  F.min("year").alias("year_first"), F.max("year").alias("year_last"))
             .orderBy(F.desc("n_pairs")))
    rows_st = [[r["station"], r["n_pairs"], r["mae_height_m"], r["max_abs_err_h"],
                r["mae_peak_hour_h"], r["year_first"], r["year_last"]] for r in by_st.collect()]

    findings = [
        f"`official_forecasts`: {n_fc} bản tin → {n_pred} dự báo (đỉnh + chân).",
        f"`tide_extremes` có giá trị: {n_obs}; cặp khớp trong ±{tol_h:.0f} h: {n_matched} "
        f"({100.0 * n_matched / n_pred:.1f}% dự báo có quan trắc đối chiếu).",
    ]
    for r in rows_lead:
        findings.append(f"lead_day {r[0]}: {r[1]} cặp / {r[2]} trạm; MAE chiều cao "
                        f"{r[3]} m; MAE chiều giờ đỉnh {r[6]} h; sai số lớn nhất {r[5]} m.")
    for r in rows_st:
        findings.append(f"Trạm {r[0]}: {r[1]} cặp ({r[5]}–{r[6]}), MAE cao {r[2]} m, "
                        f"MAE giờ đỉnh {r[4]} h.")

    outputs = []
    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        write_csv(ctx.path("q7_by_lead_day.csv"),
                  ["lead_day", "n_pairs", "n_stations", "mae_height_m", "median_abs_err_h",
                   "max_abs_err_h", "mae_peak_hour_h"], rows_lead)
        write_csv(ctx.path("q7_by_station_lead.csv"),
                  ["station", "lead_day", "n_pairs", "mae_height_m", "median_abs_err_h",
                   "mae_peak_hour_h"], rows_st_lead)
        write_csv(ctx.path("q7_error_distribution.csv"),
                  ["err_bin_m", "n_pairs", "pct"], rows_dist)
        write_csv(ctx.path("q7_by_station.csv"),
                  ["station", "n_pairs", "mae_height_m", "max_abs_err_h", "mae_peak_hour_h",
                   "year_first", "year_last"], rows_st)
        write_readme(
            ctx.path("README.md"), "Q7 – Độ chính xác dự báo nghiệp vụ theo chân trời",
            "Bản tin dự báo chính thức chính xác bao nhiêu, và độ chính xác giảm thế nào theo "
            "chân trời dự báo (lead day 1…5)?",
            {"Nguồn dự báo": "`official_forecasts`", "Nguồn quan trắc": "`tide_extremes`",
             "Cửa sổ ghép": f"±{tol_h:.0f} giờ quanh mốc dự báo",
             "Quy tắc khi nhiều quan trắc": "lấy quan trắc GẦN NHẤT theo `ts`",
             "Số bản tin": n_fc, "Số dự báo": n_pred, "Số cặp khớp": n_matched},
            ["q7_by_lead_day.csv", "q7_by_station_lead.csv", "q7_error_distribution.csv",
             "q7_by_station.csv"],
            findings,
            ["MAE giờ đỉnh tính trên TOÀN BỘ cặp (đỉnh và chân), không chỉ đỉnh.",
             "Thủ Dầu Một chỉ có dự báo từ giữa 2025 → cột `year_first` cho thấy kỳ dữ liệu; "
             "không so sánh trực tiếp với các trạm có dữ liệu từ 2017.",
             "Dự báo không có quan trắc đối chiếu trong cửa sổ ±6 h bị LOẠI khỏi MAE (đếm ở "
             "`n_pairs`), không coi là sai số 0."],
            now_vn())
        outputs = ["q7_by_lead_day.csv", "q7_by_station_lead.csv", "q7_error_distribution.csv",
                   "q7_by_station.csv", "README.md"]

    cache = ResultCache("q7_forecast_accuracy", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    if cache.enabled and not ctx.args.dry_run:
        import json  # noqa: PLC0415
        cache.set("by_lead_day", json.dumps(rows_lead, ensure_ascii=False))

    return findings, outputs


if __name__ == "__main__":
    def _extra(ap):
        ap.add_argument("--tolerance-h", type=float, default=TOLERANCE_H_DEFAULT,
                        help="cửa sổ ghép dự báo–quan trắc, tính bằng giờ (mặc định 6)")

    sys.exit(run_job(JOB, "Q7", body, extra_args=_extra))
