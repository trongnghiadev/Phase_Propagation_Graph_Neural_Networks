"""Test cần pyspark (chạy trong image `gis-spark:local`; tự bỏ qua khi không có pyspark):
`flatten_bulletins.apply_qc_flags` (gồm value_mismatch, cờ mồ côi) và `qc_tide_extremes.write_plan` khi xung đột.

    docker run --rm -v "<gốc repo>:/work:ro" gis-spark:local sh -c 'cd /work &&       PYTHONPATH=/opt/spark/python:$(ls /opt/spark/python/lib/py4j-*.zip) python3 -X utf8 pipelines/spark/tests/test_qc_job.py'
"""
from __future__ import annotations

import copy
import os
import sys
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import pyspark  # noqa: F401
    HAVE_SPARK = True
except ImportError:
    HAVE_SPARK = False

NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def ts(h):
    return datetime(2021, 4, 17, h, tzinfo=timezone.utc)


def ext(dt):
    return {"$date": dt.strftime("%Y-%m-%dT%H:%M:%SZ")}


@unittest.skipUnless(HAVE_SPARK, "cần pyspark")
class TestApplyQcFlags(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from pyspark.sql import SparkSession
        cls.spark = (SparkSession.builder.master("local[1]").appName("test_qc")
                     .config("spark.sql.session.timeZone", "UTC").config("spark.ui.enabled", "false").getOrCreate())

    @classmethod
    def tearDownClass(cls):
        cls.spark.stop()

    def test_ap_quyet_dinh_mismatch_va_mo_coi(self):
        from flatten import rules
        from flatten.flatten_bulletins import VALUE_MISMATCH, apply_qc_flags, qc_flags_df
        x = self.spark.createDataFrame(
            [("Biên Hòa", "high", "f", ts(2), False, "BH", 11.18, "ok"),
             ("Biên Hòa", "high", "f", ts(3), False, "BH", 1.20, "ok"),
             ("Biên Hòa", "low", "f", ts(4), False, "BH", None, "missing"),
             ("Biên Hòa", "low", "f", ts(5), False, "BH", -1.0, "ok"),
             ("Biên Hòa", "high", "f", ts(6), False, "BH", 1.5, "ok")],
            "station_name string, kind string, source_file string, ts timestamp, ts_placeholder boolean, "
            "station string, value double, qc string")
        flags = [
            {"_id": "rej", "station": "BH", "kind": "high", "ts": ext(ts(2)), "status": "rejected", "value": 11.18},
            {"_id": "cor", "station": "BH", "kind": "low", "ts": ext(ts(4)), "status": "corrected", "value": None,
             "corrected_value": -0.9},
            {"_id": "sus", "station": "BH", "kind": "high", "ts": ext(ts(3)), "status": "suspect", "value": 1.0},
            {"_id": "mis", "station": "BH", "kind": "low", "ts": ext(ts(5)), "status": "rejected", "value": -1.1},
            {"_id": "orph_auto", "station": "BH", "kind": "high", "ts": ext(ts(9)), "status": "suspect",
             "origin": "auto", "decided_at": None, "value": 1.0},
            {"_id": "orph_dec", "station": "BH", "kind": "high", "ts": ext(ts(10)), "status": "rejected",
             "origin": "auto", "decided_at": ext(NOW), "value": 1.0},
        ]
        out = {r["ts"].hour if r["ts"].tzinfo else r["ts"].replace(tzinfo=timezone.utc).hour: r.asDict()
               for r in apply_qc_flags(x, qc_flags_df(self.spark, flags)).collect()}
        self.assertEqual((out[2]["value"], out[2]["qc"], out[2]["raw_value"], out[2]["qc_flag"]),
                         (None, "rejected", 11.18, "rejected"))
        self.assertEqual((out[4]["value"], out[4]["qc"], out[4]["raw_value"], out[4]["raw_qc"]),
                         (-0.9, "corrected", None, "missing"))
        # suspect chưa quyết định: không so giá trị, không đổi value
        self.assertEqual((out[3]["value"], out[3]["qc"], out[3]["qc_flag"]), (1.2, "ok", "suspect"))
        # đã quyết định nhưng giá trị gốc đổi: không áp, đánh dấu
        self.assertEqual((out[5]["value"], out[5]["qc"], out[5]["qc_flag"]), (-1.0, "ok", VALUE_MISMATCH))
        self.assertEqual((out[6]["value"], out[6]["qc_flag"]), (1.5, None))
        self.assertEqual(len(out), 5)                           # cờ mồ côi không thêm dòng
        # cờ mồ côi: auto chưa quyết định → bỏ qua; đã quyết định → chặn
        keys = x.selectExpr("station as f_station", "kind as f_kind", "cast(ts as long) as f_t")
        unmatched = {r["f_id"] for r in qc_flags_df(self.spark, flags)
                     .join(keys, ["f_station", "f_kind", "f_t"], "left_anti").collect()}
        self.assertEqual(unmatched, {"orph_auto", "orph_dec"})
        skip, block = rules.classify_unmatched(flags, unmatched)
        self.assertEqual(([f["_id"] for f in skip], [f["_id"] for f in block]), (["orph_auto"], ["orph_dec"]))

    def test_g0_so_voi_decided_value_khi_nguoi_xem_quyet_dinh(self):
        """Đường thoát cờ `value_mismatch`: sau khi người xem quyết định lại (ghi `decided_value`), G0 áp quyết định
        thay vì gắn `value_mismatch` lần nữa — kể cả khi `tide_extremes` chưa kịp cập nhật (giá trị đọc được
        vẫn bằng `value` lúc gắn cờ)."""
        from flatten.flatten_bulletins import VALUE_MISMATCH, apply_qc_flags, qc_flags_df
        x = self.spark.createDataFrame(
            [("Biên Hòa", "high", "f", ts(2), False, "BH", 1.20, "ok")],
            "station_name string, kind string, source_file string, ts timestamp, ts_placeholder boolean, "
            "station string, value double, qc string")
        # Cờ đã quyết định lại: `value` = giá trị lúc gắn cờ (1.1), `decided_value` = giá trị người xem đã xem (1.2),
        # `current_value` = giá trị đã thấy (1.2). G0 đọc được 1.20 == decided_value → áp quyết định, KHÔNG mismatch.
        flags = [{"_id": "esc", "station": "BH", "kind": "high", "ts": ext(ts(2)), "status": "confirmed_ok",
                  "value": 1.1, "decided_value": 1.2, "current_value": 1.2, "value_mismatch": False}]
        out = apply_qc_flags(x, qc_flags_df(self.spark, flags)).collect()
        self.assertEqual(len(out), 1)
        self.assertEqual((out[0]["value"], out[0]["qc"], out[0]["qc_flag"], out[0]["raw_value"]),
                         (1.2, "ok", "confirmed_ok", 1.2))
        self.assertNotEqual(out[0]["qc_flag"], VALUE_MISMATCH)

    def test_g0_van_gan_mismatch_khi_gia_tri_thuc_su_doi(self):
        """Sau khi thoát, nếu giá trị gốc đổi tiếp thì `value_mismatch` được gắn lại đúng như mong đợi."""
        from flatten.flatten_bulletins import VALUE_MISMATCH, apply_qc_flags, qc_flags_df
        x = self.spark.createDataFrame(
            [("Biên Hòa", "high", "f", ts(2), False, "BH", 9.99, "ok")],
            "station_name string, kind string, source_file string, ts timestamp, ts_placeholder boolean, "
            "station string, value double, qc string")
        flags = [{"_id": "esc2", "station": "BH", "kind": "high", "ts": ext(ts(2)), "status": "confirmed_ok",
                  "value": 1.1, "decided_value": 1.2, "current_value": 1.2, "value_mismatch": False}]
        out = apply_qc_flags(x, qc_flags_df(self.spark, flags)).collect()
        self.assertEqual(len(out), 1)
        self.assertEqual(out[0]["qc_flag"], VALUE_MISMATCH)
        self.assertEqual(out[0]["value"], 9.99)


class FakeAdm:
    """Mô phỏng tối thiểu lệnh MongoDB mà `write_plan` dùng; `hook(cmd, store)` chạy trước mỗi lệnh."""

    def __init__(self, docs, hook=None):
        self.store = {d["_id"]: copy.deepcopy(d) for d in docs}
        self.hook = hook
        self.calls = []

    def command(self, cmd):
        name = next(iter(cmd))          # tên lệnh = khóa đầu tiên
        self.calls.append(name)
        if self.hook:
            self.hook(cmd, self.store)
        if name == "insert":
            d = cmd["documents"][0]
            if d["_id"] in self.store:
                return {"writeErrors": [{"code": 11000}]}
            self.store[d["_id"]] = d
            return {"n": 1}
        if name == "update":
            u = cmd["updates"][0]
            self.store[u["q"]["_id"]].update(u["u"]["$set"])
            return {"n": 1}
        if name == "findAndModify":
            q = cmd["query"]
            d = self.store.get(q["_id"])
            if d is None or d.get("version") != q["version"]:
                return {"value": None}
            d.update(cmd["update"]["$set"])
            d["version"] += cmd["update"]["$inc"]["version"]
            return {"value": d}
        if name == "delete":
            q = cmd["deletes"][0]["q"]
            d = self.store.get(q["_id"])
            if d and d.get("version") == q["version"] and d.get("status") == q["status"] and d.get("decided_at") is None:
                del self.store[q["_id"]]
                return {"n": 1}
            return {"n": 0}
        raise ValueError(cmd)

    def find_one(self, name, q):
        d = self.store.get(q["_id"])
        return copy.deepcopy(d) if d else None


@unittest.skipUnless(HAVE_SPARK, "cần pyspark")
class TestWritePlan(unittest.TestCase):
    def setUp(self):
        from vngis import qc_rules as R
        self.R = R
        self.hit = {"station": "BH", "kind": "high", "ts": int(ts(2).timestamp()), "ts_placeholder": False,
                    "value": 11.18, "source_file": "f", "rules": [{"rule": "R1", "detail": "d", "metric": {}}]}
        self.fid = R.flag_id("BH", "high", self.hit["ts"])

    def test_xung_dot_khi_cap_nhat_thi_doc_lai_va_khong_de_quyet_dinh(self):
        from qc.qc_tide_extremes import write_plan
        doc = self.R.new_flag_doc(self.hit, "run1", NOW, 1)
        fired = []

        def hook(cmd, store):   # người ghi quyết định ngay trước lệnh cập nhật đầu tiên của job
            if "findAndModify" in cmd and not fired:
                fired.append(1)
                store[self.fid].update({"status": "rejected", "reason": "r", "decided_by": "m",
                                        "decided_at": ext(NOW)})
                store[self.fid]["version"] += 1
        adm = FakeAdm([doc], hook)
        new_hit = {**self.hit, "rules": [{"rule": "R1", "detail": "d2", "metric": {}}]}
        done = write_plan(adm, {self.fid: doc}, {self.fid: new_hit}, "run2", NOW, 1, lambda: None)
        d = adm.store[self.fid]
        self.assertEqual(done["conflict_retry"], 1)
        self.assertEqual((d["status"], d["decided_by"], d["rules"][0]["detail"], d["version"]), ("rejected", "m", "d2", 3))

    def test_xoa_bi_xung_dot_thanh_auto_cleared(self):
        from qc.qc_tide_extremes import write_plan
        doc = self.R.new_flag_doc(self.hit, "run1", NOW, 1)

        def hook(cmd, store):
            if "delete" in cmd:
                store[self.fid].update({"status": "confirmed_ok", "decided_at": ext(NOW)})
                store[self.fid]["version"] += 1
        adm = FakeAdm([doc], hook)
        done = write_plan(adm, {self.fid: doc}, {}, "run2", NOW, 1, lambda: None)
        d = adm.store[self.fid]
        self.assertEqual((done["conflict_retry"], d["status"], d["auto_cleared"]), (1, "confirmed_ok", True))

    def test_xoa_co_mo_coi_chua_quyet_dinh(self):
        from qc.qc_tide_extremes import write_plan
        doc = self.R.new_flag_doc(self.hit, "run1", NOW, 1)
        adm = FakeAdm([doc])
        done = write_plan(adm, {self.fid: doc}, {}, "run2", NOW, 1, lambda: None)
        self.assertEqual((done["delete"], self.fid in adm.store), (1, False))

    def test_insert_trung_khoa_thi_gop(self):
        from qc.qc_tide_extremes import write_plan
        manual = {**self.R.new_flag_doc(self.hit, "r", NOW, 1), "origin": "manual", "rules": [],
                  "decided_at": ext(NOW), "reason": "K5"}

        def hook(cmd, store):   # CLI tạo cờ thủ công ngay trước insert của job
            if "insert" in cmd and self.fid not in store:
                store[self.fid] = copy.deepcopy(manual)
        adm = FakeAdm([], hook)
        done = write_plan(adm, {}, {self.fid: self.hit}, "run2", NOW, 1, lambda: None)
        d = adm.store[self.fid]
        self.assertEqual((done["conflict_retry"], d["origin"], d["reason"], d["rules"][0]["rule"]),
                         (1, "manual", "K5", "R1"))


if __name__ == "__main__":
    unittest.main()
