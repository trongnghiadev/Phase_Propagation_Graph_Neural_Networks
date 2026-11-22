# research/benchmarks/ – Kịch bản tải

**Chức năng:** đo hiệu năng hệ thống web (E11–E15) với các phương án W0–W3.

- `locustfile.py` hoặc `*.k6.js`: 90% đọc / 10% ghi; 10, 50, 200 người dùng.
- Kịch bản đột biến khi triều cường (E12), sửa đồng thời (E13).
- Chạy **trên Windows**, ngoài cụm Docker, để không tranh tài nguyên với hệ thống đang đo.
