# schemas/prediction/ - Dinh dang du bao chung

**Chuc nang:** JSON Schema cua ban ghi du bao ma **moi** mo hinh phai xuat ra, de mot ham danh gia
(`pipelines/r/evaluation.R`) dung duoc cho tat ca (de cuong 4.5, checklist GD4.5).

File: `v1.schema.json` (da chot dinh dang).

## Truong

| Truong | Y nghia |
|---|---|
| `model` | ten mo hinh: `B0`, `B_KTTV`, `B_harm`, `B1`, `B2`, `B3`, `B4`, `B5a`, `B5b`, `phasegnn-vN` |
| `station` | ma tram trong collection `stations` (vd `PHU_AN`) |
| `issued_date` | ngay phat du bao (giao dien), `YYYY-MM-DD` gio Viet Nam |
| `target_date` | ngay du bao (ngay quan sat), `YYYY-MM-DD` gio Viet Nam |
| `lead_day` | chan troi: ngay phat ban tin = 1 (khoang 1..5) |
| `peak_idx` | chi so dinh trieu trong ngay: 0 = dinh thu nhat, 1 = dinh thu hai |
| `h_q10`, `h_q50`, `h_q90` | do cao dinh (m); `null` khi mo hinh khong cho phan vi |
| `t_peak` | thoi diem dinh du bao, ISO 8601 co UTC (vd `2024-03-10T02:00:00Z`); `null` khi khong du doan duoc |

Moi ban ghi la **mot (mo hinh, tram, ngay phat, ngay du bao, chi so dinh trieu)**.

## Quy tac

- Moi mo hinh ghi ra **cung mot bang** (CSV hoac Parquet) theo dung schema nay; ham danh gia doc bang
  `model` de tach tung mo hinh.
- Do cao dinh lay theo **cung moc** cua tram (khong so truc tiep giua Vung Tau va tram song).
- Khong ghi them truong ngoai schema (`additionalProperties: false`); truong moi -> tao `v2.schema.json`
  va migration tuong ung (xem `schemas/README.md`).
