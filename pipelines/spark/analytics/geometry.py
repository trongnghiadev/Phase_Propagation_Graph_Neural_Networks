"""Hình học thuần Python cho Q5 – ghép OSM ↔ MERIT (KHÔNG cần Spark).

Module này tách riêng toàn bộ phép tính khoảng cách không gian khỏi job Q5 để:
1. Job Q5 (`analytics/q5_spatial_join.py`) chỉ còn phần điều phối Spark.
2. Unit test (`pipelines/spark/tests/test_features_rules.py`) chạy được bằng Python
   cục bộ, KHÔNG cần `pyspark` (đúng như `test_flatten_rules.py`).

**Công thức.** Mọi khoảng cách tính trên mặt cầu Trái Đất với bán kính
`EARTH_RADIUS_M = 6371008.8` m (IUGG mean radius).  Phép chiếu cục bộ
**equirectangular** quanh vĩ độ trung bình của cặp điểm:
- `x = lon · cos(lat0) · π/180 · R`
- `y = lat · π/180 · R`

Sai số so với haversine/khoảng cách geodesic thật < 0,1 % với đoạn thẳng vài
chục km ở vĩ độ 10–11° (tức dưới 1 m) — không ảnh hưởng ngưỡng 50/100/250 m.

**Tham chiếu.** Times, B. (2005). *Spectral Methods in Practice*, 2nd ed., ch. 1.
Vì nhiều, Đại học Quốc gia TP.HCM — tài liệu nội bộ.
"""
from __future__ import annotations

import json
import math

EARTH_RADIUS_M = 6371008.8


def _as_mapping(obj):
    """Chuẩn hoá mọi kiểu đầu vào (dict / Row / chuỗi JSON) về `dict` hoặc `None`.

    MongoDB Spark Connector trả document lồng thành `pyspark.sql.Row` khi schema được
    khai báo, nên `geometry` có thể là `Row(type=..., coordinates=...)` chứ không phải dict.
    """
    if obj is None:
        return None
    if isinstance(obj, str):
        try:
            obj = json.loads(obj)
        except (ValueError, TypeError):
            return None
    if isinstance(obj, dict):
        return obj
    as_dict = getattr(obj, "asDict", None)
    if callable(as_dict):
        try:
            return as_dict(recursive=True)
        except TypeError:
            return as_dict()
    return None


def parse_geom(geom) -> list:
    """GeoJSON (dict, `Row` hoặc chuỗi JSON) → danh sách `[lon, lat]`.

    Hỗ trợ `LineString`, `MultiLineString` (nối các nhánh) và `Point`.
    """
    g = _as_mapping(geom)
    if not g:
        return []
    gtype = g.get("type")
    coords = g.get("coordinates")
    if gtype == "LineString":
        return coords or []
    if gtype == "MultiLineString":
        out = []
        for line in coords or []:
            out.extend(line)
        return out
    if gtype == "Point" and coords:
        return [coords]
    return []


def simplify_polyline(coords: list, tol_deg: float) -> list:
    """Rút gọn đa tuyến bằng **Douglas–Peucker** với sai số `tol_deg` (đơn vị độ).

    Mục đích (Q5): CSDL OSM có đoạn tới 1 941 điểm; so sánh hai đa tuyến dài với
    O(n·m) cặp đoạn làm spatial join chậm. Rút gọn với sai số **bé hơn nhiều** bán
    kính khớp nhỏ nhất (250 m ≈ 0,0023° ở vĩ độ 10,8°) không làm đổi kết luận
    "khớp / không khớp", nhưng giảm mạnh số đoạn phải so sánh.

    Giữ nguyên điểm đầu và điểm cuối; các điểm bị loại có sai số ≤ `tol_deg` so với
    đường gấp khúc ban đầu (theo khoảng cách vuông góc tới đoạn thẳng).
    """
    n = len(coords)
    if n <= 2 or tol_deg <= 0:
        return coords

    keep = [False] * n
    keep[0] = keep[n - 1] = True
    stack = [(0, n - 1)]
    while stack:
        i0, i1 = stack.pop()
        if i1 <= i0 + 1:
            continue
        ax, ay = coords[i0]
        bx, by = coords[i1]
        dx, dy = bx - ax, by - ay
        den = dx * dx + dy * dy
        best_idx = -1
        best_d = -1.0
        for k in range(i0 + 1, i1):
            px, py = coords[k]
            if den <= 1e-18:
                d = math.hypot(px - ax, py - ay)
            else:
                t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / den))
                d = math.hypot(px - (ax + t * dx), py - (ay + t * dy))
            if d > best_d:
                best_d = d
                best_idx = k
        if best_d > tol_deg and best_idx > 0:
            keep[best_idx] = True
            stack.append((i0, best_idx))
            stack.append((best_idx, i1))
    return [c for c, k in zip(coords, keep) if k]


def parse_bbox(bbox) -> list[float]:
    """GeoJSON bbox → `[minLon, minLat, maxLon, maxLat]`.

    Chấp nhận list phẳng, dict GeoJSON (`{"type": "Polygon", "coordinates": [...]}`),
    `Row` của connector, hoặc chuỗi JSON.
    """
    if isinstance(bbox, str):
        try:
            bbox = json.loads(bbox)
        except (ValueError, TypeError):
            return []
    if bbox is None:
        return []
    if isinstance(bbox, dict):
        bbox = bbox.get("coordinates") or []
    elif not isinstance(bbox, (list, tuple)):
        # `Row` của connector → chuyển về dict rồi lấy `coordinates`
        as_dict = getattr(bbox, "asDict", None)
        if not callable(as_dict):
            return []
        try:
            bbox = as_dict(recursive=True).get("coordinates") or []
        except TypeError:
            bbox = as_dict().get("coordinates") or []
    if not bbox or len(bbox) < 4:
        return []
    lons = [float(bbox[i]) for i in range(0, len(bbox), 2)]
    lats = [float(bbox[i]) for i in range(1, len(bbox), 2)]
    return [min(lons), min(lats), max(lons), max(lats)]


def bbox_gap_m(b1: list[float], b2: list[float]) -> float:
    """Khoảng cách (m) giữa hai bbox (0 nếu chồng lấn) — dùng để prefilter."""
    if not b1 or not b2:
        return 0.0
    lat0 = math.radians((b1[1] + b1[3] + b2[1] + b2[3]) / 4.0)
    kx = math.cos(lat0) * math.pi / 180.0 * EARTH_RADIUS_M
    ky = math.pi / 180.0 * EARTH_RADIUS_M
    dx = max(0.0, max(b1[0] - b2[2], b2[0] - b1[2])) * kx
    dy = max(0.0, max(b1[1] - b2[3], b2[1] - b1[3])) * ky
    return math.hypot(dx, dy)


def point_segment_distance_m(p, a, b) -> float:
    """Khoảng cách từ điểm `p` tới đoạn `a`–`b` (toạ độ đã chiếu phẳng)."""
    dx, dy = b[0] - a[0], b[1] - a[1]
    den = dx * dx + dy * dy
    if den <= 1e-12:
        return math.hypot(p[0] - a[0], p[1] - a[1])
    t = max(0.0, min(1.0, ((p[0] - a[0]) * dx + (p[1] - a[1]) * dy) / den))
    return math.hypot(p[0] - (a[0] + t * dx), p[1] - (a[1] + t * dy))


def segment_segment_distance_m(p1, p2, p3, p4) -> float:
    """Khoảng cách (m) giữa hai đoạn thẳng trên mặt cầu Trái Đất.

    Tham số là cặp toạ độ `(lon, lat)`.  Dùng phép chiếu cục bộ equirectangular quanh
    vĩ độ trung bình của bốn điểm; sai số < 0,1 % với đoạn ngắn ở vĩ độ thấp.

    Thuật toán: kiểm tra giao cắt trước (khoảng cách = 0), nếu không giao cắt thì dùng
    khoảng cách tối thiểu giữa một đầu mút của đoạn này và đoạn kia (4 trường hợp) —
    đây là công thức đúng cho hai đoạn thẳng không giao nhau trong mặt phẳng, tránh
    được trường hợp `denom = 0` (hai đoạn vuông góc) mà thuật toán tham số bị chia 0.
    """
    lat0 = math.radians((p1[1] + p2[1] + p3[1] + p4[1]) / 4.0)
    kx = math.cos(lat0) * math.pi / 180.0 * EARTH_RADIUS_M
    ky = math.pi / 180.0 * EARTH_RADIUS_M

    def proj(p):
        return (p[0] * kx, p[1] * ky)

    a1, a2 = proj(p1), proj(p2)
    b1, b2 = proj(p3), proj(p4)

    # --- giao cắt (bao gồm tiếp xúc đầu mút) → khoảng cách 0 ---------------
    d1 = (a2[0] - a1[0], a2[1] - a1[1])
    d2 = (b2[0] - b1[0], b2[1] - b1[1])
    denom = d1[0] * d2[0] + d1[1] * d2[1]
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) > 1e-9:
        s = ((b1[0] - a1[0]) * d2[1] - (b1[1] - a1[1]) * d2[0]) / cross
        t = ((b1[0] - a1[0]) * d1[1] - (b1[1] - a1[1]) * d1[0]) / cross
        if -1e-12 <= s <= 1.0 + 1e-12 and -1e-12 <= t <= 1.0 + 1e-12:
            return 0.0

    # --- không giao cắt: min trên 4 cặp (đầu mút ↔ đoạn) ------------------
    return min(point_segment_distance_m(a1, b1, b2),
               point_segment_distance_m(a2, b1, b2),
               point_segment_distance_m(b1, a1, a2),
               point_segment_distance_m(b2, a1, a2))


def polyline_distance_m(a: list, b: list) -> float:
    """Khoảng cách nhỏ nhất giữa hai đa tuyến (chuỗi `[lon, lat]`)."""
    if not a or not b:
        return float("inf")
    if len(a) == 1:
        return segment_segment_distance_m(a[0], a[0], b[0], b[-1])
    if len(b) == 1:
        return segment_segment_distance_m(a[0], a[-1], b[0], b[0])
    best = float("inf")
    for i in range(len(a) - 1):
        for j in range(len(b) - 1):
            d = segment_segment_distance_m(a[i], a[i + 1], b[j], b[j + 1])
            if d < best:
                best = d
    return best


# ---------------------------------------------------------------------------
# Bản tối ưu cho spatial join hàng loạt (Q5)
# ---------------------------------------------------------------------------
def project_polyline(coords: list, lat0: float) -> list[tuple[float, float]]:
    """Chiếu một đa tuyến sang mặt phẳng (metre) quanh vĩ độ tham chiếu `lat0`.

    Chiếu **một lần** cho cả hai đa tuyến rồi so sánh — nhanh hơn nhiều so với chiếu
    lại ở mỗi cặp đoạn (hàm `segment_segment_distance_m` phải chiếu lại 4 điểm mỗi lần).
    """
    kx = math.cos(lat0) * math.pi / 180.0 * EARTH_RADIUS_M
    ky = math.pi / 180.0 * EARTH_RADIUS_M
    return [(c[0] * kx, c[1] * ky) for c in coords]


def projected_segment_distance_m(a1, a2, b1, b2) -> float:
    """Khoảng cách (m) giữa hai đoạn **đã chiếu phẳng** (toạ độ metre).

    Cùng thuật toán với `segment_segment_distance_m` nhưng nhận sẵn toạ độ đã chiếu.
    """
    d1 = (a2[0] - a1[0], a2[1] - a1[1])
    d2 = (b2[0] - b1[0], b2[1] - b1[1])
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) > 1e-9:
        s = ((b1[0] - a1[0]) * d2[1] - (b1[1] - a1[1]) * d2[0]) / cross
        t = ((b1[0] - a1[0]) * d1[1] - (b1[1] - a1[1]) * d1[0]) / cross
        if -1e-12 <= s <= 1.0 + 1e-12 and -1e-12 <= t <= 1.0 + 1e-12:
            return 0.0
    return min(point_segment_distance_m(a1, b1, b2),
               point_segment_distance_m(a2, b1, b2),
               point_segment_distance_m(b1, a1, a2),
               point_segment_distance_m(b2, a1, a2))


def projected_polyline_distance_m(pa: list, pb: list) -> float:
    """Khoảng cách nhỏ nhất giữa hai đa tuyến **đã chiếu phẳng**."""
    if not pa or not pb:
        return float("inf")
    best = float("inf")
    na, nb = len(pa), len(pb)
    for i in range(na - 1):
        a1, a2 = pa[i], pa[i + 1]
        for j in range(nb - 1):
            d = projected_segment_distance_m(a1, a2, pb[j], pb[j + 1])
            if d < best:
                best = d
                if best <= 0.0:
                    return 0.0
    return best


def buffer_summary(matches, buffers, n_osm, n_merit) -> list[list]:
    """Tổng hợp cặp khớp theo từng ngưỡng khoảng cách.

    **Đơn vị:** `buffers` và giá trị khoảng cách trong `matches` đều tính bằng **mét**.
    Hàm này tập trung quy tắc lọc để tránh nhầm đơn vị (đã từng xảy ra lỗi nhân thêm
    1 000 ở Q5 khiến mọi cặp trong bán kính 250 km đều bị coi là khớp).

    `matches`: các cặp `(..., distance_m, ...)`, distance ở vị trí index 5.
    Trả về mỗi dòng: `[buffer_m, n_matches, n_osm_matched, n_merit_matched,
    pct_osm_matched, pct_merit_matched, mean_distance_m]`.
    """
    out = []
    for b in buffers:
        sel = [m for m in matches if m[5] <= b]
        osm_hit = {m[0] for m in sel}
        mer_hit = {m[3] for m in sel}
        out.append([b, len(sel), len(osm_hit), len(mer_hit),
                    round(100.0 * len(osm_hit) / n_osm, 3) if n_osm else 0.0,
                    round(100.0 * len(mer_hit) / n_merit, 3) if n_merit else 0.0,
                    round(sum(m[5] for m in sel) / len(sel), 2) if sel else ""])
    return out


# ---------------------------------------------------------------------------
# Bản tối ưu cho spatial join (Q5): có ngưỡng cắt + bbox đoạn
# ---------------------------------------------------------------------------
def segment_boxes(poly: list) -> list[tuple[float, float, float, float]]:
    """Bbox (metre) của từng đoạn của một đa tuyến đã chiếu phẳng."""
    boxes = []
    for i in range(len(poly) - 1):
        x1, y1 = poly[i]
        x2, y2 = poly[i + 1]
        boxes.append((min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)))
    return boxes


def projected_polyline_distance_within(pa: list, pb: list, boxes_a, boxes_b,
                                       limit_m: float) -> float:
    """Khoảng cách nhỏ nhất giữa hai đa tuyến đã chiếu, **dừng sớm** nếu vượt `limit_m`.

    Tối ưu cho spatial join:
    - Bỏ qua ngay mọi cặp đoạn mà bbox của chúng cách nhau quá `limit_m` (đoạn nào đó
      không thể tạo ra khoảng cách nhỏ hơn khoảng cách giữa hai bbox).
    - Trả về `limit_m + 1` ngay khi biết khoảng cách vượt ngưỡng → không cần quét hết.

    Giá trị trả về là khoảng cách thực khi ≤ `limit_m`, ngược lại là số > `limit_m`
    (người gọi chỉ cần biết "vượt ngưỡng").
    """
    if not pa or not pb:
        return limit_m + 1.0
    best = limit_m + 1.0
    na = len(pa) - 1
    nb = len(pb) - 1
    lim2 = limit_m * limit_m
    for i in range(na):
        a1, a2 = pa[i], pa[i + 1]
        ax1, ay1, ax2, ay2 = boxes_a[i]
        for j in range(nb):
            bx1, by1, bx2, by2 = boxes_b[j]
            # khoảng cách giữa hai bbox (0 nếu chồng lấn)
            dx = max(0.0, ax1 - bx2, bx1 - ax2)
            dy = max(0.0, ay1 - by2, by1 - ay2)
            if dx * dx + dy * dy > lim2:
                continue
            d = projected_segment_distance_m(a1, a2, pb[j], pb[j + 1])
            if d < best:
                best = d
                if best <= 0.0:
                    return 0.0
    return best
