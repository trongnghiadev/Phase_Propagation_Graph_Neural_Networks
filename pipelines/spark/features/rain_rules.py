"""Quy tắc thuần Python cho G4 – quy hoạch mưa theo nút đồ thị (KHÔNG cần Spark).

Module này chứa toàn bộ phép tính hình học và quy tắc gán trọng số của G4, tách khỏi
job `features/rainfall_by_node.py` để:
1. Job G4 chỉ còn phần điều phối Spark (đọc/ghi MongoDB, Parquet).
2. Unit test (`pipelines/spark/tests/test_features_rules.py`) chạy được bằng Python
   cục bộ mà KHÔNG cần cài `pyspark`.

**Quy tắc quy hoạch (đã tài liệu hoá trong `results/G4/README.md`).**
Chưa có sản phẩm mưa vệ tinh (IMERG/CHIRPS) trong CSDL, nên G4 dùng **fallback**
dựa trên trạm mưa (`rainfall_daily`):
- Mỗi trạm mưa được gán vào mọi nút đồ thị nằm trong bán kính `radius_km`
  (mặc định 40 km) tính từ **đoạn thẳng** (LineString) của reach — không phải tâm
  điểm.
- Trọng số diện tích: `w(n, s) = uparea(n) / Σ_{n'} uparea(n')` với `n'` là các nút
  mà trạm `s` được gán. Tổng trọng số mỗi nút = 1 (được assert trong test và job).
- Khi `uparea = 0` (không có diện tích) → chia đều cho các trạm được gán.

**Tham chiếu hình học.** Phép chiếu cục bộ equirectangular với bán kính Trái Đất
`EARTH_RADIUS_M = 6371008.8` m; sai số < 0,1 % với đoạn ngắn ở vĩ độ 10–11°.
"""
from __future__ import annotations

import json
import math

EARTH_RADIUS_M = 6371008.8
RADIUS_KM_DEFAULT = 40.0


def point_in_bbox(lon: float, lat: float, bbox: list[float]) -> bool:
    """Điểm có nằm trong bbox không? `bbox` = [minLon, minLat, maxLon, maxLat]."""
    if not bbox or len(bbox) < 4:
        return True
    return bbox[0] <= lon <= bbox[2] and bbox[1] <= lat <= bbox[3]


def distance_point_segment_m(lon: float, lat: float,
                             a: tuple[float, float], b: tuple[float, float]) -> float:
    """Khoảng cách (m) từ điểm `(lon, lat)` tới đoạn thẳng `a`–`b` trên mặt cầu Trái Đất.

    Dùng phép chiếu cục bộ (equirectangular) quanh điểm: đủ chính xác cho đoạn ngắn
    (mỗi reach của MERIT dài vài chục km) và nhanh hơn nhiều so với trục địa lý.
    """
    lat0 = math.radians((lat + a[1] + b[1]) / 3.0)
    kx = math.cos(lat0) * math.pi / 180.0 * EARTH_RADIUS_M
    ky = math.pi / 180.0 * EARTH_RADIUS_M
    px, py = lon * kx, lat * ky
    ax, ay = a[0] * kx, a[1] * ky
    bx, by = b[0] * kx, b[1] * ky
    dx, dy = bx - ax, by - ay
    denom = dx * dx + dy * dy
    if denom <= 1e-12:
        return math.hypot(px - ax, py - ay)
    t = ((px - ax) * dx + (py - ay) * dy) / denom
    t = max(0.0, min(1.0, t))
    return math.hypot(px - (ax + t * dx), py - (ay + t * dy))


def distance_point_line_m(lon: float, lat: float, coords: list) -> float:
    """Khoảng cách nhỏ nhất từ điểm tới đa tuyến `coords` = [[lon, lat], ...]."""
    if not coords:
        return float("inf")
    if len(coords) == 1:
        c = coords[0]
        return distance_point_segment_m(lon, lat, (c[0], c[1]), (c[0], c[1]))
    best = float("inf")
    for i in range(len(coords) - 1):
        a, b = coords[i], coords[i + 1]
        d = distance_point_segment_m(lon, lat, (a[0], a[1]), (b[0], b[1]))
        if d < best:
            best = d
            if best < 1.0:
                break
    return best


def _as_mapping(obj):
    """Chuẩn hoá mọi kiểu đầu vào (dict / Row / chuỗi JSON) về `dict` hoặc `None`.

    MongoDB Spark Connector trả document lồng thành `pyspark.sql.Row` khi schema được
    khai báo, nên `geometry` / `bbox` có thể là `Row(...)` chứ không phải dict.
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


def parse_geometry(geom) -> list:
    """`geometry` (GeoJSON từ MongoDB) → danh sách `[lon, lat]`.

    MongoDB trả `geometry` dưới dạng document lồng; connector có thể trả chuỗi JSON
    hoặc `Row` tuỳ theo schema khai báo — cả ba đều được xử lý.
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
    return []


def parse_bbox(bbox) -> list[float]:
    """`bbox` (GeoJSON từ MongoDB) → [minLon, minLat, maxLon, maxLat].

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


def assign_station_to_nodes(rain_stations: list[dict], node_geoms: dict[int, dict],
                            radius_km: float) -> dict[int, list[tuple[str, float]]]:
    """Gán trạm mưa vào các nút trong bán kính.

    `rain_stations`: [{code, lon, lat}].
    `node_geoms`: {COMID: {"coords": [[lon, lat], ...], "bbox": [...]}}.

    Trả về {COMID: [(mã trạm, khoảng cách m), ...]} — ứng viên trong bán kính.
    """
    radius_m = radius_km * 1000.0
    out: dict[int, list[tuple[str, float]]] = {}
    for comid, g in node_geoms.items():
        coords = g.get("coords") or []
        if not coords:
            continue
        bbox = g.get("bbox") or []
        cands = []
        for st in rain_stations:
            lon, lat = st["lon"], st["lat"]
            if bbox and not point_in_bbox(lon, lat, bbox):
                # bbox của reach rất hẹp so với bán kính → kiểm tra bằng bbox mở rộng
                if not point_in_bbox(lon, lat, [bbox[0] - 1, bbox[1] - 1, bbox[2] + 1, bbox[3] + 1]):
                    continue
            d = distance_point_line_m(lon, lat, coords)
            if d <= radius_m:
                cands.append((st["code"], d))
        if cands:
            out[comid] = sorted(cands, key=lambda x: (x[1], x[0]))
    return out


def node_weights(node_area: dict[int, float],
                 assign: dict[int, list[tuple[str, float]]]) -> dict[int, dict[str, float]]:
    """Trọng số diện tích cho mỗi (nút, trạm mưa), tổng mỗi nút = 1.

    **Công thức (đã kiểm tra bằng test, tổng trọng số mỗi nút = 1 tuyệt đối).**

    Với nút `n` và tập trạm được gán `S(n)`:

        w(n, s) = [ uparea(n) / A(s) ] / Σ_{s' ∈ S(n)} [ uparea(n) / A(s') ]

    trong đó `A(s)` = tổng `uparea` của **các nút** mà trạm `s` được gán vào — tức là
    diện tích lưu vực mà trạm `s` đại diện.

    Ý nghĩa: trạm `s` càng bao phủ lưu vực lớn (A(s) lớn) thì giá trị quan trắc của nó
    càng "loãng" trên nút `n`, nên trọng số càng nhỏ; đồng thời trọng số được **chuẩn hoá
    lại theo tập S(n)** để tổng mỗi nút = 1 (không tạo mưa 0 giả khi một trạm thiếu số
    liệu — việc phân bổ lại được làm ở tầng Spark).

    Trường hợp `uparea(n) = 0` hoặc `A(s) = 0` → dùng trọng số đều `1/|S(n)|`
    (không chia được theo diện tích).

    `node_area`: {COMID: uparea km²}.
    `assign`: kết quả `assign_station_to_nodes`.

    Trả về {COMID: {mã trạm: trọng số}}. Tổng trọng số mỗi nút = 1.
    """
    # A(s) = tổng uparea của các nút mà trạm s được gán vào
    station_area: dict[str, float] = {}
    for comid, cands in assign.items():
        area = float(node_area.get(comid, 0.0) or 0.0)
        for code, _d in cands:
            station_area[code] = station_area.get(code, 0.0) + area

    out: dict[int, dict[str, float]] = {}
    for comid, cands in assign.items():
        area = float(node_area.get(comid, 0.0) or 0.0)
        # phần số thô (trước khi chuẩn hoá): uparea(n) / A(s)
        raw: dict[str, float] = {}
        for code, _d in cands:
            a_s = station_area.get(code, 0.0)
            raw[code] = (area / a_s) if (area > 0.0 and a_s > 0.0) else 1.0
        total = sum(raw.values())
        if total <= 0.0:
            w = {code: 1.0 / len(cands) for code, _d in cands}
        else:
            w = {code: raw[code] / total for code, _d in cands}
        out[comid] = w
    return out
