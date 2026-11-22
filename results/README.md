# results/ – Kết quả thực nghiệm

**Chức năng:** đầu ra của `research/experiments/`; mọi số liệu trong báo cáo phải truy được về đây.

```
results/
├── E01/
│   ├── summary.csv        # commit: bảng tổng hợp dùng trong báo cáo
│   ├── figures/           # commit: hình
│   └── runs/              # KHÔNG commit: từng lần chạy
│       └── 20261201_ab12cd3/meta.json, raw/…
├── …
└── infra/                 # docker stats, tài nguyên từng kịch bản
```

- Không ghi đè lần chạy cũ; báo cáo ghi rõ lấy từ lần chạy nào.
- `mlruns/` (MLflow) cũng đặt dưới đây và bị gitignore.
