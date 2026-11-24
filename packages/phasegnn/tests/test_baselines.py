"""Kiểm thử các mô hình cơ sở (checklist 6.5 — mục 6, và đề cương 5.2.7).

Các test dùng dữ liệu **tổng hợp** (không phụ thuộc file thật) để chạy được ở mọi
môi trường; riêng test đọc dữ liệu thật sẽ bị bỏ qua nếu file không có.

* **B0** persistence có điều hoà triều: đỉnh dự báo = đỉnh quan trọc + biến thiên thiên văn.
* **B_harm** dùng đúng giá trị triều thiên văn.
* **B_KTTV** chuyển đổi từ collection/danh sách dict sang định dạng chung.
* **B1** hồi quy phần dư có độ trễ hồ chứa: hệ số khớp với dữ liệu tổng hợp.
* **B2** LSTM trạm: forward đúng hình dạng và huấn luyện giảm loss.
* **Kiểm tra schema chung**: mọi bản ghi dự báo phải hợp lệ theo
  `schemas/prediction/v1.schema.json`.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd
import pytest

from phasegnn.baselines import (DEFAULT_SPREAD_M, QUANTILE_LEVELS, ResidualRegressor,
                                StationLSTM, b1_forecast, build_lagged_features,
                                fit_residual_regression, harmonic_forecast,
                                kttv_forecast_from_dataframe, persistence_forecast,
                                validate_prediction_records, write_predictions)


# ---------------------------------------------------------------------------
# Dữ liệu tổng hợp
# ---------------------------------------------------------------------------
def _fake_g5(n_days: int = 60, stations=("PHU_AN", "NHA_BE")) -> pd.DataFrame:
    """Bảng G5 giả lập: đỉnh/chân, giờ, mưa, xả hồ, cột triều thiên văn."""
    days = pd.date_range("2020-01-01", periods=n_days, freq="D").strftime("%Y-%m-%d").tolist()
    rows = []
    for st in stations:
        for i, d in enumerate(days):
            rows.append({
                "station": st, "day": d, "year": int(d[:4]),
                "h_peak1": 1.20 + 0.05 * np.sin(i / 5.0),
                "h_peak2": 1.05 + 0.04 * np.cos(i / 5.0),
                "t_peak1_h": 6.0, "t_peak2_h": 18.0,
                "h_low1": -0.4, "h_low2": 0.1,
                "t_low1_h": 2.0, "t_low2_h": 12.0,
                "missing_peak1": False, "missing_peak2": False,
                "res_inflow_m3s": 200.0 + 3.0 * i,
                "res_spill_m3s": 30.0, "res_turbine_m3s": 60.0,
                "rain_mm": 4.0, "lunar_day": (i % 30) + 1, "moon_phase": 0.5,
                "astro_h1": 1.10, "astro_h2": 0.95,
                "astro_t1_h": 6.5, "astro_t2_h": 18.5,
                "max_obs_day": pd.Timestamp(d) - pd.Timedelta(days=1),
            })
    return pd.DataFrame(rows)


def _fake_astro(n_days: int = 60) -> pd.DataFrame:
    """Triều thiên văn giả lập (biên độ 0,1 m quanh mực 1,0 m)."""
    days = pd.date_range("2020-01-01", periods=n_days, freq="D").strftime("%Y-%m-%d").tolist()
    return pd.DataFrame({
        "day": days,
        "h1": [1.0 + 0.1 * np.sin(i / 6.0) for i in range(n_days)],
        "h2": [0.95 + 0.1 * np.cos(i / 6.0) for i in range(n_days)],
        "t1_h": [6.5] * n_days,
        "t2_h": [18.5] * n_days,
    })


# ---------------------------------------------------------------------------
# B0 — persistence có điều hoà triều
# ---------------------------------------------------------------------------
def test_persistence_forecast_uses_astro_variation():
    """B0: đỉnh dự báo = đỉnh hôm trước + biến thiên triều thiên văn."""
    df = _fake_g5(n_days=30)
    astro = _fake_astro(n_days=30)
    issued = "2020-01-20"
    recs = persistence_forecast(df, issued, t_out=5, astro=astro)
    assert recs, "B0 phải sinh ra bản ghi"
    assert {r["model"] for r in recs} == {"B0"}
    assert {r["station"] for r in recs} == {"PHU_AN", "NHA_BE"}

    prev_day = "2020-01-19"
    h_prev = float(df[(df["station"] == "PHU_AN") & (df["day"] == prev_day)]["h_peak1"].iloc[0])
    a_prev = float(astro[astro["day"] == prev_day]["h1"].iloc[0])
    a_now = float(astro[astro["day"] == issued]["h1"].iloc[0])
    expect = h_prev + (a_now - a_prev)
    got = [r for r in recs if r["station"] == "PHU_AN" and r["target_date"] == issued
           and r["peak_idx"] == 0][0]
    assert got["h_q50"] == pytest.approx(expect, abs=1e-4)
    # phân vị đơn điệu và cách nhau đúng độ rộng
    assert got["h_q10"] < got["h_q50"] < got["h_q90"]
    assert got["h_q50"] - got["h_q10"] == pytest.approx(DEFAULT_SPREAD_M)


def test_persistence_forecast_lead_day_range():
    df = _fake_g5(n_days=30)
    astro = _fake_astro(n_days=30)
    recs = persistence_forecast(df, "2020-01-20", t_out=5, astro=astro)
    assert {r["lead_day"] for r in recs} == {1, 2, 3, 4, 5}
    # ngày phát bản tin là ngày dự báo thứ 1 (không rò rỉ)
    for r in recs:
        assert r["target_date"] >= r["issued_date"]


# ---------------------------------------------------------------------------
# B_harm — điều hoà thiên văn
# ---------------------------------------------------------------------------
def test_harmonic_forecast_equals_astro_values():
    astro = _fake_astro(n_days=30)
    recs = harmonic_forecast(astro, "2020-01-20", t_out=3,
                              stations=("PHU_AN", "NHA_BE"))
    assert recs
    row = astro[astro["day"] == "2020-01-20"].iloc[0]
    got = [r for r in recs if r["target_date"] == "2020-01-20" and r["peak_idx"] == 0][0]
    assert got["h_q50"] == pytest.approx(float(row["h1"]), abs=1e-6)
    assert got["station"] == "PHU_AN"


def test_harmonic_forecast_time_is_iso_utc():
    """Giờ 6,5 h giờ VN ⇒ 23:30 h UTC của ngày trước."""
    astro = _fake_astro(n_days=30)
    recs = harmonic_forecast(astro, "2020-01-20", t_out=1, stations=("PHU_AN",))
    got = [r for r in recs if r["peak_idx"] == 0][0]
    assert got["t_peak"] == "2020-01-19T23:30:00Z"


# ---------------------------------------------------------------------------
# B_KTTV — dự báo nghiệp vụ
# ---------------------------------------------------------------------------
def test_kttv_forecast_from_dataframe():
    rows = [{
        "issued_date": "2020-01-20", "target_date": "2020-01-20", "lead_day": 1,
        "station": "PHU_AN",
        "high": [{"ts": "2020-01-20T00:00:00Z", "value": 1.35},
                 {"ts": "2020-01-20T12:00:00Z", "value": 1.28}],
    }]
    recs = kttv_forecast_from_dataframe(rows, "2020-01-20", t_out=5)
    assert len(recs) == 2
    assert {r["model"] for r in recs} == {"B_KTTV"}
    assert recs[0]["peak_idx"] == 0
    assert recs[0]["h_q50"] == pytest.approx(1.35)
    # trạm ngoài danh sách KTTV bị loại
    rows2 = [dict(rows[0], station="BIEN_HOA")]
    assert kttv_forecast_from_dataframe(rows2, "2020-01-20") == []


def test_kttv_forecast_respects_lead_day_limit():
    rows = [{
        "issued_date": "2020-01-20", "target_date": "2020-01-25", "lead_day": 6,
        "station": "PHU_AN", "high": [{"ts": "2020-01-25T00:00:00Z", "value": 1.2}],
    }]
    assert kttv_forecast_from_dataframe(rows, "2020-01-20", t_out=5) == []


# ---------------------------------------------------------------------------
# B1 — hồi quy phần dư theo xả hồ có độ trễ
# ---------------------------------------------------------------------------
def test_residual_regressor_recovers_linear_coefficients():
    """Hồi quy tuyến tính phải khôi phục đúng hệ số khi dữ liệu đúng mô hình."""
    rng = np.random.default_rng(0)
    x = rng.normal(size=(200, 3))
    y = 0.5 + 2.0 * x[:, 0] - 1.5 * x[:, 1] + 0.25 * x[:, 2]
    reg = ResidualRegressor.fit(x, y, ["a", "b", "c"], [24, 1, 1])
    assert reg.intercept == pytest.approx(0.5, abs=1e-6)
    assert reg.coefficients[0] == pytest.approx(2.0, abs=1e-6)
    assert reg.coefficients[1] == pytest.approx(-1.5, abs=1e-6)
    assert reg.coefficients[2] == pytest.approx(0.25, abs=1e-6)
    pred = reg.predict(x[:5])
    assert np.allclose(pred, y[:5], atol=1e-6)


def test_residual_regressor_requires_enough_samples():
    with pytest.raises(ValueError):
        ResidualRegressor.fit(np.ones((3, 2)), np.ones(3), ["a", "b"], [24, 1])


def test_fit_residual_regression_and_forecast():
    """B1: dự báo = triều thiên văn + phần dư hồi quy."""
    df = _fake_g5(n_days=120)
    astro = _fake_astro(n_days=120)
    model = fit_residual_regression(df, "PHU_AN", ("2020-01-01", "2020-03-01"), astro)
    assert model.lags == [24, 1, 1]
    recs = b1_forecast(df, astro, "PHU_AN", "2020-03-05", model, t_out=3)
    assert recs
    for r in recs:
        assert r["model"] == "B1"
        assert r["h_q10"] <= r["h_q50"] <= r["h_q90"]
        assert r["lead_day"] in (1, 2, 3)


def test_build_lagged_features_shifts_by_lag():
    s = pd.Series({"2020-01-01": 1.0, "2020-01-02": 2.0, "2020-01-03": 3.0})
    days = ["2020-01-02", "2020-01-03"]
    out = build_lagged_features(s, days, lag=1)
    assert out.shape == (2, 1)
    assert out[0, 0] == pytest.approx(1.0)
    assert out[1, 0] == pytest.approx(2.0)


# ---------------------------------------------------------------------------
# B2 — LSTM tại từng trạm
# ---------------------------------------------------------------------------
def test_station_lstm_forward_shapes():
    """Bước đầu: dự báo 1 ngày có đúng hình dạng (2 đỉnh × 3 phân vị + 2 giờ)."""
    torch = pytest.importorskip("torch")
    m = StationLSTM(in_dim=6, hidden=16, t_out=5, n_quantiles=3)
    x = torch.randn(4, 14, 6)
    out = m.forward(x, steps=1)
    assert out["peak"].shape == (4, 2, 3)
    assert out["hour"].shape == (4, 2)
    assert out["low"].shape == (4, 2, 3)


def test_station_lstm_autoregressive_decode_shapes():
    """Giải mã tuần tự `t_out` ngày: đầu ra có trục thời gian."""
    torch = pytest.importorskip("torch")
    m = StationLSTM(in_dim=6, hidden=16, t_out=5, n_quantiles=3)
    x = torch.randn(4, 14, 6)
    out = m.forward(x, steps=5)
    assert out["peak"].shape == (4, 5, 2, 3)
    assert out["hour"].shape == (4, 5, 2)
    assert out["low"].shape == (4, 5, 2, 3)
    assert torch.isfinite(out["peak"]).all()


def test_station_lstm_fit_reduces_loss():
    """LSTM phải học được (loss giảm trên một tập nhỏ)."""
    pytest.importorskip("torch")
    rng = np.random.default_rng(1)
    n, t_in, t_out, f = 64, 14, 5, 6
    x = rng.normal(size=(n, t_in, f)).astype(np.float32)
    # nhãn = trung bình cửa sổ đầu vào (tín hiệu học được)
    y = x.mean(axis=1)[:, None, :] @ np.ones((f, 2), dtype=np.float32)
    y = np.repeat(y, t_out, axis=1).astype(np.float32)
    m = StationLSTM(in_dim=f, hidden=32, t_out=t_out, n_quantiles=3)
    hist = m.fit(x, y, epochs=8, batch_size=32, lr=5e-3, seed=0)
    assert hist["loss_history"][-1] < hist["loss_history"][0]


# ---------------------------------------------------------------------------
# Định dạng đầu ra chung
# ---------------------------------------------------------------------------
def test_write_predictions_and_validate(tmp_path):
    df = _fake_g5(n_days=30)
    astro = _fake_astro(n_days=30)
    recs = persistence_forecast(df, "2020-01-20", t_out=5, astro=astro)
    errors = validate_prediction_records(recs)
    assert errors == [], f"bản ghi không hợp lệ: {errors[:3]}"

    path = write_predictions(recs, tmp_path, filename="pred.csv")
    assert path.exists()
    out = pd.read_csv(path)
    assert list(out.columns) == ["model", "station", "issued_date", "target_date",
                                "lead_day", "peak_idx", "h_q10", "h_q50", "h_q90", "t_peak"]
    assert len(out) == len(recs)


def test_validate_prediction_records_catches_bad_rows():
    bad = [{
        "model": "X", "station": "PHU_AN", "issued_date": "2020/01/01",
        "target_date": "2020-01-01", "lead_day": 9, "peak_idx": 3,
        "h_q10": 2.0, "h_q50": 1.0, "h_q90": 0.5, "t_peak": "không phải ISO",
    }]
    errors = validate_prediction_records(bad)
    assert len(errors) >= 4


def test_quantile_levels_match_schema():
    """Mức phân vị phải khớp schema chung (10 %, 50 %, 90 %)."""
    assert QUANTILE_LEVELS == (0.1, 0.5, 0.9)


@pytest.mark.skipif(
    not (pd.io.common.file_exists("data/processed/features/g5/_meta.json")),
    reason="cần dữ liệu G5 thật trong repo",
)
def test_baselines_run_on_real_data():
    """Trên dữ liệu thật: B0 và B_harm phải sinh bản ghi hợp lệ theo schema."""
    from phasegnn.data import load_astro_tide, load_g5

    df = load_g5()
    # chỉ kiểm tra định dạng bản ghi → bỏ kiểm tra nguồn gốc astro (bảng cũ fit trên cả test
    # bị `check_astro_provenance` từ chối cho mọi lần chạy thực nghiệm)
    astro = load_astro_tide(check_leak=False)
    issued = sorted(df["day"].unique())[100]
    recs = persistence_forecast(df, issued, t_out=5, astro=astro if not astro.empty else None)
    assert recs
    assert validate_prediction_records(recs) == []
    assert math.isfinite(min(r["h_q50"] for r in recs))
