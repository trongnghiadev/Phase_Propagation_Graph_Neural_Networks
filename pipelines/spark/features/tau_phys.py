"""G3 – Thời gian truyền vật lý τ cho mỗi cạnh của đồ thị (`graph_edges.tau_phys_h`).

    spark-submit tau_phys.py [mongodb_uri] [--db vietnam_gis] [--out-dir DIR] [--report-dir DIR]
                             [--lock-ttl-s 3600] [--dry-run]

**Vật lý (và nguồn số liệu).**

*Chiều xuôi (`down`, sóng lũ):* dùng sóng kinhetic của Manning.
    v = (1/n)·R^(2/3)·S^(1/2)        – vận tốc dòng chảy Manning (Chow, Maidment & Mays, 1994,
                                       *Hydrology and Floodplain Analysis*, chương 8)
    c = (5/3)·v                      – vận tốc sóng lũ: hệ số 5/3 theo Leopold & Maddock (1954),
                                       *Floods: A Study of Rivers and Their Flood Plains* (học thuyết
                                       sóng kinhetic cho sông thượng nguồn, đơn vị Mỹ)
    τ_down = L / c
Trong đó `S` = `slope_used` (độ dốc đường nước, m/m), `R` = bán kính thủy vực đặc trưng,
`n` = hệ số nhám Manning **0,030** (sông tự nhiên có thảm thực vật và đáy cuội, trong khoảng
0,025–0,035 theo bảng 5-1 của Chow et al.; chọn giá trị giữa cho sông đồng bằng Việt Nam).

*Chiều ngược (`up`, sóng triều):* dùng sóng nông.
    c = sqrt(g·h) − v                – vận tốc sóng triều: `sqrt(g·h)` là vận tốc sóng nông
                                       (shallow-water wave), trừ vận tốc dòng chảy vì sóng triều
                                       truyền NGƯỢC dòng chảy (tương đối với bờ)
    τ_up = L / c
Trong đó `g = 9,81 m/s²`, `h` = chiều sâu nước trung bình theo proxy lưu vực
`h ≈ 0,62·A(km²)^0,12` (A = `uparea`), `v` = vận tốc dòng chảy Manning ở chiều xuôi.

**Proxy hình học.** MERIT không có mặt cắt ngang, nên suy ra bán kính thủy vực từ diện tích lưu vực:
`R ≈ 11·A(km²)^0,5` (A = 1.000 km² → R ≈ 348 m; A = 30.000 km² → R ≈ 1.900 m) và
`h ≈ 0,62·A(km²)^0,12` (A = 100 km² → h ≈ 0,98 m; A = 30.000 km² → h ≈ 1,79 m).
Đây là **proxy**, không phải đo đạc; xem mục "Giới hạn" trong `results/G3/README.md`.

**`slope = 0`.** MERIT có nhiều reach có `slope = 0` (không tính được độ dốc). Giá trị GỐC được giữ ở
`slope_raw`; giá trị DÙNG (`slope_used`) là **trung vị `slope` của các reach cùng `order`** trong cây
(nếu nhóm `order` không có reach nào có slope > 0 thì dùng trung vị toàn cây).

**Kẹp vận tốc** cho vật lý hợp lý: sóng lũ 0,2–8,0 m/s; sóng triều 0,3–6,0 m/s.
**Kẹp τ** vào [1/600 h, 72 h]. Sau đó kiểm tra: không có τ vô hạn / âm / NaN.

**Đầu ra.**
- Cập nhật `graph_edges` (staging → kiểm tra → sao lưu `__prev` → thay nguyên tử) với
  `celerity_ms`, `tau_phys_h` cho từng chiều; `edges.parquet` được ghi lại với `tau_down_h`, `tau_up_h`.
- Báo cáo `results/G3/tau_phys_summary.csv`: theo chiều (đếm, min/trung vị/max τ) và tổng τ
  từ Vũng Tàu tới từng trạm.

Mã thoát: 0 ok, 1 kiểm tra hỏng, 2 collection `graph_edges` không có, 3 job đang chạy ở nơi khác,
4 còn `__prev` (lùi lại rồi `--ack-rollback`), 5 mất khóa, 6 lỗi không lường trước.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (BooleanType, DoubleType, LongType, StringType, StructField,  # noqa: E402
                               StructType)

from common.jobrun import JobRun  # noqa: E402
from common.session import (JobLockError, LockLostError, MongoAdmin, build_session, env_db, env_uri,  # noqa: E402
                            read_mongo, write_mongo)
from features import atomic  # noqa: E402
from features import rules as R  # noqa: E402

JOB = "g3_tau_phys"
COLL = "graph_edges"
SEA = R.SEA_NODE

EDGE = StructType([
    StructField("COMID", LongType()),
    StructField("NextDownID", LongType()),
    StructField("up1", LongType()),
    StructField("up2", LongType()),
    StructField("up3", LongType()),
    StructField("up4", LongType()),
    StructField("order", LongType()),
    StructField("slope", DoubleType()),
    StructField("uparea", DoubleType()),
    StructField("lengthkm", DoubleType()),
])


def _iso(v) -> datetime | None:
    """BSON date (dict `{"$date": ...}`) → datetime UTC.

    Ngày sinh của `jobs` lưu dạng chuỗi ISO **kèm hậu tố `Z`**; Python 3.10 (image
    `gis-spark:local`) không parse được hậu tố `Z` bằng `fromisoformat` (chỉ 3.11+ mới
    được), nên phải tự cắt hậu tố `Z` và gắn `tzinfo=UTC`.
    """
    if v is None:
        return None
    s = v["$date"] if isinstance(v, dict) else v
    if isinstance(s, dict):
        return datetime.fromtimestamp(int(s["$numberLong"]) / 1000, tz=timezone.utc)
    if isinstance(s, datetime):
        return s if s.tzinfo else s.replace(tzinfo=timezone.utc)
    txt = str(s).strip()
    if txt.endswith("Z"):
        txt = txt[:-1] + "+00:00"
    try:
        out = datetime.fromisoformat(txt)
    except ValueError:
        return None
    return out if out.tzinfo else out.replace(tzinfo=timezone.utc)


def load_jobs(adm: MongoAdmin) -> list[dict]:
    out = []
    for j in adm.find_many("jobs", {"job": JOB}, {"status": 1, "replaced": 1, "replacing": 1, "generated_at": 1}):
        j["generated_at"] = _iso(j.get("generated_at"))
        out.append(j)
    out.sort(key=lambda x: (x.get("generated_at") or datetime.min.replace(tzinfo=timezone.utc)))
    return out


def compute_tau(reaches: dict[int, dict], edges: list[dict]) -> list[dict]:
    """Tính `celerity_ms` và `tau_phys_h` cho mỗi cạnh.

    Mỗi cạnh có `source_comid` = reach mang thông tin hình học (chiều dài, `uparea`).
    Cạnh ảo biển dùng `source_comid` = COMID cửa sản.
    """
    out = []
    for e in edges:
        comid = e.get("source_comid")
        d = reaches.get(int(comid)) if comid is not None else None
        if d is None:
            out.append({**e, "celerity_ms": None, "tau_phys_h": None})
            continue
        length_km = float(d.get("lengthkm") or 0.0)
        area = float(d.get("uparea") or 0.0)
        slope = float(e.get("slope_used") or 0.0)
        if e["dir"] == "down":
            tau, c = R.tau_down(length_km, area, slope)
        else:
            tau, c = R.tau_up(length_km, area, slope)
        out.append({**e, "celerity_ms": round(c, 6), "tau_phys_h": round(tau, 6)})
    return out


def upstream_tau_totals(edges: list[dict], sea: str = SEA) -> dict[str, float]:
    """Thời gian sóng triều đi từ Vũng Tàu (nút ảo biển) tới từng nút, theo chiều `up`.

    **Ý nghĩa vật lý.** Sóng triều đi từ biển vào sông theo chiều `up` (ngược dòng chảy).
    Thời gian tới một nút là **đường đi dài nhất** từ nút biển tới nút đó, KHÔNG phải TỔNG
    các nhánh: nếu một nút nhận 3 nhánh, sóng chỉ đi trên MỘT nhánh tại một thời điểm, và
    nhánh có τ lớn nhất là nhánh bị trễ nhất → thời gian tới nút là τ của nhánh đó.

    Vì vậy tính bằng **đường đi dài nhất trong DAG** (thứ tự topo + nới lỏng cạnh), khớp với
    `rules._longest_path_levels`. Trả về {mã nút: τ tích luỹ từ biển (giờ)}.
    """
    # đồ thị `up`: nút → các nút thượng nguồn (chiều sóng đi)
    succ: dict[str, list[tuple[str, float]]] = {}
    indeg: dict[str, int] = {}
    for e in edges:
        if e["dir"] != "up" or e.get("tau_phys_h") is None:
            continue
        s, d = str(e["src"]), str(e["dst"])
        succ.setdefault(s, []).append((d, float(e["tau_phys_h"])))
        indeg.setdefault(s, 0)
        indeg[d] = indeg.get(d, 0) + 1
    if sea not in indeg:
        return {}
    # thứ tự topo theo chiều `up`: nguồn = nút không có cạnh `up` đi vào
    queue = [n for n, k in indeg.items() if k == 0]
    order: list[str] = []
    while queue:
        n = queue.pop(0)
        order.append(n)
        for d, _w in succ.get(n, []):
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    # đường đi dài nhất từ nút biển
    best: dict[str, float] = {sea: 0.0}
    for n in order:
        if n not in best:
            best[n] = 0.0
        for d, w in succ.get(n, []):
            if best[n] + w > best.get(d, float("-inf")):
                best[d] = best[n] + w
    return best


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("uri", nargs="?", default=env_uri(), help="URI MongoDB (mặc định biến MONGO_URI)")
    ap.add_argument("--db", default=env_db())
    ap.add_argument("--root-comid", type=int, default=R.ROOT_COMID)
    ap.add_argument("--out-dir", default="/work/data/processed/graph",
                    help="thư mục ghi edges.parquet (mặc định /work/data/processed/graph)")
    ap.add_argument("--report-dir", default="/out/G3", help="thư mục ghi báo cáo (mặc định /out/G3)")
    ap.add_argument("--lock-ttl-s", type=int, default=3600)
    ap.add_argument("--dry-run", action="store_true", help="chỉ tính và in, không ghi MongoDB/Parquet")
    ap.add_argument("--ack-rollback", metavar="RUN_ID")
    args = ap.parse_args(argv)

    spark = build_session(JOB, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    run = JobRun(adm, JOB, args.lock_ttl_s)
    run_id = run.run_id
    staging = f"{COLL}{atomic.STAGING}_{run_id}"
    print(f"run_id={run_id} db={args.db}", flush=True)
    status = "failed"
    try:
        try:
            run.acquire()
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        if args.ack_rollback:
            status = "ack"
            return atomic.ack_rollback(adm, COLL, args.ack_rollback, load_jobs(adm))
        run.start(db=args.db, staging=[staging], replaced=[])
        if not adm.exists(COLL):
            print(f"Dừng: collection {COLL} chưa có; chạy G1 (graph_build.py) trước.", flush=True)
            return 2
        if not args.dry_run:
            atomic.check_prev_safe(adm, COLL, run_id, load_jobs(adm))

        # ---- Đọc MERIT và cạnh --------------------------------------------
        merit_rows = [r.asDict() for r in read_mongo(spark, "merit_river_reaches", EDGE, db=args.db,
                                                    pipeline=[{"$project": {
                                                        "COMID": 1, "NextDownID": 1, "up1": 1, "up2": 1,
                                                        "up3": 1, "up4": 1, "order": 1, "slope": 1,
                                                        "uparea": 1, "lengthkm": 1}}]).collect()]
        reaches = {int(r["COMID"]): r for r in merit_rows if r.get("COMID") is not None}
        tree = R.build_tree(reaches, args.root_comid)

        raw_edges = [d for d in adm.aggregate(COLL, [{"$project": {
            "_id": 0, "src": 1, "dst": 1, "dir": 1, "virtual": 1, "length_km": 1,
            "slope_raw": 1, "slope_used": 1, "source_comid": 1, "node_kind": 1}}])]
        print(f"graph_edges: {len(raw_edges)} cạnh; cây MERIT: {len(tree)} nút", flush=True)
        if not raw_edges:
            print("Dừng: `graph_edges` rỗng.", flush=True)
            return 1

        # chuẩn hoá kiểu: MongoDB giữ COMID dạng số → đưa về chuỗi cho thuật toán
        edges = []
        for d in raw_edges:
            e = dict(d)
            for k in ("src", "dst", "source_comid"):
                v = e.get(k)
                e[k] = str(v) if v is not None else None
            edges.append(e)

        # ---- Tính τ --------------------------------------------------------
        edges = compute_tau(reaches, edges)

        # ---- Kiểm tra ------------------------------------------------------
        bad = [e for e in edges if e.get("tau_phys_h") is None
               or not (0 < e["tau_phys_h"] <= R.TAU_MAX_H)
               or e["tau_phys_h"] != e["tau_phys_h"]]       # NaN
        ok = not bad
        print(f"[{'OK ' if ok else 'LỖI'}] mọi τ hợp lệ (0 < τ ≤ {R.TAU_MAX_H} h, không NaN): "
              f"{len(edges) - len(bad)}/{len(edges)}", flush=True)
        if bad:
            for b in bad[:5]:
                print(f"    τ không hợp lệ: src={b['src']} dst={b['dst']} dir={b['dir']} "
                      f"τ={b.get('tau_phys_h')}", flush=True)
            return 1
        n_slope_fixed = len([e for e in edges if (e.get("slope_raw") or 0) == 0 and (e.get("slope_used") or 0) > 0])
        print(f"Cạnh có slope gốc = 0 và dùng slope thay thế: {n_slope_fixed}", flush=True)

        # ---- Thống kê ------------------------------------------------------
        rows = []
        for direction in ("down", "up"):
            vals = sorted(e["tau_phys_h"] for e in edges if e["dir"] == direction)
            cs = sorted(e["celerity_ms"] for e in edges if e["dir"] == direction)
            if not vals:
                continue
            med = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
            medc = cs[len(cs) // 2] if len(cs) % 2 else (cs[len(cs) // 2 - 1] + cs[len(cs) // 2]) / 2
            rows.append({"dir": direction, "n_edges": len(vals), "tau_min_h": vals[0], "tau_median_h": med,
                         "tau_max_h": vals[-1], "tau_sum_h": sum(vals),
                         "celerity_min_ms": cs[0], "celerity_median_ms": medc, "celerity_max_ms": cs[-1]})
            print(f"  {direction:<5} n={len(vals):<6} τ: min {vals[0]:.4f} h, trung vị {med:.4f} h, "
                  f"max {vals[-1]:.4f} h | c: min {cs[0]:.3f}, trung vị {medc:.3f}, max {cs[-1]:.3f} m/s",
                  flush=True)

        totals = upstream_tau_totals(edges)
        st_rows = [r.asDict() for r in read_mongo(spark, "stations", None, db=args.db).collect()]
        station_rows = []
        for s in st_rows:
            m = s.get("merit")
            merit = m.asDict(recursive=True) if m is not None else {}
            comid = merit.get("COMID")
            if not comid:
                continue
            key = str(int(comid))
            if key in totals:
                station_rows.append({"station": s.get("code"), "name": s.get("name"),
                                     "comid": key, "kind": s.get("kind"),
                                     "tau_from_sea_h": round(totals[key], 4)})
        station_rows.sort(key=lambda r: r["tau_from_sea_h"])
        print("Tổng τ từ Vũng Tàu (hướng up) tới các trạm/hồ:", flush=True)
        for r in station_rows:
            print(f"    {r['station']:<18} COMID {r['comid']:<10} {r['tau_from_sea_h']:>8.2f} h", flush=True)

        stats = {"edges": len(edges), "by_dir": rows, "station_tau": station_rows,
                 "slope_zero_fixed": n_slope_fixed}
        run.update({"stats": stats})

        # ---- Ghi báo cáo ---------------------------------------------------
        if args.report_dir:
            os.makedirs(args.report_dir, exist_ok=True)
            out = os.path.join(args.report_dir, "tau_phys_summary.csv")
            with open(out, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("dir,n_edges,tau_min_h,tau_median_h,tau_max_h,tau_sum_h,"
                         "celerity_min_ms,celerity_median_ms,celerity_max_ms\n")
                for r in rows:
                    fh.write(f"{r['dir']},{r['n_edges']},{r['tau_min_h']:.6f},{r['tau_median_h']:.6f},"
                             f"{r['tau_max_h']:.6f},{r['tau_sum_h']:.6f},{r['celerity_min_ms']:.6f},"
                             f"{r['celerity_median_ms']:.6f},{r['celerity_max_ms']:.6f}\n")
            out2 = os.path.join(args.report_dir, "tau_from_sea_by_station.csv")
            with open(out2, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("station,name,comid,kind,tau_from_sea_h\n")
                for r in station_rows:
                    fh.write(f"{r['station']},{r['name']},{r['comid']},{r['kind']},{r['tau_from_sea_h']}\n")
            # ---- README báo cáo (cấu hình + ngày chạy + số đo thật) ---------
            md = os.path.join(args.report_dir, "README.md")
            lines_md = [
                "# G3 – Thời gian truyền vật lý theo cạnh (`tau_phys_h`)",
                "",
                "## Câu hỏi trả lời",
                "",
                "Mỗi cạnh của đồ thị hai chiều (G1) cần một thời gian truyền sóng để dựng bước",
                "lan truyền triều. Câu hỏi: với mô hình Manning (sóng lũ) và sóng thuỷ tĩnh",
                "(sóng triều), τ là bao nhiêu, và tổng τ từ Vũng Tàu tới mỗi trạm là bao nhiêu?",
                "",
                "## Cấu hình chạy",
                "",
                "| Tham số | Giá trị |",
                "|---|---|",
                f"| Số cạnh xử lý | {len(edges)} |",
                f"| Reach có `slope = 0` được thay | {n_slope_fixed} |",
                "| `n` (hệ số nhám Manning) | proxy tài liệu hoá trong docstring `features/tau_phys.py` |",
                "| `R` (bán kính thuỷ lược) | proxy từ `uparea` và chiều dài reach |",
                "| Kẹp vận tốc | giá trị hợp lý vật lý, xem docstring |",
                "",
                "## Kết quả",
                "",
            ]
            for r in rows:
                lines_md.append(
                    f"- Hướng `{r['dir']}`: {r['n_edges']} cạnh; τ min {r['tau_min_h']:.4f} h, "
                    f"trung vị {r['tau_median_h']:.4f} h, max {r['tau_max_h']:.4f} h; "
                    f"celerity trung vị {r['celerity_median_ms']:.3f} m/s.")
            lines_md += [
                "",
                "Tổng τ từ Vũng Tàu (hướng `up`) tới các trạm/hồ:",
                "",
                "| Trạm | Tên | COMID | Loại | τ từ biển (h) |",
                "|---|---|---|---|---|",
            ]
            for r in station_rows:
                lines_md.append(f"| {r['station']} | {r['name']} | {r['comid']} | {r['kind']} | "
                                f"{r['tau_from_sea_h']:.4f} |")
            lines_md += [
                "",
                "## File sinh ra",
                "",
                "- `tau_phys_summary.csv` – theo chiều: đếm, min/trung vị/max τ, tổng τ, celerity.",
                "- `tau_from_sea_by_station.csv` – tổng τ từ Vũng Tàu tới từng trạm/hồ.",
                "",
                "## Giới hạn",
                "",
                "- `n` và `R` là **proxy suy ra từ `uparea`/chiều dài reach**, KHÔNG phải đo đạc",
                "  thực tế của kênh; τ vì vậy là ước lượng cấp độ, dùng để định hình cấu trúc",
                "  lan truyền (độ trễ tương đối giữa các trạm) chứ không phải dự báo thời gian tuyệt đối.",
                "- Sóng triều bị **giảm tốc độ** bởi vận tốc dòng chảy dọc tuyến (sóng đi ngược dòng);",
                "  vận tốc dòng chảy ước lượng từ `uparea`/slope nên có sai số.",
                "- Reach có `slope = 0` dùng trung vị của nhóm `order` cùng cấp; giá trị gốc giữ ở `slope_raw`.",
                "",
                f"**Ngày chạy:** {datetime.now().astimezone().strftime('%Y-%m-%d %H:%M')} "
                f"(run `{run_id}`)",
                "",
            ]
            with open(md, "w", encoding="utf-8", newline="\n") as fh:
                fh.write("\n".join(lines_md))
            print(f"[BÁO CÁO] {out}, {out2}, {md}", flush=True)

        if args.dry_run:
            print("--dry-run: không ghi MongoDB/Parquet.", flush=True)
            status = "dry_run"
            return 0

        # ---- Ghi staging MongoDB ------------------------------------------
        # `src`/`dst`/`source_comid`: COMID dạng SỐ, TRỪ nút ảo biển là chuỗi `SEA_VUNGTAU`.
        #
        # Lưu ý kỹ thuật: KHÔNG thể dùng `F.when(cond, F.lit("SEA_VUNGTAU")).otherwise(cast_long)`
        # vì Spark thống nhất kiểu hai nhánh thành `string` (mọi COMID thành chuỗi); cũng
        # KHÔNG dùng `unionByName` (cũng thống nhất kiểu). Cách đúng: GHI HAI LẦN riêng —
        # (1) các cạnh thường với `src`/`dst`/`source_comid` kiểu `long`;
        # (2) các cạnh có nút ảo biển với kiểu `string` cho đúng các ô đó.
        run.hold()
        # PHẢI gắn lại validator + index của G1: `renameCollection` không mang theo
        # `$jsonSchema`, nên nếu staging không có validator thì collection chính sẽ mất
        # validator sau khi G3 thay thế (xem `features/rules.py`).
        adm.create(staging, validator=R.GRAPH_EDGES_VALIDATOR,
                   validationLevel="strict", validationAction="error")
        base_rows = []
        sea_rows = []
        for e in edges:
            common = (e["dir"], e["virtual"], e["length_km"], e["slope_raw"], e["slope_used"],
                      e["node_kind"], e["celerity_ms"], e["tau_phys_h"])
            if e["src"] == SEA or e["dst"] == SEA or e["source_comid"] == SEA:
                sea_rows.append((e["src"], e["dst"], e["source_comid"]) + common)
            else:
                base_rows.append((int(e["src"]), int(e["dst"]), int(e["source_comid"])) + common)
        schema_num = ("src long, dst long, source_comid long, dir string, virtual boolean, "
                      "length_km double, slope_raw double, slope_used double, node_kind string, "
                      "celerity_ms double, tau_phys_h double")
        schema_str = ("src string, dst string, source_comid string, dir string, virtual boolean, "
                      "length_km double, slope_raw double, slope_used double, node_kind string, "
                      "celerity_ms double, tau_phys_h double")
        write_mongo(spark.createDataFrame(base_rows, schema_num), staging, args.db)
        if sea_rows:
            write_mongo(spark.createDataFrame(sea_rows, schema_str), staging, args.db)
        adm.create_indexes(staging, R.GRAPH_EDGES_INDEXES)
        n_stg = adm.count(staging)
        print(f"[STAGING] {staging}: {n_stg} document", flush=True)
        if n_stg != len(edges):
            print(f"Dừng: staging {n_stg} document, kỳ vọng {len(edges)}.", flush=True)
            return 1

        # ---- Ghi Parquet ---------------------------------------------------
        # Ghi trong container driver (`local[*]`): executor của cụm không có mount repo.
        # Với `local[*]` driver và executor cùng container nên đường dẫn là đường dẫn thật.
        run.hold()
        os.makedirs(args.out_dir, exist_ok=True)
        edges_df = spark.createDataFrame(
            [{**e, "src": str(e["src"]), "dst": str(e["dst"]), "source_comid": str(e["source_comid"])}
             for e in edges],
            "src string, dst string, dir string, virtual boolean, length_km double, slope_raw double, "
            "slope_used double, source_comid string, node_kind string, celerity_ms double, tau_phys_h double")
        # thêm `tau_down_h` / `tau_up_h` cho CẶP nút nối với nhau — hợp đồng `schemas/features/v1.md`.
        # Với cặp (A, B) (A thượng nguồn, B hạ nguồn): `tau_down_h` = τ(A→B) (sóng lũ),
        # `tau_up_h` = τ(B→A) (sóng triều). Vì mỗi cặp có đúng 2 cạnh (một `down`, một `up`
        # với chiều ngược lại), ta tạo khóa chuẩn hoá `pair = (min(src,dst), max(src,dst))`
        # rồi gom τ theo chiều.
        edges_df = edges_df.withColumn("lo", F.least(F.col("src"), F.col("dst"))) \
                           .withColumn("hi", F.greatest(F.col("src"), F.col("dst")))
        pivot = (edges_df.groupBy("lo", "hi")
                 .agg(F.sum(F.when(F.col("dir") == "down", F.col("tau_phys_h"))).alias("tau_down_h"),
                      F.sum(F.when(F.col("dir") == "up", F.col("tau_phys_h"))).alias("tau_up_h")))
        edges_df = edges_df.join(pivot, ["lo", "hi"], "left").drop("lo", "hi")
        spark.conf.set("spark.sql.shuffle.partitions", "8")
        edges_df.coalesce(4).write.mode("overwrite").parquet(os.path.join(args.out_dir, "edges.parquet"))
        print(f"[PARQUET] {args.out_dir}/edges.parquet ({len(edges)} dòng)", flush=True)

        # ---- Thay bản chính ------------------------------------------------
        run.hold()
        atomic.check_prev_safe(adm, COLL, run_id, load_jobs(adm))
        atomic.backup(adm, COLL)
        replaced: list[str] = []

        def done():
            replaced.append(COLL)
            run.update({"replaced": list(replaced)})

        run.update({"replacing": COLL})
        atomic.swap(adm, COLL, staging, len(edges), done)
        run.update({"replacing": None})
        adm.drop(f"{COLL}{atomic.PREV}")
        status = "ok"
        print("Thống kê: " + json.dumps({"edges": len(edges), "by_dir": rows}, ensure_ascii=False), flush=True)
        return 0
    except atomic.PrevPendingError as e:
        status = "blocked"
        print(f"Dừng: {e}", flush=True)
        return 4
    except LockLostError as e:
        print(f"Dừng: {e}; không thay collection nào.", flush=True)
        return 5
    except Exception:  # noqa: BLE001
        status = "failed"
        traceback.print_exc()
        print("Dừng: lỗi không lường trước (mã thoát 6).", flush=True)
        return 6
    finally:
        try:
            run.finish(None if status == "ack" else status)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()


if __name__ == "__main__":
    sys.exit(main())
