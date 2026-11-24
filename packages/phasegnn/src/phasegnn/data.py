"""Nạp và chuẩn bị dữ liệu cho PhaseGNN (checklist 6.2).

**Nguồn dữ liệu.**

* `data/processed/features/g5/year=<YYYY>/part-*.parquet` — tập huấn luyện do G5 (Spark) sinh
  (32.352 dòng, 9 trạm, 2016-12-31 → 2026-11-10). Mỗi dòng là (trạm, ngày) với 2 đỉnh,
  2 chân, mưa, lưu lượng hồ, triều thiên văn.
* `results/G3/edges.parquet` (hoặc `data/processed/graph/edges.parquet`) — cạnh đồ thị hai
  chiều kèm `tau_phys_h` (giờ) từ G3.
* `data/processed/graph/nodes.parquet` — đặc trưng tĩnh của nút (`order`, `uparea_km2`,
  `topo_level_down/up`, `length_km`, `slope_raw`, `station_codes`).
* `data/processed/features/astro_tide.csv` — triều thiên văn (dùng cho `B_harm`).

**Chia tập** đọc từ `config/splits.json` (nguồn duy nhất, dùng chung với Spark G5 và R;
giá trị hiện tại: train 2017-01-01→2022-12-31, val 2023-01-01→2024-06-30, test 2024-07-01→2026-11-23).

**Triều thiên văn.** `astro_tide*.csv` do `pipelines/r/harmonic.R` sinh kèm
`astro_tide_meta.json` (khoảng ngày đã fit). `check_astro_provenance` từ chối file fit trên
ngày ≥ mốc test (rò rỉ) hoặc không có meta.

**Cửa sổ mẫu.** Mỗi mẫu gồm `T_in` ngày quan trọc (đầu vào) và `T_out` ngày dự báo (nhãn),
theo đề cương 5.2.6: `T_in = 14`, `T_out = 5`. Ngày phát bản tin = ngày dự báo thứ 1.

**Rò rỉ.** Mọi đặc trưng đầu vào lấy từ ngày `≤ d−1` (d = ngày phát bản tin). Cột
`max_obs_day` của G5 đã được kiểm tra ở phía Spark; ở đây ta kiểm tra lại theo nguyên tắc:
ngày đầu vào cuối cùng < ngày phát bản tin.
"""
from __future__ import annotations

import json
import math
import os
import warnings
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Iterable, Iterator, Sequence

import numpy as np
import pandas as pd

#: Tên file cấu hình biên chia tập (nguồn duy nhất, dùng chung với Spark G5 và R).
SPLITS_FILE_NAME = "splits.json"


def splits_config_path() -> Path:
    """Đường dẫn `config/splits.json`.

    Thứ tự dò: biến môi trường `PGNN_SPLITS_FILE` → `VNGIS_CONFIG_DIR/splits.json` →
    `<gốc repo>/config/splits.json`.
    """
    env = os.environ.get("PGNN_SPLITS_FILE")
    if env:
        return Path(env)
    cfg_dir = os.environ.get("VNGIS_CONFIG_DIR")
    if cfg_dir and (Path(cfg_dir) / SPLITS_FILE_NAME).is_file():
        return Path(cfg_dir) / SPLITS_FILE_NAME
    return _find_repo_root() / "config" / SPLITS_FILE_NAME


def load_split_config(path: str | os.PathLike | None = None) -> dict:
    """Đọc `config/splits.json` → `{"splits": {name: (from, to)}, "harmonic_fit_splits": [...]}`.

    Không có giá trị mặc định viết cứng trong code: thiếu file hoặc sai định dạng → lỗi ngay.
    """
    f = Path(path) if path is not None else splits_config_path()
    if not f.is_file():
        raise FileNotFoundError(
            f"không tìm thấy cấu hình chia tập {f} (đặt PGNN_SPLITS_FILE hoặc VNGIS_CONFIG_DIR)")
    with open(f, encoding="utf-8") as fh:
        raw = json.load(fh)
    sp = raw.get("splits") or {}
    out: dict[str, tuple[str, str]] = {}
    for name in ("train", "val", "test"):
        if name not in sp:
            raise ValueError(f"{f}: thiếu tập {name!r}")
        a, b = str(sp[name]["from"]), str(sp[name]["to"])
        date.fromisoformat(a), date.fromisoformat(b)  # sai định dạng → ValueError
        if a > b:
            raise ValueError(f"{f}: tập {name}: from {a} > to {b}")
        out[name] = (a, b)
    if not (out["train"][1] < out["val"][0] and out["val"][1] < out["test"][0]):
        raise ValueError(f"{f}: các tập phải nối tiếp, không chồng nhau: {out}")
    fit = list(raw.get("harmonic_fit_splits") or ["train"])
    if "test" in fit or any(x not in out for x in fit):
        raise ValueError(f"{f}: harmonic_fit_splits không hợp lệ (không được chứa 'test'): {fit}")
    return {"splits": out, "harmonic_fit_splits": fit, "path": str(f)}

#: Số ngày quan trọc và ngày dự báo (đề cương 5.2.6).
DEFAULT_T_IN = 14
DEFAULT_T_OUT = 5

#: Các trạm có dự báo chính thức của Đài KTTV Nam Bộ (đề cương 5.2.7).
KTTV_STATIONS: tuple[str, ...] = ("PHU_AN", "NHA_BE", "THU_DAU_MOT")

#: Trạm "chính" (dùng cho bản tin) — 3 trạm Phú An, Nhà Bè, Thủ Dầu Một.
MAIN_STATIONS: tuple[str, ...] = ("PHU_AN", "NHA_BE", "THU_DAU_MOT")

#: 6 trạm còn lại → nhiệm vụ phụ (đề cương 5.2.5).
AUX_STATIONS: tuple[str, ...] = ("BEN_LUC", "TAN_AN", "BIEN_HOA", "GO_DAU_HA",
                                "DAU_TIENG", "VUNG_TAU")

#: Nhãn cột đích trong G5 (đỉnh 1, đỉnh 2).
PEAK_COLS: tuple[str, ...] = ("h_peak1", "h_peak2")
LOW_COLS: tuple[str, ...] = ("h_low1", "h_low2")
PEAK_HOUR_COLS: tuple[str, ...] = ("t_peak1_h", "t_peak2_h")
LOW_HOUR_COLS: tuple[str, ...] = ("t_low1_h", "t_low2_h")

#: Đặc trưng động theo ngày (mỗi trạm), dùng làm đầu vào mô hình.
DYNAMIC_FEATURES: tuple[str, ...] = (
    "h_peak1", "h_peak2", "t_peak1_h", "t_peak2_h",
    "h_low1", "h_low2", "t_low1_h", "t_low2_h",
    "missing_peak1", "missing_peak2",
    "res_inflow_m3s", "res_spill_m3s", "res_turbine_m3s", "rain_mm",
    "astro_h1", "astro_h2", "astro_t1_h", "astro_t2_h",
)


# ---------------------------------------------------------------------------
# Đường dẫn mặc định
# ---------------------------------------------------------------------------
def _find_repo_root() -> Path:
    """Dò gốc repo từ vị trí file này.

    Vị trí file: `<repo>/packages/phasegnn/src/phasegnn/data.py` ⇒ `parents[4]` là gốc
    repo. Ta dò dọc lên (tối đa 6 cấp) và chọn cấp có thư mục `data/processed` **hoặc**
    `packages` — nhờ vậy không phụ thuộc package được cài ở `site-packages` hay chạy
    trực tiếp từ source.
    """
    here = Path(__file__).resolve()
    for cand in here.parents:
        if (cand / "data" / "processed").is_dir() or (cand / "packages").is_dir():
            return cand
    # dự phòng: `<repo>/packages/phasegnn/src/phasegnn/data.py` → parents[4]
    return here.parents[4] if len(here.parents) > 4 else here.parent


#: Biên chia tập — đọc từ `config/splits.json` (khớp Spark G5 và R, không viết cứng).
SPLITS: dict[str, tuple[str, str]] = load_split_config()["splits"]

#: Ngày đầu tiên của tập test: mọi thứ "fit" (chuẩn hoá, điều hòa thủy triều, hồi quy) phải
#: chỉ dùng ngày < mốc này.
TEST_START: str = SPLITS["test"][0]


def default_paths(root: str | os.PathLike | None = None) -> dict[str, Path]:
    """Đường dẫn mặc định tới các nguồn dữ liệu (tương đối tới gốc repo)."""
    if root is None:
        root = _find_repo_root()
    root = Path(root)
    return {
        "g5": root / "data" / "processed" / "features" / "g5",
        "g5_meta": root / "data" / "processed" / "features" / "g5" / "_meta.json",
        "edges": root / "results" / "G3" / "edges.parquet",
        "edges_fallback": root / "data" / "processed" / "graph" / "edges.parquet",
        "nodes": root / "data" / "processed" / "graph" / "nodes.parquet",
        "astro": root / "data" / "processed" / "features" / "astro_tide.csv",
        "astro_meta": root / "data" / "processed" / "features" / "astro_tide_meta.json",
        "astro_by_station": root / "results" / "E1" / "astro_tide_by_station.csv",
    }


def _read_parquet_dir(path: Path) -> pd.DataFrame:
    """Đọc một thư mụng Parquet (Spark ghi `part-*.parquet`).

    Spark ghi dữ liệu theo phân vùng `year=<YYYY>/part-*.parquet` ⇒ ta dò **đệ quy**
    (`rglob`) để gom mọi tệp Parquet dưới thư mục, kể cả khi có phân vùng lồng nhau.

    Khi đọc bằng `pandas` thì **cột phân vùng bị loại** (Spark chỉ ghi nó vào đường
    dẫn) ⇒ ta khôi phục lại từ tên thư mục (`year=2017` ⇒ cột `year` = 2017).
    """
    if path.is_dir():
        files = sorted(path.rglob("*.parquet"))
        if not files:
            raise FileNotFoundError(f"không có file Parquet trong {path}")
        frames = []
        for f in files:
            df = pd.read_parquet(f)
            rel = f.relative_to(path)
            for part in rel.parts[:-1]:
                key, sep, val = part.partition("=")
                if not sep:
                    continue
                if key not in df.columns:
                    df[key] = _cast_partition_value(val)
            frames.append(df)
        return pd.concat(frames, ignore_index=True)
    if path.is_file():
        return pd.read_parquet(path)
    raise FileNotFoundError(f"không tìm thấy {path}")


def _cast_partition_value(value: str):
    """Ép giá trị phân vùng sang kiểu số nếu có thể (ví dụ `year=2017` ⇒ int)."""
    try:
        return int(value)
    except ValueError:
        return value

# ---------------------------------------------------------------------------
# Đồ thị
# ---------------------------------------------------------------------------
@dataclass
class GraphData:
    """Đồ thị hai chiều đã đánh số nút, kèm τ vật lý và đặc trưng tĩnh.

    Thuộc tính
    ----------
    node_ids : mảng ``[N]`` — mã nút (chuỗi) theo thứ tự chỉ số.
    node_index : dict mã nút → chỉ số.
    edge_index : ``[2, E]`` int64 — (`src`, `dst`) theo `edge_index[0]` = nguồn.
    edge_dir : ``[E]`` int64 — 0 = `down` (xuôi dòng), 1 = `up` (ngược dòng, triều).
    tau_phys : ``[E]`` float32 — độ trễ vật lý của cạnh (giờ).
    edge_attr : ``[E, D]`` float32 — đặc trưng cạnh.
    static_attr : ``[N, S]`` float32 — đặc trưng tĩnh của nút.
    station_node : dict mã trạm → chỉ số nút.
    """

    node_ids: list[str]
    node_index: dict[str, int]
    edge_index: np.ndarray
    edge_dir: np.ndarray
    tau_phys: np.ndarray
    edge_attr: np.ndarray
    static_attr: np.ndarray
    station_node: dict[str, int] = field(default_factory=dict)

    @property
    def num_nodes(self) -> int:
        return len(self.node_ids)

    @property
    def num_edges(self) -> int:
        return int(self.edge_index.shape[1])

    def edge_index_by_dir(self, direction: int) -> np.ndarray:
        """Chỉ số cạnh theo chiều (0 = down, 1 = up)."""
        return np.nonzero(self.edge_dir == direction)[0]

    def edge_index_dir(self, direction: int) -> np.ndarray:
        """`edge_index` giới hạn theo chiều."""
        return self.edge_index[:, self.edge_index_by_dir(direction)]

    def edge_attr_dir(self, direction: int) -> np.ndarray:
        """Đặc trưng cạnh giới hạn theo chiều."""
        return self.edge_attr[self.edge_index_by_dir(direction)]

    def tau_dir(self, direction: int) -> np.ndarray:
        """τ vật lý giới hạn theo chiều."""
        return self.tau_phys[self.edge_index_by_dir(direction)]


#: Số đặc trưng tĩnh của nút: lengthkm, log(uparea), order, slope, topo_down, topo_up,
#: cờ là nút biển, cờ là nút hồ, log1p(τ tới biển).
STATIC_FEATURE_NAMES: tuple[str, ...] = (
    "length_km", "log_uparea", "order", "slope_raw",
    "topo_level_down", "topo_level_up", "is_sea", "is_reservoir",
)


def load_graph(edges_path: Path | None = None, nodes_path: Path | None = None,
               paths: dict[str, Path] | None = None) -> GraphData:
    """Nạp đồ thị hai chiều từ Parquet (edges + nodes) và gắn τ vật lý.

    `edges_path` ưu tiên `results/G3/edges.parquet` (có `tau_phys_h`); nếu không có thì
    dùng `data/processed/graph/edges.parquet` (chưa có τ → τ mặc định 1 h và cảnh báo).
    """
    p = paths or default_paths()
    edges_file = edges_path or p["edges"]
    if not edges_file.exists():
        edges_file = p["edges_fallback"]
    if not edges_file.exists():
        raise FileNotFoundError(f"không tìm thấy edges.parquet ({p['edges']} hoặc {p['edges_fallback']})")
    edges = _read_parquet_dir(edges_file)
    nodes = _read_parquet_dir(nodes_path or p["nodes"])

    edges = edges.copy()
    edges["src"] = edges["src"].astype(str)
    edges["dst"] = edges["dst"].astype(str)

    node_ids: list[str] = []
    seen: set[str] = set()
    for col in ("src", "dst"):
        for v in edges[col].tolist():
            if v not in seen:
                seen.add(v)
                node_ids.append(v)
    node_index = {v: i for i, v in enumerate(node_ids)}

    src = edges["src"].map(node_index).to_numpy(dtype=np.int64)
    dst = edges["dst"].map(node_index).to_numpy(dtype=np.int64)
    edge_index = np.stack([src, dst], axis=0)

    edge_dir = (edges["dir"].astype(str).str.strip().str.lower() == "up").to_numpy(dtype=np.int64)

    if "tau_phys_h" in edges.columns:
        tau = edges["tau_phys_h"].to_numpy(dtype=np.float32)
    else:
        tau = np.ones(len(edges), dtype=np.float32)
    # τ phải dương và hữu hạn (nếu không, dùng 1 h để tránh lỗi số)
    bad = ~np.isfinite(tau) | (tau <= 0)
    if bool(bad.any()):
        tau[bad] = 1.0
    tau = np.clip(tau, 1e-3, 72.0).astype(np.float32)

    # --- đặc trưng cạnh ----------------------------------------------------
    edge_feat: dict[str, np.ndarray] = {
        "length_km": edges["length_km"].to_numpy(dtype=np.float32),
        "slope_used": edges["slope_used"].to_numpy(dtype=np.float32),
        "log_tau": np.log(tau).astype(np.float32),
        "is_down": (edge_dir == 0).astype(np.float32),
        "is_up": (edge_dir == 1).astype(np.float32),
    }
    if "celerity_ms" in edges.columns:
        c = edges["celerity_ms"].to_numpy(dtype=np.float32)
        c = np.where(np.isfinite(c) & (c > 0), c, 1.0)
        edge_feat["log_celerity"] = np.log(c).astype(np.float32)
    else:
        edge_feat["log_celerity"] = np.zeros(len(edges), dtype=np.float32)
    edge_attr = np.stack([edge_feat[k] for k in sorted(edge_feat)], axis=1).astype(np.float32)

    # --- đặc trưng tĩnh của nút -------------------------------------------
    nodes = nodes.copy()
    nodes["comid"] = nodes["comid"].astype(str)
    n = len(node_ids)
    static = np.zeros((n, len(STATIC_FEATURE_NAMES)), dtype=np.float32)
    idx_by_comid = {v: i for i, v in enumerate(nodes["comid"].tolist())}
    station_node: dict[str, int] = {}

    # τ tích luỹ từ nút biển theo chiều `up` (đường đi dài nhất) — đặc trưng "khoảng cách
    # dọc sông tới cửa biển" dưới dạng độ trễ triều tích luỹ.
    tau_from_sea = _tau_from_sea(edge_index, edge_dir, tau, node_ids)

    for i, vid in enumerate(node_ids):
        j = idx_by_comid.get(vid)
        if j is None:
            continue
        row = nodes.iloc[j]
        uparea = float(row.get("uparea_km2") or 0.0)
        static[i] = (
            float(row.get("length_km") or 0.0),
            math.log1p(max(uparea, 0.0)),
            float(row.get("order") or 0),
            float(row.get("slope_raw") or 0.0),
            float(row.get("topo_level_down") or 0),
            float(row.get("topo_level_up") or 0),
            1.0 if str(row.get("kind")) == "sea" else 0.0,
            1.0 if str(row.get("kind")) == "reservoir" else 0.0,
        )
        codes = row.get("station_codes")
        if codes is not None and not isinstance(codes, (str, float, int)):
            try:
                for code in list(codes):
                    station_node[str(code)] = i
            except TypeError:
                pass
        elif isinstance(codes, str) and codes:
            station_node[codes] = i

    # cột thứ 9 (log1p τ tới biển) — ghi đè sau khi đã tạo mảng
    tau_feat = np.log1p(np.clip(tau_from_sea, 0.0, 72.0)).astype(np.float32)
    static = np.concatenate([static, tau_feat[:, None]], axis=1)

    return GraphData(
        node_ids=node_ids,
        node_index=node_index,
        edge_index=edge_index,
        edge_dir=edge_dir,
        tau_phys=tau,
        edge_attr=edge_attr,
        static_attr=static.astype(np.float32),
        station_node=station_node,
    )


def _tau_from_sea(edge_index: np.ndarray, edge_dir: np.ndarray, tau: np.ndarray,
                  node_ids: list[str]) -> np.ndarray:
    """Tổng τ từ nút biển tới mỗi nút theo chiều `up` (đường đi dài nhất).

    Cùng nguyên tắc với `pipelines/spark/features/tau_phys.py::upstream_tau_totals`:
    sóng triều đi trên MỘT nhánh tại một thời điểm, nên thời gian tới nút là τ của nhánh
    bị trễ nhất ⇒ đường đi dài nhất trong DAG chiều `up`.
    """
    n = len(node_ids)
    best = np.zeros(n, dtype=np.float64)
    # đồ thị `up`: cạnh src → dst (dst là nút thượng nguồn)
    succ: dict[int, list[tuple[int, float]]] = {}
    indeg = np.zeros(n, dtype=np.int64)
    for e in range(edge_index.shape[1]):
        if edge_dir[e] != 1:
            continue
        s, d = int(edge_index[0, e]), int(edge_index[1, e])
        succ.setdefault(s, []).append((d, float(tau[e])))
        indeg[d] += 1
    queue = [i for i in range(n) if indeg[i] == 0]
    order: list[int] = []
    head = 0
    while head < len(queue):
        node = queue[head]
        head += 1
        order.append(node)
        for d, w in succ.get(node, ()):
            if best[node] + w > best[d]:
                best[d] = best[node] + w
            indeg[d] -= 1
            if indeg[d] == 0:
                queue.append(d)
    return best.astype(np.float32)


# ---------------------------------------------------------------------------
# Bảng quan trắc
# ---------------------------------------------------------------------------
def load_g5(g5_dir: Path | None = None, paths: dict[str, Path] | None = None) -> pd.DataFrame:
    """Nạp toàn bộ bảng (trạm, ngày) từ Parquet G5."""
    p = paths or default_paths()
    df = _read_parquet_dir(g5_dir or p["g5"])
    df = df.copy()
    df["station"] = df["station"].astype(str)
    df["day"] = df["day"].astype(str)
    # `year` là cột phân vùng kiểu Hive (`year=YYYY/`) — nếu không khôi phục được từ
    # đường dẫn thì suy ra từ `day`.
    if "year" not in df.columns:
        df["year"] = df["day"].str.slice(0, 4).astype(int)
    df["year"] = df["year"].astype(int)
    return df.sort_values(["station", "day"]).reset_index(drop=True)


def load_g5_meta(g5_dir: Path | None = None, paths: dict[str, Path] | None = None) -> dict:
    """Đọc `_meta.json` của G5 (data_snapshot để ghi vào bản ghi mô hình)."""
    p = paths or default_paths()
    meta_path = (g5_dir or p["g5"]) / "_meta.json"
    if not meta_path.exists():
        return {}
    with open(meta_path, encoding="utf-8") as fh:
        return json.load(fh)


class AstroLeakError(ValueError):
    """Triều thiên văn được fit trên dữ liệu của tập test (hoặc không rõ nguồn gốc)."""


def check_astro_provenance(meta_path: Path | None = None,
                           splits: dict[str, tuple[str, str]] | None = None) -> dict:
    """Kiểm tra `astro_tide_meta.json`: khoảng fit điều hòa phải kết thúc **trước** mốc test.

    Trả về nội dung meta nếu hợp lệ; nếu thiếu meta (file astro do phiên bản cũ của
    `harmonic.R` sinh, fit trên 2017–2026) hoặc `fit_to >= test.from` → `AstroLeakError`.
    """
    sp = splits or SPLITS
    test_from = sp["test"][0]
    f = Path(meta_path) if meta_path is not None else default_paths()["astro_meta"]
    if not f.is_file():
        raise AstroLeakError(
            f"thiếu {f}: không xác định được giai đoạn fit điều hòa của triều thiên văn "
            f"(bản cũ fit trên cả tập test). Chạy lại pipelines/r/harmonic.R.")
    with open(f, encoding="utf-8") as fh:
        meta = json.load(fh)
    fit_to = str(meta.get("fit_to") or "")
    if not fit_to or fit_to >= test_from:
        raise AstroLeakError(
            f"{f}: điều hòa fit tới {fit_to or '?'} — phải < mốc test {test_from}. "
            f"Chạy lại pipelines/r/harmonic.R.")
    return meta


def load_astro_tide(path: Path | None = None, paths: dict[str, Path] | None = None,
                    check_leak: bool = True) -> pd.DataFrame:
    """Nạp CSV triều thiên văn (`day`, `h1`, `h2`, `t1_h`, `t2_h`).

    `check_leak=True` (mặc định): gọi `check_astro_provenance` trước khi đọc.
    """
    p = paths or default_paths()
    f = path or p["astro"]
    if not f.exists():
        return pd.DataFrame(columns=["day", "h1", "h2", "t1_h", "t2_h"])
    if check_leak:
        check_astro_provenance(Path(f).with_name("astro_tide_meta.json")
                               if path is not None else p["astro_meta"])
    df = pd.read_csv(f, dtype={"day": str})
    df.columns = [c.strip() for c in df.columns]
    df["day"] = df["day"].astype(str)
    for c in ("h1", "h2", "t1_h", "t2_h"):
        if c not in df.columns:
            df[c] = np.nan
    return df[["day", "h1", "h2", "t1_h", "t2_h"]]


def split_of(day: str, splits: dict[str, tuple[str, str]] = SPLITS) -> str | None:
    """Tập chia của một ngày (`train`/`val`/`test`) hoặc `None`."""
    for name, (a, b) in splits.items():
        if a <= day <= b:
            return name
    return None


# ---------------------------------------------------------------------------
# Chuẩn hoá đặc trưng
# ---------------------------------------------------------------------------
class FeatureScaler:
    """Chuẩn hoá đặc trưng theo tập huấn luyện (mean/std), lưu để tái dùng khi dự báo."""

    def __init__(self) -> None:
        self.mean: np.ndarray | None = None
        self.std: np.ndarray | None = None

    def fit(self, x: np.ndarray) -> "FeatureScaler":
        x = np.asarray(x, dtype=np.float64)
        # Cột hoàn toàn NaN (ví dụ đặc trưng không quan trắc tại một số trạm) sẽ cho
        # mean=NaN; ta đặt mean=0, std=1 để `transform` không sinh NaN.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            self.mean = np.nanmean(x, axis=0)
            self.std = np.nanstd(x, axis=0)
        self.std = np.where(np.isfinite(self.std) & (self.std >= 1e-6), self.std, 1.0)
        self.mean = np.where(np.isfinite(self.mean), self.mean, 0.0)
        return self

    def transform(self, x: np.ndarray) -> np.ndarray:
        if self.mean is None or self.std is None:
            raise RuntimeError("FeatureScaler chưa được fit")
        x = np.asarray(x, dtype=np.float32)
        mean = self.mean.astype(np.float32)
        std = self.std.astype(np.float32)
        out = (x - mean) / std
        return np.nan_to_num(out, nan=0.0, posinf=0.0, neginf=0.0).astype(np.float32)

    def fit_transform(self, x: np.ndarray) -> np.ndarray:
        return self.fit(x).transform(x)

    def to_dict(self) -> dict:
        return {"mean": (self.mean.tolist() if self.mean is not None else []),
                "std": (self.std.tolist() if self.std is not None else [])}

    @classmethod
    def from_dict(cls, d: dict) -> "FeatureScaler":
        obj = cls()
        obj.mean = np.asarray(d.get("mean", []), dtype=np.float64)
        obj.std = np.asarray(d.get("std", []), dtype=np.float64)
        return obj


# ---------------------------------------------------------------------------
# Mẫu huấn luyện
# ---------------------------------------------------------------------------
@dataclass
class WindowSample:
    """Một mẫu: cửa sổ quan trọc + cửa sổ dự báo cho một trạm."""

    station: str
    issue_date: str          # ngày phát bản tin (ngày dự báo thứ 1)
    days: list[str]          # T_in ngày quan trọc (cũ → mới)
    targets: np.ndarray      # [T_out, 2] độ cao đỉnh (NaN = không quan trắc)
    target_hours: np.ndarray  # [T_out, 2] giờ đỉnh (NaN)
    target_lows: np.ndarray  # [T_out, 2] độ cao chân (NaN)
    target_low_hours: np.ndarray  # [T_out, 2] giờ chân (NaN)
    peak_heights: np.ndarray  # [T_out] độ cao đỉnh ngày (max trong ngày, NaN)
    peak_hours: np.ndarray  # [T_out] giờ đỉnh ngày (NaN)
    features: np.ndarray     # [T_in, F] đặc trưng động đã chuẩn hoá
    #: Độ cao đỉnh **đã chuẩn hoá** (cùng thang với `features`) — xem `HeightScale`.
    #: NaN = không quan trắc.
    targets_scaled: np.ndarray | None = None
    peak_heights_scaled: np.ndarray | None = None
    target_lows_scaled: np.ndarray | None = None


class HeightScale:
    """Thang chuyển đổi giữa **mét** và **đơn vị chuẩn hoá** của độ cao đỉnh.

    **Vì sao cần (sai lệch đã sửa 24/11/2026).** Trước đây mô hình dự báo **độ cao
    tuyệt đối (mét)** trong khi đầu vào là **đặc trưng đã chuẩn hoá**. Với đầu ra một
    hằng số toàn cục (bias của tầng cuối), mô hình chỉ có thể học **một** giá trị
    trung bình cho **cả lưu vực** ⇒ sai số ở Vũng Tàu (mốc cao hơn ~2,7 m) lên tới
    ~2,9 m, và MAE test đạt 0,70 m — **xấu hơn cả dự báo hằng số tối ưu** (0,20 m).

    Nay mô hình dự báo trên **cùng thang chuẩn hoá** với đầu vào:
    `h_norm = (h − μ) / σ` với `μ, σ` lấy từ **tập train** (thống kê theo trạm),
    rồi chuyển ngược về mét khi xuất kết quả. Nhờ vậy:
    * mô hình chỉ cần học **độ lệch so với mức trung bình của trạm** (lớn hay nhỏ
      đều quy mô tương đương) ⇒ gradient cân bằng giữa các trạm;
    * giá trị tuyệt đối của trạm được khôi phục chính xác khi xuất (không còn lệch
      2,7 m ở Vũng Tàu).
    """

    def __init__(self) -> None:
        #: `mean[station]`, `std[station]` — thống kê tập train (mét).
        self.mean: dict[str, float] = {}
        self.std: dict[str, float] = {}

    # -- API ----------------------------------------------------------------
    def to_meters(self, values: np.ndarray, station: str) -> np.ndarray:
        """Chuẩn hoá → mét."""
        mu, sd = self._stats(station)
        return np.asarray(values, dtype=np.float32) * sd + mu

    def to_normalized(self, values: np.ndarray, station: str) -> np.ndarray:
        """Mét → chuẩn hoá."""
        mu, sd = self._stats(station)
        return (np.asarray(values, dtype=np.float32) - mu) / sd

    def _stats(self, station: str) -> tuple[float, float]:
        """Thống kê của trạm (dùng thang chung `::both` cho cả 2 đỉnh)."""
        key = f"{station}::both"
        if key not in self.mean:
            # trạm lạ (không có trong train) ⇒ dùng thống kê chung của các trạm đã biết
            mus = [v for k, v in self.mean.items() if k.endswith("::both")]
            sds = [v for k, v in self.std.items() if k.endswith("::both")]
            mu = float(np.mean(mus)) if mus else 0.0
            sd = float(np.mean(sds)) if sds else 1.0
            self.mean[key] = mu
            self.std[key] = sd
        return self.mean[key], max(self.std[key], 1e-6)

    def to_dict(self) -> dict:
        return {"mean": dict(self.mean), "std": dict(self.std)}

    @classmethod
    def from_dict(cls, d: dict) -> "HeightScale":
        obj = cls()
        obj.mean = {str(k): float(v) for k, v in (d.get("mean") or {}).items()}
        obj.std = {str(k): float(v) for k, v in (d.get("std") or {}).items()}
        return obj


@dataclass
class SampleSet:
    """Tập mẫu + thông tin chia tập."""

    samples: list[WindowSample]
    split: str
    feature_names: list[str]
    stations: list[str]
    scaler: FeatureScaler | None = None
    #: Thang chuyển đổi mét ↔ chuẩn hoá cho độ cao (xem `HeightScale`).
    height_scale: "HeightScale | None" = None

    def __len__(self) -> int:
        return len(self.samples)

    def filter_station(self, stations: Iterable[str]) -> "SampleSet":
        keep = set(stations)
        return SampleSet([s for s in self.samples if s.station in keep], self.split,
                         self.feature_names, self.stations, self.scaler, self.height_scale)

    def station_weight(self, station: str, main_weight: float = 1.0, aux_weight: float = 0.3) -> float:
        """Trọng số nhiệm vụ: trạm chính 1,0; trạm phụ 0,3 (đề cương 5.2.5)."""
        return main_weight if station in MAIN_STATIONS else aux_weight


def calendar_days(first: str, last: str) -> list[str]:
    """Mọi ngày lịch từ `first` tới `last` (hai đầu tính cả), dạng YYYY-MM-DD."""
    d0, d1 = date.fromisoformat(str(first)[:10]), date.fromisoformat(str(last)[:10])
    return [(d0 + timedelta(days=i)).isoformat() for i in range((d1 - d0).days + 1)]


def _day_range(start: str, n: int) -> list[str]:
    d0 = date.fromisoformat(start)
    return [(d0 + timedelta(days=i)).isoformat() for i in range(n)]


def build_samples(df: pd.DataFrame, station: str, days: Sequence[str], t_in: int = DEFAULT_T_IN,
                  t_out: int = DEFAULT_T_OUT, feature_names: Sequence[str] = DYNAMIC_FEATURES,
                  scaler: FeatureScaler | None = None,
                  fit_scaler: bool = False,
                  height_scale: "HeightScale | None" = None) -> list[WindowSample]:
    """Dựng mẫu cửa sổ cho một trạm.

    `days` phải là **lưới ngày lịch liên tục** (xem `calendar_days`); phần tử thứ `i` là
    ngày thứ `i`. Với ngày phát bản tin `issue` = ngày dự báo thứ 1:
    * đầu vào: `issue − t_in` … `issue − 1` (tất cả `< issue` ⇒ không rò rỉ);
    * nhãn: `issue` … `issue + t_out − 1` (độ cao đỉnh 1, đỉnh 2 và giờ tương ứng).

    **Hiệu năng.** Toàn bộ tra cứu được vectorise bằng numpy (mảng `values` + ánh xạ
    `day → hàng`), nên dựng được ~30 nghìn mẫu trong vài giây thay vì vài phút.
    """
    sub = df[df["station"] == station]
    if sub.empty:
        return []
    feat_cols = [c for c in feature_names if c in sub.columns]
    days = [str(d)[:10] for d in days]
    n_days = len(days)
    if n_days and days != calendar_days(days[0], days[-1]):
        raise ValueError("build_samples: `days` phải là lưới ngày lịch liên tục, tăng dần "
                         "(dùng calendar_days); cửa sổ đếm theo ngày lịch, không theo vị trí")

    # --- ánh xạ day → hàng trong bảng con ---------------------------------
    sub = sub.drop_duplicates(subset=["day"]).set_index("day")
    row_of = {d: i for i, d in enumerate(sub.index.tolist())}
    rows = np.array([row_of.get(d, -1) for d in days], dtype=np.int64)

    def _column(col: str) -> np.ndarray:
        """Giá trị của một cột theo `days` (NaN nếu ngày không có trong bảng)."""
        out = np.full(n_days, np.nan, dtype=np.float32)
        if col not in sub.columns:
            return out
        vals = sub[col].to_numpy(dtype=np.float32)
        mask = rows >= 0
        out[mask] = vals[rows[mask]]
        return out

    # --- đặc trưng đầu vào --------------------------------------------------
    if feat_cols:
        raw_arr = np.column_stack([_column(c) for c in feat_cols]).astype(np.float32)
    else:
        raw_arr = np.zeros((n_days, 0), dtype=np.float32)

    if fit_scaler:
        scaler = FeatureScaler().fit(raw_arr.reshape(-1, raw_arr.shape[1]))
    if scaler is not None:
        feat_arr = scaler.transform(raw_arr)
    else:
        feat_arr = np.nan_to_num(raw_arr, nan=0.0)

    # --- nhãn (mảng trên trục ngày) ----------------------------------------
    h1 = _column("h_peak1")
    h2 = _column("h_peak2")
    t1 = _column("t_peak1_h")
    t2 = _column("t_peak2_h")
    l1 = _column("h_low1")
    l2 = _column("h_low2")
    lt1 = _column("t_low1_h")
    lt2 = _column("t_low2_h")

    # độ cao đỉnh ngày = max(h1, h2) (NaN nếu cả hai đều thiếu)
    with np.errstate(invalid="ignore"):
        peak_h_all = np.fmax(h1, h2)
    peak_h_all[~(np.isfinite(h1) | np.isfinite(h2))] = np.nan
    # giờ của đỉnh cao nhất
    take1 = np.isfinite(h1) & (~np.isfinite(h2) | (h1 >= h2))
    take2 = (~take1) & np.isfinite(h2)
    peak_t_all = np.where(take1, t1, np.where(take2, t2, np.nan)).astype(np.float32)

    # --- dựng mẫu ----------------------------------------------------------
    samples: list[WindowSample] = []
    day_arr = np.asarray(days, dtype=object)
    for i in range(n_days):
        # cần đủ t_in ngày trước và t_out ngày sau
        if i - t_in < 0 or i + t_out > n_days:
            continue
        sl = slice(i, i + t_out)
        tg = np.stack([h1[sl], h2[sl]], axis=1).astype(np.float32)
        # Không có nhãn đỉnh nào trong cả cửa sổ dự báo (mọi ngày đều thiếu) → không học /
        # không chấm được gì, bỏ mẫu. Nhãn thiếu một phần vẫn giữ (NaN = mặt nạ).
        if not np.isfinite(tg).any():
            continue
        tg_low = np.stack([l1[sl], l2[sl]], axis=1).astype(np.float32)
        pk_h = peak_h_all[sl].astype(np.float32)
        # **Nhãn ở thang chuẩn hoá** (cùng thang với đầu vào) — xem `HeightScale`.
        if height_scale is not None:
            tg_scaled = height_scale.to_normalized(tg, station)
            low_scaled = height_scale.to_normalized(tg_low, station)
            pk_scaled = height_scale.to_normalized(pk_h, station)
        else:
            tg_scaled = low_scaled = pk_scaled = None
        samples.append(WindowSample(
            station=station,
            issue_date=days[i],
            days=day_arr[i - t_in:i].tolist(),
            targets=tg,
            target_hours=np.stack([t1[sl], t2[sl]], axis=1).astype(np.float32),
            target_lows=tg_low,
            target_low_hours=np.stack([lt1[sl], lt2[sl]], axis=1).astype(np.float32),
            peak_heights=pk_h,
            peak_hours=peak_t_all[sl].astype(np.float32),
            features=feat_arr[i - t_in:i].astype(np.float32),
            targets_scaled=None if tg_scaled is None else tg_scaled.astype(np.float32),
            peak_heights_scaled=None if pk_scaled is None else pk_scaled.astype(np.float32),
            target_lows_scaled=None if low_scaled is None else low_scaled.astype(np.float32),
        ))
    return samples

def build_dataset(df: pd.DataFrame, split: str, t_in: int = DEFAULT_T_IN,
                  t_out: int = DEFAULT_T_OUT,
                  feature_names: Sequence[str] = DYNAMIC_FEATURES,
                  stations: Sequence[str] | None = None,
                  splits: dict[str, tuple[str, str]] = SPLITS,
                  scaler: FeatureScaler | None = None,
                  fit_scaler: bool = False,
                  height_scale: "HeightScale | None" = None) -> SampleSet:
    """Dựng tập mẫu cho một tập chia.

    Tham số
    -------
    df : bảng G5 (đã nạp bằng `load_g5`).
    split : `train` | `val` | `test`.
    scaler : bộ chuẩn hoá (nếu `fit_scaler=True` thì fit trên chính tập này — chỉ dùng cho
        `train`; các tập khác phải dùng lại scaler của train).
    height_scale : thang chuyển đổi mét ↔ chuẩn hoá cho độ cao đỉnh (xem `HeightScale`).
        Khi `fit_scaler=True` ta tự tính thang này từ tập `train` (thống kê **theo trạm**).
    """
    if split not in splits:
        raise ValueError(f"split phải là một trong {list(splits)}, nhận {split!r}")
    lo, hi = splits[split]
    sub = df[(df["day"] >= lo) & (df["day"] <= hi)]
    st_list = list(stations) if stations is not None else sorted(sub["station"].unique().tolist())
    feat_cols = [c for c in feature_names if c in df.columns]

    per_station: list[list[WindowSample]] = []
    per_station_scalers: list[FeatureScaler] = []
    out_hscale = height_scale
    for st in st_list:
        # QUAN TRỌNG: lọc trên `sub` (đã giới hạn theo tập chia), **không** trên `df` —
        # nếu lọc trên `df` thì cả 3 tập đều chứa toàn bộ ngày ⇒ rò rỉ dữ liệu.
        sdf = sub[sub["station"] == st]
        if sdf.empty:
            continue
        # **Lưới ngày LIÊN TỤC theo lịch** (từ ngày đầu tới ngày cuối có dữ liệu của trạm
        # trong tập chia): cửa sổ `T_in`/`T_out` đếm theo ngày lịch, không theo vị trí phần
        # tử. Ngày thiếu dữ liệu → hàng NaN (đầu vào → 0 sau chuẩn hoá, nhãn NaN = bỏ qua
        # trong hàm mất mát/chấm điểm). Trước 24/11/2026 lưới chỉ gồm ngày có dữ liệu nên
        # một ngày thiếu làm cửa sổ "nhảy" qua ngày đó và nhãn lead k lệch sang ngày khác.
        days = calendar_days(sdf["day"].min(), sdf["day"].max())
        if fit_scaler:
            raw = sdf.set_index("day").reindex(days)[feat_cols].to_numpy(dtype=np.float32)
            s = FeatureScaler().fit(raw)
            per_station_scalers.append(s)
        else:
            s = scaler
        # **Thang độ cao (mét ↔ chuẩn hoá)**: fit trên tập train, thống kê **theo trạm**.
        # Đây là thang dùng chung cho cả 2 đỉnh (`h_peak1` và `h_peak2`) để nhãn và
        # dự báo nằm trên cùng thang.
        hs = out_hscale
        if hs is None and fit_scaler:
            hs = HeightScale()
            out_hscale = hs
        if fit_scaler:
            for col in ("h_peak1", "h_peak2"):
                if col not in sdf.columns:
                    continue
                vals = sdf[col].to_numpy(dtype=np.float32)
                vals = vals[np.isfinite(vals)]
                if vals.size:
                    hs.mean[f"{st}::{col}"] = float(vals.mean())
                    hs.std[f"{st}::{col}"] = float(max(vals.std(), 1e-6))
            for col in ("h_peak1", "h_peak2"):
                key = f"{st}::{col}"
                if key in hs.mean:
                    hs.mean[f"{st}::both"] = hs.mean[key]
                    hs.std[f"{st}::both"] = hs.std[key]
        per_station.append(build_samples(sdf, st, days, t_in=t_in, t_out=t_out,
                                         feature_names=feat_cols, scaler=s,
                                         height_scale=hs))
    samples = [x for lst in per_station for x in lst]
    # Bộ chuẩn hoá dùng chung: khi `fit_scaler=True` ta fit trên chính tập này (chỉ dùng
    # cho `train`) và trả về nó để các tập sau dùng lại y hệt.
    out_scaler = scaler
    if fit_scaler and per_station_scalers:
        out_scaler = _merge_scalers(per_station_scalers)
    return SampleSet(samples=samples, split=split, feature_names=list(feat_cols),
                     stations=st_list, scaler=out_scaler, height_scale=out_hscale)


def _merge_scalers(scalers: Sequence["FeatureScaler"]) -> "FeatureScaler":
    """Gộp nhiều bộ chuẩn hoá (mỗi trạm một bộ) thành một bộ duy nhất.

    Ta ghép `mean` và `std` của các trạm theo công thức phương sai tổng quát (Chu–Demange):
    `mean = Σ nᵢ·meanᵢ / Σ nᵢ`, `var = Σ nᵢ·(varᵢ + meanᵢ²)/Σ nᵢ − mean²`.
    """
    means = np.array([s.mean for s in scalers], dtype=np.float64)
    stds = np.array([s.std for s in scalers], dtype=np.float64)
    n = means.shape[0]
    mean = means.mean(axis=0)
    var = ((stds ** 2) + means ** 2).mean(axis=0) - mean ** 2
    obj = FeatureScaler()
    obj.mean = mean
    obj.std = np.where(var < 1e-12, 1.0, np.sqrt(var))
    return obj


def build_datasets(df: pd.DataFrame, t_in: int = DEFAULT_T_IN, t_out: int = DEFAULT_T_OUT,
                   feature_names: Sequence[str] = DYNAMIC_FEATURES,
                   stations: Sequence[str] | None = None,
                   splits: dict[str, tuple[str, str]] = SPLITS) -> dict[str, SampleSet]:
    """Dựng cả 3 tập, dùng chung một bộ chuẩn hoá (fit trên `train`).

    **Thang độ cao** (`HeightScale`) cũng được fit trên `train` và dùng lại cho
    `val`/`test` ⇒ không rò rỉ thống kê.
    """
    train = build_dataset(df, "train", t_in=t_in, t_out=t_out, feature_names=feature_names,
                          stations=stations, splits=splits, scaler=None, fit_scaler=True)
    scaler = train.scaler
    hscale = train.height_scale
    out = {"train": train}
    for sp in ("val", "test"):
        if sp in splits:
            out[sp] = build_dataset(df, sp, t_in=t_in, t_out=t_out, feature_names=feature_names,
                                     stations=stations, splits=splits, scaler=scaler,
                                     fit_scaler=False, height_scale=hscale)
    return out


def check_leakage(samples: Sequence[WindowSample]) -> list[str]:
    """Kiểm tra rò rỉ: ngày quan trọc cuối cùng phải < ngày phát bản tin.

    Trả về danh sách lỗi (rỗng = không rò rỉ).
    """
    errors: list[str] = []
    for s in samples:
        if not s.days:
            continue
        if s.days[-1] >= s.issue_date:
            errors.append(f"{s.station} {s.issue_date}: ngày quan trọc cuối {s.days[-1]} >= ngày phát bản tin")
    return errors


def iter_by_station(samples: Sequence[WindowSample]) -> Iterator[tuple[str, list[WindowSample]]]:
    """Nhóm mẫu theo trạm."""
    groups: dict[str, list[WindowSample]] = {}
    for s in samples:
        groups.setdefault(s.station, []).append(s)
    for st in sorted(groups):
        yield st, groups[st]
