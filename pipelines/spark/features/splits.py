"""Biên chia tập (đọc `config/splits.json`) và kiểm tra nguồn gốc triều thiên văn — thuần Python, có test.

`config/splits.json` là nguồn DUY NHẤT của biên train/val/test; `packages/phasegnn` (`phasegnn.data`) và R
(`pipelines/r/R/rules.R::load_splits`) đọc cùng file (luật phụ thuộc: không import chéo, chỉ dùng chung dữ liệu
cấu hình).

Triều thiên văn (`astro_tide.csv`) do `pipelines/r/harmonic.R` sinh kèm `astro_tide_meta.json`
(`fit_from`, `fit_to`, ...). G5 chỉ ghép file này khi `fit_to < test.from`: hệ số điều hòa fit trên cả giai
đoạn test làm cột `astro_*` (đầu vào mô hình) rò rỉ dữ liệu test.
"""
from __future__ import annotations

import json
import os
from datetime import date

SPLIT_NAMES = ("train", "val", "test")


def default_splits_path() -> str:
    """`PGNN_SPLITS_FILE` → `VNGIS_CONFIG_DIR/splits.json` → `<repo>/config/splits.json`."""
    env = os.environ.get("PGNN_SPLITS_FILE")
    if env:
        return env
    cfg = os.environ.get("VNGIS_CONFIG_DIR")
    if cfg and os.path.isfile(os.path.join(cfg, "splits.json")):
        return os.path.join(cfg, "splits.json")
    # <repo>/pipelines/spark/features/splits.py → <repo>
    repo = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    return os.path.join(repo, "config", "splits.json")


def parse_split_config(raw: dict) -> dict:
    """Kiểm tra và chuẩn hoá nội dung `splits.json` → `{"splits": {tên: (from, to)}, "harmonic_fit_splits": [...]}`."""
    sp = raw.get("splits") or {}
    out: dict[str, tuple[str, str]] = {}
    for name in SPLIT_NAMES:
        if name not in sp:
            raise ValueError(f"splits.json: thiếu tập {name!r}")
        a, b = str(sp[name]["from"]), str(sp[name]["to"])
        date.fromisoformat(a)
        date.fromisoformat(b)
        if a > b:
            raise ValueError(f"splits.json: tập {name}: from {a} > to {b}")
        out[name] = (a, b)
    if not (out["train"][1] < out["val"][0] and out["val"][1] < out["test"][0]):
        raise ValueError(f"splits.json: các tập phải nối tiếp, không chồng nhau: {out}")
    fit = list(raw.get("harmonic_fit_splits") or ["train"])
    if "test" in fit or any(x not in out for x in fit):
        raise ValueError(f"splits.json: harmonic_fit_splits không hợp lệ (không được chứa 'test'): {fit}")
    return {"splits": out, "harmonic_fit_splits": fit}


def load_split_config(path: str | None = None) -> dict:
    f = path or default_splits_path()
    if not os.path.isfile(f):
        raise FileNotFoundError(f"không tìm thấy cấu hình chia tập {f} (đặt PGNN_SPLITS_FILE hoặc VNGIS_CONFIG_DIR)")
    with open(f, encoding="utf-8") as fh:
        cfg = parse_split_config(json.load(fh))
    cfg["path"] = f
    return cfg


def split_of(day: str, splits: dict[str, tuple[str, str]]) -> str | None:
    for name, (a, b) in splits.items():
        if a <= day <= b:
            return name
    return None


def astro_meta_path(astro_csv: str) -> str:
    """`astro_tide_meta.json` nằm cạnh `astro_tide.csv`."""
    return os.path.join(os.path.dirname(astro_csv), "astro_tide_meta.json")


def astro_leak_reason(meta: dict | None, splits: dict[str, tuple[str, str]]) -> str | None:
    """Lý do KHÔNG được dùng file triều thiên văn, hoặc None nếu hợp lệ (`fit_to < test.from`)."""
    test_from = splits["test"][0]
    if meta is None:
        return ("thiếu astro_tide_meta.json: không biết giai đoạn fit điều hòa (bản cũ của harmonic.R fit trên "
                "2017–2026, gồm cả tập test)")
    fit_to = str(meta.get("fit_to") or "")
    if not fit_to:
        return "astro_tide_meta.json không có fit_to"
    if fit_to >= test_from:
        return f"điều hòa fit tới {fit_to} >= mốc test {test_from} (rò rỉ dữ liệu test)"
    return None
