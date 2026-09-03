# Digest-pinned so a rebuild is reproducible; Dependabot bumps tag + digest together.
FROM python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

LABEL org.opencontainers.image.title="panoramax-exporter" \
      org.opencontainers.image.description="Prometheus exporter for a Panoramax (GeoVisio) instance" \
      org.opencontainers.image.source="https://github.com/osm-fulda/panoramax-exporter" \
      org.opencontainers.image.licenses="MIT"

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY exporter.py .

EXPOSE 9155
USER nobody
ENTRYPOINT ["python", "-u", "exporter.py"]
