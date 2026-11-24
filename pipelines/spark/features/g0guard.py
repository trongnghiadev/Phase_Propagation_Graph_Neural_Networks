"""Chặn job đọc đầu ra G0 (`tide_extremes`, `reservoir_daily`, `rainfall_daily`, ...) khi G0 đang chạy/dở dang.

Cùng quy tắc với job QC (`qc/qc_tide_extremes.py`): sau khi đã giữ khóa của mình,

* G0 đang giữ khóa `job_locks` (`g0_flatten_bulletins`) → mã thoát 3 (chạy lại sau);
* G0 còn dở thay thế (`flatten.rules.g0_unsettled`: còn `<tên>__prev`, hoặc run G0 `failed`/`running` đã thay một
  phần sau lần ok gần nhất) → mã thoát 4, `jobs.status = blocked`.

Đọc giữa lúc G0 đang thay collection có thể lấy `tide_extremes` mới cùng `rainfall_daily` cũ (hoặc collection rỗng)
mà kiểm tra số lượng không phát hiện được.
"""
from __future__ import annotations

from flatten import rules as frules
from flatten.flatten_bulletins import NAMES as G0_NAMES, PREV as G0_PREV, load_jobs as g0_jobs

G0_JOB = "g0_flatten_bulletins"
EXIT_G0_RUNNING = 3
EXIT_G0_UNSETTLED = 4


def g0_block(run, adm) -> tuple[int, str, str] | None:
    """`(mã thoát, jobs.status, thông điệp)` nếu phải dừng vì G0, hoặc None. Gọi SAU `run.acquire()`."""
    other = run.other_running(G0_JOB)
    if other:
        return (EXIT_G0_RUNNING, "failed",
                f"G0 đang chạy (khóa {other.get('_id', G0_JOB)}, run {other.get('run_id')}); chạy lại sau.")
    why = frules.g0_unsettled(g0_jobs(adm), [n + G0_PREV for n in G0_NAMES if adm.exists(n + G0_PREV)])
    if why:
        return (EXIT_G0_UNSETTLED, "blocked",
                f"G0 còn dở thay thế: {why}. Xử lý theo hướng dẫn của G0 (lùi lại, --ack-rollback) rồi chạy lại "
                f"G0 trước job này.")
    return None
