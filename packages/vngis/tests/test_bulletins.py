from datetime import datetime

from vngis.bulletins import duplicate_exclusions


def test_giu_ban_dung_thu_muc_ngay():
    rows = [{"source_file": "2019/01/10/a.pdf", "sha256": "h", "date_from_filename": 1, "bulletin_date": 2},
            {"source_file": "2020/01/10/a.pdf", "sha256": "h", "date_from_filename": 2, "bulletin_date": 2},
            {"source_file": "2020/01/11/b.pdf", "sha256": "k", "date_from_filename": 3, "bulletin_date": 3}]
    out = duplicate_exclusions(rows)
    assert [(o["source_file"], o["kept"]) for o in out] == [("2019/01/10/a.pdf", "2020/01/10/a.pdf")]


def test_hoa_thi_giu_ten_nho_nhat_va_datetime():
    d = datetime(2020, 1, 1)
    rows = [{"source_file": "b.pdf", "sha256": "h", "date_from_filename": d, "bulletin_date": d},
            {"source_file": "a.pdf", "sha256": "h", "date_from_filename": d, "bulletin_date": d}]
    assert [o["source_file"] for o in duplicate_exclusions(rows)] == ["b.pdf"]


def test_khong_sha256_khong_trung():
    rows = [{"source_file": f"{i}.pdf", "sha256": None, "date_from_filename": 1, "bulletin_date": 1} for i in range(3)]
    assert duplicate_exclusions(rows) == []
