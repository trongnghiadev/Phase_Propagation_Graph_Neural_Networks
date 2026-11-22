"""
Chuyển document (key tiếng Việt từ kttv_parser/kttv_manual) sang schema tiếng Anh,
đồng bộ quy ước với các collection khác trong vietnam_gis (snake_case tiếng Anh).
Tên riêng (trạm, sông, hồ, tỉnh) và văn bản gốc giữ nguyên tiếng Việt.
"""
import re

# key mặc định
KEYS = {
    "nguon": "source", "file": "file", "ten_file": "file_name", "thu_muc_khac": "in_other_folder",
    "la_ban_chinh": "is_primary", "so_trang": "page_count", "creator": "creator",
    "ngay_theo_ten_file": "date_from_filename",
    "don_vi_phat_hanh": "issuer", "khu_vuc": "region", "loai_ban_tin": "bulletin_type", "tieu_de": "title",
    "so_ban_tin": "bulletin_no", "so_ban_tin_stt": "bulletin_seq", "thu": "weekday",
    "ngay_ban_tin": "bulletin_date", "tin_phat_luc": "issued_at", "ngay_quan_trac": "observation_date",
    "am_lich": "lunar_date", "nam_goc": "year_name_raw", "nam_duong_lich_tuong_ung": "gregorian_year",
    "nguoi_thuc_hien": "staff", "du_bao_vien": "forecasters", "nguoi_duyet": "approver",
    "soat_tin": "reviewer", "nguoi_chiu_trach_nhiem": "responsible_person", "nguoi_ky": "signer",
    "cap_bao_dong": "alarm_levels", "tram_ap_dung": "stations", "bd1_m": "level_1_m", "bd2_m": "level_2_m", "bd3_m": "level_3_m",
    "luong_mua": "rainfall", "khoang_thoi_gian": "period", "luong_mua_mm": "rainfall_mm",
    "ho_chua": "reservoirs", "ho_chua_goc": "reservoir_raw", "song": "river", "song_goc": "river_raw",
    "tinh": "province", "tram": "station", "tram_goc": "station_raw",
    "muc_nuoc_thuong_luu_m": "upstream_water_level_m", "luu_luong_ve_ho_m3s": "inflow_m3s",
    "luu_luong_chay_may_m3s": "turbine_discharge_m3s", "luu_luong_xa_m3s": "spillway_discharge_m3s",
    "mua_mm": "rainfall_mm", "co_hieu": "flags",
    "muc_nuoc_thuc_do": "observed_water_levels", "muc_nuoc_du_bao": "forecast_water_levels",
    "dinh_trieu": "high_tides", "chan_trieu": "low_tides", "muc_nuoc_m": "water_level_m",
    "gio": "time", "thoi_diem": "datetime", "ngay": "date", "raw": "raw",
    "nhan_xet": "remarks", "canh_bao": "warning", "ghi_chu": "notes", "noi_dung_van_ban": "full_text",
    "nam": "year", "thang": "month",
    "parse": "parse", "trang_thai": "status", "phuong_phap": "method",
    # tin cảnh báo triều cường
    "noi_dung": "content", "tinh_hinh_da_qua": "past_situation", "du_bao_canh_bao": "forecast_and_warning",
    "cap_do_rui_ro": "risk_level_info", "cap_do_rui_ro_thien_tai": "disaster_risk_level",
    # khảo sát xâm nhập mặn
    "xam_nhap_man": "salinity_survey", "dot": "campaign", "so_tram": "station_count", "stt": "no",
    "vi_tri": "location", "bq": "avg", "max": "max", "min": "min", "ph": "ph", "do_man_g_l": "salinity_g_l",
    "do_dan_dien_ms_cm": "conductivity_ms_cm", "do_duc_ntu": "turbidity_ntu",
    "do_man_thang_06_2019_g_l": "salinity_2019_06_g_l", "do_man_dot2_thang_05_2021_g_l": "salinity_2021_05_round2_g_l",
    "tong_hop": "summary", "ph_binh_quan": "ph_avg", "ph_min": "ph_min", "ph_max": "ph_max",
    "do_duc_binh_quan_ntu": "turbidity_avg_ntu", "do_duc_min": "turbidity_min", "do_duc_max": "turbidity_max",
    "ec_binh_quan_ms_cm": "conductivity_avg_ms_cm", "ec_min": "conductivity_min", "ec_max": "conductivity_max",
    "gia_tri": "value",
}

# key phụ thuộc ngữ cảnh: (key_cha, key) -> key mới
CTX_KEYS = {
    ("luong_mua", "tram"): "stations",            # danh sách trạm mưa
    ("xam_nhap_man", "tram"): "stations",
    ("reservoir_item", "ho_chua"): "reservoir",   # tên hồ trong phần tử
    ("lunar", "nam"): "year_name",
    ("lunar", "ngay"): "day",
    ("value_item", "ghi_chu"): "flag",            # ghi_chu trong 1 giá trị = cờ
    ("reservoir_item", "ghi_chu"): "notes",
    ("parse", "canh_bao"): "warnings",
}

FLAGS = {"khong_mua": "no_rain", "khong_so_lieu": "no_data", "khong_co_gia_tri": "no_value",
         "khong_doc_duoc": "unparseable", "khong_xuat_hien": "not_occurred", "loi_excel": "excel_error"}
TYPES = {"du_bao_dac_trung_thuy_trieu_5_ngay": "tide_forecast_5day",
         "canh_bao_trieu_cuong": "spring_tide_warning",
         "bao_cao_khao_sat_xam_nhap_man": "salinity_intrusion_survey",
         "khong_co_lop_text": "no_text_layer", "khac": "other", "loi": "error"}
STATUS = {"ok": "ok", "co_canh_bao": "ok_with_warnings", "loi": "error", "can_xu_ly_anh": "needs_ocr"}
METHODS = {"pymupdf_find_tables+regex": "pymupdf_find_tables+regex",
           "doc_thu_cong_tu_anh_pdf": "manual_transcription_from_pdf_image"}
WARNINGS = {
    "nam_ban_tin_bi_cat_lay_tu_so_ban_tin": "year_truncated_taken_from_bulletin_no",
    "ngay_ban_tin_khac_ten_file": "bulletin_date_differs_from_filename",
    "ngay_ban_tin_lay_tu_ten_file": "bulletin_date_taken_from_filename",
    "tach_hang_bi_gop_o": "merged_table_row_split",
    "bao_dong_suy_tu_gia_tri": "alarm_levels_inferred_from_values",
    "pdf_khong_co_lop_text": "pdf_has_no_text_layer", "pdf_khong_co_text": "pdf_has_no_text_layer",
    "pdf_scan": "scanned_pdf", "chep_tay_tu_anh": "manually_transcribed_from_image",
    "khong_co_muc_nuoc_thuc_do": "no_observed_water_levels", "khong_co_du_bao": "no_forecast",
    "khong_co_ho_chua": "no_reservoirs", "khong_co_luong_mua": "no_rainfall",
    "ngay_ban_tin_khong_hop_le": "invalid_bulletin_date", "khong_tim_thay_so_ban_tin": "bulletin_no_not_found",
    "khong_nhan_dien_header_ho_chua": "reservoir_header_not_recognized",
    "nhom_du_bao_khong_co_ten_tram": "forecast_group_missing_station",
    "nhom_du_bao_nhieu_tram": "forecast_group_multiple_stations",
    "hang_ho_chua_khong_tach_duoc": "reservoir_row_unsplittable",
    "hang_muc_nuoc_la": "unexpected_water_level_row", "hang_du_bao_la": "unexpected_forecast_row",
    "find_tables_loi": "find_tables_error",
}

def _warning(w):
    code, sep, rest = w.partition(":")
    return WARNINGS.get(code, code) + (sep + rest if sep else "")

def _ctx_of(parent, key, value):
    """Ngữ cảnh truyền xuống cho các phần tử con."""
    if key == "ho_chua" and isinstance(value, list):
        return "reservoir_item"
    if key in ("dinh_trieu", "chan_trieu") or (parent == "luong_mua" and key == "tram"):
        return "value_item"
    if key == "am_lich":
        return "lunar"
    return key

def translate(o, parent=None):
    if isinstance(o, list):
        return [translate(x, parent) for x in o]
    if not isinstance(o, dict):
        return o
    out = {}
    for k, v in o.items():
        if k == "_id":
            continue
        nk = CTX_KEYS.get((parent, k)) or KEYS.get(k, k)
        if k == "loai_ban_tin":
            v = TYPES.get(v, v)
        elif k == "trang_thai":
            v = STATUS.get(v, v)
        elif k == "phuong_phap":
            v = METHODS.get(v, v)
        elif nk == "flag":
            v = FLAGS.get(v, v)
        elif k == "co_hieu":
            v = {KEYS.get(a, a): FLAGS.get(b, b) for a, b in v.items()}
        elif parent == "parse" and k == "canh_bao":
            v = [_warning(w) for w in v]
        if k != "co_hieu":
            v = translate(v, _ctx_of(parent, k, v))
        out[nk] = v
    return out

def to_english(doc):
    """doc tiếng Việt (có _id = đường dẫn file) -> doc tiếng Anh, source_file ở cấp gốc."""
    d = translate(doc)
    src = d.get("source", {})
    d = {"source_file": src.get("file", doc.get("_id")), **d}
    d["source_dataset"] = "KTTV Nam Bo - HCMC_TVHN"
    return d
