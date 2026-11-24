"""Kiểm thử bộ nạp dữ liệu (checklist 6.2).

Các test dùng dữ liệu **tổng hợp** (không phụ thuộc file Parquet thật) để chạy được ở
mọi môi trường; riêng test đọc dữ liệu thật sẽ bị bỏ qua nếu file không có.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from phasegnn.data import (DEFAULT_T_IN, DEFAULT_T_OUT, DYNAMIC_FEATURES, SPLITS,
                           FeatureScaler, build_dataset, build_datasets, build_samples,
                           check_leakage, split_of)


def _fake_g5(n_days: int = 120, stations: tuple[str, ...] = ("PHU_AN", "NHA_BE")) -> pd.DataFrame:
    """Bảng G5 giả lập: 2 đỉnh, 2 chân, giờ, mưa, xả hồ."""
    days = pd.date_range("2020-01-01", periods=n_days, freq="D").strftime("%Y-%m-%d").tolist()
    rows = []
    for st in stations:
        for i, d in enumerate(days):
            h1 = 1.0 + 0.3 * np.sin(i / 7.0)
            h2 = 0.8 + 0.2 * np.cos(i / 7.0)
            rows.append({
                "station": st, "day": d, "year": int(d[:4]),
                "h_peak1": h1, "h_peak2": h2,
                "t_peak1_h": 6.0 + (i % 3), "t_peak2_h": 18.0 + (i % 2),
                "h_low1": -0.5, "h_low2": 0.1,
                "t_low1_h": 2.0, "t_low2_h": 12.0,
                "peak1_day_offset": 0, "peak2_day_offset": 0,
                "missing_peak1": False, "missing_peak2": False,
                "exceed_level": "", "exceed_level_known": False,
                "res_inflow_m3s": 100.0 + i, "res_spill_m3s": 10.0,
                "res_turbine_m3s": 50.0, "rain_mm": 5.0,
                "lunar_day": (i % 30) + 1, "moon_phase": 0.5,
                "astro_h1": 1.1, "astro_h2": 0.9, "astro_t1_h": 6.5, "astro_t2_h": 18.5,
                "max_obs_day": pd.Timestamp(d) - pd.Timedelta(days=1),
                "n_reservoirs": 4, "n_rain_stations": 3,
            })
    return pd.DataFrame(rows)


def test_split_of_matches_agreed_boundaries():
    """Biên chia tập đã chốt (khớp G5)."""
    assert split_of("2022-12-31") == "train"
    assert split_of("2023-01-01") == "val"
    assert split_of("2024-06-30") == "val"
    assert split_of("2024-07-01") == "test"
    assert split_of("2016-12-31") is None


def test_build_samples_window_shape():
    """Cửa sổ đầu vào có `T_in` ngày, nhãn có `T_out` ngày."""
    df = _fake_g5()
    st = "PHU_AN"
    days = sorted(df[df["station"] == st]["day"].tolist())
    samples = build_samples(df, st, days, t_in=14, t_out=5)
    assert samples
    s = samples[0]
    assert s.features.shape == (14, len(DYNAMIC_FEATURES))
    assert s.targets.shape == (5, 2)
    assert s.target_hours.shape == (5, 2)
    assert s.peak_heights.shape == (5,)


def test_build_samples_has_no_leakage():
    """Ngày quan trọc cuối cùng luôn < ngày phát bản tin."""
    df = _fake_g5()
    st = "PHU_AN"
    days = sorted(df[df["station"] == st]["day"].tolist())
    samples = build_samples(df, st, days, t_in=14, t_out=5)
    errors = check_leakage(samples)
    assert errors == []
    for s in samples:
        assert s.days[-1] < s.issue_date
        # nhãn bắt đầu từ ngày phát bản tin
        assert s.issue_date in days


def test_build_dataset_respects_split_bounds():
    """Mọi mẫu của tập `train` phải có ngày phát bản tin trong khoảng train."""
    df = _fake_g5(n_days=400)
    ds = build_dataset(df, "train", splits=SPLITS)
    assert ds.samples
    lo, hi = SPLITS["train"]
    for s in ds.samples:
        assert lo <= s.issue_date <= hi


def test_build_datasets_shares_one_scaler():
    """Cả 3 tập dùng CHUNG một bộ chuẩn hoá (fit trên train)."""
    df = _fake_g5(n_days=800)
    out = build_datasets(df)
    assert set(out) == {"train", "val", "test"}
    scaler = out["train"].scaler
    assert scaler is not None
    assert out["val"].scaler is scaler
    assert out["test"].scaler is scaler


def test_feature_scaler_transform_is_finite():
    """Chuẩn hoá phải cho ra giá trị hữu hạn kể cả khi dữ liệu có NaN."""
    x = np.array([[1.0, np.nan], [3.0, 4.0], [5.0, 6.0]], dtype=np.float32)
    sc = FeatureScaler().fit(x)
    out = sc.transform(x)
    assert np.isfinite(out).all()


def test_feature_scaler_roundtrip():
    sc = FeatureScaler().fit(np.array([[0.0, 1.0], [2.0, 3.0]], dtype=np.float32))
    d = sc.to_dict()
    sc2 = FeatureScaler.from_dict(d)
    x = np.array([[1.0, 2.0]], dtype=np.float32)
    assert np.allclose(sc.transform(x), sc2.transform(x))


def test_peak_height_target_is_max_of_two_peaks():
    """Độ cao đỉnh ngày = max(h_peak1, h_peak2)."""
    df = _fake_g5(n_days=60)
    st = "PHU_AN"
    days = sorted(df[df["station"] == st]["day"].tolist())
    samples = build_samples(df, st, days, t_in=14, t_out=5)
    s = samples[0]
    for d in range(5):
        idx = days.index(s.issue_date) + d
        row = df[(df["station"] == st) & (df["day"] == days[idx])].iloc[0]
        expect = max(row["h_peak1"], row["h_peak2"])
        assert float(s.peak_heights[d]) == pytest.approx(float(expect))


def test_missing_peak_is_nan_not_zero():
    """Đỉnh không quan trắc phải là NaN (không dùng 0)."""
    df = _fake_g5(n_days=60)
    st = "PHU_AN"
    mask = df["station"] == st
    df.loc[mask, "h_peak2"] = np.nan
    days = sorted(df[mask]["day"].tolist())
    samples = build_samples(df, st, days, t_in=14, t_out=5)
    for s in samples:
        for d in range(5):
            row = s.targets[d]
            if np.isnan(row[1]):
                assert np.isnan(row[1])


@pytest.mark.skipif(
    not (pd.io.common.file_exists("data/processed/features/g5/_meta.json")),
    reason="cần dữ liệu G5 thật trong repo",
)
def test_real_g5_loads_and_splits_are_consistent():
    """Với dữ liệu thật: số mẫu theo tập phải khớp biên đã chốt."""
    from phasegnn.data import build_datasets, load_g5, load_g5_meta

    df = load_g5()
    assert len(df) > 30000
    meta = load_g5_meta()
    assert meta["splits"]["train"]["from"] == SPLITS["train"][0]
    ds = build_datasets(df)
    assert len(ds["train"].samples) > 0
    assert len(ds["val"].samples) > 0
    assert len(ds["test"].samples) > 0
    assert check_leakage(ds["train"].samples) == []
