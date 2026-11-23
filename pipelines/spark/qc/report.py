"""Báo cáo QC (thuần Python, có test): `summary.csv`, `exceedance.csv`. Chỉ số tổng hợp, không tên người.

- `summary_rows`: trạm × năm (giờ VN của `ts`) × quy tắc → số cờ theo `status`. Một cờ có nhiều quy tắc được đếm
  ở mỗi quy tắc; dòng `rule = any` đếm mỗi cờ một lần; cờ thủ công không có quy tắc đếm ở `rule = manual`.
  Chỉ đếm cờ có hiệu lực (`qc_rules.is_active`); `auto_cleared` = số cờ đã có quyết định mà quy tắc không còn kích hoạt.
- `exceedance_rows`: số ngày quan trắc có đỉnh triều cao nhất ngày ≥ cấp báo động I/II/III, theo trạm × năm, dùng
  cấp báo động in trong CHÍNH bản tin của ngày đó (`hydromet_bulletins.alarm_levels`; K9). Đỉnh dùng giá trị hiệu
  lực sau QC (rejected bị bỏ, corrected dùng giá trị sửa; suspect giữ nguyên).
"""
from __future__ import annotations

import csv
import os
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Iterable

from vngis import qc_rules as R

SUMMARY_HEADER = ["station", "year", "rule", "suspect", "confirmed_ok", "rejected", "corrected", "total",
                  "auto_cleared"]
EXCEEDANCE_HEADER = ["station", "year", "days_with_levels", "days_with_peak", "ge_level_1", "ge_level_2",
                     "ge_level_3", "days_level_3_undefined"]


def vn_year(ts: datetime) -> int:
    return (ts.astimezone(timezone.utc) + timedelta(hours=7)).year


def summary_rows(flags: Iterable[dict]) -> list[list]:
    acc: dict[tuple, dict] = defaultdict(lambda: {s: 0 for s in (*R.STATUSES, "auto_cleared")})
    for f in flags:
        if not R.is_active(f):
            continue
        year = vn_year(f["ts"])
        rules = sorted({r["rule"] for r in f.get("rules") or []}) or ["manual"]
        for rule in [*rules, "any"]:
            c = acc[(f["station"], year, rule)]
            c[f["status"]] += 1
            if f.get("auto_cleared"):
                c["auto_cleared"] += 1
    order = {r: i for i, r in enumerate([*R.RULES, "manual", "any"])}
    rows = []
    for (st, y, rule) in sorted(acc, key=lambda k: (k[0], k[1], order.get(k[2], 99))):
        c = acc[(st, y, rule)]
        rows.append([st, y, rule, *(c[s] for s in R.STATUSES), sum(c[s] for s in R.STATUSES), c["auto_cleared"]])
    return rows


def exceedance_rows(days: Iterable[dict]) -> list[list]:
    """days: {station, day ('YYYY-MM-DD' giờ VN), peak (đỉnh cao nhất ngày hoặc None), levels: [l1, l2, l3] (phần tử
    có thể None) hoặc None nếu bản tin không ghi cấp báo động cho trạm}. Chỉ trạm có cấp báo động mới có dòng."""
    acc: dict[tuple, list[int]] = defaultdict(lambda: [0] * 6)
    for d in days:
        if not d.get("levels"):
            continue
        c = acc[(d["station"], int(d["day"][:4]))]
        c[0] += 1
        lv = list(d["levels"]) + [None] * (3 - len(d["levels"]))
        if lv[2] is None:
            c[5] += 1
        if d.get("peak") is None:
            continue
        c[1] += 1
        for i in range(3):
            if lv[i] is not None and d["peak"] >= lv[i]:
                c[2 + i] += 1
    return [[st, y, *acc[(st, y)]] for st, y in sorted(acc)]


def write_csv(path: str, header: list[str], rows: list[list]) -> None:
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(header)
        w.writerows(rows)
