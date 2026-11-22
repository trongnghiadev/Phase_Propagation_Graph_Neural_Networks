from datetime import date, datetime, timedelta, timezone

import pytest

from vngis import admin
from vngis.admin import BEFORE, EFFECTIVE_UTC, FROM, era_of, find_province, province_at, successor_of, to_utc

VN = timezone(timedelta(hours=7))
US = timezone(timedelta(hours=-5))


# ---------------------------------------------------------------- era_of -----------
def test_hang_so():
    assert (BEFORE, FROM) == ("before_2025_07", "from_2025_07")
    assert EFFECTIVE_UTC == datetime(2025, 6, 30, 17, 0, tzinfo=timezone.utc)
    assert EFFECTIVE_UTC == datetime(2025, 7, 1, 0, 0, tzinfo=VN)


@pytest.mark.parametrize("dt, era", [
    (datetime(2025, 6, 30, 16, 59, 59, 999999), BEFORE),          # naive = UTC
    (datetime(2025, 6, 30, 17, 0), FROM),                          # đúng mốc thuộc thời kỳ mới
    (datetime(2025, 6, 30, 17, 0, 0, 1), FROM),
    (datetime(2017, 1, 1), BEFORE),
    (datetime(2026, 11, 11), FROM),
])
def test_era_of_naive_la_utc(dt, era):
    assert era_of(dt) == era


@pytest.mark.parametrize("dt, era", [
    (datetime(2025, 6, 30, 23, 59, 59, tzinfo=VN), BEFORE),
    (datetime(2025, 7, 1, 0, 0, tzinfo=VN), FROM),
    (datetime(2025, 6, 30, 17, 0, tzinfo=timezone.utc), FROM),
    (datetime(2025, 6, 30, 16, 59, tzinfo=timezone.utc), BEFORE),
    (datetime(2025, 6, 30, 11, 59, tzinfo=US), BEFORE),             # = 16:59 UTC
    (datetime(2025, 6, 30, 12, 0, tzinfo=US), FROM),                # = 17:00 UTC
])
def test_era_of_aware_doi_ve_utc(dt, era):
    assert era_of(dt) == era


def test_era_of_naive_khong_bi_hieu_la_gio_vn():
    # 2025-07-01 00:00 naive là UTC (= 07:00 giờ VN) → thời kỳ mới; 2025-06-30 20:00 naive cũng đã qua mốc
    assert era_of(datetime(2025, 6, 30, 20, 0)) == FROM
    # nếu (sai) coi naive là giờ VN thì 20:00 30/06 sẽ là BEFORE
    assert era_of(datetime(2025, 6, 30, 20, 0, tzinfo=VN)) == BEFORE


def test_era_of_date_la_ngay_lich_viet_nam():
    assert era_of(date(2025, 6, 30)) == BEFORE
    assert era_of(date(2025, 7, 1)) == FROM


@pytest.mark.parametrize("bad", ["2025-07-01", 1751302800, None])
def test_era_of_sai_kieu(bad):
    with pytest.raises(TypeError):
        era_of(bad)


def test_to_utc():
    assert to_utc(datetime(2025, 1, 1)).tzinfo == timezone.utc
    assert to_utc(datetime(2025, 1, 1, 7, tzinfo=VN)) == datetime(2025, 1, 1, tzinfo=timezone.utc)


# ---------------------------------------------------------------- truy vấn (giả lập) ----
class FakeCursor(list):
    def sort(self, key, direction):
        return FakeCursor(sorted(self, key=lambda d: d[key], reverse=direction < 0))


class FakeCollection:
    """Ghi lại truy vấn và trả kết quả dựng sẵn theo (era, code)."""

    def __init__(self, docs):
        self.docs = docs
        self.queries = []

    def find_one(self, flt, projection=None):
        self.queries.append(flt)
        for d in self.docs:
            if d["era"] != flt["era"]:
                continue
            if "code" in flt and d["code"] == flt["code"]:
                return dict(d)
            if "$or" in flt:
                n = flt["$or"][0]["name_norm"]
                if d["name_norm"] == n or n in d.get("aliases_norm", []):
                    return dict(d)
        return None

    def find(self, flt, projection=None):
        self.queries.append(flt)
        return FakeCursor(d for d in self.docs if d["era"] == flt["era"])


DOCS = [
    {"era": BEFORE, "code": 80, "name": "Tỉnh Long An", "name_norm": "long an",
     "successor": {"code": 80, "name": "Tỉnh Tây Ninh"}},
    {"era": BEFORE, "code": 72, "name": "Tỉnh Tây Ninh", "name_norm": "tay ninh",
     "successor": {"code": 80, "name": "Tỉnh Tây Ninh"}},
    {"era": BEFORE, "code": 79, "name": "Thành phố Hồ Chí Minh", "name_norm": "ho chi minh",
     "aliases_norm": ["tp hcm", "tphcm"], "successor": {"code": 79, "name": "Thành phố Hồ Chí Minh"}},
    {"era": FROM, "code": 80, "name": "Tỉnh Tây Ninh", "name_norm": "tay ninh"},
    {"era": FROM, "code": 79, "name": "Thành phố Hồ Chí Minh", "name_norm": "ho chi minh"},
]


@pytest.fixture
def fake_db():
    col = FakeCollection(DOCS)
    return {admin.TIMELINE: col}, col


def test_find_province_kem_era_va_norm(fake_db):
    db, col = fake_db
    assert find_province(db, BEFORE, "Tỉnh Long An")["code"] == 80
    assert col.queries[-1] == {"era": BEFORE, "$or": [{"name_norm": "long an"}, {"aliases_norm": "long an"}]}
    assert find_province(db, BEFORE, "TP HCM")["code"] == 79                  # qua aliases_norm
    # cùng mã 80 nhưng khác tỉnh ở hai thời kỳ
    assert find_province(db, FROM, "Tây Ninh")["code"] == 80
    assert find_province(db, BEFORE, "Tây Ninh")["code"] == 72
    assert find_province(db, FROM, "Long An") is None


def test_find_province_nhan_rong_va_era_sai(fake_db):
    db, col = fake_db
    assert find_province(db, BEFORE, None) is None
    assert find_province(db, BEFORE, "  ") is None
    assert col.queries == []
    with pytest.raises(ValueError):
        find_province(db, "2025", "Long An")


def test_province_at_truy_van_kem_era_va_hieu_luc(fake_db):
    db, col = fake_db
    got = province_at(db, datetime(2025, 7, 1, tzinfo=VN))
    assert [d["code"] for d in got] == [79, 80]
    q = col.queries[-1]
    assert q["era"] == FROM
    t = q["$and"][0]["$or"][1]["valid_from"]["$lte"]
    assert t == EFFECTIVE_UTC and q["$and"][1]["$or"][1]["valid_to"]["$gt"] == EFFECTIVE_UTC
    assert province_at(db, datetime(2025, 6, 30, 16, 59)) and col.queries[-1]["era"] == BEFORE


def test_successor_of(fake_db):
    db, col = fake_db
    assert successor_of(db, 80)["name"] == "Tỉnh Tây Ninh"
    assert col.queries[-2] == {"era": BEFORE, "code": 80}
    assert col.queries[-1] == {"era": FROM, "code": 80}
    assert successor_of(db, 1) is None


# ---------------------------------------------------------------- MongoDB thật -----
@pytest.fixture
def timeline(mongo_db):
    if mongo_db.province_timeline.estimated_document_count() == 0:
        pytest.skip("province_timeline rỗng")
    return mongo_db


@pytest.mark.mongo
def test_mongo_find_province(timeline):
    assert find_province(timeline, BEFORE, "Long An")["code"] == 80
    assert find_province(timeline, FROM, "Tây Ninh")["code"] == 80
    assert find_province(timeline, BEFORE, "Tỉnh Tây Ninh")["code"] == 72
    assert find_province(timeline, FROM, "Thành phố Hồ Chí Minh")["code"] == 79
    assert find_province(timeline, FROM, "Long An") is None


@pytest.mark.mongo
def test_mongo_province_at(timeline):
    assert len(province_at(timeline, datetime(2025, 6, 30, 16, 59))) == 63
    assert len(province_at(timeline, EFFECTIVE_UTC)) == 34
    assert len(province_at(timeline, date(2026, 1, 1))) == 34


@pytest.mark.mongo
def test_mongo_successor_of(timeline):
    assert successor_of(timeline, 80)["name_norm"] == "tay ninh"       # Long An → Tây Ninh
    assert successor_of(timeline, 74)["name_norm"] == "ho chi minh"    # Bình Dương → TP.HCM
    assert successor_of(timeline, 9999) is None
