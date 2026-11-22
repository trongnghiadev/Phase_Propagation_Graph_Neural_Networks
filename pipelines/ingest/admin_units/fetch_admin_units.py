"""Tải dữ liệu đơn vị hành chính Việt Nam từ https://provinces.open-api.vn/ (dữ liệu gốc, không sửa).

  data/admin_units/v1_depth3.json       trước 01/07/2025: 63 tỉnh → huyện → xã   (/api/v1/?depth=3)
  data/admin_units/v2_depth2.json       từ  01/07/2025: 34 tỉnh → xã (không còn cấp huyện)  (/api/v2/?depth=2)
  data/admin_units/v2_to_legacies.jsonl mỗi xã mới → danh sách xã cũ hợp thành   (/api/v2/w/{code}/to-legacies/)
  data/admin_units/fetch_meta.json      thời điểm tải, URL

Tải tuần tự, giãn cách 0,2 giây, có resume (chạy lại sẽ bỏ qua xã đã tải).

    python -X utf8 pipelines/ingest/admin_units/fetch_admin_units.py
"""
import json
import os
import time
import urllib.error
import urllib.request
from datetime import datetime, timezone

BASE = "https://provinces.open-api.vn"
OUT = os.path.join(os.path.dirname(__file__), "..", "..", "..", "data", "admin_units")
HEADERS = {"User-Agent": "research-data-fetch/1.0", "Accept": "application/json"}


def get(path):
    for attempt in range(5):
        try:
            req = urllib.request.Request(BASE + path, headers=HEADERS)
            with urllib.request.urlopen(req, timeout=60) as r:
                return json.load(r)
        except (urllib.error.URLError, TimeoutError):
            time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"Không tải được {path}")


def main():
    os.makedirs(OUT, exist_ok=True)
    meta = {"source": BASE, "fetched_at": datetime.now(timezone.utc).isoformat(), "files": {}}
    for name, path in [("v1_depth3.json", "/api/v1/?depth=3"), ("v2_depth2.json", "/api/v2/?depth=2")]:
        fp = os.path.join(OUT, name)
        if not os.path.exists(fp):
            json.dump(get(path), open(fp, "w", encoding="utf-8"), ensure_ascii=False)
        meta["files"][name] = path

    wards = [w for p in json.load(open(os.path.join(OUT, "v2_depth2.json"), encoding="utf-8")) for w in p["wards"]]
    fp = os.path.join(OUT, "v2_to_legacies.jsonl")
    done = set()
    if os.path.exists(fp):
        done = {json.loads(line)["code"] for line in open(fp, encoding="utf-8")}
    with open(fp, "a", encoding="utf-8") as f:
        for i, w in enumerate(wards):
            if w["code"] in done:
                continue
            f.write(json.dumps({"code": w["code"], "legacies": get(f"/api/v2/w/{w['code']}/to-legacies/")},
                               ensure_ascii=False) + "\n")
            f.flush()
            if i % 200 == 0:
                print(f"{i}/{len(wards)}", flush=True)
            time.sleep(0.2)
    meta["files"]["v2_to_legacies.jsonl"] = "/api/v2/w/{code}/to-legacies/"
    json.dump(meta, open(os.path.join(OUT, "fetch_meta.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("Xong:", OUT)


if __name__ == "__main__":
    main()
