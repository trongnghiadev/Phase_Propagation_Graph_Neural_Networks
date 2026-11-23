"""QC số liệu bản tin: quy tắc tự động R1–R3, khóa cờ, gộp kết quả vào `qc_flags`, validator (chỉ thư viện chuẩn).

Module này không import pymongo/pydantic/pyyaml để job Spark (driver và executor) dùng được; phần ghi quyết định
có kiểm toán nằm ở `vngis.qc` (pymongo).

Quy tắc (tham số ở `config/default.yaml` mục `qc`, bản sao mặc định là `DEFAULT_PARAMS`, test giữ hai bên khớp nhau):

- **R1** giá trị ngoài khoảng hợp lý của trạm: theo từng (trạm, loại đỉnh/chân), ngoài
  [p_low − margin·(p_high − p_low), p_high + margin·(p_high − p_low)] với p_low/p_high là phân vị 0,1%/99,9% của
  chính chuỗi đó, hoặc ngoài ngưỡng vật lý `physical_m` (theo trạm; Vũng Tàu dùng mốc Hải đồ, các trạm sông mốc khác).
- **R2** trong cùng trạm, cùng ngày quan trắc (cùng bản tin): đỉnh thấp nhất ngày thấp hơn chân cao nhất ngày
  (quá `min_gap_m`). Chỉ gắn cờ CẶP vi phạm lớn nhất (đỉnh thấp nhất và chân cao nhất), không gắn cả ngày. Lưu ý triều
  hỗn hợp/nhật triều: đỉnh phụ của ngày có thể rất thấp và chân phụ rất cao, nên một phần cờ R2 là hiện tượng thật,
  không phải lỗi số liệu.
- **R3** chỉ trạm sông, chỉ đỉnh có giờ thật (`ts_placeholder = false`, qc gốc `ok`): độ trễ = giờ đỉnh − giờ đỉnh
  Vũng Tàu trước đó (trong `max_ref_gap_h` giờ). Hai lượt: (1) ghép với đỉnh Vũng Tàu gần nhất trước đó → trung vị
  sơ bộ của trạm; (2) trong các đỉnh Vũng Tàu trước đó, ghép với đỉnh làm độ trễ gần trung vị sơ bộ nhất (tránh ghép
  nhầm sang chu kỳ trước khi đỉnh Vũng Tàu tương ứng thiếu hoặc lệch) → trung vị, IQR. Gắn cờ khi
  |độ trễ − trung vị| > max(`iqr_k` × IQR, `min_dev_h`). Chỉ xét khi ngày (giờ VN) của thời điểm tham chiếu kỳ vọng
  (giờ đỉnh − trung vị sơ bộ) có ít nhất `min_ref_peaks_per_day` đỉnh Vũng Tàu tin được: ngày Vũng Tàu chỉ có một đỉnh
  (đỉnh kia "không xảy ra") thì không biết đỉnh sông ứng với chu kỳ nào. Trạm có ít hơn `min_samples` độ trễ thì
  không xét.

Thời điểm trong các hàm thuần là epoch giây UTC (int).
"""
from __future__ import annotations

import bisect
import copy
import math
from datetime import datetime, timezone
from typing import Any, Iterable

COLLECTION = "tide_extremes"
FLAGS = "qc_flags"
AUDIT = "audit_logs"
COLLECTION_BULLETINS = "hydromet_bulletins"

STATUSES = ("suspect", "confirmed_ok", "rejected", "corrected")
DECIDED = ("confirmed_ok", "rejected", "corrected")
ORIGINS = ("auto", "manual")
RULES = ("R1", "R2", "R3")
# qc gốc của G0 coi là "có số và giờ tin được" khi chọn đỉnh tham chiếu và đỉnh xét R3
GOOD_QC = ("ok",)

DEFAULT_PARAMS: dict[str, Any] = {
    "rules_version": 1,
    "reference_station": "VUNG_TAU",
    "r1": {
        "p_low": 0.001,
        "p_high": 0.999,
        "margin": 0.1,
        # [min, max] theo trạm; "default" áp cho trạm không khai báo riêng (trạm sông)
        "physical_m": {"default": [-3.5, 3.5], "VUNG_TAU": [-1.0, 5.0]},
    },
    "r2": {"min_gap_m": 0.0},
    "r3": {"max_ref_gap_h": 14.0, "iqr_k": 3.0, "min_dev_h": 3.0, "min_samples": 100, "min_ref_peaks_per_day": 2},
    # vngis.qc.reconcile_pending chỉ xử lý bản audit pending cũ hơn ngần này giây (không đụng lệnh đang chạy)
    "audit_pending_grace_s": 600,
}

ROUND = 4  # làm tròn số đo ghi vào `rules[].metric` (để so sánh giữa các lần chạy không nhiễu dấu phẩy động)


def params_with(overrides: dict | None = None) -> dict:
    """`DEFAULT_PARAMS` gộp đệ quy với `overrides` (mapping gộp theo khóa, giá trị khác bị thay)."""
    out = copy.deepcopy(DEFAULT_PARAMS)

    def merge(a: dict, b: dict) -> None:
        for k, v in b.items():
            if isinstance(v, dict) and isinstance(a.get(k), dict):
                merge(a[k], v)
            else:
                a[k] = copy.deepcopy(v)

    merge(out, overrides or {})
    return out


def _r(x: float | None) -> float | None:
    return None if x is None else round(float(x), ROUND)


# ---------------------------------------------------------------------------
# Thống kê
# ---------------------------------------------------------------------------
def percentile(sorted_vals: list[float], p: float) -> float:
    """Phân vị nội suy tuyến tính (vị trí p·(n−1)), như `percentile` của Spark SQL và numpy mặc định."""
    if not sorted_vals:
        raise ValueError("chuỗi rỗng")
    if not 0.0 <= p <= 1.0:
        raise ValueError(f"p = {p} ngoài [0, 1]")
    pos = p * (len(sorted_vals) - 1)
    lo = math.floor(pos)
    hi = min(lo + 1, len(sorted_vals) - 1)
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


# ---------------------------------------------------------------------------
# R1
# ---------------------------------------------------------------------------
def physical_limits(station: str, physical_m: dict) -> tuple[float, float] | None:
    lim = physical_m.get(station, physical_m.get("default"))
    if lim is None:
        return None
    lo, hi = float(lim[0]), float(lim[1])
    if lo >= hi:
        raise ValueError(f"ngưỡng vật lý của {station} không hợp lệ: {lim}")
    return lo, hi


def r1_bounds(station: str, p_lo: float, p_hi: float, r1: dict) -> dict:
    """Khoảng hợp lý của một (trạm, loại) từ phân vị đã tính và tham số `r1`."""
    span = p_hi - p_lo
    if span < 0:
        raise ValueError(f"{station}: p_low {p_lo} > p_high {p_hi}")
    phys = physical_limits(station, r1.get("physical_m") or {})
    return {"p_lo": _r(p_lo), "p_hi": _r(p_hi), "lo": _r(p_lo - r1["margin"] * span),
            "hi": _r(p_hi + r1["margin"] * span),
            "phys_lo": phys[0] if phys else None, "phys_hi": phys[1] if phys else None}


def r1_check(value: float | None, b: dict | None) -> dict | None:
    """Cờ R1 cho một giá trị, hoặc None. Biên nằm trong khoảng (không gắn cờ khi đúng bằng biên)."""
    if value is None or b is None:
        return None
    reasons = []
    if value > b["hi"]:
        reasons.append(f"> {b['hi']} (p_high {b['p_hi']} + biên)")
    if value < b["lo"]:
        reasons.append(f"< {b['lo']} (p_low {b['p_lo']} − biên)")
    if b["phys_hi"] is not None and value > b["phys_hi"]:
        reasons.append(f"> ngưỡng vật lý {b['phys_hi']}")
    if b["phys_lo"] is not None and value < b["phys_lo"]:
        reasons.append(f"< ngưỡng vật lý {b['phys_lo']}")
    if not reasons:
        return None
    return {"rule": "R1", "detail": f"giá trị {value} m " + "; ".join(reasons),
            "metric": {"value": _r(value), **b}}


# ---------------------------------------------------------------------------
# R2
# ---------------------------------------------------------------------------
def r2_check(highs: list[tuple[Any, float | None]], lows: list[tuple[Any, float | None]],
             min_gap_m: float = 0.0) -> dict[Any, dict]:
    """highs/lows: (khóa, giá trị) của một trạm trong một ngày quan trắc. → {khóa: cờ R2}.

    Chỉ xét cặp vi phạm lớn nhất: đỉnh thấp nhất h_min và chân cao nhất l_max; nếu h_min < l_max − min_gap thì gắn
    cờ đúng hai phần tử đó. Bằng nhau thì lấy phần tử có khóa nhỏ nhất (khóa = (trạm, loại, ts) → phần tử sớm nhất), nên
    kết quả không phụ thuộc thứ tự truyền vào. Giá trị None bị bỏ qua;
    ngày thiếu đỉnh hoặc thiếu chân thì không xét.
    """
    hv = [(k, v) for k, v in highs if v is not None]
    lv = [(k, v) for k, v in lows if v is not None]
    if not hv or not lv:
        return {}
    kh, min_high = min(sorted(hv, key=lambda x: x[0]), key=lambda x: x[1])
    kl, max_low = max(sorted(lv, key=lambda x: x[0]), key=lambda x: x[1])
    if not min_high < max_low - min_gap_m:
        return {}
    return {kh: {"rule": "R2", "detail": f"đỉnh {min_high} m thấp hơn chân {max_low} m cùng ngày",
                 "metric": {"value": _r(min_high), "other": _r(max_low), "min_gap_m": min_gap_m}},
            kl: {"rule": "R2", "detail": f"chân {max_low} m cao hơn đỉnh {min_high} m cùng ngày",
                 "metric": {"value": _r(max_low), "other": _r(min_high), "min_gap_m": min_gap_m}}}


# ---------------------------------------------------------------------------
# R3
# ---------------------------------------------------------------------------
def prev_reference(ref_sorted: list[int], t: int, max_gap_s: float, expected_lag_s: float | None = None
                   ) -> int | None:
    """Đỉnh tham chiếu có thời điểm trong [t − max_gap_s, t]; None nếu không có.

    `expected_lag_s` None → đỉnh gần nhất trước đó; có giá trị → đỉnh làm (t − r) gần `expected_lag_s` nhất
    (bằng nhau thì lấy đỉnh muộn hơn).
    """
    i = bisect.bisect_right(ref_sorted, t)
    if i == 0 or t - ref_sorted[i - 1] > max_gap_s:
        return None
    if expected_lag_s is None:
        return ref_sorted[i - 1]
    best, j = None, i - 1
    while j >= 0 and t - ref_sorted[j] <= max_gap_s:
        r = ref_sorted[j]
        if best is None or abs(t - r - expected_lag_s) < abs(t - best - expected_lag_s):
            best = r
        j -= 1
    return best


def lag_hours(ref_sorted: list[int], t: int, max_gap_h: float, expected_lag_h: float | None = None
              ) -> float | None:
    r = prev_reference(ref_sorted, t, max_gap_h * 3600, None if expected_lag_h is None else expected_lag_h * 3600)
    return None if r is None else (t - r) / 3600.0


def lag_stats(lags_h: Iterable[float]) -> dict:
    """Trung vị, tứ phân vị, IQR của độ trễ (giờ)."""
    s = sorted(lags_h)
    if not s:
        return {"n": 0, "median": None, "q1": None, "q3": None, "iqr": None}
    q1, med, q3 = percentile(s, 0.25), percentile(s, 0.5), percentile(s, 0.75)
    return {"n": len(s), "median": _r(med), "q1": _r(q1), "q3": _r(q3), "iqr": _r(q3 - q1)}


def r3_limit(stats: dict, r3: dict) -> float | None:
    """Độ lệch tối đa (giờ) so với trung vị; None khi trạm không đủ mẫu."""
    if not stats or stats.get("n", 0) < r3["min_samples"] or stats.get("iqr") is None:
        return None
    return round(max(r3["iqr_k"] * stats["iqr"], r3["min_dev_h"]), ROUND)


def r3_check(lag_h: float | None, ref_ts: int | None, stats: dict, r3: dict) -> dict | None:
    limit = r3_limit(stats, r3)
    if lag_h is None or limit is None:
        return None
    dev = lag_h - stats["median"]
    if abs(dev) <= limit:
        return None
    return {"rule": "R3",
            "detail": f"trễ {lag_h:.2f} h sau đỉnh tham chiếu, lệch {dev:+.2f} h so với trung vị {stats['median']} h "
                      f"(giới hạn ±{limit} h)",
            "metric": {"lag_h": _r(lag_h), "ref_ts": iso_utc(ref_ts) if ref_ts is not None else None,
                       "median_h": stats["median"], "q1_h": stats["q1"], "q3_h": stats["q3"], "limit_h": limit}}


VN_OFFSET_S = 7 * 3600


def vn_day(t: int) -> str:
    """Ngày giờ Việt Nam 'YYYY-MM-DD' của epoch UTC."""
    return datetime.fromtimestamp(int(t) + VN_OFFSET_S, tz=timezone.utc).strftime("%Y-%m-%d")


def reference_peaks(elements: Iterable[dict], reference_station: str, exclude_ts: Iterable[int] = ()
                    ) -> tuple[list[int], dict[str, int]]:
    """(epoch các đỉnh tham chiếu tin được đã sắp xếp, số đỉnh đó theo ngày VN).

    `exclude_ts`: ts (epoch) của đỉnh tham chiếu bị loại khỏi tập tham chiếu – các đỉnh Vũng Tàu có quyết định
    `rejected` (giờ/giá trị không tin được). Các trạm khác vẫn được đánh giá trên giá trị gốc.
    """
    ex = {int(t) for t in exclude_ts}
    ref = sorted(int(e["ts"]) for e in elements
                 if e["station"] == reference_station and e["kind"] == "high" and not e.get("ts_placeholder")
                 and e.get("value") is not None and e.get("qc") in GOOD_QC and int(e["ts"]) not in ex)
    days: dict[str, int] = {}
    for t in ref:
        days[vn_day(t)] = days.get(vn_day(t), 0) + 1
    return ref, days


def reference_day_ok(t: int, pairing_median_h: float | None, ref_days: dict[str, int] | None, r3: dict) -> bool:
    """Ngày VN của thời điểm tham chiếu kỳ vọng (t − trung vị sơ bộ) có đủ đỉnh tham chiếu không."""
    need = r3.get("min_ref_peaks_per_day", 0)
    if not need or ref_days is None:
        return True
    expected = int(t - (pairing_median_h or 0.0) * 3600)
    return ref_days.get(vn_day(expected), 0) >= need


def r3_candidates(elements: Iterable[dict], reference_station: str) -> list[tuple[str, int]]:
    """(trạm, ts) của các đỉnh xét R3."""
    return [(e["station"], int(e["ts"])) for e in elements if r3_applies(e, reference_station)]


def build_lag_stats(cands: Iterable[tuple[str, int]], ref_sorted: list[int], r3: dict,
                    ref_days: dict[str, int] | None = None) -> dict[str, dict]:
    """Thống kê độ trễ theo trạm (hai lượt ghép, xem docstring module). Lượt 2 chỉ dùng đỉnh có ngày tham chiếu đủ
    đỉnh (`reference_day_ok`), như khi đánh giá."""
    by: dict[str, list[int]] = {}
    for s, t in cands:
        by.setdefault(s, []).append(t)
    out = {}
    for s, ts in sorted(by.items()):
        first = [x for x in (lag_hours(ref_sorted, t, r3["max_ref_gap_h"]) for t in ts) if x is not None]
        pm = lag_stats(first)["median"]
        second = [x for x in (lag_hours(ref_sorted, t, r3["max_ref_gap_h"], pm) for t in ts
                              if reference_day_ok(t, pm, ref_days, r3)) if x is not None]
        out[s] = {**lag_stats(second), "pairing_median": pm}
    return out


# ---------------------------------------------------------------------------
# Đánh giá một nhóm (trạm, ngày quan trắc)
# ---------------------------------------------------------------------------
def element_key(e: dict) -> tuple[str, str, int]:
    return (e["station"], e["kind"], int(e["ts"]))


def r3_applies(e: dict, reference_station: str) -> bool:
    return (e["station"] != reference_station and e["kind"] == "high" and not e.get("ts_placeholder")
            and e.get("value") is not None and e.get("qc") in GOOD_QC)


def evaluate_group(elements: list[dict], ctx: dict) -> list[dict]:
    """Áp R1–R3 cho mọi phần tử của MỘT trạm trong MỘT ngày quan trắc (một bản tin).

    elements: {station, kind, ts (epoch s), ts_placeholder, value (giá trị gốc), qc (qc gốc của G0), source_file}.
    ctx: {"params", "bounds": {(station, kind): r1_bounds},
          "lag_stats": {station: lag_stats của lượt 2 + "pairing_median" (trung vị lượt 1)},
          "ref": [epoch đỉnh tham chiếu đã sắp xếp], "ref_days": {ngày VN: số đỉnh tham chiếu}}
    – dựng bằng `reference_peaks`, `build_lag_stats`.
    → [{station, kind, ts, ts_placeholder, value, source_file, rules: [...]}] chỉ các phần tử có ít nhất một cờ,
    `rules` sắp theo tên quy tắc.
    """
    p = ctx["params"]
    elements = sorted(elements, key=element_key)     # thứ tự ổn định (groupByKey của Spark không giữ thứ tự)
    hits: dict[tuple, list[dict]] = {}

    def add(k, h):
        if h:
            hits.setdefault(k, []).append(h)

    for e in elements:
        add(element_key(e), r1_check(e.get("value"), ctx["bounds"].get((e["station"], e["kind"]))))
    highs = [(element_key(e), e.get("value")) for e in elements if e["kind"] == "high"]
    lows = [(element_key(e), e.get("value")) for e in elements if e["kind"] == "low"]
    for k, h in r2_check(highs, lows, p["r2"]["min_gap_m"]).items():
        add(k, h)
    ref = ctx.get("ref") or []
    for e in elements:
        if not r3_applies(e, p["reference_station"]):
            continue
        st = ctx["lag_stats"].get(e["station"]) or {}
        exp = st.get("pairing_median")
        if not reference_day_ok(int(e["ts"]), exp, ctx.get("ref_days"), p["r3"]):
            continue
        r = prev_reference(ref, int(e["ts"]), p["r3"]["max_ref_gap_h"] * 3600, None if exp is None else exp * 3600)
        lag = None if r is None else (int(e["ts"]) - r) / 3600.0
        add(element_key(e), r3_check(lag, r, st, p["r3"]))
    by_key = {element_key(e): e for e in elements}
    out = []
    for k in sorted(hits):
        e = by_key[k]
        out.append({"station": e["station"], "kind": e["kind"], "ts": int(e["ts"]),
                    "ts_placeholder": bool(e.get("ts_placeholder")), "value": e.get("value"),
                    "source_file": e.get("source_file"), "rules": sorted(hits[k], key=lambda h: h["rule"])})
    return out


# ---------------------------------------------------------------------------
# Khóa cờ, giá trị hiệu lực
# ---------------------------------------------------------------------------
def iso_utc(t: int | datetime) -> str:
    dt = t if isinstance(t, datetime) else datetime.fromtimestamp(int(t), tz=timezone.utc)
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def flag_id(station: str, kind: str, ts: int | datetime, collection: str = COLLECTION) -> str:
    """`_id` ổn định của cờ: `<collection>:<station>:<kind>:<ts UTC ISO>` (ts thật hoặc ts giữ chỗ)."""
    if kind not in ("high", "low"):
        raise ValueError(f"kind '{kind}' phải là high/low")
    return f"{collection}:{station}:{kind}:{iso_utc(ts)}"


def effective(raw_value: float | None, raw_qc: str, status: str | None, corrected_value: float | None = None
              ) -> tuple[float | None, str]:
    """(value, qc) sau khi áp quyết định: rejected → (None, "rejected"); corrected → (corrected_value, "corrected");
    suspect/confirmed_ok/không cờ → giữ nguyên. Cùng quy tắc với G0 (`flatten_bulletins.apply_qc_flags`)."""
    if status == "rejected":
        return None, "rejected"
    if status == "corrected":
        if corrected_value is None:
            raise ValueError("corrected cần corrected_value")
        return float(corrected_value), "corrected"
    return raw_value, raw_qc


def validate_decision(status: str, corrected_value: Any, reason: str | None, decided_by: str | None) -> None:
    """Quyết định hợp lệ: status thuộc STATUSES; corrected cần corrected_value số hữu hạn, status khác không được có;
    reason và decided_by không rỗng."""
    if status not in STATUSES:
        raise ValueError(f"status '{status}' không thuộc {STATUSES}")
    if status == "corrected":
        if isinstance(corrected_value, bool) or not isinstance(corrected_value, (int, float)) \
                or not math.isfinite(corrected_value):
            raise ValueError("status corrected cần corrected_value là số")
    elif corrected_value is not None:
        raise ValueError(f"status {status} không được có corrected_value")
    if not (reason or "").strip():
        raise ValueError("cần lý do (reason)")
    if not (decided_by or "").strip():
        raise ValueError("cần người/vai trò quyết định (decided_by)")


def is_decided(doc: dict) -> bool:
    """Cờ đã được người xem (có `decided_at`) hoặc đang mang quyết định khác suspect."""
    return doc.get("decided_at") is not None or doc.get("status", "suspect") != "suspect"


def is_active(doc: dict) -> bool:
    """Cờ có hiệu lực khi G0 áp dụng: không `retired`, và (có quyết định, hoặc cờ thủ công, hoặc suspect mà quy tắc
    tự động vẫn kích hoạt)."""
    if doc.get("retired"):
        return False
    if doc.get("status") in DECIDED or doc.get("origin") == "manual":
        return True
    return not doc.get("auto_cleared", False)


# ---------------------------------------------------------------------------
# Gộp kết quả quy tắc vào qc_flags (KHÔNG BAO GIỜ đụng các field quyết định của người)
# ---------------------------------------------------------------------------
DECISION_FIELDS = ("status", "corrected_value", "reason", "decided_by", "decided_at")


def new_flag_doc(hit: dict, run_id: str, now: datetime, rules_version: int, collection: str = COLLECTION) -> dict:
    ts = datetime.fromtimestamp(hit["ts"], tz=timezone.utc)
    return {"_id": flag_id(hit["station"], hit["kind"], hit["ts"], collection), "collection": collection,
            "station": hit["station"], "kind": hit["kind"], "ts": ts, "ts_placeholder": hit["ts_placeholder"],
            "value": hit["value"], "source_file": hit["source_file"], "origin": "auto", "rules": hit["rules"],
            "rules_version": rules_version, "auto_cleared": False,
            "status": "suspect", "corrected_value": None, "reason": None, "decided_by": None, "decided_at": None,
            "first_flagged_run": run_id, "last_seen_run": run_id, "created_at": now, "updated_at": now,
            "version": 1}


def plan_one(existing: dict | None, hit: dict | None, run_id: str, now: datetime, rules_version: int) -> dict | None:
    """Thao tác cho một cờ. → None (không làm gì) hoặc
    {"op": "insert", "doc"} | {"op": "update", "id", "version", "set"} (tăng version) |
    {"op": "touch", "id", "set"} (chỉ last_seen_run, không tăng version) | {"op": "delete", "id", "version"}.

    - Có kết quả quy tắc, chưa có cờ → insert (status suspect, origin auto).
    - Có cả hai: cập nhật `rules`, `rules_version`, bỏ `auto_cleared`; `value` chỉ cập nhật khi chưa có quyết định,
      có quyết định mà giá trị gốc đổi thì ghi `value_mismatch`/`current_value`. Không đổi gì → touch.
    - Có cờ, quy tắc không còn kích hoạt: cờ auto chưa ai quyết định → delete; còn lại (có quyết định, hoặc cờ
      thủ công từng bị quy tắc gắn) → `auto_cleared: true`. Cờ thủ công không có quy tắc → không làm gì.
    - Cờ `retired` (mồ côi, người đánh dấu) → không làm gì.
    """
    if existing is None and hit is None:
        return None
    if existing is None:
        return {"op": "insert", "doc": new_flag_doc(hit, run_id, now, rules_version)}
    if existing.get("retired"):
        return None          # cờ đã được người đánh dấu mồ côi: job không đụng tới (gỡ retired bằng tay)
    fid, ver = existing["_id"], existing.get("version", 0)
    if hit is None:
        if not existing.get("rules") or existing.get("auto_cleared"):
            return None
        if existing.get("origin", "auto") == "auto" and not is_decided(existing):
            return {"op": "delete", "id": fid, "version": ver}
        return {"op": "update", "id": fid, "version": ver,
                "set": {"auto_cleared": True, "auto_cleared_run": run_id, "updated_at": now}}
    s: dict[str, Any] = {}
    if existing.get("rules") != hit["rules"]:
        s["rules"] = hit["rules"]
    if existing.get("rules_version") != rules_version:
        s["rules_version"] = rules_version
    if existing.get("auto_cleared"):
        s["auto_cleared"] = False
        s["auto_cleared_run"] = None
    if is_decided(existing):
        # Giá trị gốc hiện tại (hit) so với:
        #   `decided_value` — giá trị quyết định đang gắn vào (người đã xem và quyết định với giá trị đó);
        #   `value`        — giá trị lúc gắn cờ (giá trị CŨ mà quyết định dựa trên đó).
        # Mismatch khi giá trị hiện tại khác cả hai.
        decided = existing.get("decided_value")
        bound = decided if decided is not None else existing.get("value")
        mismatch = bound != hit["value"]
        # Người xem đã thấy giá trị lệch (`current_value` khác null) và job QC lại thấy đúng giá trị LÚC GẮN CỜ
        # (`value`, tức G0 chưa kịp cập nhật) → đó là giá trị CŨ, không phải giá trị mới: KHÔNG gắn lại mismatch,
        # nếu không cờ mắc kẹt vô hạn và không có đường thoát.
        if mismatch and existing.get("current_value") is not None and hit["value"] == existing.get("value"):
            mismatch = False
        if bool(existing.get("value_mismatch")) != mismatch:
            s["value_mismatch"] = mismatch
            s["current_value"] = hit["value"] if mismatch else None
        elif mismatch and existing.get("current_value") != hit["value"]:
            s["current_value"] = hit["value"]
    elif existing.get("value") != hit["value"]:
        s["value"] = hit["value"]
    for f in ("source_file", "ts_placeholder"):
        if existing.get(f) != hit[f]:
            s[f] = hit[f]
    if not s:
        if existing.get("last_seen_run") == run_id:
            return None
        return {"op": "touch", "id": fid, "set": {"last_seen_run": run_id}}
    s.update({"last_seen_run": run_id, "updated_at": now})
    assert not set(s) & set(DECISION_FIELDS)
    return {"op": "update", "id": fid, "version": ver, "set": s}


def plan_updates(existing: dict[str, dict], hits: dict[str, dict], run_id: str, now: datetime,
                 rules_version: int) -> list[dict]:
    """Thao tác cho mọi cờ (theo `_id`), thứ tự ổn định."""
    ops = []
    for fid in sorted(set(existing) | set(hits)):
        op = plan_one(existing.get(fid), hits.get(fid), run_id, now, rules_version)
        if op:
            ops.append(op)
    return ops


# ---------------------------------------------------------------------------
# Validator / index (dùng chung cho job Spark và vngis.qc)
# ---------------------------------------------------------------------------
_NUM_OR_NULL = {"bsonType": ["double", "int", "long", "null"]}
_STR_OR_NULL = {"bsonType": ["string", "null"]}
_RULE_ITEM = {"bsonType": "object", "required": ["rule", "detail"],
              "properties": {"rule": {"enum": list(RULES)}, "detail": {"bsonType": "string"},
                             "metric": {"bsonType": ["object", "null"]}}}

QC_FLAGS_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["_id", "collection", "station", "kind", "ts", "value", "origin", "rules", "status", "version",
                 "created_at", "updated_at"],
    "properties": {
        "_id": {"bsonType": "string"},
        "collection": {"enum": [COLLECTION]},
        "station": {"bsonType": "string"},
        "kind": {"enum": ["high", "low"]},
        "ts": {"bsonType": "date"},
        "ts_placeholder": {"bsonType": "bool"},
        "value": _NUM_OR_NULL,
        "source_file": _STR_OR_NULL,
        "origin": {"enum": list(ORIGINS)},
        "rules": {"bsonType": "array", "items": _RULE_ITEM},
        "rules_version": {"bsonType": ["int", "long", "null"]},
        "auto_cleared": {"bsonType": "bool"},
        "status": {"enum": list(STATUSES)},
        "corrected_value": _NUM_OR_NULL,
        "reason": _STR_OR_NULL,
        "decided_by": _STR_OR_NULL,
        "decided_at": {"bsonType": ["date", "null"]},
        "first_flagged_run": _STR_OR_NULL,
        "last_seen_run": _STR_OR_NULL,
        "version": {"bsonType": ["int", "long"], "minimum": 1},
        "retired": {"bsonType": "bool"},
        "retired_at": {"bsonType": ["date", "null"]},
        "retired_by": _STR_OR_NULL,
        "retired_reason": _STR_OR_NULL,
        "unretired_at": {"bsonType": ["date", "null"]},
        "unretired_by": _STR_OR_NULL,
        "unretired_reason": _STR_OR_NULL,
        "value_mismatch": {"bsonType": "bool"},
        "current_value": _NUM_OR_NULL,
        "decided_value": _NUM_OR_NULL,
    },
    # corrected ⇔ có corrected_value
    "oneOf": [
        {"required": ["corrected_value"],
         "properties": {"status": {"enum": ["corrected"]}, "corrected_value": {"bsonType": ["double", "int", "long"]}}},
        {"properties": {"status": {"enum": ["suspect", "confirmed_ok", "rejected"]},
                        "corrected_value": {"bsonType": "null"}}},
    ],
}}
QC_FLAGS_INDEXES = [
    {"key": {"collection": 1, "station": 1, "kind": 1, "ts": 1}, "name": "target_unique", "unique": True},
    {"key": {"status": 1, "station": 1}, "name": "status_station_idx"},
    {"key": {"rules.rule": 1}, "name": "rule_idx"},
]

AUDIT_LOGS_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["at", "actor", "action", "collection", "doc_id", "before", "after", "reason", "state"],
    "properties": {
        "at": {"bsonType": "date"},
        "actor": {"bsonType": "string"},
        "action": {"bsonType": "string"},
        "source": _STR_OR_NULL,
        "collection": {"bsonType": "string"},
        "doc_id": {"bsonType": "string"},
        "target": {"bsonType": ["object", "null"]},
        "before": {"bsonType": ["object", "null"]},
        "after": {"bsonType": ["object", "null"]},
        "reason": {"bsonType": "string"},
        "state": {"enum": ["pending", "applied"]},
    },
}}
AUDIT_LOGS_INDEXES = [
    {"key": {"collection": 1, "doc_id": 1, "at": 1}, "name": "doc_at_idx"},
    {"key": {"at": -1}, "name": "at_idx"},
]

# Module `vngis` chỉ dùng thư viện chuẩn (gửi sang executor Spark bằng `pipelines/spark/common/pyfiles.ship_vngis`;
# test `test_module_thuan_khong_can_pymongo_pydantic_yaml` kiểm tra import được khi không có pymongo/pydantic/yaml).
PURE_MODULES = ("__init__", "text", "admin", "province_labels", "bulletins", "qc_rules")
