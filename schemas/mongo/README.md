# schemas/mongo/ – Validator MongoDB

**Chức năng:** mỗi collection một thư mục, mỗi phiên bản schema một file.

```
schemas/mongo/
├── stations/v1.json
├── admin_units/v1.json
├── province_timeline/v1.json
├── tide_extremes/v1.json
└── …
```

- Document trong MongoDB mang field `schema_version` khớp với file đang áp dụng.
- Migration (`migrations/mongo/`) đọc file ở đây để `collMod` validator.
- Validator hiện có đang viết trong code `create_stations.py`, `create_admin_units.py` và parser KTTV; sẽ tách ra đây.
