FROM python:3.12-slim

WORKDIR /app

# Install system deps (PostgreSQL client libs for psycopg2)
RUN apt-get update \
    && apt-get install -y --no-install-recommends gcc libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Create data directory for SQLite (if used)
RUN mkdir -p /app/data

CMD ["python", "bot.py"]
