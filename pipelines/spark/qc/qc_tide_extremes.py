"""QC tự động cho `tide_extremes` (GĐ2.3): quy tắc R1–R3 → `qc_flags`; báo cáo `summary.csv`, `exceedance.csv`.

    spark-submit qc_tide_extremes.py [mongodb_uri] [--db vietnam_gis] [--params JSON] [--lock-ttl-s 3600]
                                     [--report-dir DIR] [--dry-run]

Quy tắc và cách gộp vào `qc_flags` nằm trong `vngis.qc_rules` (thuần Python, có test); job chỉ đọc dữ liệu, tính
thống kê theo trạm và chạy các hàm đó trên executor (gửi `vngis` bằng `addPyFile`). Giá trị xét là giá trị GỐC
(`raw_value`/`raw_qc` do G0 ghi; bản `tide_extremes` cũ chưa có thì dùng `value`/`qc`), nên quyết định của người
(G0 đã áp) không làm cờ tự biến mất.

Không bao giờ ghi đè các field quyết định (`status`, `corrected_value`, `reason`, `decided_by`, `decided_at`).
Mỗi thay đổi có ý nghĩa tăng `version` (điều kiện `{_id, version}`; xung đột với người đang ghi quyết định → đọc
lại và tính lại, tối đa 3 lần).

Đỉnh Vũng Tàu có quyết định `rejected` bị loại khỏi tập tham chiếu R3 (các trạm vẫn đánh giá trên giá trị gốc).

Mã thoát: 0 ok (hoặc --dry-run), 1 kiểm tra hỏng, 3 job này hoặc G0 đang chạy (khóa `job_locks`), 4 G0 còn dở
thay thế (`<tên>__prev` còn, hoặc run G0 failed/running đã thay một phần sau lần ok gần nhất; lùi lại theo hướng dẫn
của G0 trước), 5 mất khóa, 6 lỗi không lường trước. `jobs.status`: running → ok / dry_run / failed / blocked.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pyspark.sql import functions as F  # noqa: E402
from pyspark.sql.types import (BooleanType, DoubleType, StringType, StructField, StructType,  # noqa: E402
                               TimestampType)

import common  # noqa: E402,F401  (bảo đảm import được vngis)
from common.jobrun import JobRun  # noqa: E402
from common.pyfiles import ship_vngis  # noqa: E402
from common.session import (SINGLE_PARTITIONER, JobLockError, LockLostError, MongoAdmin, bson_date,  # noqa: E402
                            build_session, env_db, env_uri, read_mongo)
from flatten import rules as frules  # noqa: E402
from flatten.flatten_bulletins import NAMES as G0_NAMES, PREV as G0_PREV, load_jobs as g0_jobs  # noqa: E402
from flatten.schemas import STATION  # noqa: E402
from qc import report  # noqa: E402
from vngis import qc_rules as R  # noqa: E402

JOB = "qc_tide_extremes"
G0_JOB = "g0_flatten_bulletins"
MAX_RETRY = 3

TIDE = StructType([
    StructField("ts", TimestampType()), StructField("station", StringType()), StructField("kind", StringType()),
    StructField("value", DoubleType()), StructField("qc", StringType()), StructField("ts_placeholder", BooleanType()),
    StructField("source_file", StringType()),
])
# Giá trị/qc GỐC: raw_value có thể là null thật (ô trống), nên phân biệt "thiếu field" bằng $type.
TIDE_PIPELINE = [{"$project": {
    "_id": 0, "ts": 1, "station": "$meta.station", "kind": "$meta.kind", "ts_placeholder": 1, "source_file": 1,
    "value": {"$cond": [{"$eq": [{"$type": "$raw_value"}, "missing"]}, "$value", "$raw_value"]},
    "qc": {"$ifNull": ["$raw_qc", "$qc"]},
}}]
ALARM_PIPELINE = [{"$match": {"source.is_primary": True, "bulletin_type": "tide_forecast_5day"}},
                  {"$project": {"_id": 0, "source_file": 1, "alarm_levels": 1}}]


# ---------------------------------------------------------------------------
# JSON mở rộng
# ---------------------------------------------------------------------------
def to_ext(x):
    """datetime → {"$date"}; đệ quy cho dict/list (để gửi qua Document.parse)."""
    if isinstance(x, datetime):
        return bson_date(x)
    if isinstance(x, dict):
        return {k: to_ext(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [to_ext(v) for v in x]
    return x


def from_date(v) -> datetime | None:
    if v is None:
        return None
    # `MongoAdmin.command` đã giải mã JSON mở rộng → ngày giờ BSON về kiểu `datetime`.
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    s = v["$date"] if isinstance(v, dict) else v
    if isinstance(s, dict):
        return datetime.fromtimestamp(int(s["$numberLong"]) / 1000, tz=timezone.utc)
    return frules.parse_iso_utc(s)


def load_flags(adm: MongoAdmin) -> dict[str, dict]:
    if not adm.exists(R.FLAGS):
        return {}
    docs = adm.aggregate(R.FLAGS, [{"$match": {"collection": R.COLLECTION}}])
    return {d["_id"]: d for d in docs}


def ensure_flags_collection(adm: MongoAdmin) -> None:
    if adm.exists(R.FLAGS):
        adm.command({"collMod": R.FLAGS, "validator": R.QC_FLAGS_VALIDATOR, "validationLevel": "moderate",
                     "validationAction": "error"})
    else:
        adm.create(R.FLAGS, validator=R.QC_FLAGS_VALIDATOR, validationLevel="moderate", validationAction="error")
    adm.create_indexes(R.FLAGS, R.QC_FLAGS_INDEXES)


# ---------------------------------------------------------------------------
# Ghi kế hoạch vào qc_flags
# ---------------------------------------------------------------------------
def apply_op(adm: MongoAdmin, op: dict) -> bool:
    """True nếu áp được; False nếu xung đột (cờ đã đổi version / đã có người tạo)."""
    wc = {"w": "majority"}
    if op["op"] == "insert":
        try:
            res = adm.command({"insert": R.FLAGS, "documents": [to_ext(op["doc"])], "writeConcern": wc})
        except Exception as e:  # noqa: BLE001
            if "11000" in str(e):
                return False
            raise
        if res.get("writeErrors"):
            if any(w.get("code") == 11000 for w in res["writeErrors"]):
                return False
            raise RuntimeError(f"insert {op['doc']['_id']}: {res['writeErrors']}")
        return True
    if op["op"] == "touch":
        adm.command({"update": R.FLAGS, "updates": [{"q": {"_id": op["id"]}, "u": {"$set": op["set"]}}],
                     "writeConcern": wc})
        return True
    if op["op"] == "update":
        res = adm.command({"findAndModify": R.FLAGS, "query": {"_id": op["id"], "version": op["version"]},
                           "update": {"$set": to_ext(op["set"]), "$inc": {"version": 1}}, "new": True,
                           "writeConcern": wc})
        return bool(res.get("value"))
    if op["op"] == "delete":
        res = adm.command({"delete": R.FLAGS, "deletes": [{"q": {"_id": op["id"], "version": op["version"],
                                                                  "status": "suspect", "decided_at": None},
                                                            "limit": 1}], "writeConcern": wc})
        return int(res.get("n", 0)) == 1
    raise ValueError(op)


def write_plan(adm: MongoAdmin, existing: dict[str, dict], hits: dict[str, dict], run_id: str, now: datetime,
               rules_version: int, hold) -> Counter:
    ops = R.plan_updates(existing, hits, run_id, now, rules_version)
    done: Counter = Counter()
    for i, op in enumerate(ops):
        if i % 500 == 0:
            hold()
        fid = op["doc"]["_id"] if op["op"] == "insert" else op["id"]
        for attempt in range(MAX_RETRY + 1):
            if op is None or apply_op(adm, op):
                done[op["op"] if op else "noop"] += 1
                break
            if attempt == MAX_RETRY:
                raise RuntimeError(f"{fid}: xung đột version {MAX_RETRY + 1} lần liên tiếp")
            done["conflict_retry"] += 1
            cur = adm.find_one(R.FLAGS, {"_id": fid})
            op = R.plan_one(cur, hits.get(fid), run_id, now, rules_version)
    return done


# ---------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("uri", nargs="?", default=env_uri(), help="URI MongoDB (mặc định biến MONGO_URI)")
    ap.add_argument("--db", default=env_db())
    ap.add_argument("--params", default=None,
                    help="JSON ghi đè tham số (mặc định vngis.qc_rules.DEFAULT_PARAMS = config/default.yaml mục qc)")
    ap.add_argument("--lock-ttl-s", type=int, default=3600)
    ap.add_argument("--report-dir", default=None,
                    help="thư mục results/qc (ghi summary.csv, exceedance.csv, runs/<run_id>/); bỏ trống = không ghi")
    ap.add_argument("--dry-run", action="store_true", help="tính cờ và in thống kê, không ghi qc_flags/báo cáo")
    args = ap.parse_args(argv)
    params = R.params_with(json.loads(args.params) if args.params else None)
    p1, p3 = params["r1"], params["r3"]
    ref_station = params["reference_station"]

    spark = build_session(JOB, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    run = JobRun(adm, JOB, args.lock_ttl_s)
    print(f"run_id={run.run_id} db={args.db} params={json.dumps(params, ensure_ascii=False)}", flush=True)
    status = None
    try:
        try:
            run.acquire()
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        other = run.other_running(G0_JOB)
        if other:
            status = "failed"
            run.start(db=args.db, params=params, dry_run=args.dry_run, note="G0 đang chạy")
            print(f"Dừng: G0 đang chạy (khóa {other}); chạy lại sau.", flush=True)
            return 3
        status = "failed"
        run.start(db=args.db, params=params, dry_run=args.dry_run)
        why = frules.g0_unsettled(g0_jobs(adm), [n + G0_PREV for n in G0_NAMES if adm.exists(n + G0_PREV)])
        if why:
            status = "blocked"
            print(f"Dừng: G0 còn dở thay thế: {why}. Xử lý theo hướng dẫn của G0 (lùi lại, --ack-rollback) rồi chạy "
                  f"lại G0 trước QC.", flush=True)
            return 4
        ship_vngis(spark)

        # ---- Đọc phần tử (giá trị gốc) --------------------------------------
        tide = (read_mongo(spark, R.COLLECTION, TIDE, TIDE_PIPELINE, args.db, {"partitioner": SINGLE_PARTITIONER})
                .withColumn("t", F.col("ts").cast("long")).repartition(8, "station").cache())
        n_read = tide.count()
        n_coll = adm.count(R.COLLECTION)
        if n_read != n_coll:
            print(f"Dừng: đọc {n_read} phần tử, collection có {n_coll}.", flush=True)
            return 1
        stations = sorted(r[0] for r in tide.select("station").distinct().collect())
        if ref_station not in stations:
            print(f"Dừng: không có trạm tham chiếu {ref_station} trong {R.COLLECTION}.", flush=True)
            return 1

        # ---- R1: phân vị theo (trạm, loại) ----------------------------------
        pct = (tide.filter(F.col("value").isNotNull()).groupBy("station", "kind")
                   .agg(F.expr(f"percentile(value, array({p1['p_low']}, {p1['p_high']}))").alias("p"),
                        F.count("*").alias("n")).collect())
        bounds = {(r["station"], r["kind"]): R.r1_bounds(r["station"], r["p"][0], r["p"][1], p1) for r in pct}

        # ---- R3: đỉnh tham chiếu, thống kê độ trễ theo trạm (executor) -------
        ref_rows = (tide.filter((F.col("station") == ref_station) & (F.col("kind") == "high"))
                        .select("station", "kind", F.col("t").alias("ts"), "ts_placeholder", "value", "qc").collect())
        rejected_ref = [int(from_date(f["ts"]).timestamp()) for f in load_flags(adm).values()
                        if f.get("station") == ref_station and f.get("kind") == "high"
                        and f.get("status") == "rejected" and not f.get("retired")]
        ref, ref_days = R.reference_peaks([r.asDict() for r in ref_rows], ref_station, rejected_ref)
        sc = spark.sparkContext
        ref_b, days_b = sc.broadcast(ref), sc.broadcast(ref_days)
        cand = (tide.filter((F.col("station") != ref_station) & (F.col("kind") == "high")
                            & ~F.col("ts_placeholder") & F.col("value").isNotNull() & F.col("qc").isin(*R.GOOD_QC))
                    .select("station", "t").rdd.map(lambda r: (r[0], r[1])))
        lag_stats = dict(cand.groupByKey()
                         .map(lambda kv: (kv[0], R.build_lag_stats([(kv[0], t) for t in kv[1]], ref_b.value, p3,
                                                                   days_b.value)[kv[0]]))
                         .collect())

        # ---- Đánh giá theo (trạm, ngày quan trắc = bản tin) trên executor ----
        ctx_b = sc.broadcast({"params": params, "bounds": bounds, "lag_stats": lag_stats, "ref": ref,
                              "ref_days": ref_days})
        rows = tide.select("station", "kind", F.col("t").alias("ts"), "ts_placeholder", "value", "qc", "source_file")
        hit_list = (rows.rdd.map(lambda r: ((r["station"], r["source_file"]), r.asDict()))
                        .groupByKey()
                        .flatMap(lambda kv: R.evaluate_group(list(kv[1]), ctx_b.value))
                        .collect())
        hits = {R.flag_id(h["station"], h["kind"], h["ts"]): h for h in hit_list}
        by_rule = Counter((h["station"], r["rule"]) for h in hit_list for r in h["rules"])
        print(f"Cờ tự động: {len(hits)} phần tử", flush=True)
        for st in stations:
            print(f"    {st:<12} " + "  ".join(f"{rule}={by_rule.get((st, rule), 0)}" for rule in R.RULES), flush=True)
        for (st, kind), b in sorted(bounds.items()):
            print(f"    R1 {st:<12} {kind:<4} [{b['lo']}, {b['hi']}] (p {b['p_lo']}..{b['p_hi']}; "
                  f"vật lý {b['phys_lo']}..{b['phys_hi']})", flush=True)
        for st, s in sorted(lag_stats.items()):
            print(f"    R3 {st:<12} n={s['n']} trung vị {s['median']} h IQR {s['iqr']} h "
                  f"giới hạn ±{R.r3_limit(s, p3)} h", flush=True)
        stats = {"read": n_read, "hits": len(hits), "ref_peaks": len(ref), "ref_excluded_rejected": len(rejected_ref),
                 "by_station_rule": [{"station": s, "rule": r, "n": n} for (s, r), n in sorted(by_rule.items())],
                 "r1_bounds": [{"station": s, "kind": k, **b} for (s, k), b in sorted(bounds.items())],
                 "r3_lag_stats": [{"station": s, **v} for s, v in sorted(lag_stats.items())]}
        run.update({"stats": stats})

        if args.dry_run:
            existing = load_flags(adm)
            ops = Counter(o["op"] for o in R.plan_updates(existing, hits, run.run_id, datetime.now(timezone.utc),
                                                          params["rules_version"]))
            print(f"--dry-run: không ghi qc_flags; kế hoạch {dict(ops)}", flush=True)
            status = "dry_run"
            return 0

        # ---- Gộp vào qc_flags ----------------------------------------------
        run.hold()
        ensure_flags_collection(adm)
        now = datetime.now(timezone.utc)
        done = write_plan(adm, load_flags(adm), hits, run.run_id, now, params["rules_version"], run.hold)
        print(f"qc_flags: {dict(done)}", flush=True)
        flags = list(load_flags(adm).values())
        for f in flags:
            f["ts"] = from_date(f["ts"])
        by_status = Counter((f["status"], bool(f.get("auto_cleared"))) for f in flags)
        print(f"qc_flags theo (status, auto_cleared): {dict(by_status)}", flush=True)
        retired = {f["_id"] for f in flags if f.get("retired")}
        active_auto = sum(1 for f in flags if f.get("rules") and not f.get("auto_cleared") and not f.get("retired"))
        want = len(set(hits) - retired)          # cờ retired: job không đụng tới, không tính
        ok = active_auto == want
        print(f"[{'OK ' if ok else 'LỖI'}] cờ có quy tắc đang kích hoạt = số phần tử bị quy tắc gắn: "
              f"{active_auto} / {want} (retired bỏ qua: {len(retired)})", flush=True)
        run.update({"ops": dict(done), "flags": [{"status": s, "auto_cleared": a, "n": n}
                                                 for (s, a), n in sorted(by_status.items())]})
        if not ok:
            return 1

        # ---- Báo cáo ---------------------------------------------------------
        if args.report_dir:
            decided = [(f["station"], f["kind"], int(f["ts"].timestamp()), f["status"], f.get("corrected_value"))
                       for f in flags if f["status"] in ("rejected", "corrected") and R.is_active(f)]
            dec = spark.createDataFrame(decided, "f_station string, f_kind string, f_t long, f_status string, "
                                                 "f_cv double")
            highs = tide.filter(F.col("kind") == "high")
            j = highs.join(F.broadcast(dec), (highs["station"] == dec["f_station"]) & (highs["kind"] == dec["f_kind"])
                           & (highs["t"] == dec["f_t"]), "left")
            eff = (F.when(F.col("f_status") == "rejected", F.lit(None).cast("double"))
                    .when(F.col("f_status") == "corrected", F.col("f_cv")).otherwise(F.col("value")))
            daily = (j.withColumn("eff", eff).groupBy("station", "source_file")
                      .agg(F.max("eff").alias("peak"), F.min(F.date_format("ts", "yyyy-MM-dd")).alias("day"))
                      .collect())
            st_rows = [r.asDict() for r in read_mongo(spark, "stations", STATION, db=args.db).collect()]
            name_to_code = {lbl: code for g, lbl, code in frules.station_lookup_rows(st_rows) if g == "tide"}
            alarm = {}
            for d in adm.aggregate("hydromet_bulletins", ALARM_PIPELINE):
                a = d.get("alarm_levels") or {}
                codes = {name_to_code.get(n) for n in (a.get("stations") or [])}
                alarm[d["source_file"]] = (codes, [a.get("level_1_m"), a.get("level_2_m"), a.get("level_3_m")])
            days = []
            for r in daily:
                codes, levels = alarm.get(r["source_file"], (set(), None))
                days.append({"station": r["station"], "day": r["day"], "peak": r["peak"],
                             "levels": levels if r["station"] in codes else None})
            out = args.report_dir
            report.write_csv(os.path.join(out, "summary.csv"), report.SUMMARY_HEADER, report.summary_rows(flags))
            report.write_csv(os.path.join(out, "exceedance.csv"), report.EXCEEDANCE_HEADER,
                             report.exceedance_rows(days))
            rd = os.path.join(out, "runs", run.run_id)
            report.write_csv(os.path.join(rd, "flags.csv"),
                             ["id", "station", "kind", "ts", "value", "rules", "status", "corrected_value",
                              "auto_cleared", "origin", "source_file"],
                             [[f["_id"], f["station"], f["kind"], R.iso_utc(f["ts"]), f.get("value"),
                               "+".join(r["rule"] for r in f.get("rules") or []), f["status"],
                               f.get("corrected_value"), bool(f.get("auto_cleared")), f.get("origin"),
                               f.get("source_file")] for f in sorted(flags, key=lambda x: x["_id"])])
            with open(os.path.join(rd, "run.json"), "w", encoding="utf-8", newline="\n") as fh:
                json.dump({"run_id": run.run_id, "params": params, "stats": stats, "ops": dict(done)}, fh,
                          ensure_ascii=False, indent=1, default=str)
                fh.write("\n")
            print(f"[BÁO CÁO] {out}/summary.csv, exceedance.csv, runs/{run.run_id}/", flush=True)
        status = "ok"
        return 0
    except LockLostError as e:
        status = "failed"
        print(f"Dừng: {e}", flush=True)
        return 5
    except Exception:  # noqa: BLE001
        status = "failed"
        traceback.print_exc()
        print("Dừng: lỗi không lường trước (mã thoát 6).", flush=True)
        return 6
    finally:
        try:
            run.finish(status)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()


if __name__ == "__main__":
    sys.exit(main())
