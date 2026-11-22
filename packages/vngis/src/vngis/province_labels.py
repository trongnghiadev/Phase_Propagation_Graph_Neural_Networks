"""Gán tỉnh ``{era, code}`` cho nhãn tỉnh ghi trong bản tin (ví dụ cột tỉnh của hồ chứa), theo ADR 0003.

Module thuần Python (chỉ thư viện chuẩn): dùng chung cho ``pipelines/ingest/stations/create_stations.py``
(``stations.admin_history``) và job Spark G0 (``reservoir_daily.province``).

Quy tắc, cho chuỗi bản ghi ``(ngày quan trắc, nhãn)`` của MỘT đối tượng (một hồ):

1. Thời kỳ của bản ghi tính theo NGÀY QUAN TRẮC (``observation_date``), như ``vngis.admin.era_of``:
   ``date`` = ngày lịch Việt Nam; ``datetime`` naive coi là UTC.
2. Chia thành các đoạn liên tiếp có cùng nhãn (so theo ``norm_name``, không theo chuỗi thô) và cùng thời kỳ.
3. Đoạn trước mốc: tra ``province_timeline`` ở thời kỳ ``before_2025_07``.
4. Đoạn sau mốc: tra ở ``from_2025_07``. NGOẠI LỆ "đổi tên chậm": đoạn sau mốc dùng lại đúng nhãn (đã chuẩn hóa)
   của đoạn ngay trước mốc, và đoạn kế tiếp (nhãn khác) bắt đầu trong ``max_lag_days`` ngày kể từ mốc → tra ở
   thời kỳ cũ và ghi ``note``. Cần vì cùng tên có thể là hai tỉnh ở hai thời kỳ (Tây Ninh: 72 cũ, 80 mới).
   Ngoài giới hạn đó KHÔNG tự lùi về thời kỳ cũ: tên không có ở thời kỳ mới → không khớp.
5. Đoạn sau mốc nằm ở CUỐI dữ liệu (chưa có nhãn mới phía sau) KHÔNG được coi là đổi tên chậm: tra thời kỳ mới.
   Nếu đoạn đó dùng lại nhãn trước mốc và bản ghi cuối < mốc + ``max_lag_days`` ngày thì chưa biết bản tin
   có đổi tên ngay sau đó hay không → ``provisional = True`` (kết quả chưa chắc, chạy lại khi có thêm dữ liệu).
6. Nhãn ``None``/rỗng = "không có thông tin": KHÔNG làm đứt đoạn, không được dùng làm đoạn trước/sau khi xét đổi tên
   chậm. Các bản ghi đó gom thành đoạn riêng (mỗi thời kỳ một đoạn), ``code = None``, ``reason = "nhãn tỉnh rỗng"``.
   Nhãn không có trong bảng hoặc trỏ tới nhiều mã → không khớp (``code = None``), không lỗi.

``era`` của đoạn là thời kỳ ĐEM TRA (khác ``obs_era`` – thời kỳ theo ngày quan trắc – khi đổi tên chậm).
"""
from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta

from .admin import BEFORE, EFFECTIVE_UTC, FROM, _instant
from .text import norm_name

DEFAULT_MAX_LAG_DAYS = 31        # khớp admin.max_label_lag_days trong config/default.yaml
NOTE_LAG = ("bản tin còn ghi tên tỉnh cũ sau mốc 01/07/2025 (đổi tên chậm); "
            "tra theo thời kỳ before_2025_07")


@dataclass(frozen=True)
class LabelRun:
    """Một đoạn liên tiếp cùng nhãn (đã chuẩn hóa) và cùng thời kỳ quan trắc."""
    label: str | None             # nhãn gốc gặp đầu tiên trong đoạn
    labels: tuple                 # mọi cách viết gốc trong đoạn (có thể chứa None)
    label_norm: str               # '' nếu nhãn rỗng
    first: datetime               # thời điểm (UTC) của bản ghi đầu
    last: datetime                # thời điểm (UTC) của bản ghi cuối (bao gồm)
    n: int                        # số bản ghi
    obs_era: str                  # thời kỳ theo ngày quan trắc
    era: str | None = None        # thời kỳ dùng để tra (khác obs_era khi đổi tên chậm); None nếu không khớp
    code: int | None = None
    name: str | None = None
    note: str | None = None
    reason: str | None = None     # lý do không khớp
    provisional: bool = False     # đoạn cuối dữ liệu, có thể còn đổi tên chậm (xem quy tắc 5)

    @property
    def matched(self) -> bool:
        return self.code is not None


def build_index(timeline: Iterable[Mapping]) -> dict[tuple[str, str], list[dict]]:
    """(era, tên chuẩn hóa) → các tỉnh ``{code, name}``, từ ``name_norm``/``aliases_norm`` (chuẩn hóa lại bằng
    ``norm_name`` để không phụ thuộc cách dữ liệu được nạp)."""
    idx: dict[tuple[str, str], dict[int, dict]] = defaultdict(dict)
    for p in timeline:
        names = [p.get("name_norm"), *(p.get("aliases_norm") or [])]
        if p.get("name"):
            names.append(p["name"])
        for n in names:
            k = norm_name(n) if n else ""
            if k:
                idx[(p["era"], k)][int(p["code"])] = {"code": int(p["code"]), "name": p.get("name")}
    return {k: [v[c] for c in sorted(v)] for k, v in idx.items()}


def lookup(index: Mapping[tuple[str, str], list[dict]], era: str, label: str | None) -> tuple[dict | None, str | None]:
    """→ (tỉnh, None) nếu khớp đúng một tỉnh; (None, lý do) nếu không."""
    n = norm_name(label) if label else ""
    if not n:
        return None, "nhãn tỉnh rỗng"
    hits = index.get((era, n), [])
    if len(hits) == 1:
        return hits[0], None
    if not hits:
        return None, f"'{label}' không có ở thời kỳ {era}"
    return None, f"'{label}' trỏ tới nhiều tỉnh ở thời kỳ {era}: {[h['code'] for h in hits]}"


def split_runs(records: Iterable[tuple[datetime | date, str | None]]) -> list[LabelRun]:
    """Sắp theo thời điểm, chia đoạn theo (nhãn chuẩn hóa, thời kỳ quan trắc). Bản ghi nhãn rỗng không làm đứt
    đoạn: chúng được gom riêng thành đoạn ``label_norm = ''`` (mỗi thời kỳ một đoạn), xếp theo ``first``."""
    rows = sorted(((_instant(t), lbl) for t, lbl in records), key=lambda r: (r[0], norm_name(r[1] or "")))
    runs: list[dict] = []
    empty: dict[str, dict] = {}

    def extend(r, t, lbl):
        r["last"], r["n"] = t, r["n"] + 1
        if lbl not in r["labels"]:
            r["labels"].append(lbl)

    for t, lbl in rows:
        n = norm_name(lbl) if lbl else ""
        era = FROM if t >= EFFECTIVE_UTC else BEFORE
        new = {"label": lbl, "labels": [lbl], "label_norm": n, "first": t, "last": t, "n": 1, "obs_era": era}
        if not n:
            if era in empty:
                extend(empty[era], t, lbl)
            else:
                empty[era] = new
        elif runs and runs[-1]["label_norm"] == n and runs[-1]["obs_era"] == era:
            extend(runs[-1], t, lbl)
        else:
            runs.append(new)
    out = sorted(runs + list(empty.values()), key=lambda r: (r["first"], r["label_norm"] == ""))
    return [LabelRun(**{**r, "labels": tuple(r["labels"])}) for r in out]


def assign_runs(records: Iterable[tuple[datetime | date, str | None]], timeline_or_index,
                max_lag_days: int = DEFAULT_MAX_LAG_DAYS) -> list[LabelRun]:
    """Gán ``{era, code, name, note, provisional}`` cho từng đoạn nhãn của một đối tượng (quy tắc ở đầu module).

    ``timeline_or_index``: danh sách bản ghi ``province_timeline`` hoặc kết quả ``build_index``.
    """
    if max_lag_days < 0:
        raise ValueError("max_lag_days phải >= 0")
    index = timeline_or_index if isinstance(timeline_or_index, Mapping) else build_index(timeline_or_index)
    runs = split_runs(records)
    informative = [r for r in runs if r.label_norm]          # đoạn rỗng không là đoạn trước/sau
    pos = {id(r): i for i, r in enumerate(informative)}
    lag_limit = EFFECTIVE_UTC + timedelta(days=max_lag_days)
    out = []
    for r in runs:
        era, note, provisional = r.obs_era, None, False
        i = pos.get(id(r))
        if i is not None and r.obs_era == FROM and i > 0:
            prev = informative[i - 1]
            nxt = informative[i + 1] if i + 1 < len(informative) else None
            if prev.obs_era == BEFORE and prev.label_norm == r.label_norm:
                if nxt is not None and nxt.first <= lag_limit:
                    era, note = BEFORE, NOTE_LAG
                elif nxt is None and r.last < lag_limit:     # trước mốc + max_lag_days (không gồm đầu mút)
                    provisional = True
        p, reason = lookup(index, era, r.label)
        if p is None:
            out.append(LabelRun(**{**r.__dict__, "era": None, "reason": reason, "provisional": provisional}))
        else:
            out.append(LabelRun(**{**r.__dict__, "era": era, "code": p["code"], "name": p["name"], "note": note,
                                   "provisional": provisional}))
    return out


def assign_all(records_by_key: Mapping[str, Iterable[tuple[datetime | date, str | None]]], timeline,
               max_lag_days: int = DEFAULT_MAX_LAG_DAYS) -> tuple[dict[str, list[LabelRun]], list[dict]]:
    """``assign_runs`` cho nhiều đối tượng (ví dụ mỗi hồ). → (đoạn theo khóa, danh sách không khớp)."""
    index = build_index(timeline)
    runs = {k: assign_runs(v, index, max_lag_days) for k, v in records_by_key.items()}
    unmatched = [{"key": k, "label": r.label, "obs_era": r.obs_era, "first": r.first, "last": r.last, "n": r.n,
                  "reason": r.reason, "empty": not r.label_norm} for k, rs in runs.items() for r in rs if not r.matched]
    return runs, unmatched
