"""Test báo cáo QC (thuần Python, không cần Spark).

    python -X utf8 pipelines/spark/tests/test_qc_report.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from qc import report  # noqa: E402  (qc/__init__ bảo đảm import được vngis)
from vngis import qc_rules as R  # noqa: E402


def flag(station, ts, status="suspect", rules=("R1",), auto_cleared=False):
    return {"station": station, "ts": ts, "status": status, "auto_cleared": auto_cleared,
            "rules": [{"rule": r, "detail": "", "metric": {}} for r in rules]}


class TestSummary(unittest.TestCase):
    def test_nam_theo_gio_vn_va_dem_theo_quy_tac(self):
        # 31/12/2020 18:00 UTC = 01/01/2021 01:00 giờ VN → năm 2021
        t = datetime(2020, 12, 31, 18, tzinfo=timezone.utc)
        rows = report.summary_rows([flag("A", t, "rejected", ("R1", "R2")), flag("A", t, "suspect", ("R2",)),
                                    flag("A", t, "confirmed_ok", (), auto_cleared=False)])
        by = {(r[0], r[1], r[2]): r[3:] for r in rows}
        self.assertEqual(by[("A", 2021, "R1")], [0, 0, 1, 0, 1, 0])
        self.assertEqual(by[("A", 2021, "R2")], [1, 0, 1, 0, 2, 0])
        self.assertEqual(by[("A", 2021, "manual")], [0, 1, 0, 0, 1, 0])
        self.assertEqual(by[("A", 2021, "any")], [1, 1, 1, 0, 3, 0])
        self.assertEqual([r[2] for r in rows], ["R1", "R2", "manual", "any"])

    def test_bo_suspect_da_het_kich_hoat_giu_quyet_dinh(self):
        t = datetime(2022, 5, 1, tzinfo=timezone.utc)
        rows = report.summary_rows([flag("A", t, "suspect", auto_cleared=True),
                                    flag("A", t, "rejected", auto_cleared=True)])
        by = {(r[0], r[1], r[2]): r[3:] for r in rows}
        self.assertEqual(by[("A", 2022, "any")], [0, 0, 1, 0, 1, 1])


class TestExceedance(unittest.TestCase):
    def test_cap_bao_dong_cua_ban_tin(self):
        days = [
            {"station": "PHU_AN", "day": "2020-01-01", "peak": 1.45, "levels": [1.3, 1.4, 1.5]},
            {"station": "PHU_AN", "day": "2020-01-02", "peak": 1.40, "levels": [1.4, 1.5, 1.6]},   # đúng bằng BĐ I
            {"station": "PHU_AN", "day": "2020-01-03", "peak": None, "levels": [1.4, 1.5, 1.6]},   # bị loại
            {"station": "PHU_AN", "day": "2020-01-04", "peak": 1.70, "levels": [1.4, 1.5, None]},  # BĐ III chưa có
            {"station": "VUNG_TAU", "day": "2020-01-01", "peak": 4.0, "levels": None},             # không có cấp
        ]
        rows = report.exceedance_rows(days)
        self.assertEqual(rows, [["PHU_AN", 2020, 4, 3, 3, 2, 0, 1]])

    def test_ghi_csv_lf(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "x", "a.csv")
            report.write_csv(p, ["a", "b"], [[1, None]])
            with open(p, "rb") as f:
                self.assertEqual(f.read(), b"a,b\n1,\n")


class TestKhopVoiQcRules(unittest.TestCase):
    def test_trang_thai(self):
        self.assertEqual(list(R.STATUSES), report.SUMMARY_HEADER[3:7])


if __name__ == "__main__":
    unittest.main()
