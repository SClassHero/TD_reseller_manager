FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=5000 \
    INVENTORY_DB=/data/inventory.db \
    INVENTORY_BACKUPS_DIR=/data/backups \
    INVENTORY_UPLOADS_DIR=/app/static/uploads/products \
    INVENTORY_SECRET_KEY_FILE=/data/.secret_key

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p /data/backups /data/uploads/products

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "app:app"]
