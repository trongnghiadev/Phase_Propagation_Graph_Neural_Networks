#!/usr/bin/env python
"""So sánh tốc độ huấn luyện PhaseGNN trên CPU và GPU (CUDA) — một lượt nhỏ, cùng dữ liệu, cùng seed.

**Cách chạy** (trong image `phasegnn:cuda`, xem `packages/phasegnn/README.md`, mục "Chạy trên GPU"):

    docker run --rm --gpus all -v "$PWD:/work" -w /work phasegnn:cuda \
        python -X utf8 scripts/bench_device.py --epochs 1 --max-train-samples 600 --max-val-samples 200

In thời gian mỗi epoch (giây; gồm cả đánh giá val) và val MAE đỉnh của từng thiết bị, rồi chênh lệch
MAE giữa các thiết bị (khác 0 một chút là bình thường: phép toán dấu phẩy động trên GPU không theo đúng
thứ tự như CPU; dùng ``--deterministic`` để giảm, không bảo đảm bằng 0 giữa CPU và GPU).

Thiết bị không có (ví dụ ``cuda`` trên máy/image không có GPU) được bỏ qua kèm thông báo, không lỗi.
Không ghi checkpoint, không ghi MLflow/MongoDB.
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "phasegnn" / "src"))

from phasegnn.data import build_datasets, default_paths, load_g5, load_graph  # noqa: E402
from phasegnn.device import DeviceUnavailable, device_info, resolve_device, set_deterministic  # noqa: E402
from phasegnn.train import TrainConfig, train_model  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="So sánh thời gian/epoch PhaseGNN trên CPU và CUDA")
    ap.add_argument("--devices", nargs="*", default=["cpu", "cuda"])
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=16)
    ap.add_argument("--max-train-samples", type=int, default=600)
    ap.add_argument("--max-val-samples", type=int, default=200)
    ap.add_argument("--seed", type=int, default=0)
    ap.add_argument("--num-threads", type=int, default=1)
    ap.add_argument("--deterministic", action="store_true")
    ap.add_argument("--data-root", default=None)
    args = ap.parse_args(argv)

    if args.deterministic:
        set_deterministic(True)
    paths = default_paths(Path(args.data_root) if args.data_root else None)
    df = load_g5(paths=paths)
    graph = load_graph(paths=paths)
    datasets = build_datasets(df)

    rows: list[dict] = []
    for spec in args.devices:
        try:
            dev = resolve_device(spec)
        except DeviceUnavailable as e:
            print(f"[bench] bỏ qua {spec}: {e}", flush=True)
            continue
        info = device_info(dev)
        tcfg = TrainConfig(epochs=args.epochs, batch_size=args.batch_size, seed=args.seed,
                           patience=args.epochs + 1, max_train_samples=args.max_train_samples,
                           max_val_samples=args.max_val_samples, subsample="spread",
                           num_threads=args.num_threads, device=dev,
                           deterministic=bool(args.deterministic),
                           use_mlflow=False, register_to_db=False)
        t0 = time.perf_counter()
        _, res = train_model(datasets, graph, tcfg, log=lambda *_a, **_k: None)
        total = time.perf_counter() - t0
        per_epoch = [float(h.get("seconds", float("nan"))) for h in res.history]
        rows.append({"device": dev, "gpu": info.get("gpu_name", "-"), "total_s": total,
                     "s_per_epoch": sum(per_epoch) / max(len(per_epoch), 1),
                     "val_mae": float(res.best_val_mae)})
        print(f"[bench] {dev:8s} ({info.get('gpu_name', 'CPU')}): {rows[-1]['s_per_epoch']:.1f} s/epoch, "
              f"tổng {total:.1f} s, val MAE đỉnh = {res.best_val_mae:.4f} m", flush=True)

    if len(rows) >= 2:
        base = rows[0]
        for r in rows[1:]:
            speed = base["s_per_epoch"] / r["s_per_epoch"] if r["s_per_epoch"] > 0 else float("nan")
            print(f"[bench] {r['device']} so với {base['device']}: nhanh hơn {speed:.2f}×; "
                  f"chênh val MAE = {r['val_mae'] - base['val_mae']:+.5f} m", flush=True)
    elif not rows:
        print("[bench] không chạy được thiết bị nào", flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
