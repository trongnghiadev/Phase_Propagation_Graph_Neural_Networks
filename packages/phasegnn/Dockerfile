# Môi trường chạy PhaseGNN (CPU) — dùng cho test và huấn luyện.
#
#   docker build -f packages/phasegnn/Dockerfile -t phasegnn:local .
#   docker run --rm -v "$PWD:/work" -w /work phasegnn:local pytest packages/phasegnn/tests -q
#
# Torch bản CPU (không CUDA) để image nhỏ; huấn luyện thật trên GPU dùng
# Dockerfile riêng (torch cu) — cùng mã nguồn, chỉ khác base image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    TORCH_HOME=/work/.cache/torch \
    PYTHONPATH=/work/packages/phasegnn/src

RUN apt-get update \
 && apt-get install -y --no-install-recommends build-essential \
 && rm -rf /var/lib/apt/lists/*

RUN pip install --upgrade pip \
 && pip install \
      "torch==2.11.0" --index-url https://download.pytorch.org/whl/cpu \
 && pip install \
      "numpy>=1.26" "pandas>=2.1" "pyarrow>=14" "scikit-learn>=1.3" \
      "pytest>=8" "pyyaml>=6" "pymongo>=4.6"

WORKDIR /work
CMD ["python", "-c", "import torch; print('torch', torch.__version__)"]
