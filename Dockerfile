# Мультиплатформенный образ: один тег идёт и на VPS (amd64), и на Raspberry Pi (arm64).
#
# База — alpine, а не slim: 107 МБ против 235 МБ. Это безопасно только потому, что все
# зависимости имеют колёса под musl для обеих архитектур (проверено сборкой под amd64
# и arm64 — компиляции из исходников нет). Добавляя зависимость без musl-колёс, придётся
# либо вернуться к slim, либо получить многоминутную сборку на arm64.
#
# Стадии:
#   deps      — venv только с рантайм-зависимостями; слой зависит от pyproject.toml,
#               поэтому правка исходников его не инвалидирует
#   test      — deps + pytest + исходники и тесты (в прод не попадает)
#   prod-venv — тот же venv без pip/setuptools и кешей
#   runtime   — чистый базовый образ + обрезанный venv + src

FROM python:3.14-alpine AS deps

ENV PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app
COPY pyproject.toml ./

# Зависимости читаются из pyproject через tomllib, чтобы не держать второй список
# в requirements.txt и не расходиться с ним. Сам пакет не ставим: он чисто питонячий
# и подключается через PYTHONPATH — так в образе нет ни сборки, ни egg-info.
RUN python -m venv /opt/venv \
 && python -c "import tomllib; print('\n'.join(tomllib.load(open('pyproject.toml','rb'))['project']['dependencies']))" > /tmp/requirements.txt \
 && /opt/venv/bin/pip install --no-compile -r /tmp/requirements.txt


FROM python:3.14-alpine AS zbarlib

# apk-пакет zbar тянет 47 зависимостей (python3, glib, gobject-introspection) — это
# +127 МБ ради библиотеки в 196 КБ. Забираем только сам .so и его замыкание: ~3 МБ.
RUN apk add --no-cache zbar \
 && mkdir -p /libs \
 && cp -L $(ldd /usr/lib/libzbar.so.0.3.0 | awk '/=>/ {print $3}') /libs/ \
 && cp -L /usr/lib/libzbar.so.0.3.0 /libs/libzbar.so


FROM deps AS test

COPY --from=zbarlib /libs/ /usr/lib/
RUN /opt/venv/bin/pip install --no-compile pytest pytest-asyncio
ENV PATH="/opt/venv/bin:$PATH" PYTHONPATH=/app/src
COPY src ./src
COPY tests ./tests
COPY spike ./spike
CMD ["python", "-m", "pytest", "-q"]


FROM deps AS prod-venv

# pip и setuptools нужны только на сборке; .pyc не пишем, .dist-info оставляем —
# по ним видно, что и каких версий стоит в образе.
RUN /opt/venv/bin/pip uninstall -y pip setuptools wheel \
 && find /opt/venv -name '__pycache__' -type d -prune -exec rm -rf {} + \
 && find /opt/venv -name '*.pyc' -delete


FROM python:3.14-alpine AS runtime

ENV PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app/src \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    FSBOT_STATE_DIR=/data

# su-exec (10 КБ) нужен, чтобы entrypoint выровнял владельца каталога состояния
# и уронил привилегии до fsbot. Пользователь и /data создаются до COPY: chown -R
# после копирования удваивает размер, переписывая все файлы в новый слой.
RUN apk add --no-cache su-exec \
 && adduser -D -H -u 10001 fsbot \
 && install -d -o fsbot -g fsbot /data

COPY --from=prod-venv /opt/venv /opt/venv
COPY --from=zbarlib /libs/ /usr/lib/

WORKDIR /app
COPY --chown=fsbot:fsbot src ./src
COPY docker-entrypoint.sh /usr/local/bin/

# USER здесь намеренно не задан: entrypoint стартует от root, выравнивает владельца
# состояния и переключается на fsbot. Бот работает непривилегированным всегда.
ENTRYPOINT ["docker-entrypoint.sh"]
CMD ["python", "-m", "fsbot"]
