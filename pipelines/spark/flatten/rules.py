"""Quy tắc thuần Python của G0 (không phụ thuộc Spark, có test riêng).

- Tra mã trạm/hồ theo tên chuẩn và `kind`.
- Chọn bản tin giữ lại khi nhiều bản tin chính trùng nội dung (cùng sha256): `vngis.bulletins`.
- Gán tỉnh của hồ chứa: nhãn tỉnh trong bản tin → `{era, code}` bằng `vngis.province_labels`
  (quy tắc dùng chung với `pipelines/ingest/stations/create_stations.py`; thời kỳ theo ngày quan trắc).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone

from vngis.admin import BEFORE as ERA_OLD, EFFECTIVE_UTC as ERA_CUTOFF, FROM as ERA_NEW  # noqa: F401
from vngis.bulletins import duplicate_exclusions  # noqa: F401  (dùng chung với create_stations)
from vngis.province_labels import DEFAULT_MAX_LAG_DAYS, NOTE_LAG, LabelRun, assign_all  # noqa: F401

# Nhóm tra mã theo `stations.kind`. Một tên có thể là trạm mực nước, trạm mưa và hồ (Dầu Tiếng, Nhà Bè).
KIND_GROUPS = {
    "tide": ("water_level", "sea_level"),
    "reservoir": ("reservoir",),
    "rain": ("rain",),
}


# ---------------------------------------------------------------------------
# Mã trạm
# ---------------------------------------------------------------------------
def station_lookup_rows(stations: list[dict]) -> list[tuple[str, str, str]]:
    """Trả về (group, label, code) cho mọi tên chuẩn và alias. Báo lỗi nếu một nhãn trỏ 2 mã."""
    seen: dict[tuple[str, str], str] = {}
    for s in stations:
        group = next((g for g, kinds in KIND_GROUPS.items() if s.get("kind") in kinds), None)
        if group is None:
            continue
        for label in [s["name"], *(s.get("aliases") or [])]:
            key = (group, label)
            if key in seen and seen[key] != s["code"]:
                raise ValueError(f"Nhãn '{label}' ({group}) trỏ tới 2 mã: {seen[key]}, {s['code']}")
            seen[key] = s["code"]
    return [(g, label, code) for (g, label), code in sorted(seen.items())]


# ---------------------------------------------------------------------------
# Tỉnh của hồ chứa
# ---------------------------------------------------------------------------
def province_ranges(rows: list[dict], timeline: list[dict], max_lag_days: int = DEFAULT_MAX_LAG_DAYS
                    ) -> tuple[list[dict], dict[str, list[LabelRun]], list[dict]]:
    """rows: {reservoir, label, obs} – mỗi (hồ, nhãn tỉnh gốc, ngày quan trắc UTC) một dòng.

    Trả về (khoảng để join, đoạn theo hồ, không khớp). Mỗi khoảng là
    {reservoir, label (chuỗi gốc, có thể None), obs_from, obs_to (bao gồm, datetime UTC), era, code, note,
    provisional, empty}. Các đoạn cùng nhãn của một hồ không chồng nhau theo thời gian (đoạn nhãn rỗng có thể nằm
    xen trong khoảng của đoạn có nhãn, nhưng nhãn gốc khác), nên join theo (hồ, nhãn gốc, ngày quan trắc) khớp đúng
    một khoảng. `empty = True`: nhãn rỗng (không có thông tin) – bản ghi được nạp với `province = null`.
    """
    by_res: dict[str, list] = defaultdict(list)
    for r in rows:
        by_res[r["reservoir"]].append((r["obs"], r["label"]))
    runs, unmatched = assign_all(by_res, timeline, max_lag_days)
    ranges = []
    for res, rs in sorted(runs.items()):
        for run in rs:
            for label in run.labels:
                ranges.append({"reservoir": res, "label": label, "obs_from": run.first, "obs_to": run.last,
                               "era": run.era, "code": run.code, "note": run.note,
                               "provisional": run.provisional, "empty": not run.label_norm})
    return ranges, runs, unmatched


def to_epoch(d: datetime) -> int:
    return int(d.timestamp())


# ---------------------------------------------------------------------------
# Quyết định vận hành (thuần Python): staging cũ, chặn khi còn __prev cần lùi lại
# ---------------------------------------------------------------------------
STAGING_SUFFIX = "__staging"
# trạng thái `jobs` coi là đã kết thúc (staging của chúng được dọn)
FINISHED = ("ok", "failed", "dry_run", "blocked", "rolled_back")
# trạng thái có thể là "hỏng giữa chừng" nếu đã thay/đang thay collection (running = process chết giữa chừng)
INTERRUPTED = ("failed", "running")


def run_time(run_id: str) -> datetime | None:
    """Thời điểm UTC mã hóa trong run_id `YYYYMMDDTHHMMSSZ_xxxxxx`; None nếu không đọc được."""
    try:
        return datetime.strptime(run_id.split("_")[0], "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def stale_staging(collections: list[str], names: tuple[str, ...], run_id: str, jobs_by_id: dict[str, dict],
                  now: datetime, ttl_s: int) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """→ (xóa, giữ), mỗi phần tử (collection, lý do). Staging `<tên>__staging[_<run_id>]` của run khác run hiện tại
    bị xóa khi run đó đã kết thúc (`jobs.status` ∈ FINISHED), hoặc không có trong `jobs` / vẫn `running` mà cũ hơn
    `ttl_s` (tiến trình đã chết: khóa của nó hết hạn). Staging không có run_id (bản cũ) luôn bị xóa."""
    drop, keep = [], []
    for c in collections:
        base = next((n for n in names if c == n + STAGING_SUFFIX or c.startswith(n + STAGING_SUFFIX + "_")), None)
        if base is None:
            continue
        rid = c[len(base + STAGING_SUFFIX) + 1:] if c != base + STAGING_SUFFIX else ""
        if rid == run_id:
            continue
        job = jobs_by_id.get(rid) if rid else None
        status = job.get("status") if job else None
        t = run_time(rid) if rid else None
        old = t is None or (now - t).total_seconds() > ttl_s
        if status in FINISHED:
            drop.append((c, f"jobs: {status}"))
        elif old:
            drop.append((c, f"jobs: {status or 'không có'}, quá hạn khóa"))
        else:
            keep.append((c, f"run {rid} chưa kết thúc ({status or 'không có trong jobs'}) và chưa quá hạn khóa"))
    return drop, keep


def pending_rollback(jobs: list[dict], run_id: str, has_prev: bool) -> dict | None:
    """Lần chạy hỏng giữa chừng cần lùi lại trước khi chạy tiếp, hoặc None.

    jobs: bản ghi `jobs` của job ({_id, status, replaced, replacing, generated_at: datetime}). Chỉ xét khi còn
    `__prev` (`has_prev`). Hỏng giữa chừng = run khác run hiện tại, sau lần `ok` gần nhất, `status` ∈ {failed,
    running} và `replaced` hoặc `replacing` khác rỗng (running = process chết giữa lúc thay; failed có `replaced`
    = thay xong nhưng bước sau hỏng). `blocked`, `rolled_back`, `dry_run` hay `failed` chưa thay gì thì không chặn.
    """
    if not has_prev:
        return None
    oks = [j["generated_at"] for j in jobs if j.get("status") == "ok" and j.get("generated_at") is not None]
    last_ok = max(oks, default=None)
    cands = [j for j in jobs
             if j.get("_id") != run_id and j.get("status") in INTERRUPTED
             and (j.get("replaced") or j.get("replacing"))
             and (last_ok is None or (j.get("generated_at") is not None and j["generated_at"] > last_ok))]
    return max(cands, key=lambda j: j["generated_at"], default=None)


def parse_iso_utc(s: str) -> datetime:
    """Chuỗi ngày giờ JSON mở rộng của MongoDB ('2026-11-22T22:32:43.24Z', '...43Z', '...+00:00') → datetime UTC.

    `datetime.fromisoformat` của Python 3.10 (image Spark) chỉ nhận phần giây lẻ đúng 3 hoặc 6 chữ số.
    """
    import re
    m = re.fullmatch(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})(?:\.(\d+))?(Z|[+-]\d{2}:\d{2})?", s.strip())
    if not m:
        raise ValueError(f"không đọc được ngày giờ: {s!r}")
    base, frac, tz = m.groups()
    dt = datetime.fromisoformat(base + ("." + (frac + "000000")[:6] if frac else "")
                                + ("+00:00" if tz in (None, "Z") else tz))
    return dt.astimezone(timezone.utc)


# ---------------------------------------------------------------------------
# QC: cờ mồ côi, G0 dở dang (thuần Python, dùng ở G0 và job QC)
# ---------------------------------------------------------------------------
def classify_unmatched(flags: list[dict], unmatched_ids: set[str]) -> tuple[list[dict], list[dict]]:
    """Cờ có hiệu lực mà phần tử không còn trong `tide_extremes` (ví dụ đổi bản chính) → (bỏ qua, chặn).

    Bỏ qua (G0 cảnh báo, không áp; job QC xóa ở lần chạy sau): cờ `auto` chưa ai xem (`qc_rules.is_decided` sai).
    Chặn (G0 dừng mã 1): cờ đã có quyết định hoặc cờ thủ công – người phải xem rồi `qc_decide.py retire`.
    """
    from vngis.qc_rules import is_decided
    skip, block = [], []
    for f in flags:
        if f["_id"] not in unmatched_ids:
            continue
        (skip if f.get("origin", "auto") == "auto" and not is_decided(f) else block).append(f)
    return skip, block


def g0_unsettled(jobs: list[dict], prevs: list[str]) -> str | None:
    """Lý do G0 còn dở thay thế (job QC phải dừng, mã 4), hoặc None: còn `<tên>__prev`, hoặc có run G0
    `failed`/`running` đã thay (`replaced`/`replacing`) sau lần `ok` gần nhất."""
    if prevs:
        return f"còn bản sao lưu {prevs} của G0 (đang chạy hoặc hỏng giữa chừng)"
    bad = pending_rollback(jobs, "", True)
    if bad:
        return (f"run G0 {bad['_id']} (status {bad.get('status')}) đã thay {bad.get('replaced') or bad.get('replacing')} "
                f"rồi dừng giữa chừng sau lần ok gần nhất")
    return None
