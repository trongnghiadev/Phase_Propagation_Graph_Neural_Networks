"""Kiểm thử danh sách trắng tham số hàng đợi (`vngis.job_params`)."""
from __future__ import annotations

import pytest

from vngis.job_params import INTERNAL_PARAMS, JOB_TYPES, JobParamsError, validate_job_params


@pytest.mark.parametrize("job_type,params", [
    ("reload_bulletins", {"documents": [{"a": 1}]}),
    ("reload_bulletins", {"actor": "admin"}),
    ("reload_bulletins", {"run_id": "x"}),
    ("ingest_bulletins", {"repo_root": "/tmp"}),
    ("ingest_bulletins", {"source_dir": "/etc"}),
    ("ingest_bulletins", {"run_g0": True}),
    ("cache_warm", {"guard": None}),
])
def test_tham_so_noi_bo_bi_tu_choi(job_type, params):
    with pytest.raises(JobParamsError) as ei:
        validate_job_params(job_type, params)
    assert ei.value.errors and ei.value.errors[0]["loc"][0] == "params"


def test_moi_model_cam_khoa_la_va_khong_co_tham_so_noi_bo():
    from vngis.job_params import JOB_PARAM_MODELS

    for job_type, model in JOB_PARAM_MODELS.items():
        assert not (set(model.model_fields) & INTERNAL_PARAMS), job_type
        with pytest.raises(JobParamsError):
            validate_job_params(job_type, {"khoa_la": 1})


def test_loai_viec_la_va_params_khong_phai_dict():
    with pytest.raises(JobParamsError):
        validate_job_params("rm_minus_rf", {})
    with pytest.raises(JobParamsError):
        validate_job_params("cache_warm", ["stations"])


def test_tham_so_hop_le_chi_tra_khoa_da_dat():
    assert validate_job_params("ingest_bulletins", None) == {}
    assert validate_job_params("cache_warm", {"collections": ["stations"]}) == {"collections": ["stations"]}
    assert validate_job_params("forecast_run", {"horizon_h": 24, "basins": ["DN_SG"]}) == \
        {"horizon_h": 24, "basins": ["DN_SG"]}


@pytest.mark.parametrize("job_type,params", [
    ("cache_warm", {"collections": ["users"]}),
    ("forecast_run", {"horizon_h": 0}),
    ("forecast_run", {"horizon_h": "72; drop"}),
    ("forecast_run", {"basins": ["../x"]}),
    ("train", {"epochs": 10_000}),
    ("train", {"model_version": "v1/../../x"}),
])
def test_sai_kieu_hoac_mien_gia_tri(job_type, params):
    with pytest.raises(JobParamsError):
        validate_job_params(job_type, params)


def test_tham_so_cua_api_forecast_va_train_hop_le():
    """`ForecastRunRequest`/`TrainRequest` của API dump ra đúng các khoá model chấp nhận."""
    validate_job_params("forecast_run", {"station": "PHU_AN", "basin": None, "horizon_h": 72, "issued_at": None,
                                         "model_version": None, "force_sample": False})
    validate_job_params("train", {"model_version": "v20260101", "epochs": 50, "learning_rate": 0.001,
                                  "station": None, "basin": None})
    assert {"ingest_bulletins", "reload_bulletins", "forecast_run", "cache_warm", "train"} <= JOB_TYPES


def test_parse_model_version_ep_kieu_so_nguyen():
    import pytest as _pt

    from vngis.job_params import parse_model_version
    assert parse_model_version(None) is None and parse_model_version("") is None
    assert parse_model_version(3) == 3 and parse_model_version("3") == 3 and parse_model_version("v12") == 12
    for bad in ("abc", "v", "1.5", True, -1, "9" * 19):
        with _pt.raises(ValueError):
            parse_model_version(bad)
