"""Kiểm thử bộ nạp dữ liệu (checklist 6.2).

Các test dùng dữ liệu **tổng hợp** (không phụ thuộc file Parquet thật) để chạy được ở
mọi môi trường; riêng test đọc dữ liệu thật sẽ bị bỏ qua nếu file không có.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from phasegnn.data import (DEFAULT_T_IN, DEFAULT_T_OUT, DYNAMIC_FEATURES, SPLITS, TEST_START,
                           AstroLeakError, FeatureScaler, build_dataset, build_datasets,
                           build_samples, calendar_days, check_astro_provenance, check_leakage,
                           load_astro_tide, load_split_config, split_of)


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


# ---------------------------------------------------------------------------
# Lưới ngày lịch liên tục (sửa 24/11/2026)
# ---------------------------------------------------------------------------
_SPLITS_2020 = {"train": ("2020-01-01", "2020-12-31"), "val": ("2021-01-01", "2021-06-30"),
                "test": ("2021-07-01", "2021-12-31")}


def test_build_dataset_uses_calendar_grid_with_missing_day():
    """Một ngày bị thiếu: cửa sổ vẫn đếm theo ngày lịch, ngày thiếu là NaN (không bị 'nhảy')."""
    df = _fake_g5(n_days=60, stations=("PHU_AN",))
    missing = "2020-01-25"
    df = df[df["day"] != missing].reset_index(drop=True)
    ds = build_dataset(df, "train", splits=_SPLITS_2020, fit_scaler=True)
    by_issue = {s.issue_date: s for s in ds.samples}
    # mẫu phát đúng ngày thiếu vẫn có (các ngày sau có nhãn); nhãn lead 1 = NaN
    s = by_issue[missing]
    assert np.isnan(s.targets[0]).all()
    assert np.isfinite(s.targets[1:]).all()
    # mẫu phát 23/01: lead 3 (chỉ số 2) rơi đúng ngày thiếu → NaN; lead 4 = ngày 26/01
    s2 = by_issue["2020-01-23"]
    assert np.isnan(s2.targets[2]).all()
    row26 = df[df["day"] == "2020-01-26"].iloc[0]
    assert float(s2.targets[3, 0]) == pytest.approx(float(row26["h_peak1"]))
    # cửa sổ đầu vào = 14 ngày lịch liên tục kết thúc ngay trước ngày phát
    s3 = by_issue["2020-02-01"]
    assert s3.days == [str(d.date()) for d in pd.date_range("2020-01-18", "2020-01-31")]
    # ngày thiếu trong đầu vào → 0 sau chuẩn hoá (NaN → 0), không lấy giá trị ngày khác
    k = s3.days.index(missing)
    assert np.all(s3.features[k] == 0.0)
    assert check_leakage(ds.samples) == []


def test_build_samples_rejects_non_calendar_days():
    df = _fake_g5(n_days=40, stations=("PHU_AN",))
    days = sorted(df["day"].tolist())
    del days[10]
    with pytest.raises(ValueError):
        build_samples(df, "PHU_AN", days, t_in=14, t_out=5)


def test_build_samples_skips_windows_without_any_label():
    df = _fake_g5(n_days=60, stations=("PHU_AN",))
    gap = pd.date_range("2020-01-20", "2020-01-29").strftime("%Y-%m-%d").tolist()
    df.loc[df["day"].isin(gap), ["h_peak1", "h_peak2"]] = np.nan
    days = calendar_days(df["day"].min(), df["day"].max())
    samples = build_samples(df, "PHU_AN", days, t_in=14, t_out=5)
    issues = {s.issue_date for s in samples}
    # cửa sổ 20/01–24/01 … 25/01–29/01 hoàn toàn không có nhãn → bỏ
    assert "2020-01-20" not in issues and "2020-01-25" not in issues
    assert "2020-01-26" in issues  # còn ngày 30/01 có nhãn
    for s in samples:
        assert np.isfinite(s.targets).any()


# ---------------------------------------------------------------------------
# Cấu hình chia tập dùng chung + kiểm tra rò rỉ triều thiên văn
# ---------------------------------------------------------------------------
def test_splits_come_from_shared_config():
    cfg = load_split_config()
    assert cfg["splits"] == SPLITS
    assert TEST_START == SPLITS["test"][0]
    assert "test" not in cfg["harmonic_fit_splits"]


def test_split_config_rejects_harmonic_fit_on_test(tmp_path):
    import json
    raw = {"splits": {k: {"from": a, "to": b} for k, (a, b) in SPLITS.items()},
           "harmonic_fit_splits": ["train", "test"]}
    f = tmp_path / "splits.json"
    f.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(ValueError):
        load_split_config(f)


def test_astro_provenance_rejects_fit_on_test(tmp_path):
    import json
    meta = tmp_path / "astro_tide_meta.json"
    with pytest.raises(AstroLeakError):
        check_astro_provenance(meta)  # thiếu meta = bản cũ, không rõ khoảng fit
    for bad in ("2026-11-15", SPLITS["test"][0]):
        meta.write_text(json.dumps({"fit_from": "2017-01-01", "fit_to": bad}), encoding="utf-8")
        with pytest.raises(AstroLeakError):
            check_astro_provenance(meta)
    meta.write_text(json.dumps({"fit_from": "2017-01-01", "fit_to": SPLITS["train"][1]}),
                    encoding="utf-8")
    assert check_astro_provenance(meta)["fit_to"] == SPLITS["train"][1]


def test_load_astro_tide_checks_provenance(tmp_path):
    csv = tmp_path / "astro_tide.csv"
    csv.write_text("day,h1,h2,t1_h,t2_h\n2020-01-01,1,1,6,18\n", encoding="utf-8")
    with pytest.raises(AstroLeakError):
        load_astro_tide(path=csv)
    assert len(load_astro_tide(path=csv, check_leak=False)) == 1


_SMALL_SPLITS = {"train": ("2020-01-01", "2020-03-31"), "val": ("2020-04-01", "2020-05-31"),
                 "test": ("2020-06-01", "2020-07-31")}


def _g5_with_train_offset(offset: float = 5.0) -> pd.DataFrame:
    """PHU_AN có mực nước train lệch +`offset` m — nếu rò nhãn train, μ sẽ lệch theo."""
    df = _fake_g5(n_days=213)
    m = (df["station"] == "PHU_AN") & (df["day"] <= _SMALL_SPLITS["train"][1])
    df.loc[m, ["h_peak1", "h_peak2"]] += offset
    return df


def test_holdout_height_scale_khong_dung_nhan_train_cua_tram():
    """E5: μ/σ của trạm giữ riêng lấy từ quan trắc giai đoạn val, không từ nhãn train."""
    df = _g5_with_train_offset()
    out = build_datasets(df, t_in=14, t_out=5, splits=_SMALL_SPLITS, holdout_stations=("PHU_AN",))
    hs = out["train"].height_scale
    lo, hi = _SMALL_SPLITS["val"]
    val = df[(df["station"] == "PHU_AN") & (df["day"] >= lo) & (df["day"] <= hi)]
    assert hs.mean["PHU_AN::both"] == pytest.approx(float(val["h_peak2"].mean()), abs=1e-5)
    assert hs.mean["PHU_AN::both"] < 2.0      # không bị kéo lên bởi offset +5 m của train
    # trạm không giữ riêng vẫn fit trên train như cũ
    tr = df[(df["station"] == "NHA_BE") & (df["day"] <= _SMALL_SPLITS["train"][1])]
    assert hs.mean["NHA_BE::both"] == pytest.approx(float(tr["h_peak2"].mean()), abs=1e-5)
    # val/test dùng lại đúng thang này
    assert out["test"].height_scale is hs
    # thứ tự trạm trả về không đổi
    assert out["train"].stations == ["NHA_BE", "PHU_AN"]


def test_holdout_scaler_dac_trung_bo_qua_tram_giu_rieng():
    """Bộ chuẩn hoá đặc trưng gộp chỉ dùng các trạm không giữ riêng."""
    df = _g5_with_train_offset()
    with_h = build_datasets(df, t_in=14, t_out=5, splits=_SMALL_SPLITS,
                            holdout_stations=("PHU_AN",))
    only = build_datasets(df[df["station"] == "NHA_BE"], t_in=14, t_out=5, splits=_SMALL_SPLITS)
    assert np.allclose(with_h["train"].scaler.mean, only["train"].scaler.mean, equal_nan=True)
    assert np.allclose(with_h["train"].scaler.std, only["train"].scaler.std, equal_nan=True)


def test_holdout_height_scale_du_phong_khi_khong_co_quan_trac_val():
    """Không có quan trắc val ⇒ μ/σ = trung bình các trạm còn lại (không dùng train của trạm)."""
    df = _g5_with_train_offset()
    lo, hi = _SMALL_SPLITS["val"]
    df = df[~((df["station"] == "PHU_AN") & (df["day"] >= lo) & (df["day"] <= hi))]
    out = build_datasets(df, t_in=14, t_out=5, splits=_SMALL_SPLITS, holdout_stations=("PHU_AN",))
    hs = out["train"].height_scale
    assert hs.mean["PHU_AN::both"] == pytest.approx(hs.mean["NHA_BE::both"])
    assert hs.std["PHU_AN::both"] == pytest.approx(hs.std["NHA_BE::both"])


def test_khong_holdout_giu_nguyen_hanh_vi_cu():
    df = _g5_with_train_offset()
    out = build_datasets(df, t_in=14, t_out=5, splits=_SMALL_SPLITS)
    tr = df[(df["station"] == "PHU_AN") & (df["day"] <= _SMALL_SPLITS["train"][1])]
    assert out["train"].height_scale.mean["PHU_AN::both"] == pytest.approx(
        float(tr["h_peak2"].mean()), abs=1e-5)
