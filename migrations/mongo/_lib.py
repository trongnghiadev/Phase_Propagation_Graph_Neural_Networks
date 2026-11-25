"""Hàm dùng chung cho các bước migration: áp validator + index từ `schemas/mongo/<collection>/v<n>.json`.

Mọi bước đều **idempotent**: áp lại cùng validator (`collMod`) và cùng index (`createIndexes` cùng định nghĩa)
là no-op. Trước khi áp, đếm số document hiện có KHÔNG khớp validator mới (``$nor: [validator]``) — với
``validationLevel: moderate`` các document này không bị chặn khi sửa, nhưng được báo để người vận hành biết.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[2]
SCHEMAS = REPO / "schemas" / "mongo"


def load_spec(collection: str, version: int = 1) -> dict:
    """Đọc file schema của `collection` (phiên bản `version`)."""
    return json.loads((SCHEMAS / collection / f"v{version}.json").read_text(encoding="utf-8"))


def collection_options(db, name: str) -> dict | None:
    """Tuỳ chọn hiện tại (`validator`, `validationLevel`, `validationAction`) hoặc ``None`` nếu chưa có."""
    for c in db.list_collections(filter={"name": name}):
        return dict(c.get("options") or {})
    return None


def count_invalid(db, name: str, validator: dict) -> int:
    """Số document hiện có không khớp `validator`."""
    if collection_options(db, name) is None:
        return 0
    return db[name].count_documents({"$nor": [validator]})


def apply_spec(db, spec: dict, *, dry_run: bool = False) -> dict:
    """Áp validator + index của `spec`. Trả báo cáo (kèm tuỳ chọn cũ để `down` khôi phục)."""
    name = spec["collection"]
    opts = collection_options(db, name)
    previous = None if opts is None else {k: opts.get(k) for k in ("validator", "validationLevel",
                                                                     "validationAction")}
    report: dict[str, Any] = {"collection": name, "schema_version": spec["version"], "existed": opts is not None,
                              "n_documents": db[name].estimated_document_count() if opts is not None else 0,
                              "n_invalid_existing": count_invalid(db, name, spec["validator"]),
                              "validationLevel": spec["validationLevel"], "previous": previous,
                              "indexes": [ix["name"] for ix in spec.get("indexes", [])], "dry_run": dry_run}
    if dry_run:
        return report
    if opts is None:
        db.create_collection(name, validator=spec["validator"], validationLevel=spec["validationLevel"],
                             validationAction=spec["validationAction"])
    else:
        db.command({"collMod": name, "validator": spec["validator"],
                    "validationLevel": spec["validationLevel"], "validationAction": spec["validationAction"]})
    if spec.get("indexes"):
        db.command({"createIndexes": name, "indexes": [dict(ix) for ix in spec["indexes"]]})
    return report


def verify_spec(db, spec: dict) -> list[str]:
    """Danh sách sai khác giữa DB và `spec` (rỗng = khớp)."""
    name = spec["collection"]
    opts = collection_options(db, name)
    if opts is None:
        return [f"{name}: chưa có collection"]
    errs = []
    if opts.get("validator") != spec["validator"]:
        errs.append(f"{name}: validator khác schemas/mongo/{name}/v{spec['version']}.json")
    for k in ("validationLevel", "validationAction"):
        if opts.get(k, "strict" if k == "validationLevel" else "error") != spec[k]:
            errs.append(f"{name}: {k} = {opts.get(k)!r}, cần {spec[k]!r}")
    have = db[name].index_information()
    for ix in spec.get("indexes", []):
        got = have.get(ix["name"])
        if got is None:
            errs.append(f"{name}: thiếu index {ix['name']}")
        elif [(k, int(v)) for k, v in got["key"]] != [(k, int(v)) for k, v in ix["key"].items()]:
            errs.append(f"{name}: index {ix['name']} khác định nghĩa")
    return errs


def restore(db, name: str, previous: dict | None) -> None:
    """`down`: khôi phục validator cũ (không xoá index — index thừa vô hại, xoá có thể làm chậm truy vấn)."""
    if collection_options(db, name) is None:
        return
    if previous and previous.get("validator"):
        db.command({"collMod": name, "validator": previous["validator"],
                    "validationLevel": previous.get("validationLevel") or "strict",
                    "validationAction": previous.get("validationAction") or "error"})
    else:
        db.command({"collMod": name, "validator": {}, "validationLevel": "off"})
