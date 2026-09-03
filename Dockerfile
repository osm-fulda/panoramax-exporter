# Digest-pinned so a rebuild is reproducible; Dependabot bumps tag + digest together.
FROM python:3.14-slim@sha256:cad9a2c871761c413caa6fdd6441c783451e740a48aaeba60ae62a8b53525ef6

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
