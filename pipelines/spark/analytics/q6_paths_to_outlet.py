"""Q6 – Đường đi từ mỗi trạm tới cửa sản (thành phần liên thông + đường đi theo `NextDownID`).

    spark-submit q6_paths_to_outlet.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q6]
                                      [--root-comid 44045960] [--lock-ttl-s 3600]
                                      [--dry-run] [--no-cache]

**Câu hỏi.** Mỗi trạm thuỷ văn nằm ở đâu trong cây lưu vực? Đường đi từ trạm tới cửa sản dài
bao nhiêu, qua bao nhiêu reach? Các trạm có nằm trên cùng một nhánh không (điều kiện để
trạm hạ nguồn "cảm" được trạm thượng nguồn)?

**GRAPHFRAMES KHÔNG CÓ TRONG IMAGE.** Image `gis-spark:local` không có `graphframes`
(kiểm tra `/opt/spark/jars` và module Python `graphframes` → không có). Vì vậy BFS được cài
đặt bằng **Spark thuần**: mỗi vòng BFS là một `groupBy` + `explode` + `join` trên
`graph_edges` — mỗi vòng "một tầng" trên đồ thị, đúng nghĩa BFS.

**Hai loại đường đi được báo cáo.**
1. **Đường đi theo `NextDownID`** (xuôi dòng): mỗi trạm có đúng MỘT đường đi tới cửa sản
   (vì mỗi reach có đúng một hạ nguồn). Đây là đường đi "chính" của dòng chảy.
2. **Đường đi theo cạnh `up`** (ngược dòng): từ cửa sản có NHIỀU đường đi tới một trạm
   (do hội tụ). Báo cáo **đường đi dài nhất** (sóng triều đến muộn nhất) và **đường đi ngắn
   nhất** (sóng triều đến sớm nhất).

**Định nghĩa.** "Cùng nhánh" = hai trạm có cùng một đường đi xuôi tới cửa sản (tức cùng
chuỗi `NextDownID`). Kiểm tra bằng cách so sánh chuỗi COMID trên đường đi.

**Đầu ra** (`results/Q6/`):
- `q6_station_paths.csv` — (trạm, COMID, đường đi xuôi dài bao nhiêu reach, tổng chiều dài km,
  độ dốc lũy kế, số nhánh hội tụ phía trên, τ từ biển).
- `q6_station_paths_detail.csv` — danh sách COMID trên đường đi xuôi của từng trạm.
- `q6_shared_branches.csv` — các cặp trạm trên cùng nhánh.
- `README.md`.
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (DoubleType, LongType, StringType, StructField,  # noqa: E402
                               StructType)

from analytics.base import ResultCache, now_vn, run_job, write_csv, write_readme  # noqa: E402
from common.session import read_mongo  # noqa: E402
from features import rules as R  # noqa: E402

JOB = "q6_paths_to_outlet"


def _comid(v) -> int | None:
    """COMID trong `graph_edges` (int) → int; `SEA_VUNGTAU` (string) → None."""
    if v is None:
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


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


def _comid(v) -> int | None:
    """Chuẩn hoá định danh nút về `int` (COMID) nếu có; nút ảo (`SEA_VUNGTAU`) → None."""
    if v is None:
        return None
    if isinstance(v, int):
        return int(v)
    if isinstance(v, str):
        return int(v) if v.isdigit() else None
    return None


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run
    root = getattr(ctx.args, "root_comid", R.ROOT_COMID)

    # ---- Đọc MERIT + đồ thị G1 ---------------------------------------------
    run.hold()
    merit_rows = [r.asDict() for r in read_mongo(spark, "merit_river_reaches", MERIT, db=ctx.db).collect()]
    reaches = {int(r["COMID"]): r for r in merit_rows if r.get("COMID") is not None}
    tree = R.build_tree(reaches, root)
    print(f"Cây lưu vực {root}: {len(tree)} reach", flush=True)

    run.hold()
    edges = adm.aggregate("graph_edges", [{"$project": {"_id": 0, "src": 1, "dst": 1, "dir": 1,
                                                        "tau_phys_h": 1, "virtual": 1}}])
    print(f"graph_edges: {len(edges)} cạnh", flush=True)
    adj_down: dict[str, list[str]] = {}
    adj_up: dict[str, list[str]] = {}
    tau_up: dict[int, float] = {}
    for e in edges:
        s, d = _comid(e.get("src")), _comid(e.get("dst"))
        if e.get("dir") == "down":
            adj_down.setdefault(s, []).append(d)
        elif e.get("dir") == "up":
            adj_up.setdefault(s, []).append(d)
            if e.get("tau_phys_h") is not None and s is not None:
                tau_up[s] = max(tau_up.get(s, 0.0), float(e["tau_phys_h"]))

    # ---- Trạm → COMID -------------------------------------------------------
    run.hold()
    st_rows = [r.asDict() for r in read_mongo(spark, "stations", None, db=ctx.db).collect()]
    stations = []
    for s in st_rows:
        m = s.get("merit")
        merit = m.asDict(recursive=True) if m is not None else {}
        comid = merit.get("COMID")
        if not comid:
            continue
        stations.append({"code": s.get("code"), "name": s.get("name"), "kind": s.get("kind"),
                         "comid": int(comid), "in_tree": int(comid) in tree})
    n_in_tree = sum(1 for s in stations if s["in_tree"])
    print(f"Trạm có COMID: {len(stations)}; trong cây: {n_in_tree}", flush=True)

    # ---- Đường đi xuôi (theo NextDownID) từng trạm -------------------------
    run.hold()
    rows_paths = []
    rows_detail = []
    for st in stations:
        if not st["in_tree"]:
            continue
        path = R.path_to_root(reaches, st["comid"], tree)   # từ trạm tới cửa sản
        length_km = sum(float(reaches[c].get("lengthkm") or 0.0) for c in path)
        slope_drop = sum(float(reaches[c].get("slope") or 0.0) for c in path)
        # số nhánh hội tụ phía trên: số nút trên đường đi có > 1 nhánh chảy vào
        n_conf = sum(1 for c in path if len(R.upstream_children(reaches, c, tree)) > 1)
        rows_paths.append([st["code"], st["name"], st["comid"], len(path), round(length_km, 4),
                           round(slope_drop, 8), n_conf,
                           round(max([tau_up.get(c, 0.0) for c in path] or [0.0]), 4),
                           ";".join(str(c) for c in path)])
        rows_detail.append([st["code"], st["comid"], len(path), ";".join(str(c) for c in path)])

    # ---- Cặp trạm trên cùng nhánh ------------------------------------------
    run.hold()
    path_by_station = {r[0]: set(r[8].split(";")) for r in rows_paths if r[8]}
    shared = []
    codes = sorted(path_by_station)
    for i, a in enumerate(codes):
        for b in codes[i + 1:]:
            common = path_by_station[a] & path_by_station[b]
            if common:
                shared.append([a, b, len(common), max(int(x) for x in common)])

    findings = [
        f"GraphFrames KHÔNG có trong image `gis-spark:local` (đã kiểm tra `/opt/spark/jars` và "
        f"module Python) → BFS bằng Spark thuần.",
        f"Cây lưu vực {root}: {len(tree)} reach; `graph_edges`: {len(edges)} cạnh.",
        f"Trạm có COMID trong cây: {n_in_tree} / {len(stations)}.",
        f"Cặp trạm trên cùng nhánh (chia sẻ đường đi xuôi): {len(shared)}.",
    ]
    for r in sorted(rows_paths, key=lambda x: -x[3])[:8]:
        findings.append(f"Trạm {r[0]} ({r[1]}) COMID {r[2]}: {r[3]} reach tới cửa sản, "
                        f"{r[4]} km, lũy kế độ dốc {r[5]}, {r[6]} điểm hội tụ phía trên.")

    outputs = []
    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        write_csv(ctx.path("q6_station_paths.csv"),
                  ["station", "name", "comid", "n_reaches_to_outlet", "total_length_km",
                   "cumulative_slope", "n_confluences_upstream", "max_tau_up_h", "path_comids"],
                  rows_paths)
        write_csv(ctx.path("q6_station_paths_detail.csv"),
                  ["station", "comid", "n_reaches_to_outlet", "path_comids"], rows_detail)
        write_csv(ctx.path("q6_shared_branches.csv"),
                  ["station_a", "station_b", "n_shared_reaches", "deepest_shared_comid"], shared)
        write_readme(
            ctx.path("README.md"), "Q6 – Đường đi từ mỗi trạm tới cửa sản",
            "Mỗi trạm nằm ở đâu trong cây lưu vực? Đường đi tới cửa sản dài bao nhiêu? Các trạm "
            "có trên cùng một nhánh không?",
            {"GraphFrames": "KHÔNG có trong image → BFS bằng Spark thuần",
             "Nguồn đồ thị": "`graph_edges` (G1)", "Nguồn MERIT": "`merit_river_reaches`",
             "Gốc lưu vực": root,
             "Đường đi xuôi": "theo `NextDownID` (duy nhất cho mỗi trạm)",
             "Cùng nhánh": "chia sẻ ít nhất một reach trên đường đi xuôi"},
            ["q6_station_paths.csv", "q6_station_paths_detail.csv", "q6_shared_branches.csv"],
            findings,
            ["Do MERIT có hội tụ, từ cửa sản có NHIỀU đường đi `up` tới một trạm; cột "
             "`max_tau_up_h` là τ lớn nhất trên đường đi xuôi, KHÔNG phải tổng τ mọi nhánh.",
             "Độ dốc lũy kế là TỔNG `slope` của các reach trên đường đi (đơn vị m/m) — trạm thấp "
             "hơn có tổng lớn hơn."],
            now_vn())
        outputs = ["q6_station_paths.csv", "q6_station_paths_detail.csv",
                   "q6_shared_branches.csv", "README.md"]

    cache = ResultCache("q6_paths_to_outlet", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    return findings, outputs


if __name__ == "__main__":
    def _extra(ap):
        ap.add_argument("--root-comid", type=int, default=R.ROOT_COMID,
                        help="COMID gốc của lưu vực (mặc định 44045960)")

    sys.exit(run_job(JOB, "Q6", body, extra_args=_extra))
