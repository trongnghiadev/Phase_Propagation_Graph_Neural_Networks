"""Quy tắc thuần Python về bản tin KTTV dùng chung cho job Spark G0 và script nạp (chỉ thư viện chuẩn)."""
from __future__ import annotations

from collections import defaultdict


def duplicate_exclusions(rows: list[dict]) -> list[dict]:
    """rows: {source_file, sha256, date_from_filename, bulletin_date}.

    Trong mỗi nhóm cùng sha256, giữ bản có ngày trên tên file = ngày trong nội dung
    (file nằm đúng thư mục ngày); hòa thì giữ `source_file` nhỏ nhất. Trả về các bản bị loại
    ``{source_file, kept, sha256}``. Bản không có sha256 không bao giờ bị coi là trùng.
    Ngày có thể là datetime hoặc số (epoch) – chỉ so bằng.
    """
    groups: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        if not r.get("sha256"):
            continue
        groups[r["sha256"]].append(r)
    out = []
    for sha, g in groups.items():
        if len(g) < 2:
            continue
        g = sorted(g, key=lambda r: (r["date_from_filename"] != r["bulletin_date"], r["source_file"]))
        keep = g[0]
        for r in g[1:]:
            out.append({"source_file": r["source_file"], "kept": keep["source_file"], "sha256": sha})
    return sorted(out, key=lambda r: r["source_file"])
