from datetime import datetime, timezone

import pytest

from vngis import qc_rules as R

H = 3600
T0 = int(datetime(2024, 3, 10, 0, 0, tzinfo=timezone.utc).timestamp()) - 7 * H  # 00:00 giờ VN 10/03/2024
NOW = datetime(2026, 11, 23, tzinfo=timezone.utc)


def el(station, kind, h, value, qc="ok", placeholder=False, sf="2024/03/11/a.pdf"):
    return {"station": station, "kind": kind, "ts": T0 + int(h * H), "ts_placeholder": placeholder,
            "value": value, "qc": qc, "source_file": sf}


# ---------------------------------------------------------------- cấu hình ----
def test_mac_dinh_khop_config(repo_config_dir):
    import yaml
    cfg = yaml.safe_load((repo_config_dir / "default.yaml").read_text(encoding="utf-8"))
    assert cfg["qc"] == R.DEFAULT_PARAMS


def test_config_doc_duoc_qc(repo_config_dir):
    from vngis.config import load_config
    cfg = load_config(env="dev", config_dir=repo_config_dir, environ={})
    assert cfg.qc.reference_station == R.DEFAULT_PARAMS["reference_station"]
    assert cfg.qc.r1.physical_m["VUNG_TAU"] == tuple(R.DEFAULT_PARAMS["r1"]["physical_m"]["VUNG_TAU"])


def test_params_with_gop_de_quy():
    p = R.params_with({"r1": {"margin": 0.5}})
    assert p["r1"]["margin"] == 0.5 and p["r1"]["p_low"] == 0.001
    assert R.DEFAULT_PARAMS["r1"]["margin"] == 0.1


# ---------------------------------------------------------------- thống kê ----
def test_percentile_noi_suy_tuyen_tinh():
    s = [1.0, 2.0, 3.0, 4.0]
    assert R.percentile(s, 0) == 1.0 and R.percentile(s, 1) == 4.0
    assert R.percentile(s, 0.5) == pytest.approx(2.5)
    assert R.percentile([7.0], 0.999) == 7.0
    with pytest.raises(ValueError):
        R.percentile([], 0.5)
    with pytest.raises(ValueError):
        R.percentile(s, 1.5)


# ---------------------------------------------------------------- R1 ----
def test_r1_bien_hoa_11_18():
    b = R.r1_bounds("BIEN_HOA", 0.2288, 2.0913, R.DEFAULT_PARAMS["r1"])
    h = R.r1_check(11.18, b)
    assert h["rule"] == "R1" and "ngưỡng vật lý" in h["detail"]
    assert R.r1_check(2.0, b) is None


def test_r1_bien_dung_bang_khong_gan_co():
    b = R.r1_bounds("X", 0.0, 1.0, {"margin": 0.1, "physical_m": {}})
    assert (b["lo"], b["hi"]) == (-0.1, 1.1)
    assert R.r1_check(1.1, b) is None and R.r1_check(-0.1, b) is None
    assert R.r1_check(1.1001, b) is not None and R.r1_check(-0.1001, b) is not None
    assert R.r1_check(None, b) is None and R.r1_check(5.0, None) is None


def test_r1_nguong_vat_ly_rieng_vung_tau():
    phys = R.DEFAULT_PARAMS["r1"]["physical_m"]
    assert R.physical_limits("VUNG_TAU", phys) == (-1.0, 5.0)
    assert R.physical_limits("PHU_AN", phys) == (-3.5, 3.5)
    # 4,2 m hợp lệ ở Vũng Tàu (mốc Hải đồ) nhưng vượt ngưỡng vật lý của trạm sông
    vt = R.r1_bounds("VUNG_TAU", 0.0, 4.4, {"margin": 0.1, "physical_m": phys})
    river = R.r1_bounds("PHU_AN", 0.0, 4.4, {"margin": 0.1, "physical_m": phys})
    assert R.r1_check(4.2, vt) is None
    assert "ngưỡng vật lý" in R.r1_check(4.2, river)["detail"]
    with pytest.raises(ValueError):
        R.physical_limits("A", {"A": [1, 1]})


# ---------------------------------------------------------------- R2 ----
def test_r2_dinh_thap_hon_chan():
    out = R.r2_check([("h1", 1.0), ("h2", 0.2)], [("l1", 0.5), ("l2", -1.0)])
    assert set(out) == {"h2", "l1"}
    assert out["h2"]["metric"]["other"] == 0.5 and out["l1"]["metric"]["other"] == 0.2


def test_r2_bang_nhau_va_nguong():
    assert R.r2_check([("h", 0.5)], [("l", 0.5)]) == {}
    assert R.r2_check([("h", 0.45)], [("l", 0.5)], min_gap_m=0.1) == {}
    assert set(R.r2_check([("h", 0.35)], [("l", 0.5)], min_gap_m=0.1)) == {"h", "l"}


def test_r2_thieu_dinh_hoac_chan_hoac_null():
    assert R.r2_check([("h", None)], [("l", 2.0)]) == {}
    assert R.r2_check([], [("l", 2.0)]) == {}
    assert R.r2_check([("h", 0.1)], [("l", None), ("l2", None)]) == {}


# ---------------------------------------------------------------- R3 ----
def test_prev_reference_gan_nhat_va_khoang():
    ref = [0, 10 * H, 22 * H]
    assert R.prev_reference(ref, 12 * H, 14 * H) == 10 * H
    assert R.prev_reference(ref, 10 * H, 14 * H) == 10 * H            # đúng bằng giờ tham chiếu
    assert R.prev_reference(ref, -1, 14 * H) is None                   # không có đỉnh trước
    assert R.prev_reference([0], 15 * H, 14 * H) is None               # xa quá max_gap
    assert R.prev_reference([0], 14 * H, 14 * H) == 0                  # đúng bằng max_gap


def test_prev_reference_theo_do_tre_ky_vong():
    ref = [0, 12 * H]
    # đỉnh lúc 13h: gần nhất trước đó là 12h (trễ 1h); kỳ vọng trễ 13h → chọn 0h
    assert R.prev_reference(ref, 13 * H, 14 * H) == 12 * H
    assert R.prev_reference(ref, 13 * H, 14 * H, expected_lag_s=13 * H) == 0
    assert R.prev_reference(ref, 13 * H, 14 * H, expected_lag_s=1.5 * H) == 12 * H


def test_lag_stats_va_gioi_han():
    st = R.lag_stats([1, 2, 2, 2, 3])
    assert (st["n"], st["median"], st["q1"], st["q3"], st["iqr"]) == (5, 2, 2, 2, 0)
    r3 = {"iqr_k": 3.0, "min_dev_h": 3.0, "min_samples": 5}
    assert R.r3_limit(st, r3) == 3.0                                   # IQR = 0 → dùng min_dev_h
    assert R.r3_limit({**st, "iqr": 2.0}, r3) == 6.0
    assert R.r3_limit({**st, "n": 4}, r3) is None                      # không đủ mẫu
    assert R.lag_stats([])["median"] is None


def test_r3_check_bien():
    st = {"n": 500, "median": 2.0, "q1": 1.75, "q3": 2.25, "iqr": 0.5}
    r3 = {"iqr_k": 3.0, "min_dev_h": 3.0, "min_samples": 100}
    assert R.r3_check(5.0, 0, st, r3) is None                          # lệch đúng 3h: không gắn
    assert R.r3_check(5.25, 0, st, r3)["rule"] == "R3"
    assert R.r3_check(-1.25, 0, st, r3)["rule"] == "R3"
    assert R.r3_check(None, None, st, r3) is None


def test_reference_day_ok():
    r3 = {"min_ref_peaks_per_day": 2}
    days = {R.vn_day(T0): 2, R.vn_day(T0 - 24 * H): 1}
    assert R.reference_day_ok(T0 + 10 * H, 2.0, days, r3)
    assert not R.reference_day_ok(T0 + 1 * H, 2.0, days, r3)           # tham chiếu kỳ vọng rơi vào ngày trước
    assert R.reference_day_ok(T0 + 1 * H, 2.0, days, {"min_ref_peaks_per_day": 0})


# ---------------------------------------------------------------- nhóm ----
def _ctx(elements, params=None):
    p = params or R.params_with({"r3": {"min_samples": 3}})
    bounds = {}
    for k in {(e["station"], e["kind"]) for e in elements}:
        bounds[k] = R.r1_bounds(k[0], -2.0, 2.0, p["r1"]) if k[0] != "VUNG_TAU" else R.r1_bounds(k[0], 0, 4.4, p["r1"])
    ref, days = R.reference_peaks(elements, p["reference_station"])
    stats = R.build_lag_stats(R.r3_candidates(elements, p["reference_station"]), ref, p["r3"], days)
    return {"params": p, "bounds": bounds, "lag_stats": stats, "ref": ref, "ref_days": days}


def test_evaluate_group_ca_ba_quy_tac():
    vt = [el("VUNG_TAU", "high", 2, 3.9), el("VUNG_TAU", "high", 14.5, 3.8),
          el("VUNG_TAU", "low", 8, 1.0), el("VUNG_TAU", "low", 20, 1.2)]
    pa = [el("PHU_AN", "high", 4.5, 1.2), el("PHU_AN", "high", 17, 1.1),
          el("PHU_AN", "low", 10, -2.0), el("PHU_AN", "low", 22, -1.5)]
    # thêm ngày khác để đủ mẫu độ trễ (trễ 2,5h)
    more = []
    for d in range(1, 4):
        more += [el("VUNG_TAU", "high", 24 * d + 2, 3.9, sf=f"d{d}"), el("VUNG_TAU", "high", 24 * d + 14.5, 3.8, sf=f"d{d}"),
                 el("PHU_AN", "high", 24 * d + 4.5, 1.2, sf=f"d{d}"), el("PHU_AN", "high", 24 * d + 17, 1.1, sf=f"d{d}")]
    ctx = _ctx(vt + pa + more)
    assert ctx["lag_stats"]["PHU_AN"]["median"] == 2.5
    assert R.evaluate_group(pa, ctx) == []
    bad = [el("PHU_AN", "high", 4.5, 9.0),                 # R1
           el("PHU_AN", "high", 22.0, -2.5),               # R2 (thấp hơn chân -1.5), R1 (< -2.4); R3 (trễ 7.5h)
           el("PHU_AN", "low", 10, -2.0), el("PHU_AN", "low", 23, -1.5)]
    out = {(h["kind"], h["ts"]): [r["rule"] for r in h["rules"]] for h in R.evaluate_group(bad, ctx)}
    assert out[("high", T0 + int(4.5 * H))] == ["R1"]
    assert out[("high", T0 + 22 * H)] == ["R1", "R2", "R3"]
    assert out[("low", T0 + 23 * H)] == ["R2"]              # chân -1.5 cao hơn đỉnh thấp nhất -2.5


def test_r3_khong_ap_cho_vung_tau_gio_gia_hoac_qc_khac():
    p = R.DEFAULT_PARAMS
    assert not R.r3_applies(el("VUNG_TAU", "high", 2, 3.0), p["reference_station"])
    assert not R.r3_applies(el("PHU_AN", "high", 2, 1.0, placeholder=True), p["reference_station"])
    assert not R.r3_applies(el("PHU_AN", "high", 2, 1.0, qc="bad_time"), p["reference_station"])
    assert not R.r3_applies(el("PHU_AN", "low", 2, 1.0), p["reference_station"])
    assert not R.r3_applies(el("PHU_AN", "high", 2, None, qc="not_occurred"), p["reference_station"])
    assert R.r3_applies(el("PHU_AN", "high", 2, 1.0), p["reference_station"])


# ---------------------------------------------------------------- khóa, hiệu lực ----
def test_flag_id_on_dinh():
    assert R.flag_id("BIEN_HOA", "high", T0) == "tide_extremes:BIEN_HOA:high:2024-03-09T17:00:00Z"
    assert R.flag_id("BIEN_HOA", "high", datetime.fromtimestamp(T0, tz=timezone.utc)) == R.flag_id("BIEN_HOA", "high", T0)
    with pytest.raises(ValueError):
        R.flag_id("X", "peak", T0)


def test_effective():
    assert R.effective(1.2, "ok", None) == (1.2, "ok")
    assert R.effective(1.2, "ok", "suspect") == (1.2, "ok")
    assert R.effective(1.2, "ok", "confirmed_ok") == (1.2, "ok")
    assert R.effective(11.18, "ok", "rejected") == (None, "rejected")
    assert R.effective(None, "missing", "corrected", 1.18) == (1.18, "corrected")
    with pytest.raises(ValueError):
        R.effective(1.0, "ok", "corrected", None)


@pytest.mark.parametrize("args", [
    ("bad", None, "r", "u"), ("corrected", None, "r", "u"), ("corrected", True, "r", "u"),
    ("corrected", float("nan"), "r", "u"), ("rejected", 1.0, "r", "u"), ("rejected", None, " ", "u"),
    ("rejected", None, "r", ""),
])
def test_validate_decision_sai(args):
    with pytest.raises(ValueError):
        R.validate_decision(*args)


def test_validate_decision_dung():
    R.validate_decision("corrected", 1.18, "đọc lại PDF", "qc-reviewer")
    R.validate_decision("suspect", None, "bỏ quyết định", "qc-reviewer")


# ---------------------------------------------------------------- gộp vào qc_flags ----
def hit(rules=("R1",), value=11.18):
    return {"station": "BIEN_HOA", "kind": "high", "ts": T0, "ts_placeholder": False, "value": value,
            "source_file": "f.pdf", "rules": [{"rule": r, "detail": "d", "metric": {}} for r in rules]}


def test_plan_insert_moi():
    op = R.plan_one(None, hit(), "run1", NOW, 1)
    assert op["op"] == "insert"
    d = op["doc"]
    assert (d["status"], d["origin"], d["version"], d["first_flagged_run"]) == ("suspect", "auto", 1, "run1")


def test_plan_khong_doi_chi_touch():
    d = R.new_flag_doc(hit(), "run1", NOW, 1)
    assert R.plan_one(d, hit(), "run2", NOW, 1) == {"op": "touch", "id": d["_id"], "set": {"last_seen_run": "run2"}}
    assert R.plan_one({**d, "last_seen_run": "run2"}, hit(), "run2", NOW, 1) is None


def test_plan_doi_quy_tac_tang_version_khong_dung_quyet_dinh():
    d = {**R.new_flag_doc(hit(), "run1", NOW, 1), "status": "rejected", "reason": "sai số", "decided_by": "x",
         "decided_at": NOW}
    op = R.plan_one(d, hit(("R1", "R2"), value=11.2), "run2", NOW, 1)
    assert op["op"] == "update" and op["version"] == 1
    assert not set(op["set"]) & set(R.DECISION_FIELDS)
    assert op["set"]["value_mismatch"] is True and op["set"]["current_value"] == 11.2
    assert "value" not in op["set"]                                     # giữ giá trị lúc quyết định


def test_plan_chua_quyet_dinh_cap_nhat_value():
    d = R.new_flag_doc(hit(), "run1", NOW, 1)
    op = R.plan_one(d, hit(value=11.2), "run2", NOW, 1)
    assert op["set"]["value"] == 11.2


def test_plan_mismatch_so_voi_decided_value():
    """Cờ đã quyết định: mismatch so GIÁ TRỊ QUYẾT ĐỊNH GẮN VÀO (`decided_value`), không phải `value`."""
    d = {**R.new_flag_doc(hit(), "run1", NOW, 1), "status": "rejected", "reason": "sai", "decided_by": "x",
         "decided_at": NOW, "decided_value": 11.18}
    # giá trị gốc hiện tại khác giá trị đã quyết định → mismatch
    op = R.plan_one(d, hit(value=1.18), "run2", NOW, 1)
    assert op["set"]["value_mismatch"] is True and op["set"]["current_value"] == 1.18
    # giá trị gốc hiện tại BẰNG giá trị đã quyết định → không mismatch
    op = R.plan_one({**d, "decided_value": 1.18}, hit(value=1.18), "run2", NOW, 1)
    assert "value_mismatch" not in op.get("set", {})


def test_plan_khong_gan_lai_mismatch_cho_gia_tri_cu():
    """G0 chưa kịp cập nhật: giá trị quan sát còn là giá trị LÚC GẮN CỜ (`value`) → không gắn lại mismatch.

    Nếu không có quy tắc này thì mỗi lần chạy QC lại sẽ gắn lại `value_mismatch` và cờ mắc kẹt, không có
    đường thoát cho người kiểm tra."""
    d = {**R.new_flag_doc(hit(), "run1", NOW, 1), "status": "rejected", "reason": "sai", "decided_by": "x",
         "decided_at": NOW, "decided_value": 1.18, "value_mismatch": False, "current_value": 1.18}
    # quan sát 11.18 = giá trị lúc gắn cờ (G0 chưa cập nhật) → KHÔNG gắn mismatch
    op = R.plan_one(d, hit(value=11.18), "run2", NOW, 1)
    assert "value_mismatch" not in op.get("set", {})
    # quan sát 2.5 = giá trị MỚI thật sự → gắn mismatch
    op = R.plan_one(d, hit(value=2.5), "run2", NOW, 1)
    assert op["set"]["value_mismatch"] is True and op["set"]["current_value"] == 2.5


def test_plan_co_decided_value_thi_van_so_voi_value():
    """Cờ có `decided_value` nhưng không có `current_value` (chưa từng báo lệch) → so theo `decided_value`."""
    d = {**R.new_flag_doc(hit(), "run1", NOW, 1), "status": "confirmed_ok", "reason": "ok", "decided_by": "x",
         "decided_at": NOW, "decided_value": 1.18}
    op = R.plan_one(d, hit(value=11.18), "run2", NOW, 1)
    assert op["set"]["value_mismatch"] is True and op["set"]["current_value"] == 11.18


def test_plan_het_kich_hoat():
    d = R.new_flag_doc(hit(), "run1", NOW, 1)
    assert R.plan_one(d, None, "run2", NOW, 1) == {"op": "delete", "id": d["_id"], "version": 1}
    dec = {**d, "status": "confirmed_ok", "reason": "đúng", "decided_by": "x", "decided_at": NOW}
    op = R.plan_one(dec, None, "run2", NOW, 1)
    assert op["op"] == "update" and op["set"]["auto_cleared"] is True
    assert R.plan_one({**dec, "auto_cleared": True}, None, "run3", NOW, 1) is None
    # đã đánh dấu rồi lại kích hoạt
    op = R.plan_one({**dec, "auto_cleared": True}, hit(), "run3", NOW, 1)
    assert op["set"]["auto_cleared"] is False
    # suspect nhưng từng có người xem (decided_at) → không xóa
    assert R.plan_one({**d, "decided_at": NOW}, None, "run2", NOW, 1)["op"] == "update"


def test_plan_co_thu_cong():
    d = {**R.new_flag_doc(hit(), "r", NOW, 1), "origin": "manual", "rules": [], "decided_at": NOW}
    assert R.plan_one(d, None, "run2", NOW, 1) is None
    op = R.plan_one(d, hit(), "run2", NOW, 1)
    assert op["op"] == "update" and op["set"]["rules"][0]["rule"] == "R1"


def test_plan_updates_thu_tu_on_dinh():
    a = R.new_flag_doc(hit(), "run1", NOW, 1)
    ops = R.plan_updates({a["_id"]: a}, {}, "run2", NOW, 1)
    assert [o["op"] for o in ops] == ["delete"]


def test_is_active():
    assert R.is_active({"status": "suspect"})
    assert not R.is_active({"status": "suspect", "auto_cleared": True})
    assert R.is_active({"status": "rejected", "auto_cleared": True})


# ---------------------------------------------------------------- vòng sửa 2 ----
def test_r2_chi_cap_vi_pham_lon_nhat():
    # hai đỉnh đều thấp hơn hai chân: chỉ gắn đỉnh thấp nhất và chân cao nhất
    out = R.r2_check([("h1", 0.3), ("h2", 0.1)], [("l1", 0.5), ("l2", 0.8)])
    assert set(out) == {"h2", "l2"}
    assert out["h2"]["metric"]["other"] == 0.8 and out["l2"]["metric"]["other"] == 0.1
    # bằng nhau: lấy phần tử đầu
    assert set(R.r2_check([("h1", 0.1), ("h2", 0.1)], [("l1", 0.8), ("l2", 0.8)])) == {"h1", "l1"}


def test_is_active_thu_cong_va_retired():
    assert R.is_active({"status": "suspect", "origin": "manual", "auto_cleared": True})
    assert not R.is_active({"status": "rejected", "retired": True})
    assert not R.is_active({"status": "suspect", "origin": "manual", "retired": True})


def test_plan_bo_qua_co_retired():
    d = {**R.new_flag_doc(hit(), "run1", NOW, 1), "retired": True}
    assert R.plan_one(d, None, "run2", NOW, 1) is None
    assert R.plan_one(d, hit(("R2",)), "run2", NOW, 1) is None


def test_reference_peaks_bo_dinh_rejected():
    els = [el("VUNG_TAU", "high", 2, 3.9), el("VUNG_TAU", "high", 14.5, 3.8)]
    ref, days = R.reference_peaks(els, "VUNG_TAU", exclude_ts=[T0 + 2 * H])
    assert ref == [T0 + int(14.5 * H)] and sum(days.values()) == 1


def test_validator_nhanh_corrected_bat_buoc_corrected_value():
    branch = R.QC_FLAGS_VALIDATOR["$jsonSchema"]["oneOf"][0]
    assert branch["required"] == ["corrected_value"]
    assert "retired" in R.QC_FLAGS_VALIDATOR["$jsonSchema"]["properties"]


def test_module_thuan_khong_can_pymongo_pydantic_yaml():
    """qc_rules, bulletins, admin, province_labels, text import được khi chặn pymongo/pydantic/yaml (như image Spark)."""
    import subprocess
    import sys
    from pathlib import Path
    src = str(Path(R.__file__).resolve().parents[1])
    code = (
        "import sys, importlib.abc\n"
        "class Block(importlib.abc.MetaPathFinder):\n"
        "    def find_spec(self, name, path, target=None):\n"
        "        if name.split('.')[0] in ('pymongo', 'bson', 'pydantic', 'yaml', 'redis'):\n"
        "            raise ImportError('chặn ' + name)\n"
        "sys.meta_path.insert(0, Block())\n"
        f"sys.path.insert(0, {src!r})\n"
        "import vngis.qc_rules, vngis.bulletins, vngis.admin, vngis.province_labels, vngis.text\n"
        "try:\n"
        "    import vngis.qc\n"
        "    raise SystemExit('vngis.qc lẽ ra phải cần pymongo')\n"
        "except ImportError:\n"
        "    pass\n"
        "print('ok')\n")
    r = subprocess.run([sys.executable, "-I", "-c", code], capture_output=True, text=True)
    assert r.returncode == 0 and r.stdout.strip() == "ok", r.stderr


def test_pyfiles_chi_nen_module_thuan():
    from vngis.qc_rules import PURE_MODULES
    assert set(PURE_MODULES) == {"__init__", "text", "admin", "province_labels", "bulletins", "qc_rules"}


def test_r2_hoa_on_dinh_khong_phu_thuoc_thu_tu():
    a = R.r2_check([("h2", 0.1), ("h1", 0.1)], [("l2", 0.8), ("l1", 0.8)])
    b = R.r2_check([("h1", 0.1), ("h2", 0.1)], [("l1", 0.8), ("l2", 0.8)])
    assert set(a) == set(b) == {"h1", "l1"}
    # trong evaluate_group: khóa (trạm, loại, ts) → phần tử sớm nhất thắng, dù thứ tự phần tử bị đảo
    els = [el("PHU_AN", "high", 3, 0.1), el("PHU_AN", "high", 15, 0.1),
           el("PHU_AN", "low", 9, 0.8), el("PHU_AN", "low", 21, 0.8)]
    ctx = {"params": R.params_with(), "bounds": {}, "lag_stats": {}, "ref": [], "ref_days": {}}
    x = R.evaluate_group(els, ctx)
    y = R.evaluate_group(list(reversed(els)), ctx)
    assert x == y and [(h["kind"], h["ts"]) for h in x] == [("high", T0 + 3 * H), ("low", T0 + 9 * H)]
