"""
Dữ liệu chép tay từ 2 file PDF không có lớp text (chữ vector hóa / ảnh scan).
Cùng schema với kttv_parser.py. Được gọi từ kttv_load_mongo.py.
"""
import datetime as dt
from kttv_parser import tide_pair, num

D = dt.datetime
MANUAL = "doc_thu_cong_tu_anh_pdf"


def _rain(st, raw):
    v, flag = num(raw, dash="khong_mua")
    o = {"tram": st, "luong_mua_mm": v, "raw": raw}
    if flag:
        o["ghi_chu"] = flag
    return o


def _res(name, song, tinh, mn, qv, qm, qx):
    o = {"ho_chua": name, "song": song, "tinh": tinh}
    for f, rv in zip(["muc_nuoc_thuong_luu_m", "luu_luong_ve_ho_m3s", "luu_luong_chay_may_m3s", "luu_luong_xa_m3s"], [mn, qv, qm, qx]):
        v, flag = num(rv)
        o[f] = v
        if flag:
            o.setdefault("co_hieu", {})[f] = flag
    return o


def bulletin_20251227():
    obs = dt.date(2025, 12, 26)
    wl_rows = [
        ("Vũng Tàu", "Biển Đông", "3.93 19.00 3.73 4.00 ct ct 1.49 11.45"),
        ("Tân An", "Vàm Cỏ Tây", "1.48 21.00 1.27 7.30 0.71 2.00 0.00 15.00"),
        ("Bến Lức", "Vàm Cỏ Đông", "1.29 21.30 1.06 7.00 0.43 2.30 -0.58 14.30"),
        ("Gò Dầu Hạ", "Vàm Cỏ Đông", "0.84 00.30 0.88 10.00 0.59 5.10 0.34 18.00"),
        ("Biên Hòa", "Đồng Nai", "1.81 22.30 1.40 8.00 0.22 3.00 -1.27 15.30"),
        ("Thủ Dầu Một", "Sài Gòn", "1.61 23.00 1.39 8.15 0.72 3.20 -0.52 15.45"),
        ("Dầu Tiếng", "Sài Gòn", "1.31 03.00 1.26 12.00 0.68 8.00 -0.08 20.00"),
        ("Phú An", "Sài Gòn", "1.52 22.00 1.25 7.00 0.40 2.00 -1.38 14.00"),
        ("Nhà Bè", "Đồng Điền", "1.48 21.00 1.18 6.00 0.37 1.00 -1.36 13.00"),
    ]
    fc_rows = {
        "Phú An": ["1.49 23.00 1.09 8.00 0.34 3.00 -1.12 15.00",
                   "1.44 23.30 0.93 9.00 -0.03 4.00 -0.82 16.00",
                   "ct ct 0.90 10.00 -0.49 5.00 -0.47 17.00",
                   "1.43 00.00 1.03 11.00 -0.86 6.00 -0.15 18.00",
                   "1.46 00.30 1.20 12.00 -1.21 7.00 0.21 19.00"],
        "Nhà Bè": ["1.44 22.00 0.98 7.00 0.30 2.00 -1.03 14.00",
                   "1.41 23.00 0.83 8.00 -0.07 3.00 -0.73 15.00",
                   "1.38 23.30 0.80 9.00 -0.52 4.00 -0.19 16.00",
                   "ct ct 0.97 10.00 -1.00 5.00 0.18 17.00",
                   "1.42 00.00 1.17 11.00 -1.44 6.00 0.49 18.00"],
        "Thủ Dầu Một": ["1.56 23.30 1.23 9.00 0.66 4.40 -0.42 16.00",
                        "ct ct 1.13 10.00 0.40 5.30 -0.28 17.00",
                        "1.52 00.00 1.07 10.30 0.13 6.00 -0.10 18.00",
                        "1.50 01.00 1.11 11.30 -0.13 6.30 0.11 19.00",
                        "1.53 02.00 1.19 12.00 -0.36 7.00 0.32 10.00"],
    }
    wl = []
    for st, song, vals in wl_rows:
        o = {"tram": st, "song": song}
        o.update(tide_pair(vals.split(), obs))
        wl.append(o)
    fc = []
    for st, rows in fc_rows.items():
        for k, vals in enumerate(rows):
            d = dt.date(2025, 12, 27 + k)
            o = {"tram": st, "ngay": D(d.year, d.month, d.day)}
            o.update(tide_pair(vals.split(), d))
            fc.append(o)
    return {
        "loai_ban_tin": "du_bao_dac_trung_thuy_trieu_5_ngay",
        "tieu_de": "BẢN TIN DỰ BÁO ĐẶC TRƯNG THỦY TRIỀU 5 NGÀY",
        "don_vi_phat_hanh": "ĐÀI KHÍ TƯỢNG THỦY VĂN NAM BỘ",
        "so_ban_tin": "361/2025", "so_ban_tin_stt": 361, "thu": "Thứ Bảy",
        "ngay_ban_tin": D(2025, 12, 27),
        "am_lich": {"ngay": 8, "thang": 11, "nam": "ẤT TỴ", "nam_goc": "Ất TỴ", "nam_duong_lich_tuong_ung": 2025},
        "tin_phat_luc": D(2025, 12, 27, 9, 1),
        "nguoi_thuc_hien": {"nguoi_chiu_trach_nhiem": None},  # tên người: xem PDF gốc (không công bố trong repo public)
        "cap_bao_dong": {"tram_ap_dung": ["Phú An", "Nhà Bè", "Thủ Dầu Một"], "bd1_m": 1.4, "bd2_m": 1.5, "bd3_m": 1.6},
        "ngay_quan_trac": D(2025, 12, 26),
        "luong_mua": {"khoang_thoi_gian": "từ 7h 26/12 đến 7h 27/12", "tram": [
            _rain("Mạc Đĩnh Chi", "-"), _rain("Tân Sơn Hòa", "-"), _rain("Nhà Bè", "-"),
            _rain("Vũng Tàu", "-"), dict(_rain("Thủ Dầu Một", "-"), tram_goc="Thủ Dầu I")]},
        "ho_chua": [
            _res("Trị An", "Đồng Nai", "Đồng Nai", "61.88", "286", "301", "0"),
            _res("Thác Mơ", "Bé", "Đồng Nai", "217.05", "32", "56", "0"),
            _res("Sr.P.Miêng", "Bé", "Đồng Nai", "71.82", "83", "76", "0"),
            _res("Dầu Tiếng", "Sài Gòn", "TPHCM", "23.53", "100", "-", "24"),
        ],
        "muc_nuoc_thuc_do": wl,
        "muc_nuoc_du_bao": fc,
        "nhan_xet": None,
        "canh_bao": "Mực nước đỉnh triều cao nhất ngày tại hầu hết các trạm trên sông Sài Gòn biến đổi chậm trong 2-3 ngày đầu, sau lên chậm.",
        "ghi_chu": "- Mực nước Vũng Tàu lấy theo độ cao số \"0\" Hải đồ; ( * ): không có số liệu; ( - ): không mưa; "
                   "Cấp báo động tại Phú An, Nhà Bè & Thủ Dầu Một: BĐ I: 1.40 m, BĐ II: 1.50 m, BĐ III: 1.60 m",
        "parse": {"trang_thai": "ok", "canh_bao": ["pdf_khong_co_lop_text", "chep_tay_tu_anh"], "phuong_phap": MANUAL},
    }


def salinity_20210610():
    # Bảng 1: số liệu quan trắc thủy văn xâm nhập mặn trên sông rạch TP.HCM đợt 01 tháng 06/2021
    rows = [
        ("Phà Cát Lái", [("BQ", "6,52 0,35 0,74 37,67 1,38 0,34"), ("Max", "6,80 1,03 1,82 56,00 2,84 0,52"), ("Min", "6,40 0,10 0,25 22,00 0,11 0,22")]),
        ("Mũi Nhà Bè", [("BQ", "7,02 2,25 3,54 52,17 3,09 1,42"), ("Max", "7,21 4,13 6,84 83,00 6,26 2,90"), ("Min", "6,80 0,39 0,77 22,00 0,33 0,50")]),
        ("Cầu Thủ Thiêm", [("BQ", "6,92 0,23 0,52 54,67 0,73 0,26"), ("Max", "7,22 0,31 0,69 90,00 1,51 0,31"), ("Min", "6,71 0,16 0,37 31,00 0,16 0,19")]),
        ("Kênh C – Chợ Đệm", [("BQ", "6,96 0,68 1,25 52,50 1,20 0,55"), ("Max", "7,19 0,91 1,49 91,00 1,63 0,72"), ("Min", "6,75 0,46 0,97 39,00 0,58 0,40")]),
        ("Cầu Ông Thìn", [("BQ", "6,95 1,82 3,05 88,08 2,56 1,28"), ("Max", "7,16 2,06 3,49 230,00 4,41 1,61"), ("Min", "6,70 1,68 2,81 26,00 0,86 0,78")]),
        ("Kênh Xáng – An Hạ", [("BQ", "6,52 0,20 0,47 45,50 0,34 0,23"), ("Max", "6,60 0,21 0,48 56,00 0,50 0,26"), ("Min", "6,42 0,20 0,46 41,00 0,22 0,21")]),
        ("Cầu Rạch Tra", [("BQ", "6,49 0,09 0,22 59,33 0,11 0,09"), ("Max", "6,78 0,11 0,26 133,00 0,14 0,10"), ("Min", "6,27 0,08 0,19 17,00 0,07 0,07")]),
    ]
    tram = []
    for stt, (name, vals) in enumerate(rows, 1):
        o = {"stt": stt, "vi_tri": name}
        for dac_trung, s in vals:
            f = [float(x.replace(",", ".")) for x in s.split()]
            o[dac_trung.lower()] = {"ph": f[0], "do_man_g_l": f[1], "do_dan_dien_ms_cm": f[2], "do_duc_ntu": f[3],
                                    "do_man_thang_06_2019_g_l": f[4], "do_man_dot2_thang_05_2021_g_l": f[5]}
        tram.append(o)
    return {
        "loai_ban_tin": "bao_cao_khao_sat_xam_nhap_man",
        "tieu_de": "BÁO CÁO KHẢO SÁT THỦY VĂN XÂM NHẬP MẶN TRÊN SÔNG RẠCH TP. HCM ĐỢT 1 THÁNG 06 NĂM 2021 (NGÀY 10/06/2021)",
        "don_vi_phat_hanh": "Viện Môi trường và Tài nguyên - Đại học Quốc gia TP. HCM",
        "ngay_ban_tin": D(2021, 6, 14),
        "ngay_quan_trac": D(2021, 6, 10),
        "nguoi_thuc_hien": {"nguoi_ky": None},  # tên người: xem PDF gốc (không công bố trong repo public)
        "xam_nhap_man": {
            "dot": "Đợt 01 tháng 06/2021", "so_tram": 7, "tram": tram,
            "tong_hop": {"ph_binh_quan": 6.77, "ph_min": {"gia_tri": 6.27, "tram": "Cầu Rạch Tra"},
                         "ph_max": {"gia_tri": 7.22, "tram": "Cầu Thủ Thiêm"},
                         "do_duc_binh_quan_ntu": 55.70, "do_duc_min": {"gia_tri": 17.00, "tram": "Cầu Rạch Tra"},
                         "do_duc_max": {"gia_tri": 230.00, "tram": "Cầu Ông Thìn"},
                         "ec_binh_quan_ms_cm": 1.40, "ec_min": {"gia_tri": 0.19, "tram": "Cầu Rạch Tra"},
                         "ec_max": {"gia_tri": 6.84, "tram": "Mũi Nhà Bè"}},
        },
        "noi_dung_van_ban": ("Kết quả khảo sát thủy văn và xâm nhập mặn được đo ngày 10/06/2021 tại 7 trạm trên sông rạch thuộc "
                             "Thành phố Hồ Chí Minh đợt 01. Độ mặn Max và BQ tại các trạm đều thấp hơn tháng 06/2019; so với đợt 2 "
                             "tháng 05/2021: tăng tại Mũi Nhà Bè, Phà Cát Lái, Cầu Ông Thìn, Kênh C – Chợ Đệm; tương đương tại "
                             "Cầu Thủ Thiêm, Cầu Rạch Tra; giảm tại Kênh Xáng – An Hạ."),
        "parse": {"trang_thai": "ok", "canh_bao": ["pdf_scan", "chep_tay_tu_anh"], "phuong_phap": MANUAL},
    }


MANUAL_DOCS = {
    "2025/12/27/HCMC_TVHN_20251227.pdf": bulletin_20251227,
    "2021/06/10/khac/HCMC_TVHN_20210610_KSTVXNMT06D110062021pdf.pdf": salinity_20210610,
}
