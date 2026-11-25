"""Migration 0005_audit_logs_validator: Validator $jsonSchema + index của audit_logs (trước đây chỉ nằm trong vngis.qc_rules).

Nguồn: `schemas/mongo/audit_logs/v1.json` (`validationLevel: moderate` — không chặn sửa document cũ không
hợp lệ; số document như vậy được báo trong `n_invalid_existing`). Idempotent. Chạy qua `runner.py`.
"""
from _lib import apply_spec, load_spec, restore, verify_spec

COLLECTION = "audit_logs"
DESCRIPTION = "Validator $jsonSchema + index của audit_logs (trước đây chỉ nằm trong vngis.qc_rules)"


def up(db, *, dry_run: bool = False) -> dict:
    return apply_spec(db, load_spec(COLLECTION), dry_run=dry_run)


def verify(db) -> list[str]:
    return verify_spec(db, load_spec(COLLECTION))


def down(db, previous: dict | None) -> None:
    restore(db, COLLECTION, previous)
