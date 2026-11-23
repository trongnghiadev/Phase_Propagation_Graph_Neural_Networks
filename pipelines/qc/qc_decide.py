"""CLI ghi quyết định QC có kiểm toán (dùng `vngis.qc`). Kết nối theo `vngis.config` (APP_ENV, MONGO_URI, MONGO_DB).

    python pipelines/qc/qc_decide.py list [--station BIEN_HOA] [--status suspect] [--rule R1] [--mismatch] [--all] [--limit 50]
    python pipelines/qc/qc_decide.py show ID
    python pipelines/qc/qc_decide.py decide ID --status rejected --reason "…" --by ROLE --version 1 [--value 1.18]
    python pipelines/qc/qc_decide.py flag --station PHU_AN --kind high --ts 2024-03-10T09:15:00+07:00 --reason "…" --by ROLE
    python pipelines/qc/qc_decide.py bulletin-set SOURCE_FILE --change source.is_primary true false --reason "…" --by ROLE
    python pipelines/qc/qc_decide.py review COLLECTION DOC_ID --reason "…" --by ROLE
    python pipelines/qc/qc_decide.py retire ID --reason "…" --by ROLE --version 2
    python pipelines/qc/qc_decide.py ensure | reconcile [--grace-s 600]

ID = `_id` của cờ, dạng `tide_extremes:<trạm>:<high|low>:<ts UTC ISO>` (`list`/`show` in ra).
`--version` là version đang thấy (khóa lạc quan): ai đó (hoặc job QC) vừa sửa cờ thì lệnh báo xung đột, không ghi.
Giá trị `--change` đọc như JSON (true, 1.5, "chuỗi"); chuỗi ISO có `Z`/múi giờ được hiểu là ngày giờ.
`retire`: cờ mồ côi (phần tử không còn trong `tide_extremes`) hết hiệu lực, G0 thôi chặn; chỉ được khi phần tử
thật sự không còn.

Cờ `value_mismatch` (giá trị gốc trong bản tin đổi sau khi đã quyết định): xem bằng `list --mismatch`. ĐƯỜNG THOÁT là
`decide` lại (bất kỳ status nào, kể cả `suspect` = bỏ quyết định): quyết định mới gắn vào giá trị gốc hiện tại
(`decided_value`) và xoá trạng thái `value_mismatch`. Cờ không bị gắn lại mismatch vô hạn khi `tide_extremes` chưa
kịp cập nhật (giá trị quan sát còn là giá trị cũ lúc gắn cờ).
Mã thoát: 0 ok; 1 tham số dòng lệnh sai (argparse), dữ liệu vào sai kiểu, không tìm thấy; 2 xung đột (version của
cờ, hoặc giá trị cũ của field bản tin); 3 audit không còn pending khi đặt applied (dữ liệu đã ghi, kiểm tra tay).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime

from vngis import qc
from vngis.db import get_db

ISO = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?(Z|[+-]\d{2}:\d{2})$")


def parse_value(s: str):
    if ISO.match(s):
        return parse_ts(s)
    try:
        return json.loads(s)
    except json.JSONDecodeError:
        return s


def parse_ts(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise argparse.ArgumentTypeError(f"ts '{s}' cần múi giờ (Z hoặc +07:00)")
    return dt


def show(doc: dict) -> None:
    print(json.dumps(doc, ensure_ascii=False, default=str))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Ghi quyết định QC (qc_flags + audit_logs)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("list")
    p.add_argument("--station")
    p.add_argument("--status", choices=qc.R.STATUSES)
    p.add_argument("--rule", choices=qc.R.RULES)
    p.add_argument("--mismatch", action="store_true",
                   help="chỉ cờ đang ở trạng thái value_mismatch (giá trị gốc đổi sau khi quyết định)")
    p.add_argument("--all", action="store_true", help="gồm cả cờ suspect đã auto_cleared")
    p.add_argument("--limit", type=int, default=50)
    p = sub.add_parser("show")
    p.add_argument("id")
    p = sub.add_parser("decide")
    p.add_argument("id")
    p.add_argument("--status", required=True, choices=qc.R.STATUSES)
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True, help="người/vai trò quyết định")
    p.add_argument("--version", required=True, type=int, help="version của cờ lúc xem")
    p.add_argument("--value", type=float, help="corrected_value (chỉ với --status corrected)")
    p = sub.add_parser("flag")
    p.add_argument("--station", required=True)
    p.add_argument("--kind", required=True, choices=("high", "low"))
    p.add_argument("--ts", required=True, type=parse_ts)
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p = sub.add_parser("bulletin-set")
    p.add_argument("source_file")
    p.add_argument("--change", nargs=3, action="append", required=True, metavar=("FIELD", "OLD", "NEW"))
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p = sub.add_parser("retire")
    p.add_argument("id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--version", required=True, type=int)
    p = sub.add_parser("review")
    p.add_argument("collection")
    p.add_argument("doc_id")
    p.add_argument("--reason", required=True)
    p.add_argument("--by", required=True)
    sub.add_parser("ensure")
    p = sub.add_parser("reconcile")
    p.add_argument("--grace-s", type=float, default=None,
                   help="chỉ xử lý audit pending cũ hơn ngần này giây (mặc định qc.audit_pending_grace_s)")
    try:
        a = ap.parse_args(argv)
    except SystemExit as e:          # argparse: sai cú pháp → mã 1 (mã 2 dành cho xung đột); --help → 0
        return 0 if e.code in (0, None) else 1

    db = get_db()
    try:
        if a.cmd == "ensure":
            qc.ensure_collections(db)
            print("qc_flags, audit_logs: validator và index đã cập nhật")
        elif a.cmd == "reconcile":
            from vngis.config import get_config
            grace = a.grace_s if a.grace_s is not None else get_config().qc.audit_pending_grace_s
            print(qc.reconcile_pending(db, grace_s=grace))
        elif a.cmd == "list":
            for d in qc.find_flags(db, a.station, a.status, a.rule, active_only=not a.all, limit=a.limit,
                                   mismatch_only=a.mismatch):
                print(f"{d['_id']}  v{d['version']}  {d['status']:<12} value={d.get('value')}  "
                      f"rules={'+'.join(r['rule'] for r in d.get('rules') or []) or d.get('origin')}"
                      + ("  auto_cleared" if d.get("auto_cleared") else "")
                      + (f"  value_mismatch: giá trị gốc hiện tại {d.get('current_value')} ≠ giá trị lúc quyết định "
                         f"{d.get('decided_value', d.get('value'))} → quyết định lại để thoát"
                         if d.get("value_mismatch") else ""))
        elif a.cmd == "show":
            d = qc.get_flag(db, a.id)
            if d is None:
                print(f"không có cờ {a.id}", file=sys.stderr)
                return 1
            show(d)
        elif a.cmd == "decide":
            qc.ensure_collections(db)
            show(qc.decide(db, a.id, a.status, reason=a.reason, decided_by=a.by, expected_version=a.version,
                           corrected_value=a.value))
        elif a.cmd == "flag":
            qc.ensure_collections(db)
            show(qc.create_manual_flag(db, a.station, a.kind, a.ts, reason=a.reason, decided_by=a.by))
        elif a.cmd == "bulletin-set":
            qc.ensure_collections(db)
            changes = {f: (parse_value(old), parse_value(new)) for f, old, new in a.change}
            show(qc.update_bulletin_fields(db, a.source_file, changes, reason=a.reason, actor=a.by))
        elif a.cmd == "retire":
            qc.ensure_collections(db)
            show(qc.retire_flag(db, a.id, reason=a.reason, actor=a.by, expected_version=a.version))
        elif a.cmd == "review":
            qc.ensure_collections(db)
            show(qc.record_review(db, a.collection, a.doc_id, reason=a.reason, actor=a.by))
    except (qc.VersionConflict, qc.FieldConflict) as e:
        print(f"XUNG ĐỘT: {e}", file=sys.stderr)
        return 2
    except qc.AuditStateError as e:
        print(f"LỖI AUDIT: {e}", file=sys.stderr)
        return 3
    except (qc.QcError, ValueError) as e:
        print(f"LỖI: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
