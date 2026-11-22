import pytest

from vngis.text import norm_name, slug, strip_accents


@pytest.mark.parametrize("raw, expected", [
    ("Thành phố Hồ Chí Minh", "ho chi minh"),
    ("Tỉnh Bà Rịa - Vũng Tàu", "ba ria vung tau"),
    ("  TỈNH   Long   An ", "long an"),
    ("Đồng Nai", "dong nai"),
    ("Thành phố trung ương Hà Nội", "ha noi"),
    ("Phường Thủ Dầu Một", "thu dau mot"),
    ("Xã Đá Đen", "da den"),
    ("Đặc khu Côn Đảo", "con dao"),
    ("Hồ Chí Minh", "ho chi minh"),
])
def test_norm_name(raw, expected):
    assert norm_name(raw) == expected


def test_norm_name_bo_mot_tien_to_duy_nhat():
    # chỉ bỏ tiền tố đầu tiên khớp, "thành phố" trong thân tên giữ nguyên
    assert norm_name("Tỉnh Thành phố X") == "thanh pho x"


def test_norm_name_khong_bo_tien_to_dinh_lien():
    # "tỉnhX" không có khoảng trắng sau tiền tố → không phải tiền tố
    assert norm_name("Tỉnhlộ") == "tinhlo"


def test_norm_name_unicode_to_hop():
    # chữ có dấu dạng tổ hợp (NFD) cho cùng kết quả với dạng dựng sẵn (NFC)
    import unicodedata
    assert norm_name(unicodedata.normalize("NFD", "Tây Ninh")) == "tay ninh"


def test_norm_name_tien_to_tuy_chinh():
    assert norm_name("Sông Sài Gòn", prefixes=["sông"]) == "sai gon"


@pytest.mark.parametrize("raw, expected", [
    ("Thủ Dầu Một", "THU_DAU_MOT"),
    ("Phú An", "PHU_AN"),
    ("Sr.P.Miêng", "SR_P_MIENG"),
    ("Đá Đen", "DA_DEN"),
    ("Gò Dầu Hạ", "GO_DAU_HA"),
    (" Vũng Tàu ", "VUNG_TAU"),
])
def test_slug(raw, expected):
    assert slug(raw) == expected


def test_slug_hop_le_voi_validator_stations():
    import re
    for name in ["Mạc Đĩnh Chi", "Sông Hỏa", "Tân Sơn Hòa", "Bến Lức"]:
        assert re.fullmatch(r"[A-Z0-9_]+", slug(name))


@pytest.mark.parametrize("raw, expected", [
    ("Tỉnh Bà Rịa - Vũng Tàu", "ba ria vung tau"),     # không còn hai dấu cách sau khi bỏ '-'
    ("Thị xã Thủ Dầu Một", "thu dau mot"),
    ("Phường Văn Miếu - Quốc Tử Giám", "van mieu quoc tu giam"),
    ("Bà Rịa -  Vũng\tTàu", "ba ria vung tau"),
    ("", ""),
])
def test_norm_name_gop_khoang_trang(raw, expected):
    out = norm_name(raw)
    assert out == expected and "  " not in out


def test_norm_name_nfd_co_tien_to():
    # tiền tố 'Tỉnh' ở dạng tổ hợp (NFD) vẫn phải được nhận ra và bỏ
    import unicodedata
    for raw in ["Tỉnh Tây Ninh", "Thành phố Hồ Chí Minh", "Thị xã Thủ Dầu Một"]:
        nfd = unicodedata.normalize("NFD", raw)
        assert nfd != raw
        assert norm_name(nfd) == norm_name(raw)
    assert norm_name(unicodedata.normalize("NFD", "Tỉnh X")) == "x"


def test_norm_name_none():
    assert norm_name(None) == ""


def test_strip_accents():
    assert strip_accents("Đồng Nai – Sài Gòn") == "Dong Nai  Sai Gon"   # gạch ngang dài không phải ASCII
    assert strip_accents("đđĐ") == "ddD"
