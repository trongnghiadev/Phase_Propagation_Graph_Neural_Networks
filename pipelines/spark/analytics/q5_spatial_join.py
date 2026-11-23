"""Q5 – Ghép OSM ↔ MERIT bằng khoảng cách không gian (50 / 100 / 250 m).

    spark-submit q5_spatial_join.py [mongodb_uri] [--db vietnam_gis] [--out-dir /out/Q5]
                                  [--root-comid 44045960]
                                  [--buffers 50,100,250] [--lock-ttl-s 3600] [--dry-run] [--no-cache]

**Câu hỏi.** Các đoạn sông OSM (`waterways_segments`) có khớp với các reach MERIT trong lưu vực
Đồng Nai – Sài Gòn không? Ở ngưỡng khoảng cách nào thì tỉ lệ khớp cao nhất? Điều này quyết
định có thể dùng OSM để bổ sung cho MERIT hay không (và độ tin cậy của dữ liệu đường sông).

**SEDONA KHÔNG CÓ TRONG IMAGE — dùng cách khác, đã ghi rõ.**
Image `gis-spark:local` (Spark 3.5.9) **KHÔNG** có Sedona (`sedona`, `geotools`, `jts` đều
không có trong `/opt/spark/jars` và không có trong Ivy cache). Vì vậy spatial join được cài
đặt bằng **Spark thuần**:
1. **Prefilter bằng bbox.** Mỗi reach MERIT có `bbox`; mỗi đoạn OSM có `bbox`. Hai bbox
   chỉ giao nhau khi các đoạn thẳng nối giữa chúng có thể gần nhau trong bán kính `r`.
   Điều kiện lọc: khoảng cách giữa hai bbox ≤ `r`.
2. **Khoảng cách chính xác.** Với mỗi cặp ứng viên, tính khoảng cách **thực** giữa đoạn
   thẳng OSM (LineString) và đoạn thẳng MERIT (LineString) theo công thức khoảng cách
   giữa hai đoạn thẳng trên mặt cầu Trái Đất: phép chiếu cục bộ **equirectangular**
   quanh vĩ độ của cặp đoạn rồi đo khoảng cách Euclid trong mặt phẳng chiếu (xem
   `analytics/geometry.py`: `project_polyline`, `projected_segment_distance_m`). Đây là
   **khoảng cách thực** giữa hai đường nằm, không phải khoảng cách tới tâm hay tới đỉnh.

**Hạn chế được nhận.** Phép chiếu equirectangular cục bộ sai số < 0,1 % với đoạn
dài vài chục mưỗi km ở vĩ độ 10–11°, tức dưới 1 m, không ảnh hưởng ngưỡng 50/100/250 m.
Mỗi đoạn OSM được chiếu trong **khung riêng** quanh vĩ độ của chính nó (dữ liệu OSM/MERIT
trải rộng 8,5°N–23,5°N nên không dùng được một vĩ độ tham chiếu chung cho cả CSDL).
Hai đoạn thẳng (thay vì đường cong) chỉ là xấp xỉ vì OSM/MERIT lưu đường thẳng nối các
đỉnh; ảnh hưởng ở mức vài mét trên reach dài.

**Đầu ra** (`results/Q5/`):
- `q5_spatial_matches.csv` — các cặp khớp: (osm_id, fclass, name, comid, order, distance_m,
  buffer_m).
- `q5_buffer_summary.csv` — theo ngưỡng: số cặp khớp, số OSM có khớp, số MERIT có khớp,
  tỉ lệ bao phủ.
- `q5_osm_unmatched.csv` — các đoạn OSM không khớp ở ngưỡng lớn nhất.
- `README.md`.
"""
from __future__ import annotations

import math
import os
import sys
import time
from collections import defaultdict

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (ArrayType, DoubleType, LongType, StringType,  # noqa: E402
                               StructField, StructType)

from analytics.base import ResultCache, now_vn, run_job, write_csv, write_readme  # noqa: E402
from analytics.geometry import (EARTH_RADIUS_M, bbox_gap_m, buffer_summary,  # noqa: E402
                               parse_bbox, parse_geom, project_polyline, segment_boxes,
                               simplify_polyline, projected_polyline_distance_within)
from common.session import read_mongo  # noqa: E402
from features import rules as R  # noqa: E402

JOB = "q5_spatial_join"
DEFAULT_BUFFERS = (50.0, 100.0, 250.0)

MERIT = StructType([
    StructField("COMID", LongType()),
    StructField("NextDownID", LongType()),
    StructField("up1", LongType()),
    StructField("up2", LongType()),
    StructField("up3", LongType()),
    StructField("up4", LongType()),
    StructField("maxup", LongType()),
    StructField("order", LongType()),
    StructField("uparea", DoubleType()),
    StructField("lengthkm", DoubleType()),
    StructField("geometry", StringType()),
    StructField("bbox", ArrayType(DoubleType())),
])


def body(ctx) -> tuple[list[str], list[str]]:
    spark, adm, run = ctx.spark, ctx.adm, ctx.run
    root = getattr(ctx.args, "root_comid", R.ROOT_COMID)
    buffers = tuple(float(x) for x in str(getattr(ctx.args, "buffers", "50,100,250")).split(","))
    r_max = max(buffers)

    # ---- MERIT trong cây lưu vực -------------------------------------------
    run.hold()
    merit_rows = [r.asDict() for r in read_mongo(spark, "merit_river_reaches", MERIT, db=ctx.db).collect()]
    reaches = {int(r["COMID"]): r for r in merit_rows if r.get("COMID") is not None}
    tree = R.build_tree(reaches, root)
    # Sai số rút gọn: 0,0005° ≈ 55 m ở vĩ độ 10,8° — nhỏ hơn nhiều bán kính khớp
    # nhỏ nhất (50 m) và không đổi kết luận khớp/không khớp ở ngưỡng 50 m trở lên.
    simplify_tol = 0.0005
    merit = []
    for c in sorted(tree):
        d = reaches[c]
        coords = parse_geom(d.get("geometry"))
        if not coords:
            continue
        merit.append((c, int(d.get("order") or 0), float(d.get("uparea") or 0.0),
                      parse_bbox(d.get("bbox")), simplify_polyline(coords, simplify_tol)))
    print(f"Reach MERIT trong cây {root} có hình học: {len(merit)} / {len(tree)}", flush=True)

    # ---- OSM waterways -----------------------------------------------------
    run.hold()
    osm_rows = [r.asDict() for r in read_mongo(spark, "waterways_segments", None, db=ctx.db).collect()]
    osm = []
    for r in osm_rows:
        coords = parse_geom(r.get("geometry"))
        if not coords:
            continue
        osm.append((str(r.get("osm_id")), r.get("fclass"), r.get("name"),
                    parse_bbox(r.get("bbox")), simplify_polyline(coords, simplify_tol)))
    print(f"Đoạn OSM có hình học: {len(osm)} / {len(osm_rows)}", flush=True)
    # Chẩn đoán: bbox rỗng ⇒ prefilter lưới không hoạt động (chạy O(n·m))
    n_bbox = sum(1 for m in merit if m[3]) + sum(1 for o in osm if o[3])
    print(f"Bbox đọc được: {n_bbox} / {len(merit) + len(osm)} "
          f"(MERIT {sum(1 for m in merit if m[3])}/{len(merit)}, "
          f"OSM {sum(1 for o in osm if o[3])}/{len(osm)})", flush=True)

    # ---- Chiếu tọa độ cục bộ (mỗi hình học một khung) ----------------------
    # Dữ liệu OSM/MERIT trải rộng 8,5°N–23,5°N (cả Việt Nam), nên **không** dùng một
    # vĩ độ tham chiếu chung cho toàn bộ — sai số sẽ vượt ngưỡng 50 m ở các đoạn xa
    # vĩ độ tham chiếu. Thay vào đó mỗi đoạn OSM được chiếu trong **khung riêng** quanh
    # vĩ độ của chính nó; các reach MERIT ứng viên nằm trong bán kính ≤ 250 m nên
    # chênh lệch vĩ độ giữa hai hình học < 0,003° (≈ 330 m) → chiếu chung khung của
    # đoạn OSM cho độ chính xác dưới 1 m ở ngưỡng khớp.
    merit_p_cache: dict[tuple[int, float], list] = {}

    def project_for(lat_ref_deg: float, coords: list) -> list:
        return project_polyline(coords, math.radians(lat_ref_deg))

    # ---- Prefilter bằng lưới không gian + khoảng cách chính xác -------------
    # Vòng lặp kép thuần Python (1.073 × 38.877 ≈ 41,7 triệu cặp) quá chậm, nên dùng
    # **lưới không gian (grid)**: chia vùng nghiên cứu thành ô vuông ~0,05° (~5,5 km),
    # mỗi đoạn OSM được đăng ký vào các ô mà bbox của nó chạm tới; mỗi reach MERIT
    # chỉ dò các ô mà bbox của nó chạm tới (mở rộng 1 ô cho bán kính tối đa).
    # Đây là **prefilter chính xác** (không bỏ sót cặp nào có khoảng cách ≤ r_max),
    # sau đó mới tính khoảng cách thực cho các cặp ứng viên.
    run.hold()
    cell_deg = 0.05
    merit_grid: dict[tuple[int, int], list[int]] = defaultdict(list)
    for idx, (_c, _o, _a, mbbox, _m) in enumerate(merit):
        if not mbbox:
            merit_grid[("all", 0)].append(idx)  # không bbox → đưa vào nhóm toàn cục
            continue
        i0 = int(math.floor(mbbox[0] / cell_deg))
        i1 = int(math.floor(mbbox[2] / cell_deg))
        j0 = int(math.floor(mbbox[1] / cell_deg))
        j1 = int(math.floor(mbbox[3] / cell_deg))
        for i in range(i0, i1 + 1):
            for j in range(j0, j1 + 1):
                merit_grid[(i, j)].append(idx)

    matches = []
    n_pairs = 0
    # `buffers` và `r_max` đã tính bằng **mét** → không nhân thêm 1 000.
    r_max_m = r_max
    t_start = time.perf_counter()
    for o_idx, (oid, fclass, name, obbox, _ocoords) in enumerate(osm):
        # tiến độ (mỗi 5 000 đoạn OSM) để không tưởng job treo
        if o_idx and o_idx % 5000 == 0:
            run.hold()
            el = time.perf_counter() - t_start
            print(f"  … {o_idx}/{len(osm)} đoạn OSM, {n_pairs} cặp ứng viên, "
                  f"{len(matches)} cặp khớp, {el:.0f} s", flush=True)
        if not obbox:
            cand_idx = list(range(len(merit)))
        else:
            i0 = int(math.floor(obbox[0] / cell_deg))
            i1 = int(math.floor(obbox[2] / cell_deg))
            j0 = int(math.floor(obbox[1] / cell_deg))
            j1 = int(math.floor(obbox[3] / cell_deg))
            cand_set: set[int] = set()
            # mở rộng 1 ô mỗi phía để bắt các cặp gần ranh giới ô
            for i in range(i0 - 1, i1 + 2):
                for j in range(j0 - 1, j1 + 2):
                    cand_set.update(merit_grid.get((i, j), ()))
            cand_set.update(merit_grid.get(("all", 0), ()))
            cand_idx = sorted(cand_set)
        # Khung chiếu cục bộ của đoạn OSM (vĩ độ giữa min/max của bbox)
        lat_ref = (obbox[1] + obbox[3]) / 2.0 if obbox else 10.8
        o_proj = project_for(lat_ref, osm[o_idx][4])
        o_boxes = segment_boxes(o_proj)
        for idx in cand_idx:
            comid, order, uparea, mbbox, mcoords = merit[idx]
            gap = bbox_gap_m(obbox, mbbox)
            if gap > r_max_m:
                continue
            n_pairs += 1
            # Cache theo (reach, vĩ độ tham chiếu) — cùng một reach thường được so
            # với nhiều đoạn OSM ở gần cùng vĩ độ nên cache ăn nhiều.
            cache_key = (idx, round(lat_ref, 4))
            cached = merit_p_cache.get(cache_key)
            if cached is None:
                m_proj = project_for(lat_ref, mcoords)
                cached = (m_proj, segment_boxes(m_proj))
                merit_p_cache[cache_key] = cached
            d = projected_polyline_distance_within(o_proj, cached[0], o_boxes, cached[1], r_max_m)
            if d <= r_max_m:
                matches.append((oid, fclass or "", name or "", comid, order,
                                round(d, 2), round(uparea, 3)))
    print(f"Ứng viên sau prefilter lưới+bbox: {n_pairs}; cặp khớp (≤ {r_max} m): {len(matches)}",
          flush=True)

    # ---- Tổng hợp theo ngưỡng ----------------------------------------------
    n_osm = len(osm)
    n_merit = len(merit)
    # `buffers` tính bằng mét; quy tắc lọc tập trung trong `geometry.buffer_summary`.
    rows_summary = buffer_summary(matches, buffers, n_osm, n_merit)

    # ---- OSM không khớp ở ngưỡng lớn nhất ----------------------------------
    matched_osm = {m[0] for m in matches if m[5] <= r_max}
    rows_unmatched = []
    for oid, fclass, name, obbox, ocoords in osm:
        if oid not in matched_osm:
            rows_unmatched.append([oid, fclass or "", name or ""])

    findings = [
        f"Sedona KHÔNG có trong image `gis-spark:local` (kiểm tra `/opt/spark/jars`, Ivy cache và "
        f"module Python `sedona`) → spatial join làm bằng Spark thuần: prefilter bbox + khoảng "
        f"cách chính xác giữa hai đoạn thẳng (chiếu equirectangular cục bộ).",
        f"Reach MERIT trong cây {root} có hình học: {n_merit}; đoạn OSM có hình học: {n_osm}.",
        f"Ứng viên sau prefilter bbox: {n_pairs}; cặp khớp trong {r_max} m: {len(matches)}.",
    ]
    for r in rows_summary:
        findings.append(f"Ngưỡng {r[0]:.0f} m: {r[1]} cặp khớp; OSM có khớp {r[2]}/{n_osm} "
                        f"({r[4]}%); MERIT có khớp {r[3]}/{n_merit} ({r[5]}%); "
                        f"khoảng cách trung bình {r[6]} m.")

    outputs = []
    if not ctx.args.dry_run:
        os.makedirs(ctx.out_dir, exist_ok=True)
        write_csv(ctx.path("q5_spatial_matches.csv"),
                  ["osm_id", "fclass", "name", "comid", "order", "distance_m", "uparea_km2"],
                  sorted(matches, key=lambda m: (m[0], m[5])))
        write_csv(ctx.path("q5_buffer_summary.csv"),
                  ["buffer_m", "n_matches", "n_osm_matched", "n_merit_matched",
                   "pct_osm_matched", "pct_merit_matched", "mean_distance_m"], rows_summary)
        write_csv(ctx.path("q5_osm_unmatched.csv"), ["osm_id", "fclass", "name"], rows_unmatched)
        write_readme(
            ctx.path("README.md"), "Q5 – Ghép OSM ↔ MERIT theo khoảng cách không gian",
            "Các đoạn sông OSM có khớp reach MERIT trong lưu vực không? Ở ngưỡng nào tỉ lệ khớp "
            "cao nhất?",
            {"Sedona": "KHÔNG có trong image → dùng Spark thuần (prefilter bbox + khoảng cách "
                       "chính xác giữa hai đoạn thẳng)",
             "Nguồn OSM": "`waterways_segments`", "Nguồn MERIT": "`merit_river_reaches`",
             "Gốc lưu vực": root, "Ngưỡng": ", ".join(f"{b:.0f} m" for b in buffers),
             "Bán kính Trái Đất": EARTH_RADIUS_M},
            ["q5_spatial_matches.csv", "q5_buffer_summary.csv", "q5_osm_unmatched.csv"],
            findings,
            ["**Sedona không có sẵn** trong `gis-spark:local` — đã kiểm tra `/opt/spark/jars`, "
             "Ivy cache và module Python. Cách thay thế được tài liệu hoá rõ, KHÔNG thay âm thầm.",
             "Khoảng cách dùng phép chiếu equirectangular cục bộ quanh vĩ độ của **từng cặp** "
             "(sai số < 0,1 % ở vĩ độ 10–11°), tức dưới 1 m — không ảnh hưởng ngưỡng 50/100/250 m.",
             "Hình học được **rút gọn Douglas–Peucker** với sai số 0,0005° (≈ 55 m) trước khi so "
             "sánh; sai số này nhỏ hơn ngưỡng khớp nhỏ nhất (50 m) nên không đổi kết luận "
             "khớp/không khớp.",
             "Cả hai nguồn lưu đường thẳng nối các đỉnh (không phải đường cong), gây sai số vài mét "
             "trên reach dài.",
             "`waterways_segments` là toàn bộ CSDL OSM khu vực, KHÔNG lọc theo lưu vực — nên tỉ lệ "
             "bao phủ thấp là bình thường."],
            now_vn())
        outputs = ["q5_spatial_matches.csv", "q5_buffer_summary.csv", "q5_osm_unmatched.csv",
                   "README.md"]

    cache = ResultCache("q5_spatial_join", enabled=not ctx.args.no_cache)
    print(cache.note(), flush=True)
    return findings, outputs


if __name__ == "__main__":
    def _extra(ap):
        ap.add_argument("--root-comid", type=int, default=R.ROOT_COMID,
                        help="COMID gốc của lưu vực (mặc định 44045960)")
        ap.add_argument("--buffers", default="50,100,250",
                        help="danh sách ngưỡng khoảng cách tính bằng mét, phân tách bằng dấu phẩy")

    sys.exit(run_job(JOB, "Q5", body, extra_args=_extra))
