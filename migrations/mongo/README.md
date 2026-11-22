# migrations/mongo/ – Migration MongoDB

**Chức năng:** các bước nâng cấp database `vietnam_gis`.

```
migrations/mongo/
├── runner.py                    # chạy các bước chưa áp dụng, ghi vào collection _migrations
├── 0001_stations_validator.py
├── 0002_admin_units.py
└── 0003_stations_add_admin.py
```

- `runner.py up` / `runner.py down <số>`; nhận URI MongoDB qua cấu hình.
- Thay thế dữ liệu lớn dùng mẫu đã có: ghi collection `__staging`, kiểm tra, rồi `renameCollection(dropTarget=True)`.
- Các collection đã tạo bằng script trước khi có migration (`stations`, `admin_units`, `admin_unit_mappings`, `province_timeline`) sẽ được ghi nhận bằng migration 0001–0003.
