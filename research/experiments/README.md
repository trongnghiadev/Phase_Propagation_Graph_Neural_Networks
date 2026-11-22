# research/experiments/ – Thực nghiệm E01–E22

**Chức năng:** mỗi thực nghiệm trong đề cương 7.2 là một thư mục, chạy lại được bằng một lệnh.

```
research/experiments/
├── E01_phase_lag/
│   ├── README.md      # mục đích, giả thuyết, câu hỏi nghiên cứu, cách chạy
│   ├── config.yaml    # tham số, seed, phiên bản dữ liệu
│   └── run.py         # hoặc run.ps1
├── E02_model_comparison/
└── … E22_mongo_scaling/
```

| Nhóm | Mã |
|---|---|
| A – Dự báo đỉnh mực nước | E01–E07 |
| B – Dữ liệu và pipeline | E08–E10 |
| C – Hệ thống web | E11–E17 |
| D – Hệ thống phân tán | E18–E22 |

Mỗi lần chạy ghi `results/<Exx>/runs/<ngày>_<commit>/meta.json` (commit git, `data_snapshot`, hash cấu hình).
