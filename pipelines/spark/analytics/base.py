"""Khung chung cho các job phân tích Q1–Q7 (chương 3, checklist GĐ3.2).

Các job Q1–Q7 đều: đọc MongoDB bằng **PySpark** (để chương báo cáo hiệu năng Spark),
ghi kết quả CSV ra `results/Q<n>/`, có `README.md` riêng, ghi bản ghi `jobs`, và
khoá `job_locks` để không chạy trùng.

Không dùng `mongo shell` cho các truy vấn này: mục tiêu là đo và trình bày hiệu năng
Spark trên tập dữ liệu thật của lưu vực Đồng Nai – Sài Gòn.

**Quy ước chung.**
- Mã thoát: 0 ok, 1 kiểm tra hỏng, 2 thiếu dữ liệu, 3 job đang chạy ở nơi khác,
  5 mất khóa, 6 lỗi không lường trước.
- Mọi thư mục đầu ra `results/Q<n>/` được ghi trong container driver (mount chế độ ghi).
- `REDIS_URL` (tuỳ chọn): nếu được đặt, kết quả Q1–Q4 và Q7 được đẩy lên cache để các
  lần chạy sau đọc lại nhanh. Nếu KHÔNG có biến này, bước này được BỎ QUA kèm cảnh báo
  (không phải lỗi) — môi trường hiện tại chưa có Redis.
"""
from __future__ import annotations

import argparse
import csv
import os
import sys
import traceback
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from common.jobrun import JobRun  # noqa: E402
from common.session import (JobLockError, LockLostError, MongoAdmin, build_session, env_db,  # noqa: E402
                            env_uri)

VN_TZ = timezone(timedelta(hours=7))


# ---------------------------------------------------------------------------
# Tiện ích chung
# ---------------------------------------------------------------------------
def add_common_args(ap: argparse.ArgumentParser) -> None:
    """Thêm các đối số chung cho mọi job Q1–Q7."""
    ap.add_argument("uri", nargs="?", default=env_uri(), help="URI MongoDB (mặc định biến MONGO_URI)")
    ap.add_argument("--db", default=env_db(), help="database (mặc định biến MONGO_DB hoặc vietnam_gis)")
    ap.add_argument("--out-dir", default=None, help="thư mục ghi CSV (mặc định /out/Q<n>)")
    ap.add_argument("--lock-ttl-s", type=int, default=3600, help="hạn khóa job_locks (giây)")
    ap.add_argument("--dry-run", action="store_true", help="tính và in, không ghi file kết quả")
    ap.add_argument("--no-cache", action="store_true", help="không dùng cache Redis dù có REDIS_URL")


def write_csv(path: str, header: list[str], rows: list) -> int:
    """Ghi CSV (UTF-8, phân cách `,`, xuống dòng `\\n`). Trả về số dòng dữ liệu."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(header)
        for r in rows:
            w.writerow(list(r))
    return len(rows)


def write_readme(path: str, title: str, question: str, config: dict, outputs: list[str],
                 findings: list[str], limits: list[str], run_date: str) -> None:
    """Ghi `README.md` cho thư mục kết quả."""
    lines = [f"# {title}", "", "## Câu hỏi trả lời", "", question, "", "## Cấu hình chạy", ""]
    lines.append("| Tham số | Giá trị |")
    lines.append("|---|---|")
    for k, v in config.items():
        lines.append(f"| {k} | {v} |")
    lines += ["", "## Kết quả", ""]
    for f in findings:
        lines.append(f"- {f}")
    lines += ["", "## File sinh ra", ""]
    for f in outputs:
        lines.append(f"- `{f}`")
    lines += ["", "## Giới hạn", ""]
    for f in limits:
        lines.append(f"- {f}")
    lines += ["", f"**Ngày chạy:** {run_date}", ""]
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write("\n".join(lines))


def now_vn() -> str:
    """Thời điểm hiện tại theo giờ Việt Nam (UTC+7), định dạng `YYYY-MM-DD HH:MM`."""
    return datetime.now(VN_TZ).strftime("%Y-%m-%d %H:%M") + " (UTC+7)"


# ---------------------------------------------------------------------------
# Cache Redis (TUỲ CHỌN)
# ---------------------------------------------------------------------------
class ResultCache:
    """Cache kết quả CSV trên Redis — TUỸ CHỌN, bỏ qua kèm cảnh báo nếu không có Redis.

    Biến môi trường `REDIS_URL` (ví dụ `redis://redis:6379/0`). Nếu không đặt, hoặc không
    kết nối được, mọi lệnh đọc/ghi trở thành no-op và in cảnh báo MỘT lần.
    """

    def __init__(self, prefix: str, enabled: bool = True):
        self.prefix = prefix
        self.client = None
        self.reason = ""
        if not enabled:
            self.reason = "tắt bằng --no-cache"
            return
        url = os.environ.get("REDIS_URL", "").strip()
        if not url:
            self.reason = "không đặt biến REDIS_URL"
            return
        try:
            import redis  # noqa: PLC0415
            self.client = redis.Redis.from_url(url, socket_connect_timeout=2, socket_timeout=5)
            self.client.ping()
        except Exception as e:  # noqa: BLE001
            self.client = None
            self.reason = f"không kết nối được Redis ({type(e).__name__}: {e})"

    @property
    def enabled(self) -> bool:
        return self.client is not None

    def note(self) -> str:
        """Thông báo trạng thái cache (in ra log)."""
        if self.enabled:
            return f"Redis cache BẬT (tiền tố `{self.prefix}`)"
        return f"[CẢNH BÁO] bỏ qua Redis cache: {self.reason}"

    def get(self, key: str) -> str | None:
        if not self.enabled:
            return None
        try:
            raw = self.client.get(f"{self.prefix}:{key}")
            return raw.decode("utf-8") if raw else None
        except Exception:  # noqa: BLE001
            return None

    def set(self, key: str, value: str, ttl_s: int = 86400) -> None:
        if not self.enabled:
            return
        try:
            self.client.setex(f"{self.prefix}:{key}", ttl_s, value.encode("utf-8"))
        except Exception:  # noqa: BLE001
            pass


# ---------------------------------------------------------------------------
# Ngữ cảnh một truy vấn
# ---------------------------------------------------------------------------
class Ctx:
    """Ngữ cảnh truyền cho phần thân của một truy vấn Q."""

    def __init__(self, spark, adm: MongoAdmin, run: JobRun, args, out_dir: str, job: str):
        self.spark = spark
        self.adm = adm
        self.run = run
        self.args = args
        self.out_dir = out_dir
        self.job = job

    @property
    def db(self) -> str:
        return self.args.db

    def path(self, name: str) -> str:
        """Đường dẫn tuyệt đối của một file kết quả trong thư mục đầu ra."""
        return os.path.join(self.out_dir, name)


def run_job(job_name: str, q_dir: str, body, argv: list[str] | None = None,
            extra_args=None) -> int:
    """Bọc một truy vấn Q: khoá `job_locks`, bản ghi `jobs`, mã thoát chuẩn.

    `body(ctx)` nhận một `Ctx` và trả về `(findings, outputs)` — danh sách câu kết luận
    và danh sách file đã ghi.
    `extra_args(ap)` là hook tuỳ chọn để job thêm đối số riêng.
    """
    ap = argparse.ArgumentParser(description=f"{job_name} — truy vấn phân tích")
    add_common_args(ap)
    if extra_args:
        extra_args(ap)
    args = ap.parse_args(argv)

    spark = build_session(job_name, args.uri, args.db)
    spark.sparkContext.setLogLevel("WARN")
    adm = MongoAdmin(spark, args.uri, args.db)
    run = JobRun(adm, job_name, args.lock_ttl_s)
    out_dir = args.out_dir or f"/out/{q_dir}"
    status = "failed"
    try:
        try:
            run.acquire()
        except JobLockError as e:
            print(f"Dừng: {e}", flush=True)
            return 3
        run.start(db=args.db, out_dir=out_dir, dry_run=args.dry_run)
        ctx = Ctx(spark=spark, adm=adm, run=run, args=args, out_dir=out_dir, job=job_name)
        findings, outputs = body(ctx)
        run.update({"findings": findings, "outputs": outputs})
        print("Tóm tắt:", flush=True)
        for f in findings:
            print(f"  - {f}", flush=True)
        status = "dry_run" if args.dry_run else "ok"
        return 0
    except LockLostError as e:
        print(f"Dừng: {e}", flush=True)
        return 5
    except Exception:  # noqa: BLE001
        status = "failed"
        traceback.print_exc()
        print("Dừng: lỗi không lường trước (mã thoát 6).", flush=True)
        return 6
    finally:
        try:
            run.finish(status)
        finally:
            try:
                adm.close()
            finally:
                spark.stop()
