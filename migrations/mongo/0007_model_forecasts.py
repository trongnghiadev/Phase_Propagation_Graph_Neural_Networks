"""Migration 0007_model_forecasts: Tạo model_forecasts (dự báo PhaseGNN của worker): validator + index unique (run_date, station, lead_day).

Nguồn: `schemas/mongo/model_forecasts/v1.json` (`validationLevel: moderate` — không chặn sửa document cũ không
hợp lệ; số document như vậy được báo trong `n_invalid_existing`). Idempotent. Chạy qua `runner.py`.
"""
from _lib import apply_spec, load_spec, restore, verify_spec

COLLECTION = "model_forecasts"
DESCRIPTION = "Tạo model_forecasts (dự báo PhaseGNN của worker): validator + index unique (run_date, station, lead_day)"


def up(db, *, dry_run: bool = False) -> dict:
    return apply_spec(db, load_spec(COLLECTION), dry_run=dry_run)


def verify(db) -> list[str]:
    return verify_spec(db, load_spec(COLLECTION))


def down(db, previous: dict | None) -> None:
    restore(db, COLLECTION, previous)
