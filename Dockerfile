# Digest-pinned so a rebuild is reproducible; Dependabot bumps tag + digest together.
FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928

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
