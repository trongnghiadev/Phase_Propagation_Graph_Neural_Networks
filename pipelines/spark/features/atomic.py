"""Nạp nguyên tử collection MongoDB theo mẫu G0: staging → kiểm tra → sao lưu `__prev` → thay.

Dùng chung cho G1 (`graph_edges`): mỗi job sở hữu một collection, ghi vào
`<tên>__staging_<run_id>`, kiểm tra số lượng, sao lưu bản chính thành `<tên>__prev`,
thay bằng `renameCollection(dropTarget: true)`, rồi xóa `__prev`.
"""
from __future__ import annotations

from typing import Callable

from common.session import MongoAdmin

STAGING = "__staging"
PREV = "__prev"


class PrevPendingError(RuntimeError):
    """Còn `__prev` của một lần chạy hỏng giữa chừng: cần lùi lại rồi ghi nhận trước khi chạy tiếp."""


def rollback_help(bad: dict, prevs: list[str]) -> str:
    done = list(bad.get("replaced") or [])
    if bad.get("replacing") and bad["replacing"] not in done:
        done.append(f"{bad['replacing']} (có thể đã thay)")
    return (f"lần chạy {bad['_id']} (status {bad.get('status')}) hỏng giữa chừng: đã thay {done}; còn {prevs}.\n"
            f"  Gỡ chặn: (a) lùi lại từng collection đã thay từ bản __prev: "
            f"renameCollection('<db>.<tên>{PREV}', '<db>.<tên>', dropTarget=true); "
            f"(b) chạy lại job với --ack-rollback {bad['_id']} (đặt jobs.status = rolled_back, xóa các {PREV} còn lại).")


def check_prev_safe(adm: MongoAdmin, name: str, run_id: str, jobs: list[dict]) -> None:
    """Dừng nếu còn `__prev` của một lần chạy hỏng giữa chừng (sau lần ok gần nhất)."""
    prev = name + PREV
    if not adm.exists(prev):
        return
    bad = pending_rollback(jobs, run_id)
    if bad:
        raise PrevPendingError(rollback_help(bad, [prev]))


def pending_rollback(jobs: list[dict], run_id: str) -> dict | None:
    """Run `failed`/`running` đã thay một phần sau lần `ok` gần nhất → đang chặn.

    `jobs`: [{_id, status, replaced, replacing, generated_at}] (thứ tự thời gian tăng dần).
    """
    finished = {"ok", "failed", "dry_run", "blocked", "rolled_back"}
    last_ok = None
    for j in jobs:
        if j.get("status") == "ok":
            last_ok = j
    for j in jobs:
        if j.get("_id") == run_id or j.get("status") not in ("failed", "running"):
            continue
        if not (j.get("replaced") or j.get("replacing")):
            continue
        if last_ok is not None and j.get("generated_at") and last_ok.get("generated_at") \
                and j["generated_at"] <= last_ok["generated_at"]:
            continue
        return j
    _ = finished
    return None


def backup(adm: MongoAdmin, name: str) -> bool:
    """Sao bản chính thành `<tên>__prev` (giữ validator/index của bản chính hiện tại)."""
    prev = name + PREV
    adm.drop(prev)
    if not adm.exists(name):
        return False
    adm.create(prev)
    stage = {"$out": {"db": adm.db_name, "coll": prev}}
    adm.aggregate(name, [stage], write=True, bypass_validation=True)
    n, m = adm.count(name), adm.count(prev)
    if n != m:
        raise RuntimeError(f"sao lưu {name} → {prev}: {m} / {n} document")
    print(f"[SAO LƯU] {name} → {prev}: {m} document", flush=True)
    return True


def swap(adm: MongoAdmin, name: str, staging: str, expected: int, on_replaced: Callable[[], None]) -> int:
    """Thay `name` bằng `staging`; `on_replaced()` gọi NGAY sau khi lệnh thay thành công."""
    n = adm.count(staging)
    if n != expected:
        raise RuntimeError(f"{staging}: {n} document, kỳ vọng {expected}; không thay {name}")
    adm.rename(staging, name, drop_target=True)
    on_replaced()
    got = adm.count(name)
    if got != expected:
        raise RuntimeError(f"{name}: sau khi thay có {got} document, kỳ vọng {expected}")
    print(f"[NẠP] {name}: {got} document", flush=True)
    return got


def ack_rollback(adm: MongoAdmin, name: str, target: str, jobs: list[dict]) -> int:
    """Gỡ chặn: đặt `jobs[target].status = rolled_back`, xóa mọi `__prev`. 0 thành công, 1 nếu sai run_id."""
    prev = name + PREV
    bad = pending_rollback(jobs, "")
    if bad is None or bad["_id"] != target:
        print(f"--ack-rollback {target}: không phải lần chạy đang chặn "
              f"(đang chặn: {bad['_id'] if bad else 'không có'}); không thay đổi gì.", flush=True)
        return 1
    print(f"[ACK] {target}: xóa {prev if adm.exists(prev) else 'không có __prev'}; jobs.status = rolled_back",
          flush=True)
    adm.drop(prev)
    adm.upsert("jobs", {"_id": target}, {"$set": {"status": "rolled_back"}})
    return 0
