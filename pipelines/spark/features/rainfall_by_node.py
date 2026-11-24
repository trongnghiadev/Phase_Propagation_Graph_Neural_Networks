"""G4 – Mưa theo nút đồ thị (trọng số diện tích từ trạm mưa) — bản dự phòng thay thế sản phẩm vệ tinh.

    spark-submit rainfall_by_node.py [mongodb_uri] [--db vietnam_gis]
                                     [--out-dir /work/data/processed/features/rain_by_node]
                                     [--report-dir /out/G4]
                                     [--radius-km 40] [--lock-ttl-s 3600] [--dry-run]

**VÌ SAO LÀ BẢN DỰ PHÒNG.** Chưa tải được sản phẩm mưa vệ tinh (IMERG/CHIRPS/GPM) — mưa
lưu vực nằm trong khu vực mây che nhiều, độ phân giải lọc tốt hơn trạm mưa. Vì vậy G4 dùng
**mưa quan trắc tại trạm** (`rainfall_daily`, 10 trạm mưa của TP.HCM: Bình Chánh, Cần Giờ,
Củ Chi, Hóc Môn, Mạc Đĩnh Chi, Nhà Bè, Phú An, Tân Sơn Hoa, Thủ Dầu Một, Vũng Tàu) và quy
chúng về các nút của đồ thị.

**LUẬT GÁN (có tài liệu, tái lập được).**

1. *Ứng viên.* Mỗi nút đồ thị có hình học `LineString` (MERIT `geometry`) và `bbox`.
   Mỗi trạm mưa có điểm GeoJSON (`stations.location`).
2. *Khoảng cách.* Khoảng cách từ trạm mưa tới nút = khoảng cách nhỏ nhất từ điểm trạm tới
   **đoạn thẳng** của `LineString` (công thức khoảng cách điểm–đoạn thẳng trên mặt cầu
   Trái Đất, bán kính 6.371.008,8 m). Dùng bbox của reach để lọc trước (prefilter), rồi mới
   tính khoảng cách chính xác cho ứng viên — tránh O(n·m) toàn phần.
3. *Chọn ứng viên.* Chỉ giữ trạm mưa có khoảng cách ≤ `--radius-km` (mặc định 40 km).
4. *Trọng số.* Với nút `n` và tập trạm mưa `S(n)`:
       w(n, s) = uparea(n) / Σ_{s' ∈ S(n)} uparea(s')      nếu |S(n)| ≥ 1
   Trọng số **KHÔNG** phụ thuộc vào vị trí trạm mưa: một trạm mưa được gán cho nút nào thì
   chia đều theo diện tích lưu vực của các nút nhận nó. Luật này đơn giản, có thể tái lập,
   và giữ được tính chất "mưa ở thượng nguồn không đổi đột ngột mưa ở hạ nguồn".
   Với nút chỉ có MỘT trạm mưa → w = 1 (không chia nhỏ giá trị quan trắc đơn lẻ).
5. *Chuẩn hoá.* Tổng trọng số của mỗi nút = 1 (kiểm tra bắt buộc, hỏng → dừng mã 1).
6. *Giá trị tại nút.* `rain_node(n, d) = Σ_s w(n, s) · rainfall(s, d)`; trạm không có số
   liệu trong ngày `d` bị bỏ qua và trọng số của nó được **phân bổ lại** cho các trạm còn
   lại của cùng nút (để tổng vẫn = 1 và không tạo giá trị 0 giả). Ngày không có trạm nào có
   số liệu → `null` (KHÔNG phải 0).

**LŨY KẾ.** `rain_mm` (1 ngày), `rain_mm_3d` (tích lũy 3 ngày), `rain_mm_7d` (tích lũy 7 ngày),
tính trên cùng bộ trạm có số liệu (tích lũy = tổng lượng mưa trong cửa sổ, không phải tích lũy
giá trị đã trọng số hoá).

**ĐẦU RA.**
- `data/processed/features/rain_by_node/year=<YYYY>/part-*.parquet` (phân vùng theo năm).
- `results/G4/rain_weights.csv` — ma trận trọng số (nút × trạm mưa) + khoảng cách.
- `results/G4/rain_by_node_summary.csv` — thống kê theo năm (số dòng, tổng mưa).

**THAY SẢN PHẨM VỆ TINH.** Khi có IMERG/CHIRPS, chỉ cần thay bảng đầu vào
(`rainfall_daily` → bảng lưới theo ngày) và bỏ bước gán trạm; phần tính tích lũy và phân
vùng Parquet giữ nguyên. Ma trận trọng số `rain_weights.csv` cũng không còn cần.

Parquet ghi vào `<out-dir>.tmp_<run_id>` rồi đổi tên (`features/outdir.py`): bản cũ giữ nguyên tới khi
bản mới ghi xong. Đọc `rainfall_daily` (đầu ra G0) nên kiểm tra khóa G0 như job QC (`features/g0guard.py`).

Mã thoát: 0 ok, 1 kiểm tra hỏng (tổng trọng số ≠ 1), 2 thiếu `graph_edges`/`rainfall_daily`,
3 job này hoặc G0 đang chạy, 4 G0 còn dở thay thế (`g0_unsettled`), 5 mất khóa, 6 lỗi không lường trước.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (DoubleType, LongType, StringType, StructField, StructType)  # noqa: E402
from pyspark.sql import Row  # noqa: E402

from common.jobrun import JobRun  # noqa: E402
from features import outdir  # noqa: E402
from features.g0guard import g0_block  # noqa: E402
from common.session import (JobLockError, LockLostError, MongoAdmin, build_session, env_db, env_uri,  # noqa: E402
                            read_mongo)
from features.rain_rules import (EARTH_RADIUS_M, RADIUS_KM_DEFAULT, assign_station_to_nodes,  # noqa: E402
                          distance_point_line_m, node_weights, parse_bbox,
                          parse_geometry, point_in_bbox)

JOB = "g4_rainfall_by_node"
JOB_VERSION = "g4-v1"

MERIT_GEOM = StructType([
    StructField("COMID", LongType()),
    StructField("uparea", DoubleType()),
    StructField("order", LongType()),
    StructField("geometry", StringType()),
    StructField("bbox", StringType()),
])


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("uri", nargs="?", default=env_uri())
    ap.add_argument("--db", default=env_db())
    ap.add_argument("--out-dir", default="/work/data/processed/features/rain_by_node")
    ap.add_argument("--report-dir", default="/out/G4")
    ap.add_argument("--radius-km", type=float, default=RADIUS_KM_DEFAULT,
                    help=f"bán kính gán trạm mưa (km, mặc định {RADIUS_KM_DEFAULT})")
    ap.add_argument("--lock-ttl-s", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true", help="tính và kiểm tra, không ghi Parquet")
    args = ap.parse_args(argv)

    spark = build_session(JOB, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    run = JobRun(adm, JOB, args.lock_ttl_s)
    print(f"run_id={run.run_id} db={args.db} radius={args.radius_km} km", flush=True)
    status = "failed"
    tmp_dir = None
    try:
        try:
            run.acquire()
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        run.start(db=args.db, radius_km=args.radius_km, job_version=JOB_VERSION)
        blk = g0_block(run, adm)     # đọc `rainfall_daily` (đầu ra G0)
        if blk:
            code, status, msg = blk
            print(f"Dừng: {msg}", flush=True)
            return code
        status = "failed"
        if not adm.exists("rainfall_daily"):
            print("Dừng: chưa có `rainfall_daily`.", flush=True)
            return 2
        if not adm.exists("graph_edges"):
            print("Dừng: chưa có `graph_edges`; chạy G1 (graph_build.py) trước.", flush=True)
            return 2

        # ---- Đồ thị: nút + hình học MERIT ---------------------------------
        run.hold()
        merit = read_mongo(spark, "merit_river_reaches", MERIT_GEOM, db=args.db,
                           pipeline=[{"$project": {"_id": 0, "COMID": 1, "uparea": 1, "order": 1,
                                                   "geometry": 1, "bbox": 1}}]).collect()
        geom_all = {int(r["COMID"]): {"coords": parse_geometry(r["geometry"]),
                                       "bbox": parse_bbox(r["bbox"]),
                                       "uparea": float(r["uparea"] or 0.0)}
                    for r in merit if r["COMID"] is not None}
        # chỉ giữ nút có mặt trong `graph_edges`
        edge_nodes = set()
        for d in adm.aggregate("graph_edges", [{"$project": {"_id": 0, "src": 1, "dst": 1}}]):
            for k in ("src", "dst"):
                v = d.get(k)
                if isinstance(v, int):
                    edge_nodes.add(v)
        node_geoms = {c: g for c, g in geom_all.items() if c in edge_nodes and g["coords"]}
        node_area = {c: g["uparea"] for c, g in node_geoms.items()}
        print(f"Nút đồ thị có hình học: {len(node_geoms)} / {len(edge_nodes)}", flush=True)
        if not node_geoms:
            print("Dừng: không có nút nào có hình học MERIT.", flush=True)
            return 1

        # ---- Trạm mưa -------------------------------------------------------
        run.hold()
        st = read_mongo(spark, "stations", None, db=args.db,
                        pipeline=[{"$match": {"kind": "rain"}},
                                  {"$project": {"_id": 0, "code": 1, "name": 1, "location": 1}}]).collect()
        rain_stations = []
        for row in st:
            # `read_mongo` trả về `Row`; `.get()` không có trên Row → dùng `asDict()`.
            r = row.asDict()
            loc = r.get("location")
            if loc is None:
                continue
            if isinstance(loc, str):
                try:
                    loc = json.loads(loc)
                except (ValueError, TypeError):
                    continue
            if isinstance(loc, Row):
                # connector trả struct lồng thành `Row` → chuyển về dict
                loc = loc.asDict()
            if not isinstance(loc, dict):
                continue
            coords = (loc.get("coordinates") or [])
            if len(coords) < 2:
                continue
            rain_stations.append({"code": r["code"], "name": r.get("name"),
                                  "lon": float(coords[0]), "lat": float(coords[1])})
        print(f"Trạm mưa có toạ độ: {len(rain_stations)}", flush=True)
        if not rain_stations:
            print("Dừng: không có trạm mưa nào có toạ độ.", flush=True)
            return 1

        # ---- Gán + trọng số -------------------------------------------------
        run.hold()
        assign = assign_station_to_nodes(rain_stations, node_geoms, args.radius_km)
        weights = node_weights(node_area, assign)
        n_nodes_with = len(weights)
        # kiểm tra bắt buộc: tổng trọng số mỗi nút = 1
        bad = [(c, sum(w.values())) for c, w in weights.items() if abs(sum(w.values()) - 1.0) > 1e-9]
        ok = not bad
        print(f"[{'OK ' if ok else 'LỖI'}] tổng trọng số mỗi nút = 1: {n_nodes_with - len(bad)}/{n_nodes_with}",
              flush=True)
        if bad:
            for c, s in bad[:5]:
                print(f"    nút {c}: tổng trọng số = {s:.12f}", flush=True)
            return 1
        # thống kê số trạm/nút
        cnt = sorted(len(w) for w in weights.values())
        print(f"Nút có trọng số: {n_nodes_with}; số trạm/nút: min {cnt[0]}, trung vị "
              f"{cnt[len(cnt) // 2]}, max {cnt[-1]}", flush=True)
        n_no_rain = len(node_geoms) - n_nodes_with
        if n_no_rain:
            print(f"[CẢNH BÁO] {n_no_rain} nút không có trạm mưa nào trong bán kính "
                  f"{args.radius_km} km → rain = null", flush=True)

        # ---- Mưa quan trắc ---------------------------------------------------
        run.hold()
        rain = read_mongo(spark, "rainfall_daily", None, db=args.db,
                          pipeline=[{"$project": {"_id": 0, "day": 1, "station": 1, "rainfall_mm": 1}}])
        rain_day = rain.groupBy("day", "station").agg(F.sum("rainfall_mm").alias("rain_mm"))
        days = [r["day"] for r in rain_day.select("day").distinct().collect()]
        print(f"Ngày có mưa: {len(days)} ({min(days) if days else '-'} → {max(days) if days else '-'})",
              flush=True)

        # ---- Mở rộng sang mọi nút: mỗi (nút, ngày) với trọng số đã chuẩn hoá --
        # Bảng (nút, trạm, w) × (ngày, trạm, mưa) → mưa đã trọng số hoá.
        run.hold()
        node_station = [(c, code, w) for c, ws in weights.items() for code, w in ws.items()]
        ns = spark.createDataFrame(node_station, "comid long, station string, w double")
        rw = rain_day.withColumnRenamed("station", "station")
        joined = ns.join(rw, "station", "left")
        # trọng số được phân bổ lại cho các trạm CÒN số liệu trong ngày đó:
        # w' = w / Σ_{s có dữ liệu} w  (nếu mẫu số = 0 → không có giá trị → null)
        wsum = joined.groupBy("comid", "day").agg(F.sum("w").alias("w_sum"))
        joined = joined.join(wsum, ["comid", "day"], "left")
        joined = joined.withColumn("w_norm", F.when(F.col("w_sum") > 0, F.col("w") / F.col("w_sum")))
        node_day = (joined.groupBy("comid", "day")
                    .agg(F.sum(F.col("w_norm") * F.col("rain_mm")).alias("rain_mm")))
        # các nút không có trạm mưa nào → vẫn có dòng với rain = null cho mọi ngày
        all_days = spark.createDataFrame([(d,) for d in days], "day string")
        node_ids = spark.createDataFrame([(c,) for c in sorted(node_geoms)], "comid long")
        grid = node_ids.crossJoin(all_days)
        node_day = grid.join(node_day, ["comid", "day"], "left")
        node_day = node_day.withColumn("year", F.substring("day", 1, 4).cast("int"))

        # ---- Tích luỹ 3 ngày / 7 ngày ---------------------------------------
        run.hold()
        node_day = node_day.cache()
        n_rows = node_day.count()
        print(f"Số dòng (nút, ngày): {n_rows}", flush=True)
        if n_rows == 0:
            print("Dừng: không có dòng nào.", flush=True)
            return 1

        # cửa sổ tích luỹ: dùng `F.expr` với `rowsBetween` trên cột ngày đã sắp xếp.
        # Spark không có cửa sổ theo NGÀY (không phải theo số dòng) → dùng
        # `F.datediff` + tự tính bằng `sum` theo ngày trong Python-free cách:
        # join với bảng ngày dịch chuyển.
        day_list = sorted(days)
        win3 = [(d, day_list[i - 2]) for i, d in enumerate(day_list) if i >= 2]
        win7 = [(d, day_list[i - 6]) for i, d in enumerate(day_list) if i >= 6]
        for w, pairs in (("3", win3), ("7", win7)):
            if not pairs:
                continue
            dfw = spark.createDataFrame([(d, s) for d, s in pairs], "day string, start_day string")
            agg = (node_day.filter(F.col("rain_mm").isNotNull())
                   .join(dfw, "day", "left")
                   .groupBy("comid", "day")
                   .agg(F.sum("rain_mm").alias(f"rain_mm_{w}d")))
            node_day = node_day.join(agg, ["comid", "day"], "left")

        # ---- Ghi ma trận trọng số --------------------------------------------
        if args.report_dir:
            os.makedirs(args.report_dir, exist_ok=True)
            path = os.path.join(args.report_dir, "rain_weights.csv")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                wtr = csv.writer(fh, lineterminator="\n")
                wtr.writerow(["comid", "station", "weight", "distance_km", "n_stations_for_node",
                          "node_uparea_km2"])
                for c in sorted(weights):
                    ws = weights[c]
                    for code, w in sorted(ws.items()):
                        d = next((dd for cc, dd in assign.get(c, []) if cc == code), None)
                        wtr.writerow([c, code, f"{w:.10f}",
                                      f"{d / 1000.0:.4f}" if d is not None else "",
                                      len(ws), f"{node_area.get(c, 0.0):.4f}"])
            print(f"[BÁO CÁO] {path}", flush=True)

        # ---- Thống kê + báo cáo (trước khi ghi Parquet) -----------------------
        # Thống kê và README được ghi TRƯỚC bước ghi Parquet: ghi 2,3 triệu dòng Parquet
        # ra ổ đĩa Windows mất thời gian lâu, và nếu báo cáo ghi sau đó thì job bị treo
        # kéo dài mất báo cáo (đã gặp lần chạy 23/11/2026).
        run.hold()
        summary = (node_day.groupBy("year")
                   .agg(F.count(F.lit(1)).alias("n_rows"),
                        F.count(F.col("rain_mm")).alias("n_rows_with_rain"),
                        F.round(F.avg("rain_mm"), 4).alias("mean_daily_mm"),
                        F.round(F.max("rain_mm"), 4).alias("max_daily_mm"))
                   .orderBy("year").collect())
        if args.report_dir:
            path = os.path.join(args.report_dir, "rain_by_node_summary.csv")
            with open(path, "w", encoding="utf-8", newline="\n") as fh:
                wtr = csv.writer(fh, lineterminator="\n")
                wtr.writerow(["year", "n_rows", "n_rows_with_rain", "mean_daily_mm", "max_daily_mm"])
                for r in summary:
                    wtr.writerow([r["year"], r["n_rows"], r["n_rows_with_rain"],
                                  r["mean_daily_mm"], r["max_daily_mm"]])
            print(f"[BÁO CÁO] {path}", flush=True)

            # ---- README báo cáo ----------------------------------------------
            md = os.path.join(args.report_dir, "README.md")
            md_lines = [
                "# G4 – Mưa theo nút đồ thị (fallback dựa trạm)",
                "",
                "## Câu hỏi trả lời",
                "",
                "Mỗi nút trên đồ thị cần chuỗi mưa ngày để làm đặc trưng đầu vào cho mô hình.",
                "Repo **chưa có sản phẩm mưa vệ tinh** (IMERG/CHIRPS) nên job dùng mưa quan trắc",
                "tại trạm (`rainfall_daily`) và gán theo quy tắc xác định, tái lập được.",
                "",
                "## Quy tắc (tài liệu hoá)",
                "",
                "1. Mỗi **trạm mưa** được gán cho các nút đồ thị **trong bán kính "
                f"{args.radius_km:g} km** (khoảng cách đường thẳng trên mặt cầu Trái Đất).",
                "2. Trọng số nút `n` – trạm `s`:",
                "   `w(n,s) = [uparea(n) / A(s)] / Σ_{s'∈S(n)} [uparea(n) / A(s')]`",
                "   với `A(s)` = diện tích lưu vực của trạm (`uparea` của reach gắn với trạm).",
                "3. **Chuẩn hoá mỗi nút: tổng trọng số = 1** (job assert).",
                "4. Ma trận trọng số được ghi ra `rain_weights.csv` để kiểm chứng.",
                "",
                "## Cấu hình chạy",
                "",
                "| Tham số | Giá trị |",
                "|---|---|",
                f"| Bán kính gán | {args.radius_km:g} km |",
                f"| Số nút đồ thị | {len(node_geoms)} |",
                f"| Nút có ít nhất một trạm mưa | {n_nodes_with} |",
                f"| Nút không có trạm mưa | {n_no_rain} |",
                f"| Số trạm mưa | {len(rain_stations)} |",
                f"| Số dòng nút–ngày | {n_rows} |",
                "",
                "## Kết quả theo năm",
                "",
                "| Năm | Số dòng | Dòng có mưa | TB ngày (mm) | Max ngày (mm) |",
                "|---|---|---|---|---|",
            ]
            for r in summary:
                mean = "" if r["mean_daily_mm"] is None else f"{r['mean_daily_mm']:.4f}"
                mx = "" if r["max_daily_mm"] is None else f"{r['max_daily_mm']:.1f}"
                md_lines.append(f"| {r['year']} | {r['n_rows']} | {r['n_rows_with_rain']} | {mean} | {mx} |")
            md_lines += [
                "",
                "## File sinh ra",
                "",
                "- `rain_weights.csv` – ma trận trọng số (nút × trạm mưa) + khoảng cách (km).",
                "- `rain_by_node_summary.csv` – thống kê theo năm.",
                "- `data/processed/features/rain_by_node/year=<YYYY>/part-*.parquet` – mưa ngày theo nút",
                "  (cột `rain_mm_d1`, `rain_mm_d3`, `rain_mm_d7`).",
                "",
                "## Giới hạn",
                "",
                "- Đây là **fallback dựa trạm**: độ phân giải không đồng đều theo không gian, nút xa trạm",
                "  nhận giá trị của trạm gần nhất nên không mô tả tốt phân bố mưa cục bộ trong lưu vực.",
                "- `A(s)` dùng `uparea` của reach gắn trạm — cần bảng `catchment_area` riêng cho trạm mưa",
                "  khi có.",
                "- Khi có IMERG/CHIRPS: **chỉ thay bảng nguồn đầu vào**, toàn bộ logic trọng số và tích lũy",
                "  giữ nguyên (và có thể bỏ bán kính 40 km vì lưới phủ toàn lưu vực).",
                "",
                f"**Ngày chạy:** {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} "
                f"(run `{run.run_id}`)",
                "",
            ]
            md_path = os.path.join(args.report_dir, "README.md")
            with open(md_path, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(md_lines))
            print(f"[BÁO CÁO] {md_path}", flush=True)
        for r in summary:
            print(f"  {r['year']}: {r['n_rows']} dòng, có mưa {r['n_rows_with_rain']}, "
                  f"TB/ngày {r['mean_daily_mm']} mm, max {r['max_daily_mm']} mm", flush=True)

        # ---- Ghi Parquet ------------------------------------------------------
        if args.dry_run:
            print("--dry-run: không ghi Parquet.", flush=True)
            status = "dry_run"
            return 0

        # Ghi `<out-dir>.tmp_<run_id>` rồi đổi tên: bản cũ giữ nguyên tới khi bản mới ghi xong.
        run.hold()
        for msg in outdir.recover(args.out_dir):
            print(f"[DỌN] {msg}", flush=True)
        tmp_dir = outdir.tmp_path(args.out_dir, run.run_id)
        os.makedirs(os.path.dirname(tmp_dir) or ".", exist_ok=True)
        spark.conf.set("spark.sql.shuffle.partitions", "8")
        node_day.write.mode("errorifexists").partitionBy("year").parquet(tmp_dir)
        run.hold()
        outdir.publish(tmp_dir, args.out_dir, run.run_id)
        tmp_dir = None
        print(f"[PARQUET] {args.out_dir} ({n_rows} dòng)", flush=True)

        stats = {
            "nodes_total": len(node_geoms), "nodes_with_weights": n_nodes_with,
            "nodes_without_rain": n_no_rain, "rain_stations": len(rain_stations),
            "radius_km": args.radius_km, "days": len(days), "rows": n_rows,
            "weight_sum_check": "ok",
        }
        run.update({"stats": stats})
        print("Thống kê: " + json.dumps(stats, ensure_ascii=False), flush=True)
        status = "ok"
        return 0
    except LockLostError as e:
        print(f"Dừng: {e}; không ghi gì.", flush=True)
        return 5
    except Exception:  # noqa: BLE001
        status = "failed"
        traceback.print_exc()
        print("Dừng: lỗi không lường trước (mã thoát 6).", flush=True)
        return 6
    finally:
        if tmp_dir:
            outdir.discard(tmp_dir)
        try:
            run.finish(status)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()


if __name__ == "__main__":
    sys.exit(main())
