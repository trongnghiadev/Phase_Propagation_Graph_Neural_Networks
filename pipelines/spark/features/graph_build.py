"""G1 – Dựng đồ thị hai chiều của lưu vực Đồng Nai – Sài Gòn từ MERIT (`merit_river_reaches`).

    spark-submit graph_build.py [mongodb_uri] [--db vietnam_gis] [--root-comid 44045960]
                                [--min-uparea-km2 100] [--out-dir DIR] [--lock-ttl-s 3600]
                                [--dry-run] [--keep-staging] [--ack-rollback RUN_ID]

**Cấu trúc.** Cây lưu vực lấy từ gốc `44045960` đi ngược theo cột `up1..up4` của MERIT
(`maxup` cho biết số con). Trong MERIT, `NextDownID = 0` đánh dấu **cửa sản**; kiểm tra trên dữ liệu
cho thấy gốc `44045960` CHÍNH LÀ cửa sản (không có hạ nguồn), còn `order` giảm dần khi đi xuôi
(order 6 ở gốc → order 1 ở thượng nguồn). Vì vậy:
- cạnh `down`: nút → `NextDownID` (nếu nằm trong cây) — mỗi nút **đúng một** hạ nguồn;
- cạnh `up`: chiều ngược lại của từng cạnh `down` (nút → con).

**Nút ảo.** `SEA_VUNGTAU` (đại biển, không có COMID) nối vào cửa sản bằng một cạnh ảo `up`
(nguồn = biển, đích = cửa sản `44045960`): đây là nguồn sóng triều. Cạnh ảo đảo chiều (`down`)
KHÔNG tồn tại vì biển không nằm trong cây sông.

**Cạnh ảo hồ chứa.** Với mỗi hồ có `stations.merit.COMID` nằm trong cây, tạo cạnh ảo `down`
từ nút hồ → reach **ngay dưới** hồ (hạ nguồn trực tiếp của COMID hồ). Vì nút hồ CHÍNH LÀ một reach
trong cây (MERIT định nghĩa con sông bằng chính reach hồ), cạnh ảo nối hồ với reach hạ nguồn:
`src = COMID hồ`, `dst = NextDownID(COMID hồ)`. Khi `NextDownID` không nằm trong cây thì bỏ qua
(hồ nằm ngoài lưu vực, ví dụ Sông Ray / Đá Đen) và in cảnh báo.

**Tỉa.** Giữ nút nếu `uparea_km2` ≥ ngưỡng (`--min-uparea-km2`, mặc định 100 km²) **hoặc** nút nằm
trên đường đi giữa một trạm/hồ và cửa sản. Mọi nút trạm và hồ luôn được giữ.

**Đầu ra.**
- MongoDB `graph_edges`: mỗi document = một (cạnh, chiều) `{src, dst, dir, virtual, length_km,
  slope_used, source_comid}`. `src`/`dst` là COMID dạng số, trừ nút ảo biển là chuỗi `SEA_VUNGTAU`.
  Ghi theo mẫu staging + thay nguyên tử + sao lưu `__prev`.
- Parquet `data/processed/graph/edges.parquet` và `nodes.parquet` (xem `schemas/features/v1.md`).

**Kiểm tra bắt buộc (hỏng → dừng, bản chính giữ nguyên, mã thoát 1):**
1. Cây không có chu trình hướng xuôi, mỗi nút ≤ 1 hạ nguồn, mỗi nút ≤ 1 cha.
2. Số cạnh `down` = số nút có hạ nguồn trong cây; số cạnh `up` = số cạnh `down` (+ cạnh ảo biển).
3. Không trùng khóa `(src, dst, dir)`.
4. Mọi nút trạm/hồ còn lại sau tỉa và vẫn nối được với cửa sản.

Parquet (`edges.parquet`, `nodes.parquet`) ghi vào `<đích>.tmp_<run_id>` và chỉ đổi tên thành đích sau
khi kiểm tra đạt và đã thay `graph_edges` (`features/outdir.py`); kiểm tra hỏng / `--dry-run` → bản cũ
giữ nguyên.

Mã thoát: 0 ok, 1 kiểm tra hỏng, 2 tên/mã không tra được, 3 job đang chạy ở nơi khác,
4 còn `__prev` của lần chạy hỏng giữa chừng (lùi lại rồi `--ack-rollback`), 5 mất khóa,
6 lỗi không lường trước.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import DataFrame, functions as F  # noqa: E402
from pyspark.sql.types import (ArrayType, BooleanType, DoubleType, LongType, StringType,  # noqa: E402
                               StructField, StructType)

from common.jobrun import JobRun  # noqa: E402
from features import outdir  # noqa: E402
from common.session import (JobLockError, LockLostError, MongoAdmin, bson_date, build_session, env_db,  # noqa: E402
                            env_uri, read_mongo, write_mongo)
from features import atomic  # noqa: E402
from features import rules as R  # noqa: E402

JOB = "g1_graph_build"
COLL = "graph_edges"
ROOT_COMID = R.ROOT_COMID
SEA = R.SEA_NODE

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
STATION = StructType([
    StructField("code", StringType()),
    StructField("name", StringType()),
    StructField("kind", StringType()),
    StructField("merit", StructType([
        StructField("COMID", LongType()),
        StructField("uparea_km2", DoubleType()),
        StructField("order", LongType()),
        StructField("basin_root_COMID", LongType()),
    ])),
])

# Validator + index của `graph_edges` — dùng chung với G3 (xem `features/rules.py`).
# `src`/`dst` là COMID dạng số HOẶC chuỗi `SEA_VUNGTAU` (nút ảo biển).
ID_OR_NULL = R.GRAPH_EDGES_ID_OR_NULL
VALIDATOR = R.GRAPH_EDGES_VALIDATOR
INDEXES = R.GRAPH_EDGES_INDEXES


# ---------------------------------------------------------------------------
# Kiểm tra
# ---------------------------------------------------------------------------
class Report:
    """Danh sách kiểm tra: tên, đạt/không, chi tiết."""

    def __init__(self):
        self.items: list[tuple[str, bool, str]] = []

    def add(self, name: str, ok: bool, detail: str) -> None:
        self.items.append((name, ok, detail))
        print(f"[{'OK ' if ok else 'LỖI'}] {name}: {detail}", flush=True)

    @property
    def ok(self) -> bool:
        return all(ok for _, ok, _ in self.items)


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
    """Bản ghi `jobs` của job (dùng cho quy tắc `__prev`)."""
    out = []
    for j in adm.find_many("jobs", {"job": JOB}, {"status": 1, "replaced": 1, "replacing": 1, "generated_at": 1}):
        j["generated_at"] = _iso(j.get("generated_at"))
        out.append(j)
    out.sort(key=lambda x: (x.get("generated_at") or datetime.min.replace(tzinfo=timezone.utc)))
    return out


# ---------------------------------------------------------------------------
# Dựng đồ thị
# ---------------------------------------------------------------------------
def build_graph(reaches: dict[int, dict], tree: set[int], keep: set[int],
                station_comids: dict[int, list[str]],
                reservoir_comids: dict[int, list[str]], root: int = ROOT_COMID):
    """Trả về (edges, levels) dựng từ cây lưu vực ĐÃ TỈA.

    **Hướng cạnh (theo hợp đồng `schemas/features/v1.md`):**
    - `down` = hướng DÒNG CHẢY XUÔI: nút → `NextDownID` (về phía cửa sản).
    - `up`   = hướNG NGƯỢC (sóng triều truyền từ biển vào sông): nút → nhánh sông chảy vào nó
      (`up1..up4`), tức chiều ngược lại của cạnh `down` tương ứng.

    Vì MERIT cho phép nhiều nhánh hợp nhất vào cùng một reach, cây là **DAG**: một nút có thể có
    nhiều cạnh `up` (nhiều nhánh chảy vào) nhưng chỉ **một** cạnh `down` (một `NextDownID`).

    Chỉ giữ cạnh có HAI đầu còn lại sau tỉa; nhờ luật tỉa giữ cả đường đi hai chiều
    (`rules.keep_nodes`), đồ thị còn lại vẫn liên thông hai chiều với cửa sản.

    `edges`: danh sách {src, dst, dir, virtual, length_km, slope_raw, slope_used, source_comid, node_kind}.
    """
    levels = R.topo_levels(reaches, tree, root)
    slopes = R.fill_slope(reaches, tree)
    kept = set(keep)

    edges: list[dict] = []
    # ---- cạnh xuôi (dòng chảy xuôi, theo NextDownID) --------------------
    down_pairs: list[tuple[int, int]] = []
    for n in sorted(kept):
        dst = R.downstream_child(reaches, n, tree)
        if dst is None or dst not in kept:
            continue
        down_pairs.append((n, dst))
        raw, used = slopes.get(n, (0.0, 0.0))
        edges.append({
            "src": int(n), "dst": int(dst), "dir": "down", "virtual": False,
            "length_km": float(reaches[n].get("lengthkm") or 0.0),
            "slope_raw": float(raw), "slope_used": float(used),
            "source_comid": int(n), "node_kind": "reach",
        })
    # ---- cạnh ngược (chiều ngược lại của từng cạnh xuôi) ----------------
    for src, dst in down_pairs:
        raw, used = slopes.get(src, (0.0, 0.0))
        edges.append({
            "src": int(dst), "dst": int(src), "dir": "up", "virtual": False,
            "length_km": float(reaches[src].get("lengthkm") or 0.0),
            "slope_raw": float(raw), "slope_used": float(used),
            "source_comid": int(src), "node_kind": "reach",
        })

    # ---- cạnh ảo: biển → cửa sản (nguồn sóng triều) --------------------
    outlet = root
    edges.append({
        "src": SEA, "dst": int(outlet), "dir": "up", "virtual": True,
        "length_km": float(reaches[outlet].get("lengthkm") or 0.0),
        "slope_raw": float(reaches[outlet].get("slope") or 0.0),
        "slope_used": float(slopes[outlet][1]),
        "source_comid": int(outlet), "node_kind": "sea",
    })

    # ---- cạnh ảo: hồ → reach hạ nguồn ngay dưới hồ ----------------------
    # Nút hồ CHÍNH LÀ một reach trong cây (MERIT định nghĩa con sông bằng chính reach hồ), nên
    # cạnh ảo nối hồ với reach hạ nguồn của nó: src = COMID hồ, dst = NextDownID(COMID hồ).
    # Cạnh này TRÙNG đường với cạnh `down` thường của cùng cặp nút → vì vậy KHÔNG thêm cạnh
    # `down` thứ hai; thay vào đó đánh dấu `virtual = true` trên chính cạnh `down` đó, để mỗi
    # (src, dst, dir) vẫn là duy nhất (ràng buộc index unique của `graph_edges`).
    virtual_down: set[tuple[int, int]] = set()
    for comid in sorted(reservoir_comids):
        dst = R.downstream_child(reaches, comid, tree)
        if dst is None:
            print(f"[CẢNH BÁO] hồ COMID {comid} không có hạ nguồn trong cây; không tạo cạnh ảo", flush=True)
            continue
        virtual_down.add((comid, dst))
    for e in edges:
        if e["dir"] == "down" and (e["src"], e["dst"]) in virtual_down:
            e["virtual"] = True
            e["node_kind"] = "reservoir"
    return edges, levels


def node_rows(reaches: dict[int, dict], tree: set[int], keep: set[int], levels: dict[str, dict[int, int]],
              station_comids: dict[int, list[str]], reservoir_comids: dict[int, list[str]]):
    """Danh sách node cho `nodes.parquet`."""
    rows = []
    for n in sorted(keep):
        d = reaches[n]
        codes = sorted(station_comids.get(n, []))
        res = sorted(reservoir_comids.get(n, []))
        kind = "reservoir" if res else "reach"
        rows.append({
            "comid": str(n), "kind": kind, "order": int(d.get("order") or 0),
            "uparea_km2": float(d.get("uparea") or 0.0),
            "topo_level_down": int(levels["down"].get(n, -1)),
            "topo_level_up": int(levels["up"].get(n, -1)),
            "station_codes": codes + res,
            "length_km": float(d.get("lengthkm") or 0.0),
            "slope_raw": float(d.get("slope") or 0.0),
            "next_down_id": str(d.get("NextDownID") or 0),
        })
    # nút ảo biển
    rows.append({
        "comid": SEA, "kind": "sea", "order": 0, "uparea_km2": 0.0,
        "topo_level_down": -1, "topo_level_up": 0, "station_codes": ["VUNG_TAU"],
        "length_km": 0.0, "slope_raw": 0.0, "next_down_id": "",
    })
    return rows


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("uri", nargs="?", default=env_uri(), help="URI MongoDB (mặc định biến MONGO_URI)")
    ap.add_argument("--db", default=env_db(), help="database (mặc định biến MONGO_DB hoặc vietnam_gis)")
    ap.add_argument("--root-comid", type=int, default=ROOT_COMID,
                    help="COMID gốc của lưu vực (mặc định 44045960 = Đồng Nai – Sài Gòn)")
    ap.add_argument("--min-uparea-km2", type=float, default=R.MIN_UPAREA_DEFAULT,
                    help="ngưỡng tỉa theo uparea (km², mặc định 100)")
    ap.add_argument("--out-dir", default="/work/data/processed/graph",
                    help="thư mục ghi Parquet (mặc định /work/data/processed/graph theo hợp đồng)")
    ap.add_argument("--lock-ttl-s", type=int, default=3600, help="hạn khóa job_locks (giây)")
    ap.add_argument("--dry-run", action="store_true", help="chỉ ghi staging và kiểm tra, không thay bản chính")
    ap.add_argument("--keep-staging", action="store_true", help="giữ staging sau khi nạp")
    ap.add_argument("--ack-rollback", metavar="RUN_ID",
                    help="đã lùi lại tay: đặt jobs.status = rolled_back, xóa __prev, thoát")
    args = ap.parse_args(argv)

    spark = build_session(JOB, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    run = JobRun(adm, JOB, args.lock_ttl_s)
    run_id = run.run_id
    staging = f"{COLL}{atomic.STAGING}_{run_id}"
    print(f"run_id={run_id} db={args.db} root={args.root_comid} min_uparea={args.min_uparea_km2} km²", flush=True)
    rep = Report()
    status = "failed"
    tmp_dirs: dict[str, str] = {}   # đích → thư mục tạm chưa công bố (xóa ở `finally`)
    try:
        try:
            run.acquire()
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        if args.ack_rollback:
            status = "ack"
            return atomic.ack_rollback(adm, COLL, args.ack_rollback, load_jobs(adm))
        run.start(db=args.db, root_comid=args.root_comid, min_uparea_km2=args.min_uparea_km2,
                  staging=[staging], replaced=[])
        if not args.dry_run:
            atomic.check_prev_safe(adm, COLL, run_id, load_jobs(adm))

        # ---- Đọc MERIT ---------------------------------------------------
        merit_rows = [r.asDict() for r in read_mongo(spark, "merit_river_reaches", MERIT, db=args.db).collect()]
        reaches = {int(r["COMID"]): r for r in merit_rows if r.get("COMID") is not None}
        print(f"merit_river_reaches: {len(reaches)} reach", flush=True)
        if args.root_comid not in reaches:
            print(f"Dừng: gốc {args.root_comid} không có trong merit_river_reaches.", flush=True)
            return 2

        tree = R.build_tree(reaches, args.root_comid)
        print(f"Cây lưu vực {args.root_comid}: {len(tree)} nút", flush=True)

        # ---- Kiểm tra topology -------------------------------------------
        topo_errs = R.check_tree(reaches, tree)
        conf = R.confluence_nodes(reaches, tree)
        print(f"Điểm hội tụ (nhiều reach đổ về cùng một nút): {len(conf)}", flush=True)
        rep.add("cây lưu vực: mỗi nút có đúng một hạ nguồn, không chu trình hướng xuôi",
                not topo_errs, "đạt" if not topo_errs else "; ".join(topo_errs[:3]))
        if topo_errs:
            return 1

        # ---- Trạm / hồ → COMID --------------------------------------------
        st_rows = [r.asDict() for r in read_mongo(spark, "stations", STATION, db=args.db).collect()]
        station_comids: dict[int, list[str]] = {}
        reservoir_comids: dict[int, list[str]] = {}
        for s in st_rows:
            m = s.get("merit")
            merit = m.asDict(recursive=True) if m is not None else {}
            comid = merit.get("COMID")
            if not comid:
                continue
            comid = int(comid)
            if s.get("kind") == "reservoir":
                reservoir_comids.setdefault(comid, []).append(s["code"])
            elif s.get("kind") in ("water_level", "sea_level"):
                station_comids.setdefault(comid, []).append(s["code"])
        in_tree_st = {c: v for c, v in station_comids.items() if c in tree}
        out_tree_st = {c: v for c, v in station_comids.items() if c not in tree}
        in_tree_res = {c: v for c, v in reservoir_comids.items() if c in tree}
        out_tree_res = {c: v for c, v in reservoir_comids.items() if c not in tree}
        print(f"Trạm trong cây: {sorted(v for vs in in_tree_st.values() for v in vs)}", flush=True)
        print(f"Hồ trong cây: {sorted(v for vs in in_tree_res.values() for v in vs)}", flush=True)
        for comid, codes in out_tree_st.items():
            print(f"[CẢNH BÁO] trạm {codes} gắn COMID {comid} KHÔNG nằm trong cây {args.root_comid}; "
                  f"không gắn vào đồ thị", flush=True)
        for comid, codes in out_tree_res.items():
            print(f"[CẢNH BÁO] hồ {codes} gắn COMID {comid} nằm ngoài lưu vực {args.root_comid}; "
                  f"không tạo cạnh ảo", flush=True)

        # ---- Tỉa ----------------------------------------------------------
        must_keep = set(in_tree_st) | set(in_tree_res)
        keep = R.keep_nodes(reaches, tree, args.min_uparea_km2, must_keep)
        print(f"Tỉa: {len(tree)} nút → {len(keep)} nút (ngưỡng uparea {args.min_uparea_km2} km²; "
              f"giữ thêm {len(must_keep)} nút trạm/hồ và đường đi từ chúng tới cửa sản)", flush=True)
        rep.add("mọi nút trạm/hồ còn lại sau tỉa", must_keep <= keep,
                f"{len(must_keep)} nút; còn {len(must_keep & keep)}")
        dropped_st = sorted(v for c, v in in_tree_st.items() if c not in keep)
        if dropped_st:
            print(f"[CẢNH BÁO] nút trạm bị tỉa (không nên xảy ra): {dropped_st}", flush=True)

        # ---- Dựng cạnh ----------------------------------------------------
        edges_all, levels = build_graph(reaches, tree, keep, station_comids, reservoir_comids, args.root_comid)
        n_down = sum(1 for e in edges_all if e["dir"] == "down")
        n_up = sum(1 for e in edges_all if e["dir"] == "up")
        n_virt = sum(1 for e in edges_all if e["virtual"])
        edges = edges_all
        print(f"Cạnh (sau tỉa): {len(edges)} (down {n_down}, up {n_up}); cạnh ảo {n_virt}", flush=True)
        print(f"Cấp topo: max down {max(levels['down'].values())}, max up {max(levels['up'].values())}", flush=True)

        keys = {(e["src"], e["dst"], e["dir"]) for e in edges}
        rep.add("không trùng khóa (src, dst, dir)", len(keys) == len(edges),
                f"{len(keys)} khóa / {len(edges)} cạnh")
        n_down_expect = len([n for n in keep if R.downstream_child(reaches, n, tree) is not None])
        rep.add("mỗi nút có đúng một hạ nguồn trong cây (một cạnh down)",
                n_down == n_down_expect, f"cạnh down {n_down}; nút có hạ nguồn trong cây {n_down_expect}")
        # cạnh `up` = chiều ngược của các cạnh `down` + cạnh ảo biển; số cạnh `up` thực chất
        # bằng số nhánh `up1..up4` còn lại sau tỉa + 1 cạnh ảo biển.
        n_up_expect = len([1 for n in keep for u in R.upstream_children(reaches, n, tree)
                           if u in keep]) + 1
        rep.add("số cạnh up = số nhánh up1..up4 còn lại + 1 cạnh ảo biển",
                n_up == n_up_expect, f"up {n_up}, kỳ vọng {n_up_expect}")

        # ---- Nối được mọi nút với cửa sản ---------------------------------
        reach_undown = [n for n in keep if R.downstream_child(reaches, n, tree) is None]
        rep.add("mọi nút còn lại nối được với cửa sản bằng cạnh down",
                len(reach_undown) == 1 and reach_undown[0] == args.root_comid,
                f"nút không có hạ nguồn trong cây: {reach_undown} (kỳ vọng đúng [{args.root_comid}])")
        # mọi nút phải đi ngược dòng (theo cạnh `up`) về được cửa sản → BFS trên đồ thị ngược
        # (cạnh `up` đi từ nút hạ nguồn tới nút thượng nguồn, nên BFS bắt đầu từ cửa sản).
        up_adj: dict[int, list[int]] = {}
        for e in edges:
            if e["dir"] == "up":
                up_adj.setdefault(e["src"], []).append(e["dst"])
        seen = {args.root_comid}
        stack = [args.root_comid]
        while stack:
            n = stack.pop()
            for nb in up_adj.get(n, []):
                if nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        orphan = sorted(set(keep) - seen)
        rep.add("mọi nút đi ngược dòng (cạnh up) về được cửa sản", not orphan,
                f"{len(seen & set(keep))}/{len(keep)} nút tới được cửa sản"
                + (f"; không tới: {orphan[:5]}" if orphan else ""))
        # nút ảo biển phải nối được vào cửa sản bằng cạnh `up`
        sea_ok = any(e["dir"] == "up" and e["src"] == SEA and e["dst"] == args.root_comid for e in edges)
        rep.add("nút ảo biển nối vào cửa sản bằng cạnh up ảo", sea_ok,
                f"SEA_VUNGTAU -> {args.root_comid}" if sea_ok else "thiếu cạnh ảo biển")

        # ---- Ghi Parquet vào thư mục tạm ----------------------------------
        # Ghi trong container driver (`local[*]`): executor của cụm không có mount repo.
        # Ghi `<đích>.tmp_<run_id>`; chỉ đổi tên thành đích sau khi kiểm tra đạt và đã thay
        # `graph_edges` (bản Parquet cũ giữ nguyên nếu job dừng giữa chừng / --dry-run).
        os.makedirs(args.out_dir, exist_ok=True)
        edges_df = spark.createDataFrame(edges, "src string, dst string, dir string, virtual boolean, "
                                                "length_km double, slope_raw double, slope_used double, "
                                                "source_comid string, node_kind string")
        nodes = node_rows(reaches, tree, keep, levels, station_comids, reservoir_comids)
        nodes_df = spark.createDataFrame(nodes, "comid string, kind string, order int, uparea_km2 double, "
                                                "topo_level_down int, topo_level_up int, "
                                                "station_codes array<string>, length_km double, "
                                                "slope_raw double, next_down_id string")
        run.hold()
        spark.conf.set("spark.sql.shuffle.partitions", "8")
        for name, df_, nparts in (("edges.parquet", edges_df, 4), ("nodes.parquet", nodes_df, 1)):
            dst = os.path.join(args.out_dir, name)
            for msg in outdir.recover(dst):
                print(f"[DỌN] {msg}", flush=True)
            tmp_dirs[dst] = outdir.tmp_path(dst, run_id)
            df_.coalesce(nparts).write.mode("errorifexists").parquet(tmp_dirs[dst])
        print(f"[PARQUET tạm] {args.out_dir}/edges.parquet ({len(edges)} dòng), nodes.parquet ({len(nodes)} dòng)"
              f" → *.tmp_{run_id}", flush=True)

        # ---- Ghi staging MongoDB ------------------------------------------
        # `src`/`dst`/`source_comid`: COMID dạng SỐ, TRỪ nút ảo biển là chuỗi `SEA_VUNGTAU`.
        #
        # Lưu ý kỹ thuật: KHÔNG thể dùng `F.when(cond, F.lit("SEA_VUNGTAU")).otherwise(cast_long)`
        # vì Spark thống nhất kiểu hai nhánh thành `string` (mọi COMID thành chuỗi); cũng
        # KHÔNG dùng `unionByName` (cũng thống nhất kiểu). Cách đúng: GHI HAI LẦN riêng —
        # (1) các cạnh thường với `src`/`dst`/`source_comid` kiểu `long`;
        # (2) các cạnh có nút ảo biển với kiểu `string` cho đúng các ô đó.
        # Validator của `graph_edges` cho phép cả hai kiểu (xem `ID_OR_NULL`).
        run.hold()
        adm.create(staging, validator=VALIDATOR, validationLevel="strict", validationAction="error")
        base_rows = []
        sea_rows = []
        for e in edges:
            common = (e["dir"], e["virtual"], e["length_km"], e["slope_raw"], e["slope_used"],
                      e["node_kind"])
            if e["src"] == SEA or e["dst"] == SEA or e["source_comid"] == SEA:
                sea_rows.append((e["src"], e["dst"], e["source_comid"]) + common)
            else:
                base_rows.append((int(e["src"]), int(e["dst"]), int(e["source_comid"])) + common)
        schema_num = ("src long, dst long, source_comid long, dir string, virtual boolean, "
                      "length_km double, slope_raw double, slope_used double, node_kind string")
        schema_str = ("src string, dst string, source_comid string, dir string, virtual boolean, "
                      "length_km double, slope_raw double, slope_used double, node_kind string")
        write_mongo(spark.createDataFrame(base_rows, schema_num), staging, args.db)
        if sea_rows:
            write_mongo(spark.createDataFrame(sea_rows, schema_str), staging, args.db)
        adm.create_indexes(staging, INDEXES)
        n_stg = adm.count(staging)
        print(f"[STAGING] {staging}: {n_stg} document", flush=True)
        rep.add("số document staging = số cạnh dựng được", n_stg == len(edges), f"{n_stg} / {len(edges)}")

        stats = {
            "root_comid": args.root_comid, "min_uparea_km2": args.min_uparea_km2,
            "tree_nodes": len(tree), "kept_nodes": len(keep),
            "edges": len(edges), "edges_down": n_down, "edges_up": n_up, "virtual_edges": n_virt,
            "max_topo_down": max(levels["down"].values()), "max_topo_up": max(levels["up"].values()),
            "slope_zero_in_tree": len([n for n in tree if not (reaches[n].get("slope") or 0)]),
            "slope_zero_dataset": len([n for n in reaches if not (reaches[n].get("slope") or 0)]),
            "stations_in_tree": sorted(v for vs in in_tree_st.values() for v in vs),
            "reservoirs_in_tree": sorted(v for vs in in_tree_res.values() for v in vs),
            "stations_out_tree": sorted(v for vs in out_tree_st.values() for v in vs),
            "reservoirs_out_tree": sorted(v for vs in out_tree_res.values() for v in vs),
        }
        run.update({"stats": stats, "checks": [{"name": n, "ok": ok} for n, ok, _ in rep.items]})
        print("Thống kê: " + json.dumps(stats, ensure_ascii=False), flush=True)

        if not rep.ok:
            print(f"Dừng: kiểm tra hỏng, giữ nguyên bản chính. Staging để xem: {staging}", flush=True)
            return 1
        if args.dry_run:
            print(f"--dry-run: không thay bản chính. Staging để xem: {staging}", flush=True)
            status = "dry_run"
            return 0

        # ---- Thay bản chính ------------------------------------------------
        run.hold()
        atomic.check_prev_safe(adm, COLL, run_id, load_jobs(adm))
        atomic.backup(adm, COLL)
        replaced: list[str] = []

        def done():
            replaced.append(COLL)
            run.update({"replaced": list(replaced)})

        try:
            run.update({"replacing": COLL})
            atomic.swap(adm, COLL, staging, len(edges), done)
            run.update({"replacing": None})
        except Exception:
            print(f"[LỖI GIỮA CHỪNG] đã thay: {replaced or 'không có'}.", flush=True)
            print("  " + atomic.rollback_help({"_id": run_id, "status": "failed", "replaced": replaced},
                                              [f"{COLL}{atomic.PREV}"]), flush=True)
            raise
        adm.drop(f"{COLL}{atomic.PREV}")
        run.hold()
        for dst in list(tmp_dirs):
            outdir.publish(tmp_dirs.pop(dst), dst, run_id)
            print(f"[PUBLISH] {dst}", flush=True)
        status = "ok"
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
        for t in tmp_dirs.values():
            outdir.discard(t)
        try:
            run.finish(None if status == "ack" else status)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()


if __name__ == "__main__":
    sys.exit(main())
