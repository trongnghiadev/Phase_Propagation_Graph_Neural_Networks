"""Migration 0006_jobs_validator: Validator anyOf cho hai bộ field của jobs (Spark job/status, API-worker type/state) + index hàng đợi.

Nguồn: `schemas/mongo/jobs/v1.json` (`validationLevel: moderate` — không chặn sửa document cũ không
hợp lệ; số document như vậy được báo trong `n_invalid_existing`). Idempotent. Chạy qua `runner.py`.
"""
from _lib import apply_spec, load_spec, restore, verify_spec

COLLECTION = "jobs"
DESCRIPTION = "Validator anyOf cho hai bộ field của jobs (Spark job/status, API-worker type/state) + index hàng đợi"


def up(db, *, dry_run: bool = False) -> dict:
    return apply_spec(db, load_spec(COLLECTION), dry_run=dry_run)


def verify(db) -> list[str]:
    return verify_spec(db, load_spec(COLLECTION))


def down(db, previous: dict | None) -> None:
    restore(db, COLLECTION, previous)
