"""Sinh dự báo theo `schemas/prediction/v1.schema.json` (checklist 6.8).

**Cách chạy.**

    docker run --rm -v "$PWD:/work" -w /work phasegnn:local \
        python -m phasegnn.forecast --checkpoint results/E2/phasegnn/checkpoint_seed0.pt \
            --split test --out-dir results/E2/phasegnn

**Tham số.** Mô hình được huấn luyện trên cửa sổ `T_in` ngày; dự báo cho `T_out` ngày
tới. Với mỗi ngày phát bản tin `d` trong tập `test`, ta dự báo các ngày `d`…`d+T_out−1`
(`lead_day` 1..T_out) và xuất bản ghi theo schema chung.

**Vũng Tàu (`SEA_VUNGTAU`)** không có dự báo nghiệp vụ nhưng có quan trọc thực đo; ta vẫn
xuất dự báo cho nó (mô hình học được từ dữ liệu) — nhóm đánh giá có thể loại riêng.

**Ràng buộc.** Bản ghi xuất ra phải qua `baselines.validate_prediction_records` (kiểm tra
theo schema, không cần `jsonschema`).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Sequence

import numpy as np

from .baselines import write_predictions
from .data import (DEFAULT_T_IN, DEFAULT_T_OUT, SampleSet, build_datasets, default_paths,
                   load_g5, load_g5_meta, load_graph)
from .job import lock_key, run_forecast_job
from .model import ModelConfig, PhaseGNN, seed_everything
from .train import (StationSubgraph, TrainConfig, VARIANTS, build_station_subgraph,
                     evaluate, prepare_sample, _select_daily)


def _monotonic_quantiles(q10: float, q50: float, q90: float) -> tuple[float, float, float]:
    """Chuan hoa 3 phan vi thanh ``q10 <= q50 <= q90``.

    Mo hinh co the hoc duoc phan vi khong don dieu (L_mono chi phat, khong rang buoc)
    ⇒ khi xuat du bao phai chuan hoa de khong co ban ghi sai dinh dang
    (`schemas/prediction/v1.schema.json`). Cach nay giu **q50 lam truc** va don phan
    dao chieu vao hai duoi, khong hoan doi (hoi doi se lam lech trung vi).
    """
    if q10 > q50:
        q10 = q50
    if q90 < q50:
        q90 = q50
    if q90 < q10:
        q90 = q10
    return q10, q50, q90


def _add_days(day: str, n: int) -> str:
    d = date.fromisoformat(day)
    return (d + timedelta(days=n)).isoformat()


def _hours_to_iso(day: str, hour: float) -> str | None:
    """Giờ trong ngày (giờ VN) → ISO 8601 UTC.

    Giờ Việt Nam = UTC + 7 ⇒ giờ UTC = (giờ VN − 7) mod 24; nếu giờ VN < 7 thì thuộc
    ngày **hôm trước** (theo UTC).
    """
    if hour is None or not np.isfinite(hour):
        return None
    total = int(round(float(hour) * 60.0)) % (24 * 60)
    hh, mm = divmod(total, 60)
    y, m, d = (int(x) for x in day.split("-"))
    dt = date(y, m, d)
    hh_utc = (hh - 7) % 24
    if hh < 7:
        dt -= timedelta(days=1)
    return f"{dt.isoformat()}T{hh_utc:02d}:{mm:02d}:00Z"


def load_checkpoint(path: Path) -> tuple[PhaseGNN, TrainConfig, dict]:
    """Nạp checkpoint do `train.py` ghi.

    **An toàn (25/11/2026):** ``torch.load(weights_only=True)`` — chỉ giải nén tensor và kiểu cơ bản
    (dict/list/tuple/str/số), không chạy pickle tùy ý. Checkpoint của `train.py` chỉ chứa các kiểu đó
    (``state_dict``, ``config``/``train_config``/``dims``/``result`` là dict thuần) nên không cần allowlist
    (`torch.serialization.add_safe_globals`); mọi checkpoint trong `results/` đều nạp được (đã kiểm tra).
    Nơi gọi nhận đường dẫn từ nguồn ngoài (worker đọc `models`) phải tự kiểm tra đường dẫn trước
    (`apps/worker/src/worker/forecast.py::safe_checkpoint_path`).
    """
    import torch

    blob = torch.load(path, map_location="cpu", weights_only=True)
    mcfg = ModelConfig.from_dict(blob["config"])
    tc = dict(blob.get("train_config", {}))
    # Checkpoint trước 25/11/2026 không ghi `subgraph_version` ⇒ dựng đồ thị con kiểu cũ (v1,
    # thiếu cạnh `down`) để dự báo của chúng không đổi (xem `train.build_station_subgraph`).
    tc.setdefault("subgraph_version", 1)
    tcfg = TrainConfig(**{k: v for k, v in tc.items() if k in TrainConfig.__dataclass_fields__})
    dims = blob.get("dims", {})
    # `in_dim` của checkpoint cũ (trước 24/11/2026) KHÔNG có 2 kênh đồng hồ lưới;
    # checkpoint mới luôn ghi kèm số kênh thực tế trong `dims`.
    in_dim = int(dims.get("in_dim", 0))
    model = PhaseGNN(mcfg,
                     in_dim=in_dim,
                     static_dim=int(dims.get("static_dim", 0)),
                     edge_dim=int(dims.get("edge_dim", 0)))
    model.load_state_dict(blob["model_state"])
    model.eval()
    return model, tcfg, blob.get("result", {})


def predict_split(model: PhaseGNN, tcfg: TrainConfig, graph, dataset,
                  model_name: str = "phasegnn-v1",
                  spread_m: float = 0.15,
                  batch_size: int = 32) -> list[dict]:
    """Sinh bản ghi dự báo cho một tập mẫu.

    Mỗi mẫu = (trạm, ngày phát bản tin). Với `T_out` ngày dự báo, ta trích xuất đỉnh ngày
    (độ cao q50 và giờ) rồi xuất 2 bản ghi (peak_idx 0 và 1) cho mỗi ngày.

    **Dự báo theo lô.** Các mẫu của cùng một trạm dùng chung đồ thị con, nên ta gom
    `batch_size` mẫu lại và gọi `PhaseGNN.forward_batch` một lần. Kết quả **giống hệt**
    gọi từng mẫu (đã kiểm chứng: sai số tuyệt đối = 0) nhưng nhanh hơn 2–4× trên CPU.

    **Đơn vị đầu ra (sai lệch đã sửa 24/11/2026).** Mô hình dự báo trên **thang chuẩn
    hoá** (`data.HeightScale`) — cùng thang với đầu vào — nên trước khi ghi CSV ta **chuyển
    ngược về mét** theo thống kê tập train của từng trạm. Trước đây giá trị thô (thang
    chuẩn hoá) được ghi thẳng vào CSV ⇒ sai số ~0,70 m (và 2,9 m ở Vũng Tàu).
    """
    import torch

    subgraphs: dict[str, StationSubgraph] = {}
    for st in sorted({s.station for s in dataset.samples}):
        try:
            subgraphs[st] = build_station_subgraph(
                graph, st, version=int(getattr(tcfg, "subgraph_version", 1)))
        except KeyError:
            continue

    mcfg = model.cfg
    hscale = getattr(dataset, "height_scale", None)
    records: list[dict] = []
    by_station: dict[str, list] = {}
    for s in dataset.samples:
        if s.station in subgraphs:
            by_station.setdefault(s.station, []).append(s)

    bs = max(int(batch_size), 1)
    with torch.no_grad():
        for st, samples in by_station.items():
            sub = subgraphs[st]
            ei = torch.as_tensor(sub.edge_index, dtype=torch.long)
            ea = torch.as_tensor(sub.edge_attr, dtype=torch.float32)
            ed = torch.as_tensor(sub.edge_dir, dtype=torch.long)
            tau = torch.as_tensor(sub.tau_phys, dtype=torch.float32)
            static = torch.as_tensor(sub.static, dtype=torch.float32)
            idx = int(sub.station_node)
            # **Thang độ cao của trạm** (mét ↔ chuẩn hoá) — xem `data.HeightScale`.
            if hscale is not None:
                mu = float(hscale.mean.get(f"{st}::both", 0.0))
                sd = float(hscale.std.get(f"{st}::both", 1.0))
            else:
                mu, sd = 0.0, 1.0

            for start in range(0, len(samples), bs):
                chunk = samples[start:start + bs]
                sts = [prepare_sample(s, sub, mcfg, tcfg.t_in, tcfg.t_out) for s in chunk]
                node_seq = torch.as_tensor(np.stack([x.node_seq for x in sts]),
                                           dtype=torch.float32)
                out = model.forward_batch(node_seq, static, ei, ea, ed, tau,
                                          target_nodes=torch.as_tensor([idx], dtype=torch.long))
                # `forward_batch` giữ chiều node: `peak_height` = [B, 1, T, 2, Q],
                # `peak_hour` = [B, 1, T, 2] → bỏ chiều node để còn [B, T, ...].
                peak = out.peak_height.squeeze(1)      # [B, T, 2, Q]
                hours = out.peak_hour.squeeze(1)       # [B, T, 2]
                p_peak, p_hours, _p_pd, _p_hd = _select_daily(peak, hours, tcfg.t_out,
                                                              mcfg.delta_h)
                for bi, s in enumerate(chunk):
                    for d in range(p_peak.shape[1]):
                        target = _add_days(s.issue_date, d)
                        # **Sắp xếp 2 đỉnh theo giờ** (đề cương 5.2.7 / schema chung):
                        # `peak_idx = 0` là đỉnh **thứ nhất trong ngày** (đỉnh sớm hơn),
                        # `peak_idx = 1` là đỉnh thứ hai — đúng quy ước của B0/B_harm/B_KTTV
                        # và của `pipelines/r/R/evaluation.R` (khớp ưu tiên cùng chỉ số).
                        # Trước đây thứ tự slot 0/1 của mô hình là **tùy ý** ⇒ đỉnh sớm và
                        # đỉnh muộn có thể bị hoán đổi ⇒ sai số độ cao rất lớn.
                        hrs = p_hours[bi, d]
                        order = np.argsort(np.mod(hrs, 24.0), kind="stable")
                        for out_idx, pidx in enumerate(order):
                            q = p_peak[bi, d, int(pidx)]
                            # **Chuyển từ thang chuẩn hoá về mét** (thống kê tập train).
                            q10 = float(q[0]) * sd + mu
                            q50 = float(q[1]) * sd + mu
                            q90 = float(q[2]) * sd + mu
                            # giờ: giờ dự báo của đỉnh đó (giờ trong ngày, 0..24)
                            h = float(hrs[int(pidx)])
                            # Bảo đảm phân vị đơn điệu q10 <= q50 <= q90 (schema bắt buộc).
                            q10, q50, q90 = _monotonic_quantiles(q10, q50, q90)
                            records.append({
                                "model": model_name, "station": s.station,
                                "issued_date": s.issue_date, "target_date": target,
                                "lead_day": d + 1, "peak_idx": out_idx,
                                "h_q10": q10, "h_q50": q50, "h_q90": q90,
                                "t_peak": _hours_to_iso(target, h),
                            })
    return records


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Sinh dự báo theo schema chung")
    ap.add_argument("--checkpoint", required=True)
    ap.add_argument("--split", default="test", choices=["train", "val", "test"])
    ap.add_argument("--model-name", default=None)
    ap.add_argument("--out-dir", default=None)
    ap.add_argument("--data-root", default=None)
    ap.add_argument("--basin", default="DONG_RAI", help="tên lưu vực (dùng cho khoá)")
    ap.add_argument("--job-day", default=None,
                    help="chạy job hằng ngày cho ngày này (ISO) thay vì xuất tập test")
    ap.add_argument("--no-lock", action="store_true",
                    help="không lấy khoá Redlock (chạy thử trên máy không có Redis)")
    ap.add_argument("--threshold", default=None,
                    help="ngưỡng báo động theo trạm, dạng 'PHU_AN=1.2,NHA_BE=1.3'")
    ap.add_argument("--max-samples", type=int, default=None,
                    help="chỉ dự báo N mẫu đầu của tập (chạy nhanh để kiểm tra)")
    args = ap.parse_args(argv)

    paths = default_paths(Path(args.data_root) if args.data_root else None)
    df = load_g5(paths=paths)
    graph = load_graph(paths=paths)
    model, tcfg, result = load_checkpoint(Path(args.checkpoint))
    seed_everything(tcfg.seed)

    datasets = build_datasets(df, t_in=tcfg.t_in, t_out=tcfg.t_out,
                              holdout_stations=tuple(tcfg.holdout_stations or ()))
    ds = datasets[args.split]
    if args.max_samples:
        # giữ nguyên thứ tự nhưng lấy N mẫu đầu mỗi trạm để phủ đều các trạm
        per_station: dict[str, int] = {}
        kept = []
        for s in ds.samples:
            c = per_station.get(s.station, 0)
            if c >= args.max_samples:
                continue
            per_station[s.station] = c + 1
            kept.append(s)
        ds = SampleSet(samples=kept, split=ds.split, feature_names=ds.feature_names,
                       stations=sorted(per_station), scaler=ds.scaler,
                       height_scale=ds.height_scale)
    name = args.model_name or f"{tcfg.variant}-v1"
    records = predict_split(model, tcfg, graph, ds, model_name=name)
    print(f"[forecast] {len(records)} bản ghi cho tập {args.split}", flush=True)

    from .baselines import validate_prediction_records
    errors = validate_prediction_records(records)
    if errors:
        print(f"[forecast] CẢNH BÁO: {len(errors)} bản ghi không hợp lệ; ví dụ: {errors[0]}",
              flush=True)

    out_dir = Path(args.out_dir) if args.out_dir else Path(args.checkpoint).parent

    # --- Chế độ job hằng ngày (checklist 6.8) -----------------------------
    if args.job_day:
        from .baselines import validate_prediction_records

        job_records = [r for r in records if r.get("issued_date") == args.job_day]
        if not job_records:
            print(f"[forecast] không có bản ghi cho ngày {args.job_day}; "
                  f"dùng --split test để xuất toàn bộ tập", flush=True)
            return 1
        thresholds: dict[str, float] = {}
        if args.threshold:
            for part in args.threshold.split(","):
                if "=" in part:
                    k, v = part.split("=", 1)
                    thresholds[k.strip()] = float(v)
        job = run_forecast_job(day=args.job_day, basin=args.basin,
                               records=job_records, model_name=name,
                               thresholds=thresholds, out_dir=out_dir,
                               use_lock=not args.no_lock)
        errs = validate_prediction_records(job_records)
        if errs:
            print(f"[forecast] CẢNH BÁO: {len(errs)} bản ghi không hợp lệ", flush=True)
        print(f"[forecast] job {args.job_day}: khoá={lock_key(args.basin, args.job_day)}, "
              f"fence={job.fence}, dự báo={job.n_forecasts}, cảnh báo={job.n_alerts}, "
              f"ghi DB={job.wrote_db}, ghi cache={job.wrote_cache}", flush=True)
        for e in job.errors:
            print(f"[forecast]   - {e}", flush=True)
        return 0

    path = write_predictions(records, out_dir, filename=f"predictions_{args.split}.csv")
    print(f"[forecast] ghi {path}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
