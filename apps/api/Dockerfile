# Build từ GỐC repo để lấy được packages/vngis:
#   docker build -f apps/api/Dockerfile -t gis-dist-api:local .
# (docker-compose: context ../.., dockerfile apps/api/Dockerfile)
FROM python:3.12-slim
WORKDIR /srv
COPY packages/vngis /srv/packages/vngis
COPY apps/api /srv/apps/api
RUN pip install --no-cache-dir /srv/packages/vngis /srv/apps/api
EXPOSE 8000
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
