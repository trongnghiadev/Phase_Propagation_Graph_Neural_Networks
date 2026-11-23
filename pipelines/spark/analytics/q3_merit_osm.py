"""Q3 – Thống kê dữ liệu MERIT trong cây lưu vực và OSM (`waterways_segments`).

    spark-submit q3_merit_osm.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q3]
                                [--root-comid 44045960] [--lock-ttl-s 3600] [--dry-run] [--no-cache]

**Câu hỏi.** Cấu trúc lưu vực Đồng Nai – Sài Gòn theo MERIT thế nào (phân bố theo `order`,
chiều dài, độ dốc, diện tích)? Và lớp OSM (`waterways_segments`) phân bố ra sao theo `fclass`?

**Nguồn.**
- `merit_river_reaches`: lọc theo cây lưu vực gốc `--root-comid` (mặc định 44045960) đi ngược
  theo `up1..up4` — cùng quy tắc với G1 (`features.rules.build_tree`).
- `waterways_segments`: toàn bộ (38.877 đoạn), thống kê theo `fclass`.

**Đầu ra** (`results/Q3/`):
- `q3_merit_by_order.csv` — theo `order`: số reach, tổng/median chiều dài, median độ dốc,
  tổng/tổng median diện tích.
- `q3_merit_summary.csv` — số reach trong cây, tổng chiều dài, median độ dốc, số `slope=0`.
- `q3_osm_by_fclass.csv` — theo `fclass`: số đoạn, có tên / không tên.
- `q3_osm_summary.csv` — tổng số đoạn OSM.
- `README.md`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (DoubleType, LongType, StructField, StructType)  # noqa: E402

from analytics.base import ResultCache, now_vn, run_job, write_csv, write_readme  # noqa: E402
from common.session import read_mongo  # noqa: E402
from features import rules as R  # noqa: E402

JOB = "q3_merit_osm"

MERIT = StructType([
    StructField("COMID", LongType()),
    StructField("NextDownID", LongType()),
    StructField("up1", LongType()),
    StructField("up2", LongType()),
    StructField("up3", LongType()),
    StructField("up4", LongType()),
    StructField("maxup", LongType()),
    StructField("order", LongType()),
    StructField("slope", DoubleType()),
    StructField("uparea", DoubleType()),
    StructField("lengthkm", DoubleType()),
])


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run
    root = getattr(ctx.args, "root_comid", R.ROOT_COMID)

    # ---- MERIT trong cây lưu vực ------------------------------------------
    run.hold()
    rows = [r.asDict() for r in read_mongo(spark, "merit_river_reaches", MERIT, db=ctx.db).collect()]
    reaches = {int(r["COMID"]): r for r in rows if r.get("COMID") is not None}
    tree = R.build_tree(reaches, root)
    print(f"Cây lưu vực {root}: {len(tree)} / {len(reaches)} reach", flush=True)

    run.hold()
    merit = (spark.createDataFrame(
        [(r["COMID"], r.get("order") or 0, r.get("slope") or 0.0, r.get("uparea") or 0.0,
          r.get("lengthkm") or 0.0) for r in reaches.values()],
        "COMID long, order int, slope double, uparea double, lengthkm double")
        .withColumn("in_tree", F.col("COMID").isin(list(tree))))
    n_in = merit.filter(F.col("in_tree")).count()
    n_out = merit.filter(~F.col("in_tree")).count()

    by_order = (merit.filter(F.col("in_tree")).groupBy("order")
                .agg(F.count(F.lit(1)).alias("n_reaches"),
                     F.round(F.sum("lengthkm"), 3).alias("total_length_km"),
                     F.round(F.expr("percentile_approx(lengthkm, 0.5)"), 4).alias("median_length_km"),
                     F.round(F.expr("percentile_approx(slope, 0.5)"), 8).alias("median_slope"),
                     F.round(F.max("uparea"), 3).alias("max_uparea_km2"),
                     F.sum(F.when(F.col("slope") <= 0, 1).otherwise(0)).alias("n_slope_zero"))
                .orderBy("order"))
    rows_order = [[r["order"], r["n_reaches"], r["total_length_km"], r["median_length_km"],
                   r["median_slope"], r["max_uparea_km2"], r["n_slope_zero"]]
                  for r in by_order.collect()]

    summary = merit.filter(F.col("in_tree")).agg(
        F.count(F.lit(1)).alias("n_reaches"),
        F.round(F.sum("lengthkm"), 3).alias("total_length_km"),
        F.round(F.expr("percentile_approx(lengthkm, 0.5)"), 4).alias("median_length_km"),
        F.round(F.expr("percentile_approx(slope, 0.5)"), 8).alias("median_slope"),
        F.sum(F.when(F.col("slope") <= 0, 1).otherwise(0)).alias("n_slope_zero"),
        F.round(F.max("uparea"), 3).alias("max_uparea_km2")).collect()[0]
    rows_summary = [[root, n_in, n_out, summary["n_reaches"], summary["total_length_km"],
                     summary["median_length_km"], summary["median_slope"], summary["n_slope_zero"],
                     summary["max_uparea_km2"]]]

    # ---- OSM waterways -----------------------------------------------------
    run.hold()
    osm = read_mongo(spark, "waterways_segments", None, db=ctx.db,
                     pipeline=[{"$project": {"_id": 0, "fclass": 1, "name": 1}}])
    n_osm = osm.count()
    by_fclass = (osm.groupBy("fclass")
                 .agg(F.count(F.lit(1)).alias("n_segments"),
                      F.sum(F.when(F.col("name").isNotNull() & (F.length("name") > 0), 1)
                            .otherwise(0)).alias("n_named"),
                      F.round(F.avg(F.length(F.coalesce(F.col("name"), F.lit("")))), 3)
                      .alias("mean_name_len"))
                 .orderBy(F.desc("n_segments")))
    rows_fclass = [[r["fclass"], r["n_segments"], r["n_named"], r["mean_name_len"]]
                   for r in by_fclass.collect()]

    findings = [
        f"MERIT: cây lưu vực {root} có {n_in} reach (toàn bộ CSDL có {n_out} reach ngoài cây).",
        f"Trong cây: tổng chiều dài {summary['total_length_km']} km, median chiều dài "
        f"{summary['median_length_km']} km, median độ dốc {summary['median_slope']}, "
        f"{summary['n_slope_zero']} reach có `slope ≤ 0`.",
        f"OSM `waterways_segments`: {n_osm} đoạn.",
    ]
    for r in rows_order:
        findings.append(f"MERIT order {r[0]}: {r[1]} reach, tổng {r[2]} km, median {r[3]} km, "
                        f"median slope {r[4]}, slope=0: {r[6]}.")
    for r in rows_fclass:
        findings.append(f"OSM fclass {r[0]}: {r[1]} đoạn (có tên: {r[2]}).")

    outputs = []
    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        write_csv(ctx.path("q3_merit_by_order.csv"),
                  ["order", "n_reaches", "total_length_km", "median_length_km", "median_slope",
                   "max_uparea_km2", "n_slope_zero"], rows_order)
        write_csv(ctx.path("q3_merit_summary.csv"),
                  ["root_comid", "n_reaches_in_tree", "n_reaches_outside_tree", "n_reaches",
                   "total_length_km", "median_length_km", "median_slope", "n_slope_zero",
                   "max_uparea_km2"], rows_summary)
        write_csv(ctx.path("q3_osm_by_fclass.csv"),
                  ["fclass", "n_segments", "n_named", "mean_name_len"], rows_fclass)
        write_csv(ctx.path("q3_osm_summary.csv"), ["n_segments"], [[n_osm]])
        write_readme(
            ctx.path("README.md"), "Q3 – Thống kê MERIT theo `order` và OSM theo `fclass`",
            "Cấu trúc lưu vực theo MERIT (order, chiều dài, độ dốc, diện tích) và phân bố lớp OSM?",
            {"Nguồn MERIT": "`merit_river_reaches`", "Gốc lưu vực": root,
             "Nguồn OSM": "`waterways_segments`",
             "Số reach trong cây": n_in, "Số đoạn OSM": n_osm},
            ["q3_merit_by_order.csv", "q3_merit_summary.csv", "q3_osm_by_fclass.csv",
             "q3_osm_summary.csv"],
            findings,
            ["Cây lưu vực được dựng bằng đúng quy tắc của G1 (đi ngược `up1..up4` từ gốc).",
             "`waterways_segments` là toàn bộ CSDL OSM của khu vực, KHÔNG lọc theo lưu vực "
             "(chưa có spatial join — xem Q5)."],
            now_vn())
        outputs = ["q3_merit_by_order.csv", "q3_merit_summary.csv", "q3_osm_by_fclass.csv",
                   "q3_osm_summary.csv", "README.md"]

    cache = ResultCache("q3_merit_osm", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    return findings, outputs


if __name__ == "__main__":
    def _extra(ap):
        ap.add_argument("--root-comid", type=int, default=R.ROOT_COMID,
                        help="COMID gốc của lưu vực (mặc định 44045960)")

    sys.exit(run_job(JOB, "Q3", body, extra_args=_extra))
