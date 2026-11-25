"""Kiểm thử `phasegnn.train.select_samples` (lấy mẫu con và giữ riêng trạm cho E5)."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from phasegnn.train import TrainConfig, select_samples


def _samples():
    # Xếp theo trạm → ngày, giống `build_datasets`.
    return [SimpleNamespace(station=st, issue_date=f"2020-01-{d:02d}")
            for st in ("A", "B", "C") for d in range(1, 11)]


def test_head_mac_dinh_giu_hanh_vi_cu():
    s = _samples()
    out = select_samples(s, 12)
    assert out == s[:12]
    assert {x.station for x in out} == {"A", "B"}


def test_spread_phu_moi_tram_va_xac_dinh():
    s = _samples()
    out = select_samples(s, 6, mode="spread")
    assert len(out) == 6
    assert {x.station for x in out} == {"A", "B", "C"}
    assert out[0] is s[0] and out[-1] is s[-1]
    assert out == select_samples(s, 6, mode="spread")
    # giữ thứ tự gốc
    assert [s.index(x) for x in out] == sorted(s.index(x) for x in out)


def test_holdout_bo_tram_truoc_khi_lay_mau():
    s = _samples()
    out = select_samples(s, 10, mode="head", holdout_stations=["A"])
    assert {x.station for x in out} == {"B"}
    out = select_samples(s, None, mode="spread", holdout_stations=("A", "C"))
    assert len(out) == 10 and {x.station for x in out} == {"B"}


def test_khong_gioi_han_hoac_n_lon_tra_toan_bo():
    s = _samples()
    assert select_samples(s, None, mode="spread") == s
    assert select_samples(s, 1000, mode="spread") == s


def test_mode_sai():
    with pytest.raises(ValueError):
        select_samples(_samples(), 5, mode="random")


def test_mac_dinh_trainconfig():
    c = TrainConfig()
    assert c.subsample == "head" and c.holdout_stations == ()
