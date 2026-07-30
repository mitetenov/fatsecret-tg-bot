# Мультиплатформенный образ: один и тот же тег идёт и на VPS (amd64),
# и на Raspberry Pi (arm64) — см. решение 15.
FROM python:3.14-slim AS builder

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src

# Отдельный venv, чтобы в финальный образ не тащить pip и его зависимости.
RUN python -m venv /opt/venv \
 && /opt/venv/bin/pip install --upgrade pip \
 && /opt/venv/bin/pip install '.[dev]'


FROM python:3.14-slim AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FSBOT_STATE_DIR=/data

COPY --from=builder /opt/venv /opt/venv

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
COPY spike ./spike
COPY tests ./tests

# Состояние (SQLite, кеш токена) — только в /data, который монтируется снаружи.
RUN useradd --create-home --uid 10001 fsbot \
 && mkdir -p /data \
 && chown -R fsbot:fsbot /data /app
USER fsbot

CMD ["python", "-m", "fsbot"]
