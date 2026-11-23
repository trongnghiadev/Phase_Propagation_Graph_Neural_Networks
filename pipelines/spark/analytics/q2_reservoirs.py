"""Q2 – Hồ chứa: lưu lượng xả theo mùa và số ngày xả tràn.

    spark-submit q2_reservoirs.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q2]
                                  [--lock-ttl-s 3600] [--dry-run] [--no-cache]

**Câu hỏi.** Các hồ chứa trong lưu vực xả nước (qua máy và qua đập tràn) như thế nào theo
mùa? Bao nhiêu ngày xả tràn? Có hồ nào xả tràn thường xuyên (rủi ro downstream) không?

**Nguồn.** `reservoir_daily` (15.313 bản ghi): `reservoir`, `day`, `inflow_m3s`,
`turbine_discharge_m3s`, `spillway_discharge_m3s`, `upstream_water_level_m`.

**Định nghĩa.**
- `total_discharge_m3s = turbine_discharge_m3s + spillway_discharge_m3s`.
- **Ngày xả tràn**: `spillway_discharge_m3s > 0` (đúng định nghĩa trong đề bài: "turbine+spillway > 0"
  được hiểu là có xả qua đập tràn; ở đây báo cáo CẢ HAI: `n_days_turbine > 0` và
  `n_days_spillway > 0`, cùng `n_days_any_discharge`).
- **Mùa**: theo tháng Việt Nam — `mua_khiet` = tháng 1–4 và 11–12; `mua_gio` = tháng 5–10.

**Đầu ra** (`results/Q2/`):
- `q2_reservoir_by_season.csv` — theo (hồ, mùa).
- `q2_reservoir_by_month.csv` — theo (hồ, tháng).
- `q2_reservoir_yearly.csv` — theo (hồ, năm).
- `README.md`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402

from analytics.base import ResultCache, now_vn, run_job, write_csv, write_readme  # noqa: E402
from common.session import read_mongo  # noqa: E402

JOB = "q2_reservoirs"

# Mùa Việt Nam: mùa khô (mùa mưa) và mùa mưa (mùa giữa năm).
WET_MONTHS = (5, 6, 7, 8, 9, 10)      # tháng 5–10: mùa mưa
DRY_MONTHS = (11, 12, 1, 2, 3, 4)     # tháng 11–4: mùa khô


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run

    run.hold()
    res = read_mongo(spark, "reservoir_daily", None, db=ctx.db,
                     pipeline=[{"$project": {"_id": 0, "day": 1, "reservoir": 1, "river": 1,
                                             "inflow_m3s": 1, "spillway_discharge_m3s": 1,
                                             "turbine_discharge_m3s": 1,
                                             "upstream_water_level_m": 1}}])
    n_raw = res.count()
    print(f"reservoir_daily: {n_raw} bản ghi", flush=True)
    if n_raw == 0:
        raise SystemExit("Dừng: `reservoir_daily` rỗng.")

    df = (res.withColumn("year", F.substring("day", 1, 4).cast("int"))
          .withColumn("month", F.substring("day", 6, 2).cast("int"))
          .withColumn("total_discharge_m3s",
                      F.coalesce(F.col("turbine_discharge_m3s"), F.lit(0.0))
                      + F.coalesce(F.col("spillway_discharge_m3s"), F.lit(0.0)))
          .withColumn("season", F.when(F.col("month").isin(list(WET_MONTHS)), F.lit("mua_mua"))
                      .otherwise(F.lit("mua_kho")))
          .cache())

    n_res = df.select("reservoir").distinct().count()
    print(f"Số hồ: {n_res}", flush=True)

    # ---- Theo (hồ, mùa) ---------------------------------------------------
    run.hold()
    agg_exprs = [
        F.count(F.lit(1)).alias("n_days"),
        F.round(F.avg("inflow_m3s"), 3).alias("mean_inflow_m3s"),
        F.round(F.avg("total_discharge_m3s"), 3).alias("mean_discharge_m3s"),
        F.round(F.max("total_discharge_m3s"), 3).alias("max_discharge_m3s"),
        F.round(F.sum("total_discharge_m3s") * 86400.0 / 1e9, 4).alias("volume_billion_m3"),
        F.sum(F.when(F.col("spillway_discharge_m3s") > 0, 1).otherwise(0)).alias("n_days_spillway"),
        F.sum(F.when(F.col("turbine_discharge_m3s") > 0, 1).otherwise(0)).alias("n_days_turbine"),
        F.sum(F.when(F.col("total_discharge_m3s") > 0, 1).otherwise(0)).alias("n_days_any_discharge"),
        F.round(F.expr("percentile_approx(upstream_water_level_m, 0.5)"), 3).alias("median_upstream_wl_m"),
    ]
    by_season = (df.groupBy("reservoir", "season").agg(*agg_exprs)
                 .orderBy("reservoir", "season"))
    rows_season = [[r["reservoir"], r["season"], r["n_days"], r["mean_inflow_m3s"],
                    r["mean_discharge_m3s"], r["max_discharge_m3s"], r["volume_billion_m3"],
                    r["n_days_spillway"], r["n_days_turbine"], r["n_days_any_discharge"],
                    r["median_upstream_wl_m"]] for r in by_season.collect()]

    # ---- Theo (hồ, tháng) --------------------------------------------------
    run.hold()
    by_month = (df.groupBy("reservoir", "year", "month")
                .agg(F.count(F.lit(1)).alias("n_days"),
                     F.round(F.avg("inflow_m3s"), 3).alias("mean_inflow_m3s"),
                     F.round(F.avg("total_discharge_m3s"), 3).alias("mean_discharge_m3s"),
                     F.sum(F.when(F.col("spillway_discharge_m3s") > 0, 1).otherwise(0)).alias("n_days_spillway"),
                     F.sum(F.when(F.col("turbine_discharge_m3s") > 0, 1).otherwise(0)).alias("n_days_turbine"))
                .orderBy("reservoir", "year", "month"))
    rows_month = [[r["reservoir"], r["year"], r["month"], r["n_days"], r["mean_inflow_m3s"],
                   r["mean_discharge_m3s"], r["n_days_spillway"], r["n_days_turbine"]]
                  for r in by_month.collect()]

    # ---- Theo (hồ, năm) ----------------------------------------------------
    run.hold()
    by_year = (df.groupBy("reservoir", "year").agg(*agg_exprs).orderBy("reservoir", "year"))
    rows_year = [[r["reservoir"], r["year"], r["n_days"], r["mean_inflow_m3s"],
                  r["mean_discharge_m3s"], r["max_discharge_m3s"], r["volume_billion_m3"],
                  r["n_days_spillway"], r["n_days_turbine"], r["n_days_any_discharge"],
                  r["median_upstream_wl_m"]] for r in by_year.collect()]

    findings = [f"`reservoir_daily`: {n_raw} bản ghi, {n_res} hồ."]
    tot_spill = sum(r[7] for r in rows_year)
    tot_days = sum(r[2] for r in rows_year)
    findings.append(f"Tổng số ngày xả tràn (mọi hồ, mọi năm): {tot_spill} / {tot_days} ngày-dữ liệu.")
    for r in sorted(rows_year, key=lambda x: -x[7])[:5]:
        if r[7]:
            findings.append(
                f"Hồ {r[0]} năm {r[1]}: {r[7]} ngày xả tràn, {r[8]} ngày qua máy; "
                f"TB xả {r[4]} m³/s, max {r[5]} m³/s, thể tích {r[6]} tỷ m³.")

    outputs = []
    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        hdr_season = ["reservoir", "season", "n_days", "mean_inflow_m3s", "mean_discharge_m3s",
                      "max_discharge_m3s", "volume_billion_m3", "n_days_spillway", "n_days_turbine",
                      "n_days_any_discharge", "median_upstream_wl_m"]
        hdr_month = ["reservoir", "year", "month", "n_days", "mean_inflow_m3s", "mean_discharge_m3s",
                     "n_days_spillway", "n_days_turbine"]
        hdr_year = hdr_season[:2] + hdr_season[2:]
        write_csv(ctx.path("q2_reservoir_by_season.csv"), hdr_season, rows_season)
        write_csv(ctx.path("q2_reservoir_by_month.csv"), hdr_month, rows_month)
        write_csv(ctx.path("q2_reservoir_yearly.csv"), hdr_year, rows_year)
        write_readme(
            ctx.path("README.md"), "Q2 – Lưu lượng xả hồ chứa theo mùa và số ngày xả tràn",
            "Các hồ xả nước (qua máy, qua đập tràn) ra sao theo mùa? Bao nhiêu ngày xả tràn?",
            {"Nguồn": "`reservoir_daily`",
             "Mùa mưa": "tháng 5–10", "Mùa khô": "tháng 11–4",
             "Định nghĩa xả tràn": "`spillway_discharge_m3s > 0`",
             "Số bản ghi": n_raw, "Số hồ": n_res},
            ["q2_reservoir_by_season.csv", "q2_reservoir_by_month.csv", "q2_reservoir_yearly.csv"],
            findings,
            ["`reservoir_daily` chỉ ghi nhận các hồ có trong bản tin KTTV của TP.HCM "
             "(Trị An, Thác Mơ, Dầu Tiếng, Sr. P. Miêng…) — không phải toàn bộ hồ lớn của lưu vực.",
             "`n_days_turbine` và `n_days_spillway` có thể cùng = 1 (xả cả hai kênh trong ngày)."],
            now_vn())
        outputs = ["q2_reservoir_by_season.csv", "q2_reservoir_by_month.csv",
                   "q2_reservoir_yearly.csv", "README.md"]

    cache = ResultCache("q2_reservoirs", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    if cache.enabled and not ctx.args.dry_run:
        import json  # noqa: PLC0415
        cache.set("by_season", json.dumps(rows_season, ensure_ascii=False))

    return findings, outputs


if __name__ == "__main__":
    sys.exit(run_job(JOB, "Q2", body))
