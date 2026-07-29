# ============================================================
# Stage 1 — Builder
# Install dependencies in a venv so the runtime stage can
# copy only what it needs (no pip, no build deps, no cache).
# ============================================================
FROM python:3.11-slim AS builder

WORKDIR /app

# Only copy requirements first — Docker layer caching FTW
COPY requirements.txt .

RUN python3 -m venv /opt/venv && \
    /opt/venv/bin/pip install --no-cache-dir -r requirements.txt

# ============================================================
# Stage 2 — Runtime
# Slim, secure, production-ready image.
# ============================================================
FROM python:3.11-slim AS runtime

# Create a non-root user
RUN groupadd -r bot && useradd -r -g bot bot

WORKDIR /app

# Copy the virtualenv from the builder stage
COPY --from=builder /opt/venv /opt/venv

# Copy application code
COPY . .

# Make the venv's bin the default python
ENV PATH="/opt/venv/bin:$PATH"

# Volume for SQLite database persistence
VOLUME ["/app/data"]

# Drop to non-root user
USER bot

CMD ["python3", "bot.py"]
