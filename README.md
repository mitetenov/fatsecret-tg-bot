# fsbot — еда в FatSecret через Telegram

Telegram-бот на Go: добавляет еду в личные аккаунты FatSecret текстом, по фото
тарелки или упаковки и по штрих-коду. Перед записью показывает черновик и просит
подтверждение. Дневник смотрят в приложении FatSecret.

- Термины: [CONTEXT.md](CONTEXT.md).
- Решения предметной области: [docs/adr/](docs/adr/).
- Проверка совместимости с Python: [testdata/README.md](testdata/README.md).
- Результаты миграции и замеры образа: [docs/go-migration.md](docs/go-migration.md).

## Возможности

- Текст и фотографии через OpenRouter или напрямую через Z.AI (`glm-5.3-flash`).
  Невалидный ответ сначала исправляется той же моделью; в OpenRouter затем
  пробуется следующая из списка.
- Локальное чтение EAN-8, EAN-13, UPC-A и UPC-E через Go-декодер, без платного LLM.
- Поиск товара: своя связка штрих-кода → FatSecret → Open Food Facts → LLM с
  веб-поиском. Нечитаемое название переводится до предложения создать продукт.
- Создание своего продукта из полного КБЖУ только по явной кнопке. Удаления такого
  продукта через API нет; запись еды и создание продукта — отдельные действия.
- Различение данных на 100 г и 100 мл, проверка калорий и макронутриентов.
- Пересчёт количества в `serving_id` + `number_of_units`. Виртуальная порция с
  ID `0` исключается. Если метрической порции нет, бот просит выбрать порцию.
- Одна реплика — одна дата и приём пищи. Дневниковые сутки начинаются в 04:00 по
  часовому поясу пользователя. Сомнительные результаты требуют двух подтверждений.
- Только личные чаты, доступ по приглашению; `/allow` доступна владельцу.

FatSecret использует OAuth 1.0a, `foods.search.v5`, `food.get.v5`,
`food.find_id_for_barcode.v2`, `foods.autocomplete.v2`,
`foods.get_recently_eaten.v2`, `food.create.v2` и legacy `food_entry.create`.
Порции из search/barcode переиспользуются, чтобы сократить обращения к API.

## Запуск

```sh
cp .env.example .env  # заполнить ключи и Telegram ID владельца
# Готовый образ публикуется CI после push в main:
docker compose pull
docker compose up -d bot
docker compose ps
docker compose logs -f bot
```

Для запуска локально собранной версии:

```sh
docker build -t fsbot:local .
docker run -d --name fsbot-local --env-file .env \
  -v fsbot-local-data:/data fsbot:local
```

Не запускай две копии на одном токене Telegram. При polling conflict Go-процесс
завершается с ошибкой и удаляет heartbeat. Heartbeat обновляется только после
успешного `getUpdates`; длительная потеря связи делает контейнер unhealthy.

Переменные окружения совместимы с прежней версией; образ не читает `.env` сам,
его передаёт Docker. Для запуска Go напрямую экспортируй переменные окружения.
По умолчанию `FSBOT_STATE_DIR=/data`, `FSBOT_DEFAULT_TZ=Asia/Tbilisi`.

### LLM-провайдер

Без `LLM_PROVIDER` используется OpenRouter: нужны `OPENROUTER_API_KEY` и при
необходимости списки `OPENROUTER_TEXT_MODELS` / `OPENROUTER_VISION_MODELS`.

Для прямого подключения к Z.AI укажи в `.env`:

```dotenv
LLM_PROVIDER=zai
ZAI_API_KEY=your-zai-api-key
# Необязательно, это значения по умолчанию:
ZAI_MODEL=glm-5.3-flash
ZAI_REASONING_EFFORT=low
```

При выборе `zai` ключ OpenRouter не требуется. Бот обращается к обычному
[Z.AI Model API](https://docs.z.ai/api-reference/llm/chat-completion)
(`https://api.z.ai/api/paas/v4/chat/completions`), используя один `ZAI_MODEL`
для текста, фотографий и перевода названий. Это endpoint Model API с оплатой по
использованию; endpoint Coding Plan здесь не используется.

Для распознавания включён JSON-режим с проверкой результата на стороне бота.
Для поиска товара по штрих-коду включается встроенный `web_search` Z.AI;
доступность и стоимость поиска определяются аккаунтом Z.AI. Если поиск недоступен,
API не вернул результаты поиска или ответ неполный, бот не предлагает создать
продукт по этим данным.
`ZAI_REASONING_EFFORT` принимает `low`, `high` или `max`; thinking остаётся
включённым, как требует GLM-5.3-Flash. Настройки OpenRouter при выборе Z.AI
игнорируются. После изменения `.env` пересоздай контейнер:

```sh
docker compose up -d --force-recreate bot
```

## Данные и переход с Python

Сохраняются имя файла `fsbot.sqlite3`, схема SQLite, JSON черновиков, строки кнопок,
связки штрих-кодов, приглашения, токены и часовые пояса. Нужные старым базам колонки
добавляются автоматически. Используй прежний именованный том `fsbot-data`.
Временные состояния диалога (ожидание PIN или количества) были и остаются в памяти:
после перезапуска нужно повторить `/link` или нажать кнопку редактирования.

При старте от root бинарник исправляет владельца каталога состояния, затем
переключается на uid/gid 10001. Сам бот работает без root; можно сразу задать
`--user 10001:10001`, если том уже доступен этому пользователю. Shell и `su-exec`
в образе не нужны. Для SQLite необходима запись в каталог `/data`.

Каждый ID записи сохраняется вместе с состоянием позиции одной транзакцией.
Перед записью бот читает дневник через `food_entries.get.v2` и сохраняет снимок ID.
После таймаута или ответа без ID он ищет ровно одну новую запись с совпадающими
продуктом, порцией, количеством, названием, датой и приёмом пищи. Найденный ID
сохраняется как успешная запись и становится доступен для `/undo`.
Если совпадение не найдено, оно неоднозначно или дневник недоступен, состояние
остаётся `unknown`: кнопка «Проверить запись» повторяет только чтение для этой
позиции, включая после перезапуска. Повторного `food_entry.create` не будет.
При недоступном дневнике до отправки запрос записи не выполняется.
Старые `unknown`/`writing` без снимка требуют ручной проверки в FatSecret.
При нескольких совпадениях подтверждение блокируется. FatSecret не возвращает
идентификатор запроса: одну новую запись, добавленную вручную со всеми теми же
полями во время запроса бота, невозможно отличить от результата этого запроса.
Создание отправляется через POST без повторов по HTTP-редиректам, чтобы
HTTP-клиент не повторял запись после потери ответа.
`/undo` удаляет только подтверждённые ID, сохраняет каждое
успешное удаление и оставляет неудавшиеся удаления для повтора.

Перед переходом останови старого бота и сделай резервную копию тома. Для отката
сохрани прежний тег образа и копию базы. Не откатывай базу после новых записей в
FatSecret: потеря сохранённых ID может нарушить защиту от повторов и `/undo`.

```sh
# Резервная копия; имя тома зависит от имени Compose-проекта.
docker run --rm -v fatsecret-tg-bot_fsbot-data:/data:ro -v "$PWD:/backup" \
  alpine tar czf /backup/fsbot-data.tgz -C /data .
```

Для проверки Compose используй `docker compose config --quiet`: обычный `config`
печатает развёрнутый `env_file` с ключами.

## Сборка и тесты

Нужен Go 1.27.3. Docker собирает обе архитектуры на платформе сборщика без QEMU:

```sh
go test -race ./...
go vet ./...
go build -trimpath -ldflags='-s -w' -o bin/fsbot ./cmd/fsbot

# Изолированные unit/mock-тесты в контейнере без внешней сети:
docker build --target test -t fsbot:test .
docker run --rm --network none fsbot:test

# Production-сборка без запуска runtime:
docker build -t fsbot:runtime .

# Один тег для VPS и Raspberry Pi:
docker buildx build --platform linux/amd64,linux/arm64 \
  -t mitetenov/fsbot:latest --push .
```

Слои: сначала `go.mod`/`go.sum` и зависимости, затем код. Кеш компиляции BuildKit
помогает при повторной локальной сборке на том же builder; `type=gha` сам по себе
не сохраняет содержимое cache mount между runner'ами. Финальный `scratch`
содержит сертификаты CA, каталог
состояния и статический бинарник; база часовых поясов встроена в бинарник. Самый
изменчивый слой с бинарником идёт последним. SDK, тесты, Python, zbar и пакетный
менеджер в production не попадают. CI проверяет модули, форматирование, `vet`, lint, изолированные unit/race-тесты
и production-сборку до публикации образа.
CI параллельно собирает и проверяет AMD64 на `ubuntu-26.04`, а ARM64 на
`ubuntu-26.04-arm`, нативно и без QEMU. Кеши разделены по архитектурам. В CI target
`ci-artifacts` выполняет `tools/ci-go.sh` прямо внутри BuildKit: форматирование,
`vet`, `-race -count=1` и сборку статического бинарника. Проверки работают с
`--network=none` под uid/gid 10001. Уникальный `CI_TEST_RUN` из номера запуска и
попытки заставляет выполнять проверки заново даже при неизменном коде.

На runner экспортируется только `.ci-bin/fsbot`; большой тестовый SDK-образ
не загружается в Docker daemon. Содержимое cache mount `GOCACHE` переносится
между runner и BuildKit через закреплённый по SHA `buildkit-cache-dance` и
`actions/cache`. Совместимые записи Go восстанавливаются и при изменении
Dockerfile; Go сам проверяет версию компилятора, флаги и зависимости в ключах
своего кеша. Target `ci-runtime` упаковывает бинарник в тот же rootfs, что и
обычная сборка. SQLite/runtime smoke-тесты и их fixture stage удалены.
Обычные `docker build .` и `--target test`
продолжают работать самостоятельно. Детали — в [описании BuildKit CI](docs/ci-buildkit-tests.md).

После успешных unit/race-тестов и production-сборки образ отправляется по
digest. Успех обеих job разрешает публикацию manifest с SHA коммита; `latest`
обновляется только после проверки обеих архитектур. Устаревшие запуски PR
отменяются; публикация из `main` завершается, а новые push ожидают её.
Actions закреплены по SHA и обновляются Dependabot вместе с Go- и Docker-зависимостями.
Замеры, ограничения и следующие улучшения описаны в [аудите CI](docs/ci-audit.md).

`/fsbot healthcheck` проверяет свежесть heartbeat; `/fsbot version` выводит версию
сборки.

Групповые чаты, вес, упражнения, рецепты, аналитика и второй интерфейс дневника
не поддерживаются.

## Стандарт разработки

Go 1.27.3; Ubuntu 26.04 на нативных amd64/arm64 runners. CI проверяет
`go mod tidy -diff`, `go mod verify`, форматирование, vet, golangci-lint v2.15.0,
unit/race-тесты и production-сборку.
BuildKit выполняет тесты без сети и упаковывает проверенный бинарник; PR и ручная
проверка не публикуют образы. Dependabot еженедельно проверяет Go, Docker и Actions,
объединяя minor/patch отдельно от major. Actions закреплены полными SHA.
Сохранён замороженный JSON-эталон Python для изолированных unit-тестов.

Current test scope: integration tests using SQLite, persisted old-state databases,
and Docker runtime smoke harnesses have been removed by request. CI retains
isolated domain/mock/HTTP-fake tests, formatting/vet/lint/race, production and
native Docker builds. Runtime SQLite, bootstrap ownership and health commands
remain production features. Historical integration results above are archival.
