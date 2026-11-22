"""
Parser bản tin thủy văn KTTV Nam Bộ (HCMC_TVHN_*.pdf) -> JSON.

Cấu trúc PDF thay đổi theo thời gian (số trạm mưa, số hồ chứa, cột "Mưa"/"Ghi chú",
trạm dự báo, vị trí khối ghi chú, header...). Parser không dựa vào vị trí cố định:
- Dữ liệu bảng lấy bằng pymupdf find_tables(), ghép mọi hàng của mọi bảng/trang thành
  một luồng hàng, rồi nhận diện từng section theo tiêu đề và ánh xạ cột theo tên header.
- Thông tin header/footer (số bản tin, ngày, âm lịch, giờ phát tin, người ký, BĐ...)
  lấy bằng regex trên text dựng lại từ words.
"""
import re, sys, json, glob, os, datetime as dt
import pymupdf

pymupdf.TOOLS.mupdf_display_errors(False)

# ---- sửa lỗi glyph font VNI trong một số bản tin cũ ----
import unicodedata
# dấu kết hợp "lạ" sinh ra do font VNI bị map sai -> nguyên âm có dấu đúng
GLYPH_MAP = {"̭": "ấ", "̩": "ạ", "͓": "ị", "͇": "ế", "ͭ": "ụ", "ͣ": "ớ",
             "͉": "ề", "͙": "ố", "ͅ": "ẽ", "͝": "ổ", "̹": "ằ", "͋": "ế"}
# ký tự đơn bị map sai (bản tin 01/2025, 02/2017)
CHAR_MAP = str.maketrans({"ӈ": "ứ", "Ą": "á", "ͷ": "ữ", "ӆ": "ủ", "Ǥ": "ỳ", "Ƕ": "ỳ"})
GLYPH_RE = re.compile("([" + "".join(GLYPH_MAP) + "])[ ]?(?=\\w)|([" + "".join(GLYPH_MAP) + "])")
WORD_FIX = [("ngjy", "ngày")]

def fix_text(s):
    if s is None:
        return None
    s = unicodedata.normalize("NFC", s).translate(CHAR_MAP)
    s = GLYPH_RE.sub(lambda m: GLYPH_MAP[m.group(1) or m.group(2)], s)
    for a, b in WORD_FIX:
        s = s.replace(a, b)
    return re.sub(r"[ \t]+", " ", s).strip()

def clean_cell(c):
    return fix_text((c or "").replace("\n", " "))

def norm(s):
    return re.sub(r"\s+", "", (s or "")).lower()

# ---- giá trị ----
FLAGS = {"*": "khong_so_lieu", "-": "khong_mua", "ct": "khong_xuat_hien", "": None, "no data": "khong_so_lieu",
         "#value!": "loi_excel", "#ref!": "loi_excel", "#n/a": "loi_excel", "#div/0!": "loi_excel"}

# ---- chuẩn hóa tên (giữ tên gốc ở <key>_goc nếu khác) ----
NAME_NORM = {
    "tram": {"Thủ Dầu I": "Thủ Dầu Một"},
    "ho_chua": {"Sông Hoả": "Sông Hỏa"},
    "song": {"Hoả": "Hỏa", "Sông Hoả": "Hỏa", "Sông Hỏa": "Hỏa", "Sông Ray": "Ray", "Sông Xoài": "Xoài",
             "V.Cỏ Tây": "Vàm Cỏ Tây", "V.Cỏ Đông": "Vàm Cỏ Đông", "K.Đồng Điền": "Đồng Điền"},
}

def norm_names(obj):
    for key, mp in NAME_NORM.items():
        v = obj.get(key)
        if v and v in mp:
            obj[key + "_goc"] = v
            obj[key] = mp[v]
    return obj

CAN = ["Canh", "Tân", "Nhâm", "Quý", "Giáp", "Ất", "Bính", "Đinh", "Mậu", "Kỷ"]
CHI = ["Thân", "Dậu", "Tuất", "Hợi", "Tý", "Sửu", "Dần", "Mão", "Thìn", "Tỵ", "Ngọ", "Mùi"]

def can_chi(lunar_year):
    return f"{CAN[lunar_year % 10]} {CHI[lunar_year % 12]}".upper()

def num(raw, dash="khong_co_gia_tri"):
    """Trả về (value, flag). Hỗ trợ dấu phẩy thập phân. '-' = không mưa (lượng mưa) / không có giá trị."""
    r = (raw or "").strip()
    if r == "-":
        return None, dash
    if r.lower() in FLAGS:
        return None, FLAGS[r.lower()]
    s = r.replace(",", ".").replace(" ", "")
    try:
        return float(s), None
    except ValueError:
        return None, "khong_doc_duoc"

def hhmm(raw):
    """'16.00'->'16:00', '1.45'->'01:45', '4.3'->'04:30'."""
    r = (raw or "").strip()
    if r.lower() in FLAGS:
        return None
    r = re.sub(r"\.{2,}", ".", r).strip(".")
    m = re.match(r"^(\d{1,2})[.,:h]?(\d{0,2})$", r)
    if not m:
        return None
    h = int(m.group(1)); mi = m.group(2) or "0"
    mi = int(mi.ljust(2, "0"))
    if h > 24 or mi > 59:
        return None
    return f"{h:02d}:{mi:02d}"

def mk_dt(d, t):
    if d is None or t is None:
        return None
    h, m = map(int, t.split(":"))
    return dt.datetime(d.year, d.month, d.day) + dt.timedelta(hours=h, minutes=m)

def parse_ddmm(s, ref):
    """'31/12' -> date, suy năm theo ngày bản tin."""
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})(?:\s*/\s*(\d{4}))?", s or "")
    if not m:
        return None
    d, mo = int(m.group(1)), int(m.group(2))
    y = int(m.group(3)) if m.group(3) else ref.year
    if not m.group(3):
        if mo == 12 and ref.month == 1:
            y -= 1
        elif mo == 1 and ref.month == 12:
            y += 1
    try:
        return dt.date(y, mo, d)
    except ValueError:
        return None

def tide_pair(vals, d=None):
    """8 ô: [MN, giờ] x2 đỉnh, x2 chân."""
    def one(v, t):
        val, flag = num(v)
        g = hhmm(t)
        o = {"muc_nuoc_m": val, "gio": g}
        if flag:
            o["ghi_chu"] = flag
        if d is not None:
            o["thoi_diem"] = mk_dt(d, g)
        o["raw"] = [v, t]
        return o
    v = (vals + [""] * 8)[:8]
    return {
        "dinh_trieu": [one(v[0], v[1]), one(v[2], v[3])],
        "chan_trieu": [one(v[4], v[5]), one(v[6], v[7])],
    }

# ---- text dựng theo dòng từ words ----
def page_lines(page):
    rows = {}
    for w in page.get_text("words"):
        x0, y0, x1, y1, t = w[:5]
        rows.setdefault(round((y0 + y1) / 2 / 3), []).append((x0, t))
    return [" ".join(t for _, t in sorted(rows[k])) for k in sorted(rows)]

TABLE_SECTION_KEYS = {
    "obs_header": ["sốliệuthựcđo"],
    "rain_res": ["lượngmưa", "hồchứa"],
    "wl_obs": ["mựcnướcngày", "mựcnướcthựcđo"],
    "fc": ["mựcnướcdựbáo"],
}

def classify_row(cells):
    j = norm(" ".join(cells))
    first = norm(cells[0]) if cells else ""
    if "mựcnướcdựbáo" in j:
        return "fc"
    if ("mựcnướcngày" in j or "mựcnướcthựcđo" in j) and not any(re.match(r"^-?\d", c) for c in cells[2:] if c):
        return "wl_obs"
    if "lượngmưa" in j and "hồchứa" in j:
        return "rain_res"
    if "sốliệuthựcđo" in j:
        return "obs_header"
    if first.startswith("ghichú") or first.startswith("cảnhbáo") or first.startswith("nhậnxét"):
        return "notes"
    return None

KNOWN_ALARM_STATIONS = ["Phú An", "Nhà Bè", "Thủ Dầu Một"]
RAIN_ST = ["Mạc Đĩnh Chi", "Tân Sơn Hòa", "Nhà Bè", "Vũng Tàu", "Thủ Dầu Một", "Thủ Dầu I", "Củ Chi",
           "Hóc Môn", "Cần Giờ", "Bình Chánh", "Phú An"]
RES_NAMES = ["Trị An", "Thác Mơ", "Sr.P.Miêng", "Dầu Tiếng", "Sông Ray", "Đá Đen", "Sông Hoả", "Sông Hỏa"]
RES_SONG = ["Đồng Nai", "Sài Gòn", "Bé", "Ray", "Xoài", "Hỏa", "Hoả"]
VAL_TOK = r"(?:-?\d+(?:[.,]\d+)?|\*|-|ct)"

def repair_rain_res_row(joined):
    """Tách một hàng bị gộp ô: '[trạm mưa] [giá trị] [hồ] [sông] [tỉnh] mn q_ve q_may q_xa'."""
    s = joined.strip()
    rain = res = None
    for st in sorted(RAIN_ST, key=len, reverse=True):
        if s.startswith(st):
            s = s[len(st):].strip()
            mv = re.match(VAL_TOK + r"(?=\s|$)", s)
            val = mv.group(0) if mv else ""
            if mv:
                s = s[mv.end():].strip()
            rain = (st, val)
            break
    if rain is None and re.match(VAL_TOK + r"\s", s) and not any(s.startswith(r) for r in RES_NAMES):
        s = re.sub("^" + VAL_TOK + r"\s+", "", s)  # giá trị mưa mồ côi
    for rn in sorted(RES_NAMES, key=len, reverse=True):
        if s.startswith(rn):
            s = s[len(rn):].strip()
            song = next((x for x in RES_SONG if s.startswith(x + " ")), None)
            if song:
                s = s[len(song):].strip()
            mt = re.match(r"(.*?)\s+(" + VAL_TOK + r"(?:\s+" + VAL_TOK + r"){3})\s*$", s)
            if mt:
                tinh, nums = mt.group(1).strip(), mt.group(2).split()
            else:
                tinh, nums = s, []
            res = (rn, song, tinh or None, (nums + [""] * 4)[:4])
            break
    return rain, res

def parse_forecast_bulletin(path, doc, rel):
    warnings = []
    lines = []
    for p in doc:
        lines += page_lines(p)
    text = fix_text("\n".join(lines))
    flat = re.sub(r"\s+", " ", text)
    nflat = norm(flat)

    out = {}
    # ---- header ----
    m = re.search(r"Bản\s*tin\s*số\s*:?\s*(\d+)\s*/\s*(\d{4})", flat)
    if m:
        out["so_ban_tin"] = f"{m.group(1)}/{m.group(2)}"
        out["so_ban_tin_stt"] = int(m.group(1))
    else:
        warnings.append("khong_tim_thay_so_ban_tin")
    # dòng ngày: năm có thể bị cắt ("năm 202") -> lấy năm từ số bản tin
    m = re.search(r"(Chủ\s*Nhật|Thứ\s*\w+)\s*ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{0,4})", flat)
    if m:
        out["thu"] = re.sub(r"\s+", " ", m.group(1))
        y = m.group(4)
        if len(y) != 4:
            sb = re.search(r"/(\d{4})", out.get("so_ban_tin", ""))
            y = sb.group(1) if sb else None
            warnings.append("nam_ban_tin_bi_cat_lay_tu_so_ban_tin")
        try:
            out["ngay_ban_tin"] = dt.datetime(int(y), int(m.group(3)), int(m.group(2)))
        except (ValueError, TypeError):
            warnings.append("ngay_ban_tin_khong_hop_le")
    for ln in lines:
        lnf = fix_text(ln)
        m = re.match(r"^\(?\s*Ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*(.*?)\s*năm\s*(.*?)\s*\)?\s*$", lnf)
        if m:
            out["am_lich"] = {"ngay": int(m.group(1)), "thang": int(m.group(2)), "nam": None,
                              "nam_goc": m.group(4).strip() or None}
            break
    # fallback ngày bản tin từ tên file
    fm = re.search(r"_(\d{8})", os.path.basename(path))
    file_date = dt.datetime.strptime(fm.group(1), "%Y%m%d") if fm else None
    if "ngay_ban_tin" not in out:
        out["ngay_ban_tin"] = file_date
        warnings.append("ngay_ban_tin_lay_tu_ten_file")
    elif file_date and out["ngay_ban_tin"] != file_date:
        warnings.append(f"ngay_ban_tin_khac_ten_file:{file_date.date()}")
    ref = out["ngay_ban_tin"].date()
    if out.get("am_lich"):
        # tên năm âm lịch trong PDF hay bị cắt ("(N) ĐINH D") -> tính lại Can Chi
        ly = ref.year - 1 if out["am_lich"]["thang"] > ref.month else ref.year
        out["am_lich"]["nam"] = can_chi(ly)
        out["am_lich"]["nam_duong_lich_tuong_ung"] = ly

    m = re.search(r"Tin\s*phát\s*lúc\s*:?\s*(\d{1,2})\s*[:h]\s*(\d{2})", flat)
    if m:
        out["tin_phat_luc"] = dt.datetime(ref.year, ref.month, ref.day, int(m.group(1)), int(m.group(2)))
    nguoi = {}
    for key, pat in [
        ("du_bao_vien", r"Dự\s*báo\s*viên\s*:\s*(.+?)(?=\s+Người\s*duyệt|\s+Soát\s*tin|$)"),
        ("nguoi_duyet", r"Người\s*duyệt\s*:\s*(.+?)(?=\s+Dự\s*báo\s*viên|\s+Soát\s*tin|$)"),
        ("soat_tin", r"Soát\s*tin\s*:\s*(.+?)(?=\s+Dự\s*báo\s*viên|\s+Người\s*duyệt|$)"),
        ("nguoi_chiu_trach_nhiem", r"Người\s*chịu\s*trách\s*nhiệm\s*ban\s*hành\s*bản\s*tin\s*:\s*(.+?)$"),
    ]:
        for ln in lines:
            mm = re.search(pat, fix_text(ln))
            if mm:
                v = mm.group(1).strip()
                nguoi[key] = [x.strip() for x in re.split(r",", v) if x.strip()] if key == "du_bao_vien" else v
                break
    if nguoi:
        out["nguoi_thuc_hien"] = nguoi

    # cấp báo động
    bd = {}
    for lv, key in [("III", "bd3_m"), ("II", "bd2_m"), ("I", "bd1_m")]:
        mm = re.search(r"BĐ\s*" + lv + r"\s*:\s*(?:[^\d]{0,90}?)(\d+[.,]\d+)\s*m", flat)
        if mm:
            bd[key] = float(mm.group(1).replace(",", "."))
    if bd and len(bd) < 3:
        # nhãn "BĐ x" có thể bị mất: lấy 3 giá trị "x.xx m" trong vùng ghi chú, sắp tăng dần
        gi = flat.find("Ghi chú")
        mdb = flat.find("MỰC NƯỚC DỰ BÁO", gi) if gi >= 0 else -1
        win = flat[gi: mdb if mdb > gi else gi + 400] if gi >= 0 else ""
        vals = sorted(float(v.replace(",", ".")) for v in re.findall(r"(\d[.,]\d{2})\s*m\b", win))
        if len(vals) == 3:
            bd.update({"bd1_m": vals[0], "bd2_m": vals[1], "bd3_m": vals[2]})
            warnings.append("bao_dong_suy_tu_gia_tri")
    if bd:
        i = flat.find("Cấp báo động tại")
        win = flat[i:i + 200] if i >= 0 else ""
        tr = [s for s in ["Phú An", "Nhà Bè"] if s in win]
        if "Thủ" in win and "Dầu Một" in win:
            tr.append("Thủ Dầu Một")
        bd["tram_ap_dung"] = tr or ["Phú An"]
        out["cap_bao_dong"] = {k: bd.get(k) for k in ["tram_ap_dung", "bd1_m", "bd2_m", "bd3_m"]}

    # ---- bảng ----
    rows, raws = [], []
    for p in doc:
        try:
            tabs = p.find_tables()
        except Exception as e:
            warnings.append(f"find_tables_loi:{e}")
            continue
        for t in tabs.tables:
            for r in t.extract():
                cells = [clean_cell(c) for c in r]
                if any(cells):
                    rows.append(cells)
                    raws.append([unicodedata.normalize("NFC", c or "") for c in r])

    rain, res, wl, fc = [], [], [], []
    notes = {}
    state = None
    hdr_map = {}
    ngay_quan_trac = None
    rain_period = None
    cur_fc_station = None
    i = 0
    while i < len(rows):
        cells = rows[i]
        kind = classify_row(cells)
        if kind == "obs_header":
            i += 1; continue
        if kind == "rain_res":
            joined = " ".join(cells)
            mm = re.search(r"hồ chứa ngày\s*(\d{1,2}/\d{1,2}/\d{4})", joined)
            if mm:
                ngay_quan_trac = parse_ddmm(mm.group(1), ref)
            state = "rain_res_hdr"; i += 1; continue
        if kind == "wl_obs":
            joined = " ".join(cells)
            mm = re.search(r"(\d{1,2}/\d{1,2}/\d{4})", joined)
            if mm:
                d_wl = parse_ddmm(mm.group(1), ref)
                if ngay_quan_trac is None:
                    ngay_quan_trac = d_wl
            state = "wl_hdr"; i += 1; continue
        if kind == "fc":
            state = "fc_hdr"; cur_fc_station = None; i += 1; continue
        if kind == "notes":
            key = "canh_bao" if norm(cells[0]).startswith("cảnhbáo") else ("nhan_xet" if norm(cells[0]).startswith("nhậnxét") else "ghi_chu")
            val = " ".join(c for c in cells[1:] if c).strip()
            if not val:
                val = re.sub(r"^(Ghi chú|Cảnh báo|Nhận xét)\s*:\s*", "", cells[0]).strip()
            notes[key] = (notes.get(key, "") + " " + val).strip()
            state = "notes" if key == "ghi_chu" else state
            i += 1; continue

        if state == "rain_res_hdr":
            # hàng header cột
            hdr_map = {}
            for ci, c in enumerate(cells):
                n = norm(c)
                if n.startswith("trạm") and "rain_st" not in hdr_map: hdr_map["rain_st"] = ci
                elif n.startswith("từ7h") or "đến7h" in n:
                    hdr_map["rain_val"] = ci
                    rain_period = c
                elif n == "hồchứa": hdr_map["res"] = ci
                elif n == "sông": hdr_map["song"] = ci
                elif n == "tỉnh": hdr_map["tinh"] = ci
                elif n.startswith("mựcnước"): hdr_map["mn"] = ci
                elif "vềhồ" in n: hdr_map["q_ve"] = ci
                elif "chạy" in n: hdr_map["q_may"] = ci
                elif "xả" in n: hdr_map["q_xa"] = ci
                elif n.startswith("mưa"): hdr_map["mua"] = ci
                elif n.startswith("ghichú"): hdr_map["ghichu"] = ci
            if "res" not in hdr_map:
                warnings.append("khong_nhan_dien_header_ho_chua")
                hdr_map = {"rain_st": 0, "rain_val": 1, "res": 2, "song": 3, "tinh": 4, "mn": 5, "q_ve": 6, "q_may": 7, "q_xa": 8}
            state = "rain_res"; i += 1; continue
        if state == "rain_res":
            g = lambda k: cells[hdr_map[k]] if k in hdr_map and hdr_map[k] < len(cells) else ""
            rg = lambda k: (raws[i][hdr_map[k]] or "") if k in hdr_map and hdr_map[k] < len(raws[i]) else ""
            joined = " ".join(c for c in cells if c)
            if "mn" in hdr_map and not g("mn") and not g("q_ve") and (
                    re.search(r"\d", g("rain_st") + " " + g("res")) or re.search(r"[A-Za-zÀ-ỹ]", g("rain_val"))):
                # hàng bị gộp ô -> tách theo token
                rr, rs = repair_rain_res_row(joined)
                if rr:
                    v, flag = num(rr[1], dash="khong_mua")
                    o = {"tram": rr[0], "luong_mua_mm": v, "raw": rr[1]}
                    if flag: o["ghi_chu"] = flag
                    rain.append(o)
                if rs:
                    o = {"ho_chua": rs[0], "song": rs[1], "tinh": rs[2]}
                    for f, rv in zip(["muc_nuoc_thuong_luu_m", "luu_luong_ve_ho_m3s", "luu_luong_chay_may_m3s", "luu_luong_xa_m3s"], rs[3]):
                        v, flag = num(rv)
                        o[f] = v
                        if flag: o.setdefault("co_hieu", {})[f] = flag
                    res.append(o)
                if not rr and not rs:
                    warnings.append(f"hang_ho_chua_khong_tach_duoc:{joined}")
                else:
                    warnings.append("tach_hang_bi_gop_o")
                i += 1; continue
            st_raw, val_raw = rg("rain_st"), rg("rain_val")
            st_parts = [x.strip() for x in st_raw.split("\n") if x.strip()]
            val_parts = [x.strip() for x in val_raw.split("\n") if x.strip()]
            if len(st_parts) > 1 and len(st_parts) == len(val_parts):
                pairs = list(zip(st_parts, val_parts))   # ô gộp nhiều trạm (xuống dòng)
            else:
                pairs = [(g("rain_st"), g("rain_val"))]
            for st, rv in pairs:
                st, rv = fix_text(st), fix_text(rv)
                if st and not norm(st).startswith("trạm"):
                    v, flag = num(rv, dash="khong_mua")
                    o = {"tram": st, "luong_mua_mm": v, "raw": rv}
                    if flag: o["ghi_chu"] = flag
                    rain.append(o)
            rn = g("res")
            if rn:
                o = {"ho_chua": rn, "song": g("song") or None, "tinh": g("tinh") or None}
                for k, f in [("mn", "muc_nuoc_thuong_luu_m"), ("q_ve", "luu_luong_ve_ho_m3s"),
                             ("q_may", "luu_luong_chay_may_m3s"), ("q_xa", "luu_luong_xa_m3s"), ("mua", "mua_mm")]:
                    if k in hdr_map:
                        v, flag = num(g(k), dash="khong_mua" if k == "mua" else "khong_co_gia_tri")
                        o[f] = v
                        if flag: o.setdefault("co_hieu", {})[f] = flag
                if g("ghichu"): o["ghi_chu"] = g("ghichu")
                res.append(o)
            i += 1; continue
        if state == "wl_hdr":
            # 2 hàng header (Trạm/Sông/Đỉnh triều..., Mực nước/giờ...)
            if norm(cells[0]).startswith("trạm") or "giờ" in norm(" ".join(cells)):
                i += 1; continue
            state = "wl"
        if state == "wl":
            if cells[0] and len(cells) >= 10:
                o = {"tram": cells[0], "song": cells[1] or None}
                o.update(tide_pair(cells[2:10], ngay_quan_trac))
                wl.append(o)
            elif cells[0]:
                warnings.append(f"hang_muc_nuoc_la:{cells}")
            i += 1; continue
        if state == "fc_hdr":
            if norm(cells[0]).startswith("trạm") or "giờ" in norm(" ".join(cells)):
                i += 1; continue
            state = "fc"
        if state == "fc":
            if len(cells) >= 10 and re.search(r"\d{1,2}/\d{1,2}", cells[1] or ""):
                d = parse_ddmm(cells[1], ref)
                o = {"tram": None, "_tram_o": cells[0] or None, "ngay": dt.datetime(d.year, d.month, d.day) if d else None}
                o.update(tide_pair(cells[2:10], d))
                fc.append(o)
            elif any(cells):
                warnings.append(f"hang_du_bao_la:{cells}")
            i += 1; continue
        i += 1

    # trạm dự báo là ô gộp, tên có thể nằm ở hàng giữa nhóm -> nhóm theo chuỗi ngày
    groups, prev = [], None
    for x in fc:
        if prev is None or x["ngay"] is None or prev is None or x["ngay"] <= prev:
            groups.append([])
        groups[-1].append(x)
        prev = x["ngay"]
    for g in groups:
        names = [x["_tram_o"] for x in g if x["_tram_o"]]
        if len(set(names)) > 1:
            warnings.append(f"nhom_du_bao_nhieu_tram:{names}")
        name = names[0] if names else None
        if name is None:
            warnings.append("nhom_du_bao_khong_co_ten_tram")
        for x in g:
            x["tram"] = name
    for x in fc:
        x.pop("_tram_o", None)

    # nhận xét / cảnh báo nếu không nằm trong bảng
    for key, lab in [("nhan_xet", "Nhận xét"), ("canh_bao", "Cảnh báo")]:
        if key not in notes:
            mm = re.search(lab + r"\s*:\s*(.*?)(?=\s+Tin\s*phát\s*lúc|\s+Ghi\s*chú\s*:|$)", flat)
            if mm:
                notes[key] = mm.group(1).strip()
    for k in list(notes):
        if not notes[k]:
            notes[k] = None
    for o in rain + res + wl + fc:
        norm_names(o)

    out.update({
        "ngay_quan_trac": dt.datetime(ngay_quan_trac.year, ngay_quan_trac.month, ngay_quan_trac.day) if ngay_quan_trac else None,
        "luong_mua": {"khoang_thoi_gian": rain_period, "tram": rain},
        "ho_chua": res,
        "muc_nuoc_thuc_do": wl,
        "muc_nuoc_du_bao": fc,
        "nhan_xet": notes.get("nhan_xet"),
        "canh_bao": notes.get("canh_bao"),
        "ghi_chu": notes.get("ghi_chu"),
    })
    if not wl: warnings.append("khong_co_muc_nuoc_thuc_do")
    if not fc: warnings.append("khong_co_du_bao")
    if not res: warnings.append("khong_co_ho_chua")
    if not rain: warnings.append("khong_co_luong_mua")
    return out, warnings, text


def parse_warning_text(path, doc, rel):
    """Tin cảnh báo triều cường dạng văn bản."""
    lines = []
    for p in doc:
        lines += page_lines(p)
    text = fix_text("\n".join(lines))
    flat = re.sub(r"\s+", " ", text)
    out = {}
    m = re.search(r"ngày\s*(\d{1,2})\s*tháng\s*(\d{1,2})\s*năm\s*(\d{4})", flat)
    if m:
        out["ngay_ban_tin"] = dt.datetime(int(m.group(3)), int(m.group(2)), int(m.group(1)))
    m = re.search(r"Bản\s*tin\s*số\s*:\s*(\S+)", flat)
    if m: out["so_ban_tin"] = m.group(1)
    m = re.search(r"Tin\s*phát\s*lúc\s*:\s*(\d{1,2})h(\d{2})\s*ngày\s*(\d{1,2})/(\d{1,2})/(\d{4})", flat)
    if m:
        out["tin_phat_luc"] = dt.datetime(int(m.group(5)), int(m.group(4)), int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r"TIN\s+CẢNH\s+BÁO\s+(.+?)\s+Bản", flat)
    if m: out["tieu_de"] = "TIN CẢNH BÁO " + m.group(1)
    secs = {}
    for key, a, b in [("tinh_hinh_da_qua", r"1\.\s*Tình\s*hình[^.]*?qua", r"2\.\s*Dự\s*báo"),
                      ("du_bao_canh_bao", r"2\.\s*Dự\s*báo", r"3\.\s*Thông\s*tin"),
                      ("cap_do_rui_ro", r"3\.\s*Thông\s*tin", r"Tin\s*phát")]:
        mm = re.search(a + r"[^:]*?:?\s*(.*?)\s*(?=" + b + ")", flat)
        if mm: secs[key] = mm.group(1).strip()
    out["noi_dung"] = secs
    mm = re.search(r"cấp\s*độ\s*(\d)", secs.get("cap_do_rui_ro", ""))
    if mm: out["cap_do_rui_ro_thien_tai"] = int(mm.group(1))
    mm = re.search(r"ban\s*hành\s*bản\s*tin\s*(?:\S+\s+)?([A-ZĐ][^\n=]+?)\s*=", flat)
    if mm: out["nguoi_thuc_hien"] = {"nguoi_chiu_trach_nhiem": mm.group(1).strip()}
    return out, [], text


def parse_file(path, root):
    rel = os.path.relpath(path, root).replace("\\", "/")
    doc = pymupdf.open(path)
    fm = re.search(r"_(\d{8})", os.path.basename(path))
    base = {
        "_id": rel,
        "nguon": {
            "file": rel,
            "ten_file": os.path.basename(path),
            "thu_muc_khac": "/khac/" in rel,
            "so_trang": doc.page_count,
            "creator": doc.metadata.get("creator") or None,
            "ngay_theo_ten_file": dt.datetime.strptime(fm.group(1), "%Y%m%d") if fm else None,
        },
        "don_vi_phat_hanh": "Đài KTTV khu vực Nam Bộ",
        "khu_vuc": "TP. Hồ Chí Minh",
    }
    alltext = "".join(p.get_text() for p in doc)
    if len(alltext.strip()) < 50:
        base.update({"loai_ban_tin": "khong_co_lop_text", "parse": {"trang_thai": "can_xu_ly_anh", "canh_bao": ["pdf_khong_co_text"]}})
        return base
    nflat = norm(alltext)
    if "đặctrưngthủytriều" in nflat or "mựcnướcdựbáo" in nflat:
        data, warns, text = parse_forecast_bulletin(path, doc, rel)
        base["loai_ban_tin"] = "du_bao_dac_trung_thuy_trieu_5_ngay"
        m = re.search(r"BẢN\s*TIN\s*DỰ\s*BÁO\s*ĐẶC\s*TRƯNG\s*THỦY\s*TRIỀU\s*(\d+)\s*NGÀY", re.sub(r"\s+", " ", text))
        base["tieu_de"] = f"BẢN TIN DỰ BÁO ĐẶC TRƯNG THỦY TRIỀU {m.group(1)} NGÀY" if m else None
        m = re.search(r"(ĐÀI\s+KHÍ\s+TƯỢNG.+?)(?:\n|Địa)", text)
        if m: base["don_vi_phat_hanh"] = re.sub(r"\s+", " ", m.group(1)).strip()
    elif "cảnhbáotriềucường" in nflat:
        data, warns, text = parse_warning_text(path, doc, rel)
        base["loai_ban_tin"] = "canh_bao_trieu_cuong"
    else:
        data, warns, text = {}, ["loai_ban_tin_khong_xac_dinh"], fix_text(alltext)
        base["loai_ban_tin"] = "khac"
    base.update(data)
    base["noi_dung_van_ban"] = text
    base["parse"] = {"trang_thai": "ok" if not warns else "co_canh_bao", "canh_bao": warns, "phuong_phap": "pymupdf_find_tables+regex"}
    return base


def to_json(o):
    if isinstance(o, (dt.datetime, dt.date)):
        return {"$date": (o if isinstance(o, dt.datetime) else dt.datetime(o.year, o.month, o.day)).strftime("%Y-%m-%dT%H:%M:%S") + "+07:00"}
    raise TypeError(type(o))


def _work(args):
    f, root = args
    try:
        return parse_file(f, root)
    except Exception as e:
        rel = os.path.relpath(f, root).replace("\\", "/")
        return {"_id": rel, "nguon": {"file": rel}, "loai_ban_tin": "loi",
                "parse": {"trang_thai": "loi", "canh_bao": [repr(e)]}}


if __name__ == "__main__":
    # python kttv_parser.py <thu_muc_KTTV> <file_json_ra> [file.pdf ...]
    import contextlib, io
    from multiprocessing import Pool
    root, out_path = sys.argv[1], sys.argv[2]
    files = sys.argv[3:] or sorted(glob.glob(os.path.join(root, "**", "*.pdf"), recursive=True))
    with contextlib.redirect_stdout(io.StringIO()):
        with Pool() as pool:
            docs = pool.map(_work, [(f, root) for f in files], chunksize=8)
    docs.sort(key=lambda d: d["_id"])
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(docs, fh, default=to_json, ensure_ascii=False, indent=1)
    from collections import Counter
    print("so file:", len(docs))
    print(Counter((d["loai_ban_tin"], d["parse"]["trang_thai"]) for d in docs))
