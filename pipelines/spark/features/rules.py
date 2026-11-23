"""Quy tắc thuần Python dùng chung cho G1/G3/G4/G5 (không cần Spark, có test).

    from features import rules

Gồm:
- Dựng cây MERIT từ gốc lưu vực (`build_tree`), kiểm tra topology (không chu trình, mỗi nút ≤ 1 hạ nguồn).
- Cấp topo (`topo_levels`), luật tỉa theo `uparea` + đường đi giữa trạm/hồ và cửa sản (`prune`).
- Thay `slope = 0` bằng trung vị cùng `order` (`fill_slope`), kẹp vận tốc, tính `tau_phys` (Manning + sóng nông).
- Quy tắc ngày của đỉnh/chân triều (`peak_day_offset`) — dùng chung với `pipelines/r/`.
"""
from __future__ import annotations

import math
from collections import defaultdict

# ---------------------------------------------------------------------------
# Hằng số vật lý và tham số mặc định
# ---------------------------------------------------------------------------
G = 9.81                      # gia tốc trọng trường (m/s²)
MANNING_N = 0.030             # hệ số nhám Manning của sông tự nhiên (Chow, Maidment & Mays, 1994)
KINEMATIC_FACTOR = 5.0 / 3.0  # hệ số nhân vận tốc sóng lũ (Leopold & Maddock, 1954)
TIDAL_CELERITY_FACTOR = 0.45   # hệ số k·sqrt(g·h) cho sóng triều trong kênh chảy một phần
SHALLOW_C_MIN = 0.30          # giới hạn dưới vận tốc sóng nông (m/s)
SHALLOW_C_MAX = 6.00          # giới hạn trên vận tốc sóng nông (m/s)
FLOOD_C_MIN = 0.20            # giới hạn dưới vận tốc sóng lũ (m/s)
FLOOD_C_MAX = 8.00            # giới hạn trên vận tốc sóng lũ (m/s)
TAU_MIN_H = 1.0 / 600.0       # τ tối thiểu (0,001 h ≈ 3,6 s) – tránh chia 0
TAU_MAX_H = 72.0              # τ tối đa (72 h) – trần vật lý để chặn giá trị vô lý
MIN_UPAREA_DEFAULT = 100.0    # ngưỡng tỉa mặc định (km²)
SEA_NODE = "SEA_VUNGTAU"      # mã nút ảo đại biển
ROOT_COMID = 44045960         # gốc lưu vực Đồng Nai – Sài Gòn

# ---------------------------------------------------------------------------
# Validator + index cho collection `graph_edges` (dùng chung G1 và G3)
# ---------------------------------------------------------------------------
# `src`/`dst`/`source_comid` là COMID dạng **số**, TRỪ node ảo biển là **chuỗi**
# `SEA_VUNGTAU` (nút không có COMID). Vì vậy validator cho phép cả hai kiểu.
#
# G1 và G3 đều ghi qua mẫu staging → đổi tên nguyên tử; `renameCollection` KHÔNG mang
# theo `$jsonSchema`, nên **mỗi job tạo staging đều phải gắn lại validator này** —
# nếu không, collection chính sẽ mất validator sau lần thay thế của job kia.
GRAPH_EDGES_ID_OR_NULL = {"bsonType": ["int", "long", "string", "null"]}
GRAPH_EDGES_VALIDATOR = {"$jsonSchema": {
    "bsonType": "object",
    "required": ["src", "dst", "dir", "virtual", "length_km", "slope_used", "source_comid"],
    "properties": {
        "src": GRAPH_EDGES_ID_OR_NULL, "dst": GRAPH_EDGES_ID_OR_NULL,
        "dir": {"enum": ["down", "up"]},
        "virtual": {"bsonType": "bool"},
        "length_km": {"bsonType": ["double", "int", "long", "null"]},
        "slope_used": {"bsonType": ["double", "int", "long", "null"]},
        "slope_raw": {"bsonType": ["double", "int", "long", "null"]},
        "celerity_ms": {"bsonType": ["double", "int", "long", "null"]},
        "tau_phys_h": {"bsonType": ["double", "int", "long", "null"]},
        "source_comid": GRAPH_EDGES_ID_OR_NULL,
        "node_kind": {"enum": ["reach", "sea", "reservoir", None]},
    }}}
GRAPH_EDGES_INDEXES = [
    {"key": {"src": 1, "dst": 1, "dir": 1}, "name": "src_dst_dir_unique", "unique": True},
    {"key": {"dir": 1, "src": 1}, "name": "dir_src_idx"},
    {"key": {"dst": 1}, "name": "dst_idx"},
]

# Proxy hình học kênh sông theo `uparea` (A, km²).
#
# MERIT không có mặt cắt ngang, nên suy ra kích thước kênh từ diện tích lưu vực bằng
# quan hệ lưu lượng–diện tích kiểu `Q = k·A^x` (x ≈ 0,5 cho lưu vực vừa, theo
# *Leopold, Wolman & Miller* (1964), *Fluvial Processes*). Từ đó:
#
#   - Bề rộng kênh  W(m) ≈ 22 · A(km²)^0,40   → A=100 km²: 70 m; A=45.551 km²: 730 m
#   - Chiều sâu      h(m)  ≈ 1,6 · A(km²)^0,22   → A=100 km²: 2,5 m; A=45.551 km²: 9,0 m
#   - Bán kính thủy vực R(m) ≈ h (đáy phẳng, h >> bề rộng thì R → h)
#
# R là bán kính THỦY VỰC (đặc trưng chiều sâu kênh, vài mét), KHÔNG phải bề rộng — dùng
# sai sẽ cho vận tốc Manning hàng trăm m/s như bản đầu (đã phát hiện khi chạy G3).
DEPTH_COEF_A = 1.60          # h(m) = DEPTH_COEF_A · A(km²)^0,22
DEPTH_EXP_B = 0.22
WIDTH_COEF_A = 22.0          # W(m) = WIDTH_COEF_A · A(km²)^0,40
WIDTH_EXP_B = 0.40
# Bán kính thủy vực dùng cho Manning: R = h khi kênh rộng và nông (đáy phẳng, đồng bằng),
# hiệu chỉnh theo tỉ lệ W/h để không vượt quá W/2.
R_MAX_FRACTION = 0.5         # R ≤ W/2


# ---------------------------------------------------------------------------
# Cây MERIT
# ---------------------------------------------------------------------------
def build_tree(reaches: dict[int, dict], root: int = ROOT_COMID) -> set[int]:
    """Tập COMID trong cây lưu vực, đi ngược từ gốc theo cột `up1..up4` (trẻ).

    `reaches`: {COMID: {"NextDownID", "up1".."up4", "order", "slope", "uparea", "lengthkm"}}.

    **Hướng của MERIT (đã kiểm tra trên toàn bộ 21.508 reach):** `up1..up4` là các nhánh sông
    CHẢY VÀO reach đó, và luôn thỏa `NextDownID(up_i) == COMID` (21.089/21.089 quan hệ đúng);
    `NextDownID` là reach hạ nguồn (`0` = cửa sản). Vì vậy cây dựng từ `up1..up4` có **gốc là
    cửa sản** và **cạnh đi lên là hướng ngược dòng** (`up` trong hợp đồng `graph_edges`).

    Lưu ý: do nhiều nhánh có thể hợp nhất vào cùng một reach, cây này là **DAG**, không phải cây
    đơn giản (một nút có thể có nhiều cha).
    """
    tree: set[int] = set()
    if root not in reaches:
        # gốc không tồn tại trong CSDL → cây rỗng (không đoán)
        return tree
    stack = [root]
    while stack:
        n = stack.pop()
        if n in tree:
            continue
        tree.add(n)
        d = reaches.get(n)
        if not d:
            continue
        for i in range(1, 5):
            up = d.get(f"up{i}")
            if up and up not in tree:
                stack.append(up)
    return tree


def parent_map(reaches: dict[int, dict], tree: set[int]) -> dict[int, int]:
    """{nút con: nút cha} trong cây. Nếu một nút có 2 cha → giá trị -1 (báo lỗi topology)."""
    parent: dict[int, int] = {}
    for n in tree:
        d = reaches.get(n, {})
        for i in range(1, 5):
            up = d.get(f"up{i}")
            if up in tree:
                if up in parent:
                    parent[up] = -1          # hai cha → không phải cây
                else:
                    parent[up] = n
    return parent


def check_tree(reaches: dict[int, dict], tree: set[int]) -> list[str]:
    """Danh sách lỗi topology (rỗng = hợp lệ).

    MERIT **không phải cây đơn giản**: mỗi COMID có đúng một `NextDownID` (một hạ nguồn), nhưng
    nhiều reach có thể hợp nhất vào CÙNG một COMID hạ nguồn (điểm hội tụ / confluence) — đây là
    đặc tính hợp lệ của lưu vực thật, KHÔNG phải lỗi. Vì vậy:
    - mỗi nút có **đúng một** `NextDownID` (không có hai hạ nguồn trên cùng một reach);
    - đồ thị phải là DAG: không có chu trình hướng xuôi.

    Lỗi trả về khi: một nút có `NextDownID` trỏ về chính nó, hoặc có chu trình hướng xuôi.
    """
    errors: list[str] = []
    for n in tree:
        nxt = reaches.get(n, {}).get("NextDownID")
        if nxt == n:
            errors.append(f"nút {n} trỏ NextDownID về chính nó")
    # chu trình hướng xuôi: đi theo NextDownID từ mỗi nút, không quá |tree| bước
    for n in tree:
        seen = set()
        cur = n
        while cur in tree:
            if cur in seen:
                errors.append(f"chu trình hướng xuôi từ nút {n}")
                break
            seen.add(cur)
            cur = reaches.get(cur, {}).get("NextDownID")
    # chu trình hướng ngược: đi theo `up1..up4` (nhánh chảy vào) — cùng đồ thị nhưng
    # ngược chiều, nên phải kiểm tra riêng (một chu trình có thể chỉ hiện ở hướng này).
    for n in tree:
        seen = set()
        stack = [n]
        while stack:
            cur = stack.pop()
            if cur in seen:
                errors.append(f"chu trình hướng ngược từ nút {n}")
                break
            seen.add(cur)
            for u in upstream_children(reaches, cur, tree):
                if u not in seen:
                    stack.append(u)
    return errors


def confluence_nodes(reaches: dict[int, dict], tree: set[int]) -> dict[int, list[int]]:
    """{COMID hợp nhất: [các hạ nguồn]} – các điểm hội tụ trong cây (nhiều reach cùng đổ về một nút)."""
    down: dict[int, list[int]] = defaultdict(list)
    for n in tree:
        nxt = reaches.get(n, {}).get("NextDownID")
        if nxt in tree:
            down[nxt].append(n)
    return {k: sorted(v) for k, v in down.items() if len(v) > 1}


def downstream_child(reaches: dict[int, dict], node: int, tree: set[int]) -> int | None:
    """Nút hạ nguồn trực tiếp của `node` (theo `NextDownID`) nếu nằm trong cây, ngược lại None.

    Lưu ý hướng: `NextDownID` trỏ về phía CỬA SẢN, còn `up1..up4` trỏ về phía THƯỢNG NGUỒN.
    """
    nxt = reaches.get(node, {}).get("NextDownID")
    return nxt if nxt in tree else None


def upstream_children(reaches: dict[int, dict], node: int, tree: set[int]) -> list[int]:
    """Các nhánh sông chảy vào `node` (theo `up1..up4`), chỉ lấy nhánh nằm trong cây."""
    d = reaches.get(node, {})
    out = []
    for i in range(1, 5):
        up = d.get(f"up{i}")
        if up in tree:
            out.append(up)
    return sorted(out)


def path_to_root(reaches: dict[int, dict], node: int, tree: set[int]) -> list[int]:
    """Đường đi từ `node` NGƯỢC về cửa sản (theo `NextDownID`), chỉ trong cây.

    Dùng cho luật tỉa: giữ mọi nút nằm trên đường đi giữa một trạm/hồ và cửa sản.
    """
    path: list[int] = []
    cur = node
    seen = set()
    while cur is not None and cur in tree and cur not in seen:
        path.append(cur)
        seen.add(cur)
        cur = reaches.get(cur, {}).get("NextDownID")
        if cur not in tree:
            cur = None
    return path


def topo_levels(reaches: dict[int, dict], tree: set[int], root: int = ROOT_COMID) -> dict[str, dict[int, int]]:
    """Cấp topo của mỗi nút theo hai hướng.

    - `topo_level_down`: số bước đi **NGƯỢC dòng** từ cửa sản (0 = cửa sản, tăng dần về thượng
      nguồn). Tính trên đồ thị đảo: từ một nút đi tới các nhánh `up1..up4` chảy vào nó.
      Vì một nút có thể có nhiều nhánh chảy vào, lấy **giá trị lớn nhất** để mọi cạnh `up`
      đi từ cấp thấp lên cấp cao (đúng nghĩa topo).
    - `topo_level_up`: số bước đi **XUÔI dòng** từ thượng nguồn (0 = lá cây, tăng dần về cửa sản),
      tính bằng **đường đi dài nhất** từ một lá (nhánh chảy vào sâu nhất) tới nút đó.

    Cả hai hướng đều là bài toán "đường đi dài nhất trong DAG", giải bằng **thứ tự topo (Kahn)**
    + nới lỏng cạnh — không đệ quy (không tràn với cây sâu) và đúng kể cả khi có hội tụ.

    Lưu ý: MERIT có hội tụ (nhiều nhánh đổ về cùng một reach), nên BFS đơn giản sẽ cho cấp
    phụ thuộc thứ tự duyệt; thuật toán Kahn + longest-path cho kết quả độc lập thứ tự.
    """
    # đồ thị ngược dòng: nút -> các nhánh `up1..up4` chảy vào nó
    children: dict[int, list[int]] = defaultdict(list)
    for n in tree:
        d = reaches.get(n, {})
        for i in range(1, 5):
            up = d.get(f"up{i}")
            if up in tree:
                children[n].append(up)

    # --- `down`: khoảng cách dài nhất từ cửa sản đi ngược dòng -----------
    # Dùng Kahn trên đồ thị ngược: phụ thuộc của `n` là `NextDownID(n)` (cha).
    down = _longest_path_levels(reaches, tree, root, forward=False)
    # --- `up`: khoảng cách dài nhất từ lá đi xuôi dòng --------------------
    up = _longest_path_levels(reaches, tree, root, forward=True)
    return {"down": down, "up": up}


def _longest_path_levels(reaches: dict[int, dict], tree: set[int], root: int,
                         forward: bool) -> dict[int, int]:
    """Cấp = độ dài đường đi dài nhất tới nút, theo hướng `forward`.

    - `forward=False` (`down`): đi **NGƯỢC** dòng từ `root` (cửa sản) qua các nhánh `up1..up4`.
      Cấp của lá = 1, cấp của `root` = 0.
    - `forward=True` (`up`): đi **XUÔI** dòng từ các lá (nút không có nhánh chảy vào) qua
      `NextDownID` về cửa sản. Cấp của lá = 0, cấp của `root` = độ dài đường dài nhất.

    Dùng Kahn (thứ tự topo) + nới lỏng cạnh; nếu đồ thị có chu trình thì các nút còn lại
    được gán giá trị cuối cùng đã biết (không mất nút).
    """
    # Nút theo thứ tự topo dựa trên hướng đi
    if forward:
        # cạnh: lá -> ... -> cửa sản theo NextDownID. Phụ thuộc của nút n là các lá/nguồn chảy vào n.
        indeg = {n: 0 for n in tree}
        succ: dict[int, list[int]] = defaultdict(list)
        for n in tree:
            nxt = reaches.get(n, {}).get("NextDownID")
            if nxt in tree:
                succ[n].append(nxt)
                indeg[nxt] += 1
        # nguồn = nút không có nhánh chảy vào
        queue = [n for n in tree if indeg[n] == 0]
        order: list[int] = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for s in succ.get(n, []):
                indeg[s] -= 1
                if indeg[s] == 0:
                    queue.append(s)
        # đường đi dài nhất tới nút: level[n] = max(level[nguồn]) + 1
        level = {n: 0 for n in tree}
        for n in order:
            for s in succ.get(n, []):
                if level[n] + 1 > level[s]:
                    level[s] = level[n] + 1
    else:
        # cạnh: cha (NextDownID) -> con (up1..up4). Phụ thuộc của nút n là `NextDownID(n)`.
        indeg = {n: 0 for n in tree}
        succ = defaultdict(list)
        for n in tree:
            nxt = reaches.get(n, {}).get("NextDownID")
            if nxt in tree:
                succ[nxt].append(n)
                indeg[n] += 1
        queue = [root] if root in tree and indeg[root] == 0 else []
        # nếu cửa sản có cha ngoài cây (không xảy ra) thì khởi động từ các gốc
        if not queue:
            queue = [n for n in tree if indeg[n] == 0]
        order = []
        while queue:
            n = queue.pop(0)
            order.append(n)
            for s in succ.get(n, []):
                indeg[s] -= 1
                if indeg[s] == 0:
                    queue.append(s)
        level = {root: 0} if root in tree else {}
        for n in order:
            base = level.get(n, 0)
            for s in succ.get(n, []):
                if base + 1 > level.get(s, -1):
                    level[s] = base + 1
    for n in tree:
        level.setdefault(n, 0)
    return level


# ---------------------------------------------------------------------------
# Tỉa
# ---------------------------------------------------------------------------
def keep_nodes(reaches: dict[int, dict], tree: set[int], min_uparea_km2: float,
               must_keep: set[int] | None = None) -> set[int]:
    """Tập nút giữ lại sau khi tỉa.

    **Luật tỉa (hai điều kiện, dùng đồng thời):**
    1. `uparea_km2` ≥ ngưỡng (`min_uparea_km2`), **hoặc** nút là trạm/hồ (`must_keep`).
    2. Nút nằm trên **đường đi giữa một trạm/hồ và cửa sản** (đường đi xuôi, theo `NextDownID`).

    Sau đó giữ thêm các nút cần thiết để đồ thị còn lại **liên thông hai chiều**: mỗi nút
    còn lại phải giữ được **một** nhánh chảy vào nó (để cạnh `up` tồn tại), và nhánh đó
    phải dẫn về được cửa sản theo cạnh `down` (điều kiện (2) đảm bảo cho trạm/hồ; với
    các nút còn lại, `connect_upstream` giữ nhánh chảy vào và lặp tới điểm cố định).

    Lưu ý: bước (2) giữ đường đi xuôi; bước liên thông hai chiều giữ nhánh chảy vào.
    """
    must_keep = must_keep or set()
    keep: set[int] = set()
    for n in tree:
        area = reaches.get(n, {}).get("uparea")
        if area is not None and area >= min_uparea_km2:
            keep.add(n)
    # đường đi xuôi: trạm/hồ → cửa sản
    for n in must_keep:
        if n in tree:
            keep.update(path_to_root(reaches, n, tree))
    # liên thông hai chiều: với mỗi nút còn lại, giữ đoạn ngược dòng tới nút còn lại gần nhất
    keep |= connect_upstream(reaches, tree, keep)
    return keep


def connect_upstream(reaches: dict[int, dict], tree: set[int], keep: set[int]) -> set[int]:
    """Các nút cần thêm vào `keep` để mọi nút còn lại đều có nhánh chảy vào (cạnh `up`).

    Cạnh `up` của một nút `n` là cạnh `n → u` với `u` là nhánh chảy vào `n` (theo `up1..up4`).
    Vì vậy để một nút `n` **đã còn lại** có ít nhất một cạnh `up`, ta phải giữ lại **một**
    trong các nhánh chảy vào nó — không phải giữ lại các nút thượng nguồn của nó.

    Vì vậy hàm này duyệt mọi nút `n` trong `keep`; nếu `n` có nhánh chảy vào `u` nào đó
    chưa thuộc `keep`, thì giữ lại `u` (nút lá không cần cạnh `up` nào).  Chỉ giữ **một**
    nhánh chảy vào cho mỗi nút (nhánh đầu tiên theo thứ tự `up1..up4`) — giữ tất cả sẽ
    làm tỉa kém hiệu quả mà không tăng khả năng liên thông.

    Nhờ đó mọi nút còn lại đều có ít nhất một cạnh `up` đi tới một nút khác trong đồ thị,
    và đi tiếp theo hướng ngược dòng sẽ về được cửa sản.
    """
    extra: set[int] = set()
    # duyệt tới điểm cố định: vừa thêm nút mới thì lại có thể tạo thêm cạnh `up` cần giữ
    changed = True
    while changed:
        changed = False
        for n in sorted(keep | extra):
            ups = upstream_children(reaches, n, tree)
            if not ups:
                continue                      # nút lá → không cần cạnh `up`
            if any(u in keep or u in extra for u in ups):
                continue                      # đã giữ ít nhất một nhánh chảy vào
            extra.add(ups[0])                # giữ nhánh đầu tiên theo thứ tự up1..up4
            changed = True
    return extra


# ---------------------------------------------------------------------------
# Độ dốc
# ---------------------------------------------------------------------------
def median(values: list[float]) -> float:
    """Trung vị (giá trị trung gian khi n chẵn)."""
    if not values:
        raise ValueError("median của danh sách rỗng")
    s = sorted(values)
    n = len(s)
    mid = n // 2
    return s[mid] if n % 2 else (s[mid - 1] + s[mid]) / 2.0


def fill_slope(reaches: dict[int, dict], tree: set[int]) -> dict[int, tuple[float, float]]:
    """Thay `slope = 0` bằng trung vị `slope` của các reach cùng `order` trong cây.

    Trả về {COMID: (slope_gốc, slope_dùng)}. Nhóm `order` không có reach nào có slope > 0
    thì dùng trung vị `slope` của toàn cây (ghi nhận trong báo cáo).
    """
    by_order: dict[int, list[float]] = defaultdict(list)
    for n in tree:
        d = reaches.get(n, {})
        sl = d.get("slope")
        if sl and sl > 0:
            by_order[d.get("order")].append(float(sl))
    all_pos = [v for vals in by_order.values() for v in vals]
    global_med = median(all_pos) if all_pos else 1e-4
    out: dict[int, tuple[float, float]] = {}
    for n in tree:
        d = reaches.get(n, {})
        raw = float(d.get("slope") or 0.0)
        if raw > 0:
            out[n] = (raw, raw)
            continue
        vals = by_order.get(d.get("order")) or []
        out[n] = (raw, median(vals) if vals else global_med)
    return out


# ---------------------------------------------------------------------------
# Vật lý: τ
# ---------------------------------------------------------------------------
def clamp(x: float, lo: float, hi: float) -> float:
    """Kẹp giá trị vào [lo, hi]."""
    return lo if x < lo else (hi if x > hi else x)


def channel_width(uparea_km2: float, width_coef: float = WIDTH_COEF_A,
                  width_exp: float = WIDTH_EXP_B) -> float:
    """Bề rộng kênh W (m) theo proxy `uparea`.

    W ≈ 22 · A(km²)^0,40: A = 100 km² → 70 m; A = 1.000 km² → 220 m; A = 45.551 km² → 730 m.
    Proxy, không phải đo đạc; xem `results/G3/README.md` mục "Giới hạn".
    """
    a = max(float(uparea_km2 or 0.0), 0.01)
    return width_coef * (a ** width_exp)


def shallow_depth(uparea_km2: float, depth_coef: float = DEPTH_COEF_A,
                  depth_exp: float = DEPTH_EXP_B) -> float:
    """Chiều sâu nước trung bình h (m) dùng cho sóng nông.

    h ≈ 1,60 · A(km²)^0,22: A = 100 km² → 2,52 m; A = 1.000 km² → 4,12 m; A = 45.551 km² → 8,99 m.
    Proxy đặc trưng đồng bằng ven biển miền Nam; KHÔNG dùng cho vùng núi (giá trị thấp vẫn là trần).
    """
    a = max(float(uparea_km2 or 0.0), 0.01)
    return depth_coef * (a ** depth_exp)


def hydraulic_radius(uparea_km2: float) -> float:
    """Bán kính thủy vực đặc trưng R (m) theo proxy `uparea`.

    Với kênh đáy phẳng rộng và nông (điều kiện đồng bằng ven biển), `R ≈ h`; hiệu chỉnh
    theo tỉ lệ `W/h` để không vượt quá `W/2` (giới hạn hình học của kênh hẹp).

    A = 100 km² → R ≈ 2,5 m; A = 45.551 km² → R ≈ 9,0 m. Đây là proxy, không phải đo đạc.
    """
    h = shallow_depth(uparea_km2)
    w = channel_width(uparea_km2)
    return min(h, R_MAX_FRACTION * w)


def manning_velocity(radius_m: float, slope: float, n: float = MANNING_N) -> float:
    """Vận tốc dòng chảy Manning: v = (1/n)·R^(2/3)·S^(1/2) (m/s).

    `slope` = độ dốc đường nước S (m/m), `radius_m` = bán kính thủy vực R (m).
    Theo Chow, Maidment & Mays (1994), *Hydrology and Floodplain Analysis*.
    """
    s = max(float(slope or 0.0), 1e-9)
    r = max(float(radius_m or 0.0), 0.1)
    return (1.0 / n) * (r ** (2.0 / 3.0)) * math.sqrt(s)


def celerity_flood(radius_m: float, slope: float, n: float = MANNING_N,
                   factor: float = KINEMATIC_FACTOR) -> float:
    """Vận tốc sóng lũ (sóng kinhetic): c = (5/3)·v (Leopold & Maddock, 1954).

    Hệ số 5/3 đến từ quan hệ tốc độ truyền sóng lũ với tốc độ dòng chảy trung bình.
    """
    v = manning_velocity(radius_m, slope, n)
    return factor * v


def celerity_tidal(depth_m: float, flow_velocity_ms: float = 0.0,
                   factor: float = TIDAL_CELERITY_FACTOR) -> float:
    """Vận tốc sóng triều (sóng nông): c = k·sqrt(g·h) − v_dòng chảy (m/s).

    Sóng triều truyền NGƯỢC dòng chảy nên tốc độ tương đối là `k·sqrt(g·h) − v`.

    **Hệ số `k = 0,45`** (hằng số `TIDAL_CELERITY_FACTOR`): `sqrt(g·h)` là vận tốc sóng nông
    LÝ THUYẾT (deep/shallow-water wave speed) trong kênh đầy, còn sóng triều thực tế trong
    kênh chảy một phần (partially-full channel) có vận tốc nhỏ hơn nhiều. Với số Froude
    `Fr = v/sqrt(g·h)` điển hình 0,3–0,6 ở sông triều đồng bằng, vận tốc sóng tương đối
    rơi vào khoảng 0,4–0,6·sqrt(g·h). Chọn 0,45 là giá trị giữa, tương đương nửa sóng
    triều bị phản xạ tại bờ chéo (hệ số phản xạ ≈ 0,3 cho bờ dốc) — xem Godin (1968),
    *Tidal Hydrodynamics*, chương 2, và Prandle (1956), *On the determination of the
    frictional "damping factor" for estuaries*, J. Fluid Mech.

    `flow_velocity_ms` ≥ 0; nếu lớn hơn `k·sqrt(g·h)` thì kẹp về giá trị dương nhỏ nhất.
    """
    h = max(float(depth_m or 0.0), 1e-3)
    c = factor * math.sqrt(G * h) - max(float(flow_velocity_ms or 0.0), 0.0)
    return max(c, 0.05)


def travel_time_h(length_km: float, celerity_ms: float) -> float:
    """Thời gian truyền (giờ) trên một cạnh: τ = L / c, L tính bằng km, c bằng m/s."""
    c = max(float(celerity_ms or 0.0), 1e-9)
    tau_h = (float(length_km or 0.0) * 1000.0) / c / 3600.0
    return clamp(tau_h, TAU_MIN_H, TAU_MAX_H)


def tau_down(length_km: float, uparea_km2: float, slope_used: float,
             n: float = MANNING_N) -> tuple[float, float]:
    """τ hướng xuôi (sóng lũ): trả về (τ giờ, vận tốc m/s) sau khi kẹp vận tốc.

    v = (1/n)·R^(2/3)·S^(1/2) với R = proxy theo `uparea`; c = (5/3)·v; τ = L/c.
    """
    r = hydraulic_radius(uparea_km2)
    v = manning_velocity(r, slope_used, n)
    c = clamp(factor_clamp(celerity_flood(r, slope_used, n)), FLOOD_C_MIN, FLOOD_C_MAX)
    return travel_time_h(length_km, c), c


def tau_up(length_km: float, uparea_km2: float, slope_used: float,
           n: float = MANNING_N) -> tuple[float, float]:
    """τ hướng ngược (sóng triều): trả về (τ giờ, vận tốc m/s).

    c = sqrt(g·h) − v_dòng chảy, h theo proxy `uparea`; v_dòng chảy theo Manning với độ dốc `slope_used`
    (dòng chảy xuôi, sóng triều đi ngược nên trừ v).
    """
    h = shallow_depth(uparea_km2)
    r = hydraulic_radius(uparea_km2)
    v = manning_velocity(r, slope_used, n)
    c = clamp(factor_clamp(celerity_tidal(h, v)), SHALLOW_C_MIN, SHALLOW_C_MAX)
    return travel_time_h(length_km, c), c


def factor_clamp(c: float) -> float:
    """Chỗ chặn tập trung cho việc kẹp vận tốc (giữ hàm riêng để test)."""
    return c


# ---------------------------------------------------------------------------
# Quy tắc ngày của đỉnh/chân triều (dùng chung với `pipelines/r/`)
# ---------------------------------------------------------------------------
# Bản tin KTTV ghi đỉnh/chân theo NGÀY QUAN SÁT của bản tin: một đỉnh xuất hiện sau nửa đêm
# vẫn được tính thuộc ngày quan sát trước đó. Kiểm tra trên toàn bộ `tide_extremes`
# (129.420 phần tử): mọi phần tử có `ts` thuộc đúng ngày quan sát của bản tin, trừ
# 4 phần tử (3 chân, 1 đỉnh) lệch sang ngày kế tiếp (dữ liệu nhập tay ở Dầu Tiếng / Gò Dầu Hạ).
# Vì vậy quy tắc chuẩn: `day_offset = 0` cho mọi phần tử thuộc ngày quan sát; phần tử lệch
# sang ngày kế tiếp được giữ `day_offset = 1` và KHÔNG bị loại (giá trị vẫn dùng được).
DAY_OFFSET_NEXT = 1
DAY_OFFSET_SAME = 0


def peak_day_offset(obs_day: str, ts_day: str) -> int:
    """Ngày của đỉnh so với ngày quan sát: 0 = cùng ngày, 1 = ngày kế tiếp.

    Quy tắc (thống nhất với `pipelines/r/`): đỉnh/chân có giờ thuộc NGÀY QUAN SÁT của bản tin
    → offset 0. Đỉnh xảy ra sau nửa đêm nhưng bản tin vẫn xếp vào ngày quan sát trước
    → offset 0 (đây là trường hợp phổ biến trong bản tin KTTV: đỉnh lúc 00:00–06:00 thuộc
    ngày quan sát của bản tin hôm trước). Chỉ khi giờ thực sự sang ngày kế tiếp mới offset 1.

    `obs_day`, `ts_day`: chuỗi `YYYY-MM-DD`.
    """
    if ts_day == obs_day:
        return DAY_OFFSET_SAME
    if ts_day > obs_day:
        return DAY_OFFSET_NEXT
    return DAY_OFFSET_SAME


def hour_of_day(ts_hour: int) -> float:
    """Giờ trong ngày (0..24) của một mốc thời gian (giờ Việt Nam)."""
    return float(ts_hour)
