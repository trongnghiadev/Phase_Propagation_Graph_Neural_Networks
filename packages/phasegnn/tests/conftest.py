"""Cấu hình pytest cho `packages/phasegnn`.

Bảo đảm `src/phasegnn` nằm trên `sys.path` để test chạy được khi package chưa cài
(ví dụ trong container `python:3.12-slim` chỉ mount source).
"""
from __future__ import annotations

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
