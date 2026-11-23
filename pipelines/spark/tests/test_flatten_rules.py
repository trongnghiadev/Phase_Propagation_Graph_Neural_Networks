"""Test quy tắc thuần Python của G0 (không cần Spark).

    python -X utf8 pipelines/spark/tests/test_flatten_rules.py
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flatten import rules  # noqa: E402  (flatten/__init__ bảo đảm import được vngis)
from vngis.text import norm_name  # noqa: E402


def d(y, m, dd):
    """00:00 giờ Việt Nam của ngày y-m-dd, dạng UTC."""
    return datetime(y, m, dd, tzinfo=timezone.utc) - timedelta(hours=7)


def daily(res, label, d0, d1):
    """Mỗi ngày quan trắc từ d0 đến d1 (bao gồm) một dòng {reservoir, label, obs}."""
    return [{"reservoir": res, "label": label, "obs": d0 + timedelta(days=i)} for i in range((d1 - d0).days + 1)]


TIMELINE = [
    {"era": rules.ERA_OLD, "code": 75, "name_norm": "dong nai", "aliases_norm": []},
    {"era": rules.ERA_OLD, "code": 70, "name_norm": "binh phuoc", "aliases_norm": []},
    {"era": rules.ERA_OLD, "code": 72, "name_norm": "tay ninh", "aliases_norm": []},
    {"era": rules.ERA_OLD, "code": 79, "name_norm": "ho chi minh", "aliases_norm": ["tphcm"]},
    {"era": rules.ERA_NEW, "code": 75, "name_norm": "dong nai", "aliases_norm": []},
    {"era": rules.ERA_NEW, "code": 80, "name_norm": "tay ninh", "aliases_norm": []},
    {"era": rules.ERA_NEW, "code": 79, "name_norm": "ho chi minh", "aliases_norm": ["tphcm", "tp.hcm"]},
]


def range_for(ranges, res, label, when):
    hits = [g for g in ranges if g["reservoir"] == res and g["label"] == label
            and g["obs_from"] <= when <= g["obs_to"]]
    assert len(hits) == 1, hits
    return hits[0]


class TestText(unittest.TestCase):
    def test_norm_dung_vngis(self):
        self.assertEqual(norm_name("Thành phố Hồ Chí Minh"), "ho chi minh")
        self.assertEqual(norm_name("TPHCM"), "tphcm")
        self.assertEqual(norm_name("Tỉnh Bà Rịa - Vũng Tàu"), "ba ria vung tau")


class TestProvinceRanges(unittest.TestCase):
    """Theo ngày quan trắc; khoảng obs_from..obs_to bao gồm hai đầu."""

    def setUp(self):
        rows = (daily("Dầu Tiếng", "Tây Ninh", d(2025, 6, 1), d(2025, 7, 6))
                + daily("Dầu Tiếng", "TPHCM", d(2025, 7, 7), d(2025, 8, 1))
                + daily("Thác Mơ", "Bình Phước", d(2025, 6, 1), d(2025, 7, 6))
                + daily("Thác Mơ", "Đồng Nai", d(2025, 7, 7), d(2025, 8, 1))
                + daily("Trị An", "Đồng Nai", d(2025, 6, 1), d(2025, 8, 1))
                + daily("Sông Ray", "TPHCM", d(2026, 1, 4), d(2026, 2, 1)))
        self.ranges, self.runs, self.unmatched = rules.province_ranges(rows, TIMELINE)

    def test_no_unmatched(self):
        self.assertEqual(self.unmatched, [])

    def test_khoang_khong_chong_nhau(self):
        for res, rs in self.runs.items():
            for a, b in zip(rs, rs[1:]):
                self.assertLess(a.last, b.first, res)

    def test_doi_ten_cham(self):
        g = range_for(self.ranges, "Dầu Tiếng", "Tây Ninh", d(2025, 7, 3))
        self.assertEqual((g["era"], g["code"], g["note"]), (rules.ERA_OLD, 72, rules.NOTE_LAG))
        g = range_for(self.ranges, "Thác Mơ", "Bình Phước", d(2025, 7, 6))
        self.assertEqual((g["era"], g["code"]), (rules.ERA_OLD, 70))
        g = range_for(self.ranges, "Dầu Tiếng", "TPHCM", d(2025, 7, 7))
        self.assertEqual((g["era"], g["code"], g["note"]), (rules.ERA_NEW, 79, None))

    def test_moc_theo_ngay_quan_trac(self):
        self.assertEqual(d(2025, 7, 1), rules.ERA_CUTOFF)
        self.assertEqual(range_for(self.ranges, "Trị An", "Đồng Nai", d(2025, 6, 30))["era"], rules.ERA_OLD)
        g = range_for(self.ranges, "Trị An", "Đồng Nai", d(2025, 7, 1))
        self.assertEqual((g["era"], g["code"], g["note"]), (rules.ERA_NEW, 75, None))

    def test_ngoai_gioi_han_khong_ve_thoi_ky_cu(self):
        rows = (daily("X", "Bình Phước", d(2025, 6, 1), d(2025, 6, 30))
                + daily("X", "Bình Phước", d(2025, 7, 1), d(2025, 9, 1)) + daily("X", "Đồng Nai", d(2025, 9, 2), d(2025, 9, 5)))
        _, _, unmatched = rules.province_ranges(rows, TIMELINE)
        self.assertEqual(len(unmatched), 1)
        self.assertEqual(unmatched[0]["label"], "Bình Phước")

    def test_nhan_none(self):
        ranges, _, unmatched = rules.province_ranges(daily("X", None, d(2020, 1, 1), d(2020, 1, 2)), TIMELINE)
        self.assertEqual(len(unmatched), 1)
        self.assertIsNone(ranges[0]["label"])
        self.assertIsNone(ranges[0]["code"])
        self.assertTrue(unmatched[0]["empty"] and ranges[0]["empty"])     # không chặn job, province = null

    def test_nhan_none_xen_giua_khong_lam_dut_doan(self):
        rows = (daily("X", "Tây Ninh", d(2025, 6, 30), d(2025, 7, 1)) + daily("X", None, d(2025, 7, 2), d(2025, 7, 2))
                + daily("X", "Tây Ninh", d(2025, 7, 3), d(2025, 7, 3)) + daily("X", "TPHCM", d(2025, 7, 7), d(2025, 7, 9)))
        ranges, _, unmatched = rules.province_ranges(rows, TIMELINE)
        for day in (d(2025, 7, 1), d(2025, 7, 3)):
            g = range_for(ranges, "X", "Tây Ninh", day)
            self.assertEqual((g["era"], g["code"]), (rules.ERA_OLD, 72))
        self.assertEqual([u["empty"] for u in unmatched], [True])
        self.assertFalse(any(g["provisional"] for g in ranges))


def job(rid, status, minutes, replaced=(), replacing=None):
    return {"_id": rid, "status": status, "replaced": list(replaced), "replacing": replacing,
            "generated_at": datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(minutes=minutes)}


class TestPendingRollback(unittest.TestCase):
    """rules.pending_rollback: khi nào còn __prev phải lùi lại trước khi chạy tiếp."""

    def test_process_chet_giua_luc_thay(self):
        jobs = [job("ok1", "ok", 0), job("dead", "running", 10, replacing="tide_extremes")]
        self.assertEqual(rules.pending_rollback(jobs, "now", True)["_id"], "dead")

    def test_thay_xong_kiem_tra_sau_thay_hong(self):
        jobs = [job("ok1", "ok", 0), job("bad", "failed", 10, replaced=["tide_extremes"])]
        self.assertEqual(rules.pending_rollback(jobs, "now", True)["_id"], "bad")

    def test_blocked_va_failed_sau_khong_che_lan_hong(self):
        jobs = [job("ok1", "ok", 0), job("bad", "failed", 10, replaced=["official_forecasts"]),
                job("blk", "blocked", 20), job("early", "failed", 30)]
        self.assertEqual(rules.pending_rollback(jobs, "now", True)["_id"], "bad")

    def test_cu_hon_lan_ok_hoac_khong_thay_gi_thi_khong_chan(self):
        jobs = [job("bad_old", "failed", 0, replaced=["tide_extremes"]), job("ok1", "ok", 10),
                job("blk", "blocked", 20), job("early", "failed", 30), job("dry", "dry_run", 40)]
        self.assertIsNone(rules.pending_rollback(jobs, "now", True))

    def test_khong_con_prev_thi_khong_chan(self):
        jobs = [job("bad", "failed", 10, replaced=["tide_extremes"])]
        self.assertIsNone(rules.pending_rollback(jobs, "now", False))

    def test_ack_rollback_go_chan(self):
        jobs = [job("ok1", "ok", 0), job("bad", "failed", 10, replaced=["tide_extremes"])]
        jobs[1]["status"] = "rolled_back"                 # việc --ack-rollback làm
        self.assertIsNone(rules.pending_rollback(jobs, "now", True))

    def test_bo_qua_run_hien_tai(self):
        jobs = [job("now", "running", 10, replacing="tide_extremes")]
        self.assertIsNone(rules.pending_rollback(jobs, "now", True))


class TestStaleStaging(unittest.TestCase):
    NAMES = ("tide_extremes", "reservoir_daily")
    NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)

    def test_quyet_dinh(self):
        cols = ["tide_extremes__staging_20260101T115900Z_aaaaaa",   # running, trẻ → giữ
                "tide_extremes__staging_20260101T080000Z_bbbbbb",   # running, quá TTL → xóa
                "reservoir_daily__staging_20260101T115000Z_cccccc", # rolled_back → xóa
                "reservoir_daily__staging_20260101T115500Z_dddddd", # blocked → xóa
                "reservoir_daily__staging_20260101T115800Z_eeeeee", # không có trong jobs, trẻ → giữ
                "reservoir_daily__staging",                          # bản cũ không run_id → xóa
                "tide_extremes__staging_20260101T120000Z_ffffff",   # run hiện tại → bỏ qua
                "tide_extremes", "tide_extremes__prev"]              # không phải staging
        jobs = {"20260101T115900Z_aaaaaa": {"status": "running"}, "20260101T080000Z_bbbbbb": {"status": "running"},
                "20260101T115000Z_cccccc": {"status": "rolled_back"}, "20260101T115500Z_dddddd": {"status": "blocked"}}
        drop, keep = rules.stale_staging(cols, self.NAMES, "20260101T120000Z_ffffff", jobs, self.NOW, 3600)
        self.assertEqual(sorted(c for c, _ in drop), sorted([cols[1], cols[2], cols[3], cols[5]]))
        self.assertEqual(sorted(c for c, _ in keep), sorted([cols[0], cols[4]]))

    def test_parse_iso_utc_giay_le_bat_ky(self):
        want = datetime(2026, 11, 22, 22, 32, 43, 240000, tzinfo=timezone.utc)
        self.assertEqual(rules.parse_iso_utc("2026-11-22T22:32:43.24Z"), want)
        self.assertEqual(rules.parse_iso_utc("2026-11-22T22:32:43.24+00:00"), want)
        self.assertEqual(rules.parse_iso_utc("2026-11-23T05:32:43.240+07:00"), want)
        self.assertEqual(rules.parse_iso_utc("2026-11-22T22:32:43Z"), want.replace(microsecond=0))

    def test_trang_thai_ket_thuc(self):
        self.assertEqual(set(rules.FINISHED), {"ok", "failed", "dry_run", "blocked", "rolled_back"})


class TestStationsAndDuplicates(unittest.TestCase):
    def test_lookup_by_kind(self):
        rows = rules.station_lookup_rows([
            {"code": "DAU_TIENG", "name": "Dầu Tiếng", "kind": "water_level", "aliases": ["Dầu Tiếng"]},
            {"code": "RES_DAU_TIENG", "name": "Dầu Tiếng", "kind": "reservoir", "aliases": []},
            {"code": "RAIN_THU_DAU_MOT", "name": "Thủ Dầu Một", "kind": "rain", "aliases": ["Thủ Dầu I"]},
        ])
        self.assertIn(("tide", "Dầu Tiếng", "DAU_TIENG"), rows)
        self.assertIn(("reservoir", "Dầu Tiếng", "RES_DAU_TIENG"), rows)
        self.assertIn(("rain", "Thủ Dầu I", "RAIN_THU_DAU_MOT"), rows)

    def test_lookup_conflict(self):
        with self.assertRaises(ValueError):
            rules.station_lookup_rows([{"code": "A", "name": "X", "kind": "rain"},
                                       {"code": "B", "name": "X", "kind": "rain"}])

    def test_duplicates_keep_matching_folder(self):
        rows = [{"source_file": "2019/01/10/a.pdf", "sha256": "h", "date_from_filename": 1, "bulletin_date": 2},
                {"source_file": "2020/01/10/a.pdf", "sha256": "h", "date_from_filename": 2, "bulletin_date": 2},
                {"source_file": "2020/01/11/b.pdf", "sha256": "k", "date_from_filename": 3, "bulletin_date": 3}]
        out = rules.duplicate_exclusions(rows)
        self.assertEqual([(o["source_file"], o["kept"]) for o in out], [("2019/01/10/a.pdf", "2020/01/10/a.pdf")])


class TestQcHelpers(unittest.TestCase):
    def test_classify_unmatched(self):
        flags = [{"_id": "a", "origin": "auto", "status": "suspect", "decided_at": None},
                 {"_id": "b", "origin": "auto", "status": "rejected", "decided_at": 1},
                 {"_id": "c", "origin": "manual", "status": "suspect", "decided_at": 1},
                 {"_id": "d", "origin": "auto", "status": "suspect", "decided_at": None}]
        skip, block = rules.classify_unmatched(flags, {"a", "b", "c"})
        self.assertEqual([f["_id"] for f in skip], ["a"])
        self.assertEqual([f["_id"] for f in block], ["b", "c"])
        self.assertEqual(rules.classify_unmatched(flags, set()), ([], []))

    def test_g0_unsettled(self):
        t = lambda h: datetime(2026, 11, 23, h, tzinfo=timezone.utc)  # noqa: E731
        ok = {"_id": "r1", "status": "ok", "generated_at": t(1)}
        self.assertIsNone(rules.g0_unsettled([ok], []))
        self.assertIn("__prev", rules.g0_unsettled([ok], ["tide_extremes__prev"]))
        bad = {"_id": "r2", "status": "failed", "replaced": ["tide_extremes"], "generated_at": t(2)}
        self.assertIn("r2", rules.g0_unsettled([ok, bad], []))
        # hỏng TRƯỚC lần ok gần nhất: đã ổn
        self.assertIsNone(rules.g0_unsettled([{**bad, "generated_at": t(0)}, ok], []))
        self.assertIsNone(rules.g0_unsettled([ok, {**bad, "replaced": [], "replacing": None}], []))


if __name__ == "__main__":
    unittest.main()
