"""Chạy migration MongoDB (`migrations/mongo/NNNN_*.py`), ghi lịch sử vào collection `_migrations`.

    python -X utf8 migrations/mongo/runner.py --db <tên DB> status
    python -X utf8 migrations/mongo/runner.py --db <tên DB> up [--dry-run] [--only 0006]
    python -X utf8 migrations/mongo/runner.py --db <tên DB> verify
    python -X utf8 migrations/mongo/runner.py --db <tên DB> down 0006

- Kết nối: ``--uri`` hoặc biến môi trường ``MONGO_URI`` (mặc định ``mongodb://localhost:27017``); DB: ``--db``
  hoặc ``MONGO_DB`` — **bắt buộc** chọn rõ (không có mặc định, tránh áp nhầm vào DB thật).
- `up` chạy các bước chưa có trong `_migrations` theo thứ tự số; bước đã áp bị bỏ qua (``--force`` để áp lại —
  mỗi bước tự idempotent). Ghi `_migrations` với writeConcern majority, kèm báo cáo và tuỳ chọn validator cũ
  (để `down` khôi phục).
- Mỗi bước là module có `COLLECTION`, `DESCRIPTION`, `up(db, dry_run=)`, `verify(db)`, `down(db, previous)`.

Mã thoát: 0 = thành công; 1 = `verify` thấy sai khác hoặc bước lỗi; 2 = tham số sai.
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import ModuleType

HERE = Path(__file__).resolve().parent
HISTORY = "_migrations"
_NAME = re.compile(r"^(\d{4})_[a-z0-9_]+\.py$")


def discover() -> list[tuple[str, Path]]:
    """Các bước migration theo thứ tự số: ``[(id, đường dẫn)]`` (id = tên file không đuôi)."""
    return sorted((p.stem, p) for p in HERE.iterdir() if _NAME.match(p.name))


def load(path: Path) -> ModuleType:
    if str(HERE) not in sys.path:
        sys.path.insert(0, str(HERE))
    spec = importlib.util.spec_from_file_location(f"migration_{path.stem}", path)
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(mod)
    return mod


def _match(mid: str, only: list[str] | None) -> bool:
    return not only or any(mid == o or mid.startswith(f"{o}_") for o in only)


def applied(db) -> dict[str, dict]:
    return {d["_id"]: d for d in db[HISTORY].find({})}


def up(db, *, dry_run: bool = False, only: list[str] | None = None, force: bool = False) -> list[dict]:
    from pymongo import WriteConcern

    done = applied(db)
    out = []
    hist = db.get_collection(HISTORY, write_concern=WriteConcern("majority"))
    for mid, path in discover():
        if not _match(mid, only):
            continue
        if mid in done and not force:
            out.append({"id": mid, "skipped": "đã áp"})
            continue
        mod = load(path)
        report = mod.up(db, dry_run=dry_run)
        problems = [] if dry_run else mod.verify(db)
        out.append({"id": mid, "report": report, "verify": problems})
        if dry_run:
            continue
        if problems:
            raise RuntimeError(f"{mid}: kiểm tra sau khi áp thất bại: {problems}")
        prev = (done.get(mid) or {}).get("previous", report.get("previous"))
        hist.update_one({"_id": mid}, {"$set": {"description": mod.DESCRIPTION, "collection": mod.COLLECTION,
                                                 "applied_at": datetime.now(timezone.utc), "report": report,
                                                 "previous": prev}}, upsert=True)
    return out


def verify(db, only: list[str] | None = None) -> dict[str, list[str]]:
    return {mid: load(path).verify(db) for mid, path in discover() if _match(mid, only)}


def down(db, mid: str) -> dict:
    from pymongo import WriteConcern

    paths = dict(discover())
    full = next((m for m in paths if _match(m, [mid])), None)
    if full is None:
        raise SystemExit(f"không có migration {mid}")
    rec = db[HISTORY].find_one({"_id": full})
    if rec is None:
        return {"id": full, "skipped": "chưa áp"}
    load(paths[full]).down(db, rec.get("previous"))
    db.get_collection(HISTORY, write_concern=WriteConcern("majority")).delete_one({"_id": full})
    return {"id": full, "reverted": True}


def status(db) -> list[dict]:
    done = applied(db)
    return [{"id": mid, "applied_at": (done.get(mid) or {}).get("applied_at")} for mid, _ in discover()]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Migration MongoDB")
    ap.add_argument("--uri", default=os.getenv("MONGO_URI") or "mongodb://localhost:27017")
    ap.add_argument("--db", default=os.getenv("MONGO_DB"))
    sub = ap.add_subparsers(dest="cmd", required=True)
    p_up = sub.add_parser("up")
    p_up.add_argument("--dry-run", action="store_true")
    p_up.add_argument("--only", action="append")
    p_up.add_argument("--force", action="store_true")
    sub.add_parser("status")
    p_v = sub.add_parser("verify")
    p_v.add_argument("--only", action="append")
    p_d = sub.add_parser("down")
    p_d.add_argument("id")
    args = ap.parse_args(argv)
    if not args.db:
        print("cần --db hoặc MONGO_DB (không có mặc định)", file=sys.stderr)
        return 2
    from pymongo import MongoClient

    db = MongoClient(args.uri, tz_aware=True)[args.db]
    if args.cmd == "up":
        res = up(db, dry_run=args.dry_run, only=args.only, force=args.force)
    elif args.cmd == "verify":
        res = verify(db, args.only)
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 1 if any(res.values()) else 0
    elif args.cmd == "down":
        res = down(db, args.id)
    else:
        res = status(db)
    print(json.dumps(res, ensure_ascii=False, indent=1, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
