"""Test job `forecast_run` nối PhaseGNN (mô hình giả — không cần torch/phasegnn)."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from worker import forecast as fc
from worker import jobs
from worker.scheduler import JobRunner, Scheduler

REPO = Path(__file__).resolve().parents[3]


def _records(day: str, stations=("PHU_AN", "NHA_BE"), t_out: int = 3) -> list[dict]:
    out = []
    for st in stations:
        for lead in range(1, t_out + 1):
            for k in (0, 1):
                out.append({"model": "phasegnn-v1", "station": st, "issued_date": day,
                            "target_date": f"2026-01-{lead:02d}", "lead_day": lead, "peak_idx": k,
                            "h_q10": 1.0 + k, "h_q50": 1.2 + k, "h_q90": 1.4 + k,
                            "t_peak": "2026-01-01T03:00:00Z"})
    return out


class FakePredictor:
    def __init__(self, day="2025-12-31", **kw):
        self.day, self.kw, self.calls = day, kw, []

    def __call__(self, checkpoint, *, run_date=None, stations=None, data_root=None):
        self.calls.append({"checkpoint": checkpoint, "run_date": run_date, "stations": stations})
        recs = _records(self.day, **self.kw)
        if stations:
            recs = [r for r in recs if r["station"] in stations]
        return {"run_date": self.day, "model": "phasegnn-v1", "records": recs}


class FakeGuard:
    def __init__(self, fence=5, lost=False):
        self.fence, self.lost = fence, lost

    def check(self):
        from vngis.locks import LockLost
        if self.lost:
            raise LockLost("mất khoá")


@pytest.fixture()
def root(tmp_path, monkeypatch) -> Path:
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    ck = tmp_path / fc.DEFAULT_CHECKPOINT
    ck.parent.mkdir(parents=True)
    ck.write_bytes(b"checkpoint gia")
    return tmp_path


def test_khong_co_checkpoint_skipped(fake_db, tmp_path, monkeypatch):
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    out = jobs.forecast_run(fake_db, repo_root=str(tmp_path), predictor=FakePredictor())
    assert out["status"] == "skipped" and "checkpoint" in out["reason"]
    assert fc.MODEL_FORECASTS not in fake_db.list_collection_names()


def test_khong_co_phasegnn_skipped(fake_db, root, monkeypatch):
    monkeypatch.setattr(fc, "phasegnn_available", lambda: (False, "không có module 'torch'"))
    out = jobs.forecast_run(fake_db, repo_root=str(root))
    assert out["status"] == "skipped" and "torch" in out["reason"]


def test_ghi_model_forecasts_idempotent(fake_db, root):
    pred = FakePredictor()
    g = FakeGuard(fence=5)
    out = jobs.forecast_run(fake_db, repo_root=str(root), predictor=pred, guard=g, job_id="J1")
    assert out["status"] == "ok" and out["run_date"] == "2025-12-31"
    assert out["checkpoint"] == fc.DEFAULT_CHECKPOINT  # đường dẫn tương đối, không lộ đường dẫn máy
    coll = fake_db[fc.MODEL_FORECASTS]
    assert coll.count_documents({}) == 6 and out["upserted"] == 6
    d = coll.find_one({"_id": "2025-12-31:PHU_AN:1"})
    assert [p["peak_idx"] for p in d["peaks"]] == [0, 1] and d["h_max_q50"] == pytest.approx(2.2)
    assert d["fence"] == 5 and d["job_id"] == "J1" and d["source"] == "phasegnn"
    assert d["peaks"][0]["t_peak"].year == 2026
    # chạy lại cùng ngày → không sinh document mới
    out2 = jobs.forecast_run(fake_db, repo_root=str(root), predictor=pred, guard=FakeGuard(fence=6))
    assert out2["status"] == "ok" and coll.count_documents({}) == 6 and out2["upserted"] == 0
    assert coll.find_one({"_id": "2025-12-31:PHU_AN:1"})["fence"] == 6
    assert {ix for ix in coll.index_information()} >= {"run_station_lead_unique", "station_run_idx"}


def test_fence_cu_bi_tu_choi(fake_db, root):
    pred = FakePredictor(stations=("PHU_AN",), t_out=1)
    jobs.forecast_run(fake_db, repo_root=str(root), predictor=pred, guard=FakeGuard(fence=10))
    out = jobs.forecast_run(fake_db, repo_root=str(root), predictor=pred, guard=FakeGuard(fence=3))
    assert out["fence_rejected"] == 1
    assert fake_db[fc.MODEL_FORECASTS].find_one({})["fence"] == 10


def test_mat_khoa_khong_ghi(fake_db, root):
    from vngis.locks import LockLost
    with pytest.raises(LockLost):
        jobs.forecast_run(fake_db, repo_root=str(root), predictor=FakePredictor(), guard=FakeGuard(lost=True))
    assert fake_db[fc.MODEL_FORECASTS].count_documents({}) == 0


def test_loc_tram_va_ngay(fake_db, root):
    pred = FakePredictor()
    out = jobs.forecast_run(fake_db, repo_root=str(root), predictor=pred, station="NHA_BE",
                            issued_at="2026-01-05T08:00:00+07:00")
    assert pred.calls[0]["run_date"] == "2026-01-05" and pred.calls[0]["stations"] == ["NHA_BE"]
    assert out["stations"] == ["NHA_BE"]


def test_khong_co_mau_skipped(fake_db, root):
    out = jobs.forecast_run(fake_db, repo_root=str(root),
                            predictor=lambda *a, **k: {"run_date": None, "records": []})
    assert out["status"] == "skipped" and "mẫu" in out["reason"]


def test_checkpoint_tu_models_va_env(fake_db, tmp_path, monkeypatch):
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    monkeypatch.delenv(fc.CHECKPOINT_DIR_ENV, raising=False)
    ck = tmp_path / "results" / "m" / "v3.pt"
    ck.parent.mkdir(parents=True)
    ck.write_bytes(b"x")
    # đúng registry `phasegnn/registry.py`: `status` production, `version` số nguyên
    fake_db["models"].insert_one({"version": 3, "status": "production", "checkpoint_path": "results/m/v3.pt"})
    p, info = fc.resolve_checkpoint(fake_db, tmp_path)
    assert p == ck.resolve() and info["origin"] == "models" and info["model_version"] == "3"
    other = tmp_path / "results" / "env.pt"
    other.write_bytes(b"y")
    monkeypatch.setenv("FORECAST_CHECKPOINT", str(other))
    assert fc.resolve_checkpoint(fake_db, tmp_path)[0] == other.resolve()


def _ck(tmp_path: Path, rel: str) -> Path:
    p = tmp_path / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_bytes(b"x")
    return p


def test_checkpoint_ngoai_results_bi_tu_choi(fake_db, tmp_path, monkeypatch):
    """`checkpoint_path` từ DB: tuyệt đối/`..`/sai đuôi → không được nạp (torch.load)."""
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    monkeypatch.delenv(fc.CHECKPOINT_DIR_ENV, raising=False)
    outside = _ck(tmp_path, "secret/evil.pt")
    _ck(tmp_path, "results/ok.txt")
    for i, bad in enumerate([str(outside), "results/../secret/evil.pt", "results/ok.txt"]):
        fake_db["models"].delete_many({})
        fake_db["models"].insert_one({"version": i + 1, "status": "production", "checkpoint_path": bad})
        p, info = fc.resolve_checkpoint(fake_db, tmp_path)
        assert p is None, bad
        assert any("bị từ chối" in t for t in info["tried"])
    # env cũng phải qua kiểm tra
    monkeypatch.setenv("FORECAST_CHECKPOINT", str(outside))
    p, info = fc.resolve_checkpoint(fake_db, tmp_path)
    assert p is None and "bị từ chối" in info["tried"][0]
    with pytest.raises(fc.UnsafeCheckpoint):
        fc.safe_checkpoint_path("../x.pt", tmp_path)


def test_checkpoint_dir_cau_hinh_bang_env(fake_db, tmp_path, monkeypatch):
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    ck = _ck(tmp_path, "models_store/a.pt")
    monkeypatch.setenv(fc.CHECKPOINT_DIR_ENV, str(tmp_path / "models_store"))
    fake_db["models"].insert_one({"version": 1, "status": "production", "artifact_path": str(ck)})
    assert fc.resolve_checkpoint(fake_db, tmp_path)[0] == ck.resolve()
    # thư mục mặc định `results/` không còn được phép khi đã cấu hình thư mục khác
    with pytest.raises(fc.UnsafeCheckpoint):
        fc.safe_checkpoint_path("results/x.pt", tmp_path)


def test_model_version_chi_chon_ban_production(fake_db, tmp_path, monkeypatch):
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    monkeypatch.delenv(fc.CHECKPOINT_DIR_ENV, raising=False)
    _ck(tmp_path, "results/v1.pt")
    _ck(tmp_path, "results/v2.pt")
    _ck(tmp_path, fc.DEFAULT_CHECKPOINT)
    fake_db["models"].insert_many([
        {"version": 1, "status": "production", "checkpoint_path": "results/v1.pt"},
        {"version": 2, "status": "staging", "checkpoint_path": "results/v2.pt"},
    ])
    # tham số job là chuỗi → ép kiểu số nguyên
    p, info = fc.resolve_checkpoint(fake_db, tmp_path, "1")
    assert p.name == "v1.pt" and info["model_version"] == "1"
    assert fc.resolve_checkpoint(fake_db, tmp_path, "v1")[0].name == "v1.pt"
    # bản staging không chọn được, và không lặng lẽ thay bằng checkpoint mặc định
    p, info = fc.resolve_checkpoint(fake_db, tmp_path, "2")
    assert p is None and any("production" in t for t in info["tried"])
    # phiên bản không phải số → không chạy
    p, info = fc.resolve_checkpoint(fake_db, tmp_path, "abc")
    assert p is None and any("số nguyên" in t for t in info["tried"])
    # không chỉ định → bản production có version lớn nhất
    fake_db["models"].insert_one({"version": 5, "status": "production", "checkpoint_path": "results/v2.pt"})
    p, info = fc.resolve_checkpoint(fake_db, tmp_path)
    assert p.name == "v2.pt" and info["model_version"] == "5"
    # bản ghi kiểu cũ (`state: promoted`) không còn được dùng
    fake_db["models"].delete_many({})
    fake_db["models"].insert_one({"version": "v9", "state": "promoted", "checkpoint_path": "results/v1.pt"})
    assert fc.resolve_checkpoint(fake_db, tmp_path)[1]["origin"] == "default"


def test_job_runner_ghi_state_skipped(fake_db, fake_lock, tmp_path, monkeypatch):
    """Hàm job trả `status="skipped"` → `jobs.state="skipped"` + `reason`; việc hàng đợi không bị trả lại."""
    monkeypatch.delenv("FORECAST_CHECKPOINT", raising=False)
    monkeypatch.setenv("PGNN_REPO_ROOT", str(tmp_path))
    fake_db["jobs"].insert_one({"_id": "forecast:all:1", "type": "forecast_run", "state": "queued",
                                "params": {"horizon_h": 72}, "requested_by": "u1", "priority": 0})
    sched = Scheduler(fake_db, fake_lock, instance="w1", resources={"forecast_run": "forecast_run"})
    try:
        out = sched.run_queue_once({"forecast_run": jobs.forecast_run}, db=fake_db, cache=None)
    finally:
        sched.shutdown()
    assert out["state"] == "skipped" and out["reason"] == "job_skipped"
    doc = fake_db["jobs"].find_one({"_id": "forecast:all:1"})
    assert doc["state"] == "skipped" and "checkpoint" in doc["reason"] and doc["finished_at"]


def test_job_runner_truyen_fence_va_job_id(fake_db, fake_lock, root):
    pred = FakePredictor(stations=("PHU_AN",), t_out=1)
    out = JobRunner(fake_db, fake_lock).run("forecast_run", jobs.forecast_run, job_id="forecast_run:2026-11-24",
                                            db=fake_db, repo_root=str(root), predictor=pred)
    assert out["state"] == "succeeded", out
    d = fake_db[fc.MODEL_FORECASTS].find_one({})
    assert d["fence"] == out["fence"] >= 1 and d["job_id"] == "forecast_run:2026-11-24"


def test_validator_khop_schema_file():
    path = REPO / "schemas" / "mongo" / "model_forecasts" / "v1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["validator"] == fc.MODEL_FORECASTS_VALIDATOR
    assert data["indexes"] == fc.MODEL_FORECASTS_INDEXES


def test_checkpoint_mac_dinh_la_spread_v2():
    """Mặc định = PhaseGNN `spread` v2 (9 trạm), không phải `phasegnn_long` (chỉ BEN_LUC)."""
    import json

    assert fc.DEFAULT_CHECKPOINT == "results/E2/phasegnn_spread_v2/checkpoint_seed0.pt"
    rec = Path(__file__).resolve().parents[3] / "results" / "E2" / "phasegnn_spread_v2" / "model_record_seed0.json"
    if rec.is_file():  # tệp kết quả có trên máy đã chạy thực nghiệm
        d = json.loads(rec.read_text(encoding="utf-8"))
        assert d["train_config"]["subgraph_version"] == 2
