"""`phasegnn.train --register / --model-version`: ghi collection `models` qua `vngis.model_registry`.

Dữ liệu, đồ thị và vòng huấn luyện đều giả (không đọc G5, không huấn luyện thật); MongoDB = mongomock.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")
mongomock = pytest.importorskip("mongomock")

from phasegnn import registry as R  # noqa: E402
from phasegnn import train as T  # noqa: E402
from phasegnn.registry import ModelRecord, register_model  # noqa: E402


@pytest.fixture()
def db():
    return mongomock.MongoClient(tz_aware=True)["t_train_register"]


@pytest.fixture()
def fake_train(monkeypatch, db):
    """Thay nạp dữ liệu + huấn luyện bằng bản giả; `open_registry_db` trả mongomock."""
    monkeypatch.setattr(T, "load_g5", lambda paths=None: [])
    monkeypatch.setattr(T, "load_g5_meta", lambda paths=None: {"data_snapshot": {"g5": "test"}})
    monkeypatch.setattr(T, "load_graph", lambda paths=None: SimpleNamespace(num_nodes=1, num_edges=0))
    class _DS:
        samples: list = []

        def __len__(self) -> int:
            return 0

    monkeypatch.setattr(T, "build_datasets", lambda df, **kw: {"train": _DS(), "val": _DS()})
    monkeypatch.setattr(T, "check_leakage", lambda samples: [])

    def _train(datasets, graph, tcfg):
        model = torch.nn.Linear(1, 1)
        return model, T.TrainResult(variant=tcfg.variant, best_val_mae=0.123, best_epoch=2,
                                    history=[{"epoch": 1, "train_loss": 1.0, "val": {"mae_peak": 0.2,
                                                                                    "mae_hour": 1.5}}])

    monkeypatch.setattr(T, "train_model", _train)
    calls = []

    def _open(log=print):
        calls.append(1)
        return db

    monkeypatch.setattr(T, "open_registry_db", _open)
    return calls


def _argv(tmp_path, *extra):
    return ["--epochs", "1", "--no-mlflow", "--out-dir", str(tmp_path / "out"), *extra]


def test_mac_dinh_khong_ghi_db(tmp_path, fake_train, db):
    """Chạy thực nghiệm (không --register/--model-version) → không mở/ghi MongoDB."""
    assert T.main(_argv(tmp_path)) == 0
    assert fake_train == [] and db["models"].count_documents({}) == 0
    rec = json.loads((tmp_path / "out" / "model_record_seed0.json").read_text("utf-8"))
    assert rec["version"] is None and rec["metrics"]["val_mae_peak"] == pytest.approx(0.123)


def test_register_cap_version_qua_model_registry(tmp_path, fake_train, db):
    db["models"].insert_one({"_id": "cu", "version": 4, "status": "production"})
    assert T.main(_argv(tmp_path, "--register")) == 0
    doc = db["models"].find_one({"_id": "phasegnn-v1-5"})
    assert doc["version"] == 5 and doc["status"] == "staging" and doc["model_id"] == "phasegnn-v1-5"
    assert doc["metrics"]["val_mae_peak"] == pytest.approx(0.123)
    assert db["counters"].find_one({"_id": "models.version"})["seq"] == 5


def test_register_lai_cung_seed_khong_ha_cap_production(tmp_path, fake_train, db):
    """Chạy lại `--register` cùng seed → bản ghi MỚI theo version; bản production cũ giữ nguyên."""
    assert T.main(_argv(tmp_path, "--register")) == 0
    db["models"].update_one({"_id": "phasegnn-v1-1"}, {"$set": {"status": "production", "promote_seq": 1}})
    assert T.main(_argv(tmp_path, "--register")) == 0
    old = db["models"].find_one({"_id": "phasegnn-v1-1"})
    new = db["models"].find_one({"_id": "phasegnn-v1-2"})
    assert old["status"] == "production" and old["version"] == 1
    assert new["status"] == "staging" and new["version"] == 2
    assert db["models"].count_documents({}) == 2
    assert R.find_production_model(db)["_id"] == "phasegnn-v1-1"


def test_model_version_cap_nhat_dung_ban_ghi_api(tmp_path, fake_train, db, monkeypatch):
    """Worker gọi `--model-version 3 --train-fence 7`: cập nhật `phasegnn-3`, không đổi status/train_state."""
    monkeypatch.chdir(tmp_path)
    db["models"].insert_one({"_id": "phasegnn-3", "version": 3, "status": "staging", "train_state": "running",
                             "train_fence": 7, "requested_by": "user:acc_x"})
    out_rel = "results/models/phasegnn-3"
    assert T.main(["--epochs", "1", "--no-mlflow", "--out-dir", out_rel, "--model-version", "3",
                   "--train-fence", "7"]) == 0
    doc = db["models"].find_one({"_id": "phasegnn-3"})
    assert doc["status"] == "staging" and doc["train_state"] == "running" and doc["requested_by"] == "user:acc_x"
    assert doc["artifact_path"] == f"{out_rel}/checkpoint_seed0.pt", "đường dẫn tương đối, dạng /"
    assert doc["metrics"]["val_mae_peak"] == pytest.approx(0.123)
    assert db["models"].count_documents({}) == 1, "không tạo bản ghi mới, không cấp số mới"
    assert db["counters"].count_documents({}) == 0


def test_model_version_fence_cu_khong_ghi(tmp_path, fake_train, db):
    """Lượt mới đã giữ khoá (train_fence lớn hơn) → tiến trình của lượt cũ không ghi đè."""
    db["models"].insert_one({"_id": "phasegnn-3", "version": 3, "status": "staging", "train_fence": 9})
    assert T.main(_argv(tmp_path, "--model-version", "3", "--train-fence", "7")) == 0
    assert "metrics" not in db["models"].find_one({"_id": "phasegnn-3"})


def test_no_register_thang_model_version(tmp_path, fake_train, db):
    db["models"].insert_one({"_id": "phasegnn-3", "version": 3, "status": "staging"})
    assert T.main(_argv(tmp_path, "--model-version", "3", "--no-register")) == 0
    assert fake_train == [] and "metrics" not in db["models"].find_one({"_id": "phasegnn-3"})


def test_register_model_update_existing_khong_upsert(db):
    rec = ModelRecord(model_id="phasegnn-8", name="phasegnn", variant="phasegnn", version=8)
    register_model(rec, db=db, update_existing=True, log=lambda *_: None)
    assert rec.registered is False and db["models"].count_documents({}) == 0
    rec2 = ModelRecord(model_id="m", name="n", variant="phasegnn")
    register_model(rec2, db=None, log=lambda *_: None)
    assert rec2.registered is False
    assert R.open_registry_db is not None
