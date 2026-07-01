FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY exporter.py .

EXPOSE 9155
USER nobody
ENTRYPOINT ["python", "-u", "exporter.py"]
