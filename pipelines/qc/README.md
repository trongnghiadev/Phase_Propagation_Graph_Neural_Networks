# pipelines/qc/ – Ghi quyết định QC từ dòng lệnh

**Chức năng:** người kiểm tra xem cờ QC (`qc_flags`, do job Spark `pipelines/spark/qc/` tạo) và ghi quyết định
(`confirmed_ok`, `rejected`, `corrected`, hoặc trở lại `suspect`) kèm `audit_logs`. Logic nằm trong `vngis.qc`
(dùng lại được cho API); file `qc_decide.py` chỉ là vỏ dòng lệnh.

## Chạy (container Python, không cài gì vào máy)

```bash
MSYS_NO_PATHCONV=1 docker run --rm -it --network spark-mongodb-gis_gis-net \
  -e MONGO_URI=mongodb://mongo:27017 -e APP_ENV=dev -v "<gốc repo>:/repo:ro" -w /repo python:3.12-slim \
  sh -c "pip -q install ./packages/vngis && python -X utf8 pipelines/qc/qc_decide.py list --rule R1"
```

Kết nối theo `vngis.config` (`APP_ENV`, `MONGO_URI`, `MONGO_DB`). `MSYS_NO_PATHCONV=1` chỉ cần trên Git Bash.

| Lệnh | Việc |
|---|---|
| `list [--station S] [--status ST] [--rule R1\|R2\|R3] [--mismatch] [--all] [--limit N]` | liệt kê cờ có hiệu lực (`--all`: cả cờ suspect đã `auto_cleared`; `--mismatch`: chỉ cờ đang ở trạng thái `value_mismatch`) kèm `version` |
| `show ID` | in đầy đủ một cờ (JSON) |
| `decide ID --status ST --reason "…" --by VAI_TRÒ --version V [--value X]` | ghi quyết định; `--value` bắt buộc với `corrected`, cấm với status khác |
| `flag --station S --kind high\|low --ts 2021-04-17T09:00:00+07:00 --reason "…" --by VAI_TRÒ` | tạo cờ thủ công (`origin: manual`, `suspect`) cho một phần tử có thật trong `tide_extremes`, rồi `decide` |
| `bulletin-set FILE --change FIELD OLD NEW [...] --reason "…" --by VAI_TRÒ` | sửa metadata bản tin được phép, có điều kiện giá trị cũ; giá trị đọc như JSON, chuỗi ISO có múi giờ là ngày giờ. Kiểu bắt buộc: `source.is_primary` bool (`true`/`false`), `bulletin_date` ngày giờ có múi giờ, `weekday`/`qc_note` chuỗi hoặc `null` (giá trị cũ được `null` = field chưa có); sai kiểu → mã 1 |
| `retire ID --reason "…" --by VAI_TRÒ --version V` | đánh dấu cờ mồ côi `retired` (phần tử không còn trong `tide_extremes`, ví dụ sau khi đổi bản chính): cờ hết hiệu lực, G0 thôi chặn, job QC không đụng tới. Phần tử còn → mã 1 |
| `review COLLECTION DOC_ID --reason "…" --by VAI_TRÒ` | ghi `audit_logs` cho một lần kiểm tra tay không đổi dữ liệu |
| `ensure` | tạo/cập nhật validator và index của `qc_flags`, `audit_logs` |
| `reconcile [--grace-s S]` | xử lý bản ghi `audit_logs` còn `pending` cũ hơn S giây (mặc định `qc.audit_pending_grace_s` = 600 trong `config/default.yaml`): so nội dung hiện tại với `after` theo từng `action` → `applied` hoặc xóa; bản trẻ hơn không bị đụng |

`ID` = `tide_extremes:<trạm>:<high|low>:<ts UTC ISO>` (ts thật, hoặc ts giữ chỗ 00:00:0k giờ VN cho phần tử không có giờ).
`--by` ghi vai trò (ví dụ `manual_review`), không ghi tên người vào repo.

**Khóa lạc quan:** `--version` là version thấy ở `list`/`show`. Nếu cờ đã đổi (người khác quyết định, hoặc job QC
cập nhật quy tắc) thì lệnh báo `XUNG ĐỘT`, mã thoát 2, không ghi gì: xem lại rồi chạy lại với version mới. Với
`bulletin-set`, xung đột nêu field, giá trị mong đợi và giá trị hiện có.

**Mã thoát:** 0 ok; 1 tham số dòng lệnh sai (lỗi argparse được đổi từ 2 thành 1), dữ liệu vào sai kiểu, không tìm
thấy; 2 xung đột (version cờ, hoặc giá trị cũ của field bản tin); 3 bản audit pending không còn khi đặt `applied`
(dữ liệu ĐÃ ghi; kiểm tra tay `audit_logs`).

**Kiểm toán:** mỗi lệnh ghi (`decide`, `flag`, `retire`, `bulletin-set`) đi kèm một bản `audit_logs`, write concern
`majority`. Replica set: ghi dữ liệu và audit trong một transaction. Một node: audit `pending` → ghi dữ liệu có điều
kiện → `applied` (điều kiện `{_id, state: "pending"}`); chắc chắn không ghi được thì xóa bản pending; lỗi mơ hồ (mạng,
timeout) thì giữ pending cho `reconcile`. Xem docstring `vngis.qc`.

## Cờ `value_mismatch` và đường thoát

Khi một bản tin được nạp lại và giá trị gốc của phần tử đã bị quyết định **đổi**, G0 không áp quyết định cũ mà gắn
`qc_flag = "value_mismatch"`: job QC ghi `value_mismatch: true` và `current_value` (giá trị mới đọc được).

**Đường thoát (duy nhất):** người kiểm tra **quyết định lại** cho cờ đó (lệnh `decide` ở trên, hoặc `qc.decide()`
trong code). Quyết định mới:

- gắn vào `decided_value` = giá trị người xem đã xem (giá trị hiện tại của phần tử);
- giữ `value` nguyên vẹn = giá trị lúc gắn cờ (mốc để phân biệt "giá trị cũ, G0 chưa cập nhật" với "giá trị mới");
- giữ `current_value` = giá trị đã thấy (đánh dấu người xem đã xử lý);
- đặt `value_mismatch: false`.

Nhờ vậy job QC lần sau so giá trị gốc hiện tại với `decided_value` (không phải với `value`), và khi G0 **chưa kịp**
cập nhật `tide_extremes` thì giá trị đọc được vẫn là giá trị CŨ (`== value`) nên **không** gắn lại `value_mismatch` —
cờ không mắc kẹt vô hạn. Ngược lại, nếu giá trị gốc thực sự đổi tiếp sau đó, `value_mismatch` được gắn lại đúng như
mong đợi (một vòng xem xét mới).

Liệt kê nhanh các cờ đang kẹt: `qc_decide.py list --mismatch`.

Sau khi ghi quyết định: chạy lại G0 (`pipelines/spark/flatten/`) để áp vào `tide_extremes`, rồi job QC để cập nhật
`results/qc/`.
