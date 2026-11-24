"""Ghi thư mục đầu ra (Parquet) theo kiểu "ghi tạm → đổi tên", giữ bản cũ tới khi bản mới xong.

    tmp = outdir.tmp_path(out, run_id)        # <out>.tmp_<run_id>
    df.write.parquet(tmp)                     # ghi đầy đủ vào thư mục tạm (bản chính chưa bị đụng)
    ...kiểm tra, ghi _meta.json vào tmp...
    outdir.publish(tmp, out, run_id)          # đổi tên: out → out.old_<run_id>, tmp → out, xóa .old

Thuần Python (không cần Spark, có test). Thay cho kiểu cũ `rmtree(out)` / `mode("overwrite")` rồi ghi
thẳng vào `out`: nếu job chết giữa chừng thì bản chính đã mất mà bản mới chưa đủ.

**Tính nguyên tử.** `os.rename` trên cùng hệ thống tệp là nguyên tử cho từng lệnh, nhưng POSIX không
cho đổi tên đè lên một thư mục không rỗng, nên `publish` cần hai lệnh đổi tên; giữa hai lệnh (vài micro
giây) `out` tạm thời không tồn tại. Lệnh thứ hai hỏng → đổi tên bản cũ trở lại (`out` giữ nguyên bản cũ).
Nếu tiến trình chết đúng giữa hai lệnh, bản cũ còn nguyên ở `<out>.old_<run_id>` — `recover` (gọi ở đầu
mỗi lần chạy) đưa nó về.
"""
from __future__ import annotations

import os
import shutil

TMP_TAG = ".tmp_"
OLD_TAG = ".old_"


def tmp_path(out: str, run_id: str) -> str:
    """`<out>.tmp_<run_id>` (cùng thư mục cha với `out` ⇒ cùng hệ thống tệp, đổi tên được)."""
    return out.rstrip("/\\") + TMP_TAG + run_id


def old_path(out: str, run_id: str) -> str:
    return out.rstrip("/\\") + OLD_TAG + run_id


def _siblings(out: str, tag: str) -> list[str]:
    out = out.rstrip("/\\")
    parent = os.path.dirname(out) or "."
    base = os.path.basename(out) + tag
    if not os.path.isdir(parent):
        return []
    return sorted(os.path.join(parent, n) for n in os.listdir(parent) if n.startswith(base))


def recover(out: str, stale_s: float = 6 * 3600, now: float | None = None) -> list[str]:
    """Dọn dấu vết của lần chạy hỏng trước. Trả về danh sách thao tác đã làm (để in log).

    * `out` không còn mà có `<out>.old_*` (chết giữa hai lệnh đổi tên) → đưa bản cũ mới nhất về `out`;
    * `<out>.old_*` còn lại khi `out` đã có → xóa;
    * `<out>.tmp_*` cũ hơn `stale_s` giây (bản mới chưa xong của lần chạy đã chết) → xóa. Bản tạm mới hơn
      được giữ: có thể là của job khác đang ghi cùng đường dẫn (G1 và G3 cùng ghi `graph/edges.parquet`).
    """
    import time
    now = time.time() if now is None else now
    out = out.rstrip("/\\")
    done: list[str] = []
    olds = _siblings(out, OLD_TAG)
    if olds and not os.path.exists(out):
        os.rename(olds[-1], out)
        done.append(f"khôi phục {olds[-1]} → {out}")
        olds = olds[:-1]
    for p in olds:
        shutil.rmtree(p, ignore_errors=True)
        done.append(f"xóa {p}")
    for p in _siblings(out, TMP_TAG):
        try:
            age = now - os.path.getmtime(p)
        except OSError:
            continue
        if age >= stale_s:
            shutil.rmtree(p, ignore_errors=True)
            done.append(f"xóa {p}")
    return done


def discard(tmp: str) -> None:
    """Bỏ bản tạm (job dừng vì kiểm tra hỏng, --dry-run, lỗi)."""
    if tmp and os.path.exists(tmp):
        shutil.rmtree(tmp, ignore_errors=True)


def publish(tmp: str, out: str, run_id: str) -> None:
    """Thay `out` bằng `tmp`. Bản cũ chỉ bị xóa sau khi `tmp` đã nằm ở `out`."""
    out = out.rstrip("/\\")
    if not os.path.isdir(tmp):
        raise FileNotFoundError(f"không có bản tạm {tmp}")
    old = old_path(out, run_id)
    had_old = os.path.exists(out)
    if had_old:
        os.rename(out, old)
    try:
        os.rename(tmp, out)
    except Exception:
        if had_old and not os.path.exists(out):
            os.rename(old, out)
        raise
    if had_old:
        shutil.rmtree(old, ignore_errors=True)
