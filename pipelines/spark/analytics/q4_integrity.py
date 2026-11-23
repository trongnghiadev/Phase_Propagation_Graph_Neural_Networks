"""Q4 – Toàn vẹn dữ liệu: ngày thiếu bản tin, ngày trùng, giá trị có cờ QC, kiểm tra topology.

    spark-submit q4_integrity.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q4]
                                [--root-comid 44045960] [--lock-ttl-s 3600] [--dry-run] [--no-cache]

**Câu hỏi.** Dữ liệu bản tin KTTV có đầy đủ không (ngày thiếu, ngày trùng)? Giá trị triều
đã qua QC ra sao? Đồ thị lưu vực MERIT có giữ được tính toàn vẹn topology không?

**Kiểm tra thực hiện.**
1. **Ngày thiếu bản tin.** Với mỗi trạm, so tập ngày quan sát thực tế với tập ngày lịch
   giữa `min_day` và `max_day` → danh sách ngày thiếu.
2. **Ngày trùng.** Số bản tin có cùng (trạm, ngày quan sát) → đếm số bản trùng.
3. **Giá trị có cờ QC.** Phân bố `qc` / `qc_flag` của `tide_extremes` (kể cả giá trị bị loại).
4. **Topology.** Trên cây lưu vực gốc `--root-comid`:
   - mỗi nút có đúng một `NextDownID` trong cây (trừ cửa sản);
   - không có chu trình hướng xuôi;
   - mọi nút trong cây đều đi được về cửa sản;
   - đối chiếu với `graph_edges` của G1 (số nút/cạnh trong đồ thị đã tỉa).

**Đầu ra** (`results/Q4/`):
- `q4_missing_days.csv` — (trạm, ngày thiếu, số ngày thiếu).
- `q4_duplicate_bulletins.csv` — (trạm, ngày, số bản tin).
- `q4_qc_distribution.csv` — phân bố `qc` × `qc_flag`.
- `q4_topology_checks.csv` — kết quả các kiểm tra topology.
- `README.md`.
"""
from __future__ import annotations

import os
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (DoubleType, LongType, StringType, StructField,  # noqa: E402
                               StructType, TimestampType)

from analytics.base import ResultCache, now_vn, run_job, write_csv, write_readme  # noqa: E402
from common.session import SINGLE_PARTITIONER, read_mongo  # noqa: E402
from features import rules as R  # noqa: E402

JOB = "q4_integrity"

TIDE = StructType([
    StructField("ts", TimestampType()),
    StructField("station", StringType()),
    StructField("kind", StringType()),
    StructField("value", DoubleType()),
    StructField("qc", StringType()),
    StructField("qc_flag", StringType()),
    StructField("source_file", StringType()),
])
TIDE_PIPELINE = [{"$project": {"_id": 0, "ts": 1, "station": "$meta.station", "kind": "$meta.kind",
                                "value": 1, "qc": 1, "qc_flag": 1, "source_file": 1}}]
MERIT = StructType([
    StructField("COMID", LongType()),
    StructField("NextDownID", LongType()),
    StructField("up1", LongType()),
    StructField("up2", LongType()),
    StructField("up3", LongType()),
    StructField("up4", LongType()),
    StructField("maxup", LongType()),
    StructField("order", LongType()),
])


def _daterange(a: str, b: str) -> list[str]:
    """Danh sách ngày `YYYY-MM-DD` từ `a` đến `b` (bao gồm hai đầu)."""
    d0 = date.fromisoformat(a)
    d1 = date.fromisoformat(b)
    out = []
    d = d0
    while d <= d1:
        out.append(d.isoformat())
        d += timedelta(days=1)
    return out


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run
    root = getattr(ctx.args, "root_comid", R.ROOT_COMID)

    # ---- 1/2. Ngày quan sát theo trạm: thiếu & trùng ------------------------
    # `hydromet_bulletins` KHÔNG có trường `station` ở cấp gốc — trạm nằm trong mảng
    # `observed_water_levels[]` (mỗi phần tử có `station`, `high_tides`, `low_tides`).
    # Vì vậy: nổ (explode) mảng này để lấy (trạm, ngày quan sát) của từng bản tin.
    run.hold()
    bulletins = read_mongo(spark, "hydromet_bulletins", None, db=ctx.db,
                           pipeline=[{"$project": {"_id": 0, "source_file": 1, "observation_date": 1,
                                                   "bulletin_type": 1,
                                                   "observed_water_levels": 1}}])
    obs = (bulletins.filter(F.col("observation_date").isNotNull())
           .filter(F.col("observed_water_levels").isNotNull())
           .withColumn("day", F.date_format("observation_date", "yyyy-MM-dd"))
           .withColumn("wl", F.explode(F.col("observed_water_levels")))
           .withColumn("station", F.col("wl.station"))
           .filter(F.col("station").isNotNull())
           .select("station", "day", "source_file")
           .distinct())
    n_obs = obs.count()
    print(f"Bản tin có ngày quan sát: {n_obs}", flush=True)

    run.hold()
    obs_rows = obs.collect()
    days_by_station: dict[str, set[str]] = {}
    cnt_by_station: dict[tuple[str, str], int] = {}
    for r in obs_rows:
        days_by_station.setdefault(r["station"], set()).add(r["day"])
        cnt_by_station[(r["station"], r["day"])] = cnt_by_station.get((r["station"], r["day"]), 0) + 1

    rows_missing = []
    for st in sorted(days_by_station):
        have = days_by_station[st]
        lo, hi = min(have), max(have)
        want = _daterange(lo, hi)
        miss = [d for d in want if d not in have]
        rows_missing.append([st, lo, hi, len(have), len(want), len(miss), ";".join(miss[:30])])

    rows_dup = []
    for (st, d), n in sorted(cnt_by_station.items()):
        if n > 1:
            rows_dup.append([st, d, n])

    # ---- 3. Phân bố QC -----------------------------------------------------
    run.hold()
    tide = read_mongo(spark, "tide_extremes", TIDE, TIDE_PIPELINE, ctx.db,
                      {"partitioner": SINGLE_PARTITIONER})
    n_tide = tide.count()
    qc_dist = (tide.groupBy(F.coalesce(F.col("qc"), F.lit("(null)")).alias("qc"),
                             F.coalesce(F.col("qc_flag"), F.lit("(null)")).alias("qc_flag"))
                .agg(F.count(F.lit(1)).alias("n"))
                .orderBy(F.desc("n")))
    rows_qc = [[r["qc"], r["qc_flag"], r["n"]] for r in qc_dist.collect()]

    # ---- 4. Topology MERIT + đối chiếu `graph_edges` ------------------------
    run.hold()
    merit_rows = [r.asDict() for r in read_mongo(spark, "merit_river_reaches", MERIT, db=ctx.db).collect()]
    reaches = {int(r["COMID"]): r for r in merit_rows if r.get("COMID") is not None}
    tree = R.build_tree(reaches, root)
    topo_errs = R.check_tree(reaches, tree)
    conf = R.confluence_nodes(reaches, tree)
    reach_outlet = [n for n in tree if R.downstream_child(reaches, n, tree) is None]

    n_edges_db = adm.count("graph_edges") if adm.exists("graph_edges") else 0
    edge_nodes = set()
    if n_edges_db:
        for d in adm.aggregate("graph_edges", [{"$project": {"_id": 0, "src": 1, "dst": 1}}]):
            for k in ("src", "dst"):
                v = d.get(k)
                if isinstance(v, int):
                    edge_nodes.add(v)

    checks = [
        ["cây lưu vực không có chu trình hướng xuôi và mỗi nút ≤ 1 hạ nguồn",
         "đạt" if not topo_errs else "; ".join(topo_errs[:3]), len(tree), "reach"],
        ["cửa sản duy nhất (nút không có hạ nguồn trong cây)",
         "đạt" if reach_outlet == [root] else f"không đúng: {reach_outlet}", 1, "nút"],
        ["mọi nút trong cây đi được về cửa sản theo NextDownID",
         "đạt" if len(reach_outlet) == 1 else "không đạt", len(tree), "reach"],
        ["số điểm hội tụ (nhiều reach đổ về cùng một hạ nguồn)",
         "đặc trưng hợp lệ của lưu vực thật (không phải lỗi)", len(conf), "nút"],
        ["`graph_edges` đã được G1 nạp", "đạt" if n_edges_db else "CHƯA có", n_edges_db, "cạnh"],
        ["số nút có trong `graph_edges` (đã tỉa)",
         "đã tỉa theo `uparea` ≥ ngưỡng", len(edge_nodes), "nút"],
    ]
    rows_checks = checks

    findings = [
        f"Bản tin có ngày quan sát: {n_obs} bản tin; "
        f"{sum(r[5] for r in rows_missing)} ngày thiếu trên tổng "
        f"{sum(r[4] for r in rows_missing)} ngày lịch trong khoảng quan sát.",
        f"Ngày trùng (nhiều bản tin cùng trạm + ngày): {len(rows_dup)} cặp.",
        f"`tide_extremes`: {n_tide} phần tử; "
        f"{sum(r[2] for r in rows_qc if r[0] == 'ok' and r[1] == '(null)')} đạt QC ok "
        f"(không cờ), "
        f"{sum(r[2] for r in rows_qc if r[0] == 'ok' and r[1] != '(null)')} đạt QC ok "
        f"nhưng có cờ.",
        f"Topology cây {root}: {len(tree)} reach, {len(conf)} điểm hội tụ, "
        f"{len(topo_errs)} lỗi; `graph_edges` có {n_edges_db} cạnh / {len(edge_nodes)} nút.",
    ]
    for r in rows_missing:
        if r[5]:
            findings.append(f"Trạm {r[0]}: thiếu {r[5]} ngày trong {r[1]}→{r[2]} "
                            f"({r[5]}/{r[4]} ngày).")
    for r in rows_qc[:8]:
        findings.append(f"QC `{r[0]}` / `{r[1]}`: {r[2]} phần tử.")

    outputs = []
    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        write_csv(ctx.path("q4_missing_days.csv"),
                  ["station", "min_day", "max_day", "n_days_present", "n_days_expected",
                   "n_days_missing", "missing_days_sample"], rows_missing)
        write_csv(ctx.path("q4_duplicate_bulletins.csv"),
                  ["station", "day", "n_bulletins"], rows_dup)
        write_csv(ctx.path("q4_qc_distribution.csv"), ["qc", "qc_flag", "n"], rows_qc)
        write_csv(ctx.path("q4_topology_checks.csv"),
                  ["check", "result", "value", "unit"], rows_checks)
        write_readme(
            ctx.path("README.md"), "Q4 – Toàn vẹn dữ liệu và topology",
            "Dữ liệu bản tin đầy đủ không (thiếu/trùng ngày)? QC ra sao? Đồ thị MERIT có "
            "giữ được toàn vẹn topology không?",
            {"Bản tin": "`hydromet_bulletins`", "Triều": "`tide_extremes`",
             "MERIT": "`merit_river_reaches`", "Gốc lưu vực": root,
             "Đồ thị đã dựng": "`graph_edges` (G1)"},
            ["q4_missing_days.csv", "q4_duplicate_bulletins.csv", "q4_qc_distribution.csv",
             "q4_topology_checks.csv"],
            findings,
            ["Ngày thiếu được tính trong khoảng [min_day, max_day] của từng trạm — trạm mới "
             "tham gia giữa chừng sẽ có 'ngày thiếu' trước thời điểm bắt đầu.",
             "Ngày trùng là do nhiều bản tin phát cho cùng một trạm trong một ngày (bản tin 05h và "
             "bản tin 17h), KHÔNG phải lỗi dữ liệu.",
             "Điểm hội tụ (nhiều reach đổ về cùng một hạ nguồn) là đặc tính hợp lệ của lưu vực "
             "thật, không phải lỗi topology."],
            now_vn())
        outputs = ["q4_missing_days.csv", "q4_duplicate_bulletins.csv", "q4_qc_distribution.csv",
                   "q4_topology_checks.csv", "README.md"]

    cache = ResultCache("q4_integrity", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    return findings, outputs


if __name__ == "__main__":
    def _extra(ap):
        ap.add_argument("--root-comid", type=int, default=R.ROOT_COMID,
                        help="COMID gốc của lưu vực (mặc định 44045960)")

    sys.exit(run_job(JOB, "Q4", body, extra_args=_extra))
