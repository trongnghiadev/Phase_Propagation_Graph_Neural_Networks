# Build từ GỐC repo để lấy được packages/vngis và config/:
#   docker build -f apps/api/Dockerfile -t gis-dist-api:local .
# (docker-compose: context ../.., dockerfile apps/api/Dockerfile)
FROM python:3.12-slim
WORKDIR /srv
COPY packages/vngis /srv/packages/vngis
COPY apps/api /srv/apps/api
# Cấu hình hệ thống (YAML, không chứa bí mật): vngis cài dạng thường (không editable) nên không tự tìm thấy
# config/ của repo → chỉ rõ bằng VNGIS_CONFIG_DIR. APP_ENV, MONGO_URI, REDIS_* đặt lúc chạy (compose).
COPY config /srv/config
ENV VNGIS_CONFIG_DIR=/srv/config
RUN pip install --no-cache-dir /srv/packages/vngis /srv/apps/api
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
