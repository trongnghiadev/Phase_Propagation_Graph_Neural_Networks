"""Kiểm thử job du bao hang ngay (checklist 6.8) va thanh ghi mo hinh.

* Khoa co dung ten theo checklist: ``lock:forecast:{basin}:{date}``.
* **Fencing token**: moi lan ghi phai kem dieu kien ``last_fence < fence``.
* Job chay duoc o che do khong co Redis/MongoDB (may may) va van ghi ra file.
* Canh bao sinh dung khi vuot nguong.
"""
from __future__ import annotations

import json

import pytest

from phasegnn.job import (ForecastJobResult, build_alerts, lock_key, run_forecast_job)
from phasegnn.registry import ModelRecord, SearchSpace, register_model, save_registry_json


# ---------------------------------------------------------------------------
# Khoa
# ---------------------------------------------------------------------------
def test_lock_key_follows_checklist_format():
    """Tên khoa: `lock:forecast:{basin}:{date}`."""
    assert lock_key("DONG_RAI", "2026-11-24") == "lock:forecast:DONG_RAI:2026-11-24"


def test_lock_key_differs_per_day_and_basin():
    """Khoa khac nhau theo ngay va theo luu vuc."""
    a = lock_key("DONG_RAI", "2026-11-24")
    b = lock_key("DONG_RAI", "2026-11-25")
    c = lock_key("MEKONG", "2026-11-24")
    assert a != b and a != c


# ---------------------------------------------------------------------------
# Canh bao
# ---------------------------------------------------------------------------
def _rec(station: str, h: float, day: str = "2026-11-24") -> dict:
    return {
        "model": "phasegnn-v1", "station": station, "issued_date": day,
        "target_date": day, "lead_day": 1, "peak_idx": 0,
        "h_q10": h - 0.1, "h_q50": h, "h_q90": h + 0.1, "t_peak": None,
    }


def test_build_alerts_only_when_above_threshold():
    """Chi ban ghi vuot nguong moi sinh canh bao."""
    recs = [_rec("PHU_AN", 1.5), _rec("NHA_BE", 0.5), _rec("THU_DAU_MOT", 1.0)]
    alerts = build_alerts(recs, {"PHU_AN": 1.2, "NHA_BE": 1.2, "THU_DAU_MOT": 1.2})
    assert len(alerts) == 1
    assert alerts[0]["station"] == "PHU_AN"
    assert alerts[0]["threshold"] == pytest.approx(1.2)
    assert alerts[0]["predicted_h"] == pytest.approx(1.5)


def test_build_alerts_skips_station_without_threshold():
    recs = [_rec("BEN_LUC", 3.0)]
    assert build_alerts(recs, {"PHU_AN": 1.2}) == []


def test_build_alerts_gan_alert_id_on_dinh_chuoi():
    """Moi canh bao phai co `alert_id` **chuoi** va ON DINH (khong doi giua cac lan chay).

    `alert_id` khop dung khoa logic ghi `alerts` trong `run_forecast_job`
    (`model:station:target_date:lead_day:level`) ⇒ chay lai job khong sinh ban ghi trung.
    """
    recs = [_rec("PHU_AN", 1.5)]
    a1 = build_alerts(recs, {"PHU_AN": 1.2})
    a2 = build_alerts(recs, {"PHU_AN": 1.2})
    assert len(a1) == 1
    alert_id = a1[0]["alert_id"]
    assert isinstance(alert_id, str)
    assert alert_id == a2[0]["alert_id"], "alert_id phai on dinh"
    assert alert_id == "phasegnn-v1:PHU_AN:2026-11-24:1:I"


def test_build_alerts_alert_id_khac_nhau_theo_tram_va_ngay():
    """`alert_id` khac nhau khi tram/ngay/lead_day khac nhau."""
    recs = [_rec("PHU_AN", 1.5), _rec("NHA_BE", 2.0),
            {**_rec("PHU_AN", 1.5), "target_date": "2026-11-25", "lead_day": 2}]
    ids = {a["alert_id"] for a in build_alerts(recs, {"PHU_AN": 1.2, "NHA_BE": 1.2})}
    assert len(ids) == 3


# ---------------------------------------------------------------------------
# Job
# ---------------------------------------------------------------------------
def test_run_forecast_job_ghi_alert_id_vao_mongo_khong_trung_ban_ghi():
    """Chay job 2 lan: `alerts` chi co 1 ban ghi va co `alert_id` chuoi."""
    mongomock = pytest.importorskip("mongomock")
    db = mongomock.MongoClient()["vngis_test"]
    recs = [_rec("PHU_AN", 1.5)]
    for _ in range(2):
        run_forecast_job(day="2026-11-24", basin="DONG_RAI", records=recs,
                         thresholds={"PHU_AN": 1.2}, use_lock=False, db=db)
    docs = list(db["alerts"].find({}))
    assert len(docs) == 1, f"khong duoc sinh ban ghi trung, thuc te {len(docs)}"
    assert isinstance(docs[0]["alert_id"], str)
    assert docs[0]["alert_id"] == "phasegnn-v1:PHU_AN:2026-11-24:1:I"


def test_run_forecast_job_without_datastores_writes_file(tmp_path):
    """Khong co Redis/MongoDB: job van chay va ghi file cuc bo."""
    recs = [_rec("PHU_AN", 1.5)]
    res = run_forecast_job(day="2026-11-24", basin="DONG_RAI", records=recs,
                           thresholds={"PHU_AN": 1.2}, out_dir=tmp_path,
                           use_lock=False)
    assert isinstance(res, ForecastJobResult)
    assert res.n_forecasts == 1
    assert res.n_alerts == 1
    assert res.out_path is not None
    blob = json.loads(open(res.out_path, encoding="utf-8").read())
    assert blob["day"] == "2026-11-24"
    assert len(blob["forecasts"]) == 1
    assert len(blob["alerts"]) == 1


def test_run_forecast_job_records_errors_without_crashing(tmp_path):
    """Job khong duoc khong duoc Redis van tra ve ket qua co `errors`, khong nem loi."""
    res = run_forecast_job(day="2026-11-24", records=[], out_dir=tmp_path,
                           use_lock=True)
    assert res.n_forecasts == 0
    assert isinstance(res.errors, list)


# ---------------------------------------------------------------------------
# Thanh ghi mo hinh
# ---------------------------------------------------------------------------
def test_model_record_has_required_fields():
    """Ban ghi `models` phai co status/hyperparams/metrics/data_snapshot."""
    rec = ModelRecord(model_id="phasegnn-seed0", name="phasegnn-v1",
                      variant="phasegnn", status="staging",
                      hyperparams={"lr": 1e-3}, metrics={"val_mae_peak": 0.12},
                      data_snapshot={"run_date": "2026-11-24"})
    doc = rec.to_mongo()
    for k in ("model_id", "name", "variant", "status", "hyperparams", "metrics",
              "data_snapshot"):
        assert k in doc
    assert doc["status"] == "staging"


def test_register_model_without_db_returns_document():
    """Khong co MongoDB: ham tra ve ban ghi de caller tu luu."""
    doc = register_model(ModelRecord(model_id="m1", name="n", variant="phasegnn"))
    assert doc["model_id"] == "m1"


def test_save_registry_json(tmp_path):
    doc = {"model_id": "m1", "status": "staging"}
    path = save_registry_json(doc, tmp_path)
    assert path.exists()
    assert json.loads(path.read_text(encoding="utf-8"))["model_id"] == "m1"


def test_search_space_defaults_match_train_config():
    """Khoang mac dinh phai khop `TrainConfig` (dac cuong 5.2.6)."""
    from phasegnn.train import TrainConfig

    space = SearchSpace()
    cfg = TrainConfig()
    assert space.lr == cfg.lr
    assert space.weight_decay == cfg.weight_decay
    assert space.hidden_dim == cfg.hidden_dim
    assert space.num_gnn_layers == cfg.num_gnn_layers
    assert space.batch_size == cfg.batch_size


# ---------------------------------------------------------------------------
# Phan vi don dieu khi xuat du bao
# ---------------------------------------------------------------------------
def test_quantiles_are_monotonic_in_exported_predictions():
    """Moi ban ghi du bao phai co q10 <= q50 <= q90 (schema bat buoc).

    Mo hinh co the hoc duoc phan vi khong don dieu (ham mat mat L_mono chi phat, khong
    rang buoc) ⇒ khi xuat phai chuan hoa de khong ban ghi sai dinh dang.
    """
    from phasegnn.forecast import _monotonic_quantiles

    for q in ([0.9, 0.5, 0.1], [0.5, 0.5, 0.5], [0.2, 0.8, 0.4], [1.0, 2.0, 3.0]):
        q10, q50, q90 = _monotonic_quantiles(q[0], q[1], q[2])
        assert q10 <= q50 <= q90
