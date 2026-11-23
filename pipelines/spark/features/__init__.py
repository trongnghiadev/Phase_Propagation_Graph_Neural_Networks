"""Khối đặc trưng G1/G3/G4/G5: dựng đồ thị hai chiều, τ vật lý, mưa theo nút, mẫu huấn luyện.

    graph_build.py        G1 – đồ thị hai chiều từ MERIT → `graph_edges` + Parquet
    tau_phys.py           G3 – thời gian truyền vật lý τ cho mỗi cạnh
    rainfall_by_node.py   G4 – mưa theo nút (fallback dựa trạm mưa)
    build_training_set.py G5 – mẫu huấn luyện Parquet + `_meta.json`

Quy tắc thuần Python dùng chung nằm trong `features/rules.py` (có test, không cần Spark).
"""
