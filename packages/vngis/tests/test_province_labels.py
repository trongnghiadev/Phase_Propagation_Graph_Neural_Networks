"""Quy tắc gán tỉnh {era, code} từ nhãn tỉnh trong bản tin (vngis.province_labels)."""
from datetime import date, datetime, timedelta, timezone

import pytest

from vngis.admin import BEFORE, EFFECTIVE_UTC, FROM
from vngis.province_labels import (DEFAULT_MAX_LAG_DAYS, NOTE_LAG, assign_all, assign_runs, build_index, lookup,
                                   split_runs)

TIMELINE = [
    {"era": BEFORE, "code": 75, "name": "Tỉnh Đồng Nai", "name_norm": "dong nai"},
    {"era": BEFORE, "code": 70, "name": "Tỉnh Bình Phước", "name_norm": "binh phuoc"},
    {"era": BEFORE, "code": 72, "name": "Tỉnh Tây Ninh", "name_norm": "tay ninh"},
    {"era": BEFORE, "code": 79, "name": "Thành phố Hồ Chí Minh", "name_norm": "ho chi minh", "aliases_norm": ["tphcm"]},
    {"era": FROM, "code": 75, "name": "Tỉnh Đồng Nai", "name_norm": "dong nai"},
    {"era": FROM, "code": 80, "name": "Tỉnh Tây Ninh", "name_norm": "tay ninh"},
    {"era": FROM, "code": 79, "name": "Thành phố Hồ Chí Minh", "name_norm": "ho chi minh",
     "aliases_norm": ["tphcm", "tp.hcm"]},
]


def days(d0: date, d1: date, label):
    """Mỗi ngày quan trắc (ngày lịch Việt Nam) từ d0 đến d1 (bao gồm) một bản ghi."""
    return [(d0 + timedelta(days=i), label) for i in range((d1 - d0).days + 1)]


def at(runs, d: date):
    t = datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=7))).astimezone(timezone.utc)
    hits = [r for r in runs if r.first <= t <= r.last]
    assert len(hits) == 1, hits
    return hits[0]


def test_mac_dinh_khop_config(repo_config_dir):
    import yaml
    cfg = yaml.safe_load((repo_config_dir / "default.yaml").read_text(encoding="utf-8"))
    assert cfg["admin"]["max_label_lag_days"] == DEFAULT_MAX_LAG_DAYS


def test_tay_ninh_doi_ten_cham_la_72_thoi_ky_cu():
    rec = (days(date(2025, 6, 1), date(2025, 6, 30), "Tây Ninh") + days(date(2025, 7, 1), date(2025, 7, 7), "Tây Ninh")
           + days(date(2025, 7, 8), date(2025, 9, 1), "TPHCM"))
    runs = assign_runs(rec, TIMELINE)
    assert [(r.label, r.obs_era, r.era, r.code) for r in runs] == [
        ("Tây Ninh", BEFORE, BEFORE, 72), ("Tây Ninh", FROM, BEFORE, 72), ("TPHCM", FROM, FROM, 79)]
    mid = at(runs, date(2025, 7, 4))
    assert (mid.era, mid.code, mid.note) == (BEFORE, 72, NOTE_LAG) and mid.n == 7
    assert at(runs, date(2025, 6, 30)).note is None
    assert at(runs, date(2025, 7, 8)).note is None


def test_binh_phuoc_7_ngay_roi_dong_nai():
    rec = (days(date(2025, 6, 20), date(2025, 6, 30), "Bình Phước")
           + days(date(2025, 7, 1), date(2025, 7, 7), "Bình Phước") + days(date(2025, 7, 8), date(2025, 8, 1), "Đồng Nai"))
    runs = assign_runs(rec, TIMELINE)
    assert all(r.matched for r in runs)
    lag = at(runs, date(2025, 7, 7))
    assert (lag.era, lag.code, lag.note) == (BEFORE, 70, NOTE_LAG)
    assert (at(runs, date(2025, 7, 8)).era, at(runs, date(2025, 7, 8)).code) == (FROM, 75)


def test_dong_nai_lien_tuc_theo_ngay():
    runs = assign_runs(days(date(2025, 6, 25), date(2025, 7, 10), "Đồng Nai"), TIMELINE)
    assert len(runs) == 2
    assert (at(runs, date(2025, 6, 30)).era, at(runs, date(2025, 6, 30)).code) == (BEFORE, 75)
    r = at(runs, date(2025, 7, 1))
    assert (r.era, r.code, r.note) == (FROM, 75, None)
    assert r.first == EFFECTIVE_UTC                      # 01/07/2025 00:00 giờ Việt Nam


def test_thoi_ky_theo_ngay_quan_trac_datetime_utc():
    # datetime naive = UTC (như Date đọc từ pymongo); 2025-06-30T17:00Z = 01/07 giờ VN → thời kỳ mới
    rec = [(datetime(2025, 6, 29, 17), "Đồng Nai"), (datetime(2025, 6, 30, 17), "Đồng Nai")]
    assert [r.obs_era for r in assign_runs(rec, TIMELINE)] == [BEFORE, FROM]


def test_doi_cach_viet_sau_200_ngay_khong_ve_thoi_ky_cu():
    rec = (days(date(2025, 6, 1), date(2025, 6, 30), "Tây Ninh") + days(date(2025, 7, 1), date(2026, 1, 16), "Tây Ninh")
           + days(date(2026, 1, 17), date(2026, 2, 1), "TPHCM"))
    r = at(assign_runs(rec, TIMELINE), date(2025, 7, 3))
    assert (r.era, r.code, r.note) == (FROM, 80, None)


def test_ngoai_gioi_han_ten_cu_khong_co_o_thoi_ky_moi_la_khong_khop():
    rec = (days(date(2025, 6, 1), date(2025, 6, 30), "Bình Phước") + days(date(2025, 7, 1), date(2025, 9, 1), "Bình Phước")
           + days(date(2025, 9, 2), date(2025, 9, 9), "Đồng Nai"))
    r = at(assign_runs(rec, TIMELINE), date(2025, 8, 1))
    assert not r.matched and r.era is None and FROM in r.reason
    # max_lag_days lớn hơn thì lại là đổi tên chậm
    r = at(assign_runs(rec, TIMELINE, max_lag_days=90), date(2025, 8, 1))
    assert (r.era, r.code) == (BEFORE, 70)


def test_nhan_cu_khong_doi_ve_sau_thi_tra_thoi_ky_moi():
    # dùng lại nhãn cũ sau mốc nhưng không có đoạn kế tiếp → không phải đổi tên chậm (provisional vì còn trong hạn)
    r = at(assign_runs(days(date(2025, 6, 28), date(2025, 7, 3), "Tây Ninh"), TIMELINE), date(2025, 7, 2))
    assert (r.era, r.code, r.note, r.provisional) == (FROM, 80, None, True)


def test_so_nhan_theo_norm_name():
    # 'Tỉnh Tây Ninh' và 'Tây  Ninh' là cùng một nhãn → vẫn là đổi tên chậm
    rec = (days(date(2025, 6, 25), date(2025, 6, 30), "Tỉnh Tây Ninh")
           + days(date(2025, 7, 1), date(2025, 7, 3), "Tây  Ninh") + days(date(2025, 7, 4), date(2025, 7, 9), "TP.HCM"))
    runs = assign_runs(rec, TIMELINE)
    assert len(runs) == 3
    r = at(runs, date(2025, 7, 2))
    assert (r.era, r.code, r.note) == (BEFORE, 72, NOTE_LAG) and r.labels == ("Tây  Ninh",)
    assert at(runs, date(2025, 7, 5)).code == 79


def vn(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, tzinfo=timezone(timedelta(hours=7))).astimezone(timezone.utc)


def labeled_at(runs, d: date):
    """Đoạn có nhãn (không rỗng) phủ ngày d."""
    return at([r for r in runs if r.label_norm], d)


@pytest.mark.parametrize("label", [None, "", "   "])
def test_nhan_rong_khong_crash(label):
    rec = days(date(2025, 6, 28), date(2025, 7, 2), label) + days(date(2025, 7, 3), date(2025, 7, 4), "Đồng Nai")
    runs = assign_runs(rec, TIMELINE)
    r = at(runs, date(2025, 6, 29))
    assert not r.matched and r.reason == "nhãn tỉnh rỗng"
    assert not at(runs, date(2025, 7, 1)).matched          # nhãn rỗng không bao giờ là đổi tên chậm
    assert at(runs, date(2025, 7, 3)).code == 75


def test_A_nhan_rong_khong_lam_dut_doi_ten_cham():
    # Tây Ninh 30/06, Tây Ninh 01/07, None 02/07, Tây Ninh 03/07, TPHCM 07/07 → 01/07 và 03/07 đều 72
    rec = [(date(2025, 6, 30), "Tây Ninh"), (date(2025, 7, 1), "Tây Ninh"), (date(2025, 7, 2), None),
           (date(2025, 7, 3), "Tây Ninh"), (date(2025, 7, 7), "TPHCM")]
    runs = assign_runs(rec, TIMELINE)
    for d_ in (date(2025, 7, 1), date(2025, 7, 3)):
        r = labeled_at(runs, d_)
        assert (r.era, r.code, r.note) == (BEFORE, 72, NOTE_LAG), d_
    lag = labeled_at(runs, date(2025, 7, 1))
    assert (lag.first, lag.last, lag.n) == (vn(date(2025, 7, 1)), vn(date(2025, 7, 3)), 2)
    empty = [r for r in runs if not r.label_norm]
    assert len(empty) == 1 and empty[0].n == 1 and not empty[0].matched and empty[0].first == vn(date(2025, 7, 2))
    assert labeled_at(runs, date(2025, 7, 7)).code == 79


def test_C_nhan_rong_khong_kich_hoat_doi_ten_cham():
    # Tây Ninh liên tục, một bản None ngày 20/07 → không ngày nào sau mốc bị gán thời kỳ cũ
    rec = days(date(2025, 6, 1), date(2025, 9, 30), "Tây Ninh")
    rec = [(d_, None if d_ == date(2025, 7, 20) else lbl) for d_, lbl in rec]
    runs = assign_runs(rec, TIMELINE)
    after = [r for r in runs if r.label_norm and r.obs_era == FROM]
    assert len(after) == 1 and (after[0].era, after[0].code, after[0].note) == (FROM, 80, None)
    assert after[0].first == vn(date(2025, 7, 1)) and after[0].last == vn(date(2025, 9, 30))
    assert not after[0].provisional                        # dữ liệu kéo dài quá max_lag_days
    assert sum(1 for r in runs if not r.label_norm) == 1


def test_doan_cuoi_du_lieu_chua_chac():
    # dữ liệu dừng trong max_lag_days, nhãn trước mốc còn dùng: không phải đổi tên chậm, nhưng provisional
    rec = days(date(2025, 6, 25), date(2025, 7, 10), "Tây Ninh")
    r = labeled_at(assign_runs(rec, TIMELINE), date(2025, 7, 5))
    assert (r.era, r.code, r.note, r.provisional) == (FROM, 80, None, True)
    r = labeled_at(assign_runs(rec, TIMELINE, max_lag_days=5), date(2025, 7, 5))
    assert r.provisional is False


def test_provisional_bien_max_lag():
    # mốc + 31 ngày = 01/08/2025 00:00 giờ VN: bản ghi cuối đúng ngày đó → đã quá hạn (dùng '<'), không provisional
    pre = days(date(2025, 6, 25), date(2025, 6, 30), "Tây Ninh")
    r = labeled_at(assign_runs(pre + days(date(2025, 7, 1), date(2025, 7, 31), "Tây Ninh"), TIMELINE), date(2025, 7, 5))
    assert r.provisional is True and vn(date(2025, 7, 31)) < EFFECTIVE_UTC + timedelta(days=DEFAULT_MAX_LAG_DAYS)
    r = labeled_at(assign_runs(pre + days(date(2025, 7, 1), date(2025, 8, 1), "Tây Ninh"), TIMELINE), date(2025, 7, 5))
    assert r.provisional is False and r.last == EFFECTIVE_UTC + timedelta(days=DEFAULT_MAX_LAG_DAYS)
    # max_lag_days = 0: không bao giờ provisional
    r = labeled_at(assign_runs(pre + days(date(2025, 7, 1), date(2025, 7, 1), "Tây Ninh"), TIMELINE, 0), date(2025, 7, 1))
    assert r.provisional is False
    # đoạn đầu dữ liệu, đoạn trước mốc, đoạn có nhãn mới phía sau: không provisional
    runs = assign_runs(days(date(2025, 6, 25), date(2025, 7, 3), "Tây Ninh") + days(date(2025, 7, 4), date(2025, 7, 5), "TPHCM"),
                       TIMELINE)
    assert not any(x.provisional for x in runs)


def test_lookup_khong_khop_va_nhieu_ma():
    idx = build_index(TIMELINE + [{"era": FROM, "code": 99, "name_norm": "dong nai"}])
    p, reason = lookup(idx, FROM, "Đồng Nai")
    assert p is None and "nhiều tỉnh" in reason
    assert lookup(idx, BEFORE, "Không Có")[0] is None
    assert lookup(idx, BEFORE, "Thành phố Hồ Chí Minh")[0]["code"] == 79


def test_assign_all_va_split_runs():
    runs, unmatched = assign_all({"A": days(date(2020, 1, 1), date(2020, 1, 3), "Không Có"),
                                  "B": days(date(2020, 1, 1), date(2020, 1, 3), "Đồng Nai")}, TIMELINE)
    assert [u["key"] for u in unmatched] == ["A"] and runs["B"][0].code == 75
    assert split_runs([]) == []
    with pytest.raises(ValueError):
        assign_runs([], TIMELINE, max_lag_days=-1)


BLOCK = """
import sys
class Block:
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('pymongo', 'pydantic', 'yaml', 'redis', 'bson'):
            raise ImportError('chặn ' + name)
sys.meta_path.insert(0, Block())
import vngis.text, vngis.admin, vngis.province_labels, vngis.bulletins
print('ok')
"""


def test_module_thuan_python_khong_can_pymongo_pydantic_yaml():
    # Job Spark import vngis.text / vngis.admin / vngis.province_labels trong image không có các gói này
    import subprocess
    import sys
    out = subprocess.run([sys.executable, "-c", BLOCK], capture_output=True, text=True)
    assert out.returncode == 0 and out.stdout.strip() == "ok", out.stderr
