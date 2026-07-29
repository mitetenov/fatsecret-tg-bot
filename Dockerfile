# ── Multi-arch Dockerfile (amd64 + arm64) ──────────────────────────
# Build:  docker build --platform linux/amd64,linux/arm64 -t fatsecret-bot .

FROM python:3.12-slim

WORKDIR /app

# Install system dependencies
# - gcc + libpq-dev: for psycopg2 (PostgreSQL client)
# - libzbar0:        for pyzbar barcode scanning
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        gcc \
        libpq-dev \
        libzbar0 \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for SQLite (if used)
RUN mkdir -p /app/data

# Run as non-root for security
RUN useradd --create-home --shell /bin/bash botuser \
    && chown -R botuser:botuser /app
USER botuser

CMD ["python", "bot.py"]
