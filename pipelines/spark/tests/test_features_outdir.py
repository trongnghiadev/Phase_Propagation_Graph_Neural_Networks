"""Test ghi thư mục tạm → đổi tên (`features/outdir.py`), biên chia tập dùng chung (`features/splits.py`)
và chặn theo G0 (`features/g0guard.py`, cần pyspark — tự bỏ qua khi không có).

    docker run --rm -v "<gốc repo>:/work" gis-spark:local sh -c 'cd /work && \
      PYTHONPATH=/opt/spark/python:$(ls /opt/spark/python/lib/py4j-*.zip) python3 -X utf8 -m unittest \
      discover -s pipelines/spark/tests -p "test_*.py"'
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from features import outdir  # noqa: E402
from features import splits as SP  # noqa: E402

try:
    import pyspark  # noqa: F401
    HAVE_SPARK = True
except ImportError:
    HAVE_SPARK = False

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))


def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _read(path: str) -> str:
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class TestOutdir(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.out = os.path.join(self.root, "g5")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_publish_replaces_and_keeps_old_until_new_complete(self):
        _write(os.path.join(self.out, "year=2020", "part-0.parquet"), "cu")
        _write(os.path.join(self.out, "_meta.json"), "cu")
        tmp = outdir.tmp_path(self.out, "R1")
        self.assertEqual(tmp, self.out + ".tmp_R1")
        _write(os.path.join(tmp, "year=2021", "part-0.parquet"), "moi")
        # trong lúc ghi bản tạm, bản chính vẫn nguyên
        self.assertEqual(_read(os.path.join(self.out, "_meta.json")), "cu")
        _write(os.path.join(tmp, "_meta.json"), "moi")
        outdir.publish(tmp, self.out, "R1")
        self.assertEqual(_read(os.path.join(self.out, "_meta.json")), "moi")
        self.assertFalse(os.path.exists(os.path.join(self.out, "year=2020")))  # không trộn bản cũ
        self.assertTrue(os.path.exists(os.path.join(self.out, "year=2021")))
        self.assertFalse(os.path.exists(tmp))
        self.assertEqual(os.listdir(self.root), ["g5"])

    def test_publish_without_previous(self):
        tmp = outdir.tmp_path(self.out, "R1")
        _write(os.path.join(tmp, "a"), "x")
        outdir.publish(tmp, self.out, "R1")
        self.assertEqual(_read(os.path.join(self.out, "a")), "x")

    def test_failed_job_leaves_old_untouched(self):
        _write(os.path.join(self.out, "a"), "cu")
        tmp = outdir.tmp_path(self.out, "R2")
        _write(os.path.join(tmp, "a"), "do dang")
        outdir.discard(tmp)          # job dừng vì kiểm tra hỏng
        self.assertEqual(_read(os.path.join(self.out, "a")), "cu")
        self.assertFalse(os.path.exists(tmp))

    def test_publish_missing_tmp_raises_and_keeps_old(self):
        _write(os.path.join(self.out, "a"), "cu")
        with self.assertRaises(FileNotFoundError):
            outdir.publish(self.out + ".tmp_X", self.out, "X")
        self.assertEqual(_read(os.path.join(self.out, "a")), "cu")

    def test_recover_after_crash_between_renames(self):
        # chết sau `out → out.old_R3`, trước `tmp → out`
        _write(os.path.join(outdir.old_path(self.out, "R3"), "a"), "cu")
        _write(os.path.join(outdir.tmp_path(self.out, "R3"), "a"), "moi do dang")
        old_tmp = outdir.tmp_path(self.out, "R3")
        past = time.time() - 7 * 3600
        os.utime(old_tmp, (past, past))
        _write(os.path.join(outdir.tmp_path(self.out, "R4"), "a"), "job khac dang ghi")
        done = outdir.recover(self.out)
        self.assertEqual(_read(os.path.join(self.out, "a")), "cu")
        self.assertFalse(os.path.exists(old_tmp))
        self.assertTrue(os.path.exists(outdir.tmp_path(self.out, "R4")))   # bản tạm mới: không xóa
        self.assertTrue(any("khôi phục" in m for m in done))


class TestSplits(unittest.TestCase):
    def test_repo_config_loads(self):
        cfg = SP.load_split_config()
        self.assertEqual(cfg["path"], os.path.join(REPO, "config", "splits.json"))
        sp = cfg["splits"]
        self.assertEqual(set(sp), {"train", "val", "test"})
        self.assertLess(sp["train"][1], sp["val"][0])
        self.assertLess(sp["val"][1], sp["test"][0])
        self.assertNotIn("test", cfg["harmonic_fit_splits"])
        self.assertEqual(SP.split_of(sp["test"][0], sp), "test")

    def test_g5_uses_shared_config(self):
        """G5 không còn biên viết cứng: SPLITS = config/splits.json."""
        src = _read(os.path.join(REPO, "pipelines", "spark", "features", "build_training_set.py"))
        self.assertIn("SP.load_split_config()", src)
        with open(os.path.join(REPO, "config", "splits.json"), encoding="utf-8") as fh:
            raw = json.load(fh)
        for name, (a, b) in SP.load_split_config()["splits"].items():
            self.assertEqual((raw["splits"][name]["from"], raw["splits"][name]["to"]), (a, b))
            self.assertNotIn(f'("{a}", "{b}")', src)

    def test_parse_rejects_overlap_and_fit_on_test(self):
        good = {"splits": {"train": {"from": "2017-01-01", "to": "2022-12-31"},
                           "val": {"from": "2023-01-01", "to": "2024-06-30"},
                           "test": {"from": "2024-07-01", "to": "2026-11-23"}}}
        self.assertEqual(SP.parse_split_config(good)["harmonic_fit_splits"], ["train"])
        bad = json.loads(json.dumps(good))
        bad["splits"]["val"]["to"] = "2024-07-01"
        with self.assertRaises(ValueError):
            SP.parse_split_config(bad)
        with self.assertRaises(ValueError):
            SP.parse_split_config({**good, "harmonic_fit_splits": ["train", "test"]})

    def test_astro_leak_reason(self):
        sp = {"train": ("2017-01-01", "2022-12-31"), "val": ("2023-01-01", "2024-06-30"),
              "test": ("2024-07-01", "2026-11-23")}
        self.assertIn("thiếu", SP.astro_leak_reason(None, sp))
        self.assertIn("rò rỉ", SP.astro_leak_reason({"fit_to": "2026-11-15"}, sp))
        self.assertIn("rò rỉ", SP.astro_leak_reason({"fit_to": "2024-07-01"}, sp))
        self.assertIsNone(SP.astro_leak_reason({"fit_to": "2022-12-31"}, sp))
        self.assertIsNone(SP.astro_leak_reason({"fit_to": "2024-06-30"}, sp))
        self.assertEqual(SP.astro_meta_path("/a/b/astro_tide.csv"), os.path.join("/a/b", "astro_tide_meta.json"))


@unittest.skipUnless(HAVE_SPARK, "cần pyspark")
class TestG0Guard(unittest.TestCase):
    class Run:
        def __init__(self, lock=None):
            self.lock = lock

        def other_running(self, job):
            return self.lock if job == "g0_flatten_bulletins" else None

    class Adm:
        def __init__(self, jobs, prevs=()):
            self.jobs, self.prevs = jobs, set(prevs)

        def exists(self, name):
            return name in self.prevs

        def find_many(self, coll, flt, proj=None):
            return [dict(j) for j in self.jobs]

    def test_block_codes(self):
        from features.g0guard import g0_block
        t = "2026-11-23T01:00:00Z"
        ok = {"_id": "r1", "status": "ok", "generated_at": t}
        self.assertIsNone(g0_block(self.Run(), self.Adm([ok])))
        code, status, _ = g0_block(self.Run({"_id": "g0_flatten_bulletins", "run_id": "x"}), self.Adm([ok]))
        self.assertEqual((code, status), (3, "failed"))
        code, status, msg = g0_block(self.Run(), self.Adm([ok], prevs=["tide_extremes__prev"]))
        self.assertEqual((code, status), (4, "blocked"))
        self.assertIn("tide_extremes__prev", msg)
        bad = {"_id": "r2", "status": "failed", "replaced": ["tide_extremes"], "generated_at": "2026-11-23T02:00:00Z"}
        code, status, msg = g0_block(self.Run(), self.Adm([ok, bad]))
        self.assertEqual(code, 4)
        self.assertIn("r2", msg)
        _ = datetime(2026, 1, 1, tzinfo=timezone.utc)


if __name__ == "__main__":
    unittest.main()
