# fsbot — еда в FatSecret через Telegram

Telegram-бот, который добавляет продукты в **личные аккаунты FatSecret** нескольких
человек: текстом, по фото (тарелка или упаковка) и по штрих-коду. Дневник смотрят в
мобильном приложении FatSecret — второго интерфейса к нему бот не строит.

- Термины предметной области: [CONTEXT.md](CONTEXT.md)
- Решения, которые иначе выглядят необъяснимо: [docs/adr/](docs/adr/)

## Состояние: ограниченный режим (Basic)

Бот работает. Доступно то, что даёт тариф Basic:

| Способ ввода | Состояние |
|---|---|
| Текст («творог 5% 200г и овсянка 60г») | ✅ работает |
| Фото тарелки — блюда и оценка веса | ✅ работает |
| Фото упаковки — бренд и название | ✅ ищет продукт в базе |
| Штрих-код | ❌ scope `barcode` на Basic не выдаётся — бот честно об этом говорит |
| Создание Своего продукта из этикетки | ❌ `food.create` — Premier |
| Локальные продукты и кириллица в базе | ❌ датасет US/английский |

Запись в Дневник идёт legacy-методом `food_entry.create` с пересчётом граммов в
`serving_id` + `number_of_units`: базовый путь нового REST на нашем аккаунте пока не
подтверждён (`docker compose run --rm spike` это выяснит).

**Блокер для остального — одобрение Premier Free.** До него из трёх заявленных
способов ввода живы два: текст и фото (их распознаёт LLM, от тарифа FatSecret это не
зависит).

Не проверено на живом API: имя legacy-метода для «недавно съеденного», которым
ранжируются Кандидаты. Ошибка там не ломает запись — ранжирование просто теряет
подсказку.

## Запуск

`docker-compose.yml` содержит только бота и работает на готовом образе
`mitetenov/fsbot:latest`, который публикует CI при push в `main` — локально ничего
не собирается.

```bash
cp .env.example .env      # заполнить ключи FatSecret; .env не коммитится
docker compose pull
docker compose up -d bot
docker compose ps         # healthy / unhealthy по свежести heartbeat
docker compose logs -f bot
```

Состояние — SQLite, heartbeat и кеш access-токена — лежит в `./data`, примонтированном
в `/data`, и переживает обновление образа.

> ⚠️ Не запускай `docker compose config`: команда разворачивает `env_file` и печатает
> ключи в терминал. Для проверки синтаксиса есть `docker compose config --quiet`.

### Тесты и спайк

Тесты и спайк лежат внутри того же образа, но в compose их нет — это не сервисы:

```bash
docker run --rm --network none mitetenov/fsbot:latest python -m pytest -q
docker run --rm -it --env-file .env -v "$PWD/data:/data" mitetenov/fsbot:latest \
  python spike/check_fatsecret.py --two-legged-only
```

Без `--write` спайк ничего в аккаунте FatSecret не меняет.

### Сборка образа

Публикацию делает CI. Вручную (один тег на VPS amd64 и Raspberry Pi arm64 — см.
[ADR 0003](docs/adr/0003-tolko-oauth-1-0-dlya-vsego-fatsecret.md)):

```bash
docker buildx create --name fsbot-multi        # один раз
docker buildx build --builder fsbot-multi \
  --platform linux/amd64,linux/arm64 \
  -t mitetenov/fsbot:latest --push .
```

`--push` (или `--load` для одной платформы) обязателен: с драйвером
`docker-container` результат иначе остаётся только в кеше сборки.

### Без Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -e '.[dev]'
.venv/bin/python -m pytest -q
.venv/bin/python spike/check_fatsecret.py --two-legged-only
```

## Что бот делать не будет

Дневник веса и упражнений, рецепты, сохранённые приёмы пищи, аналитика и отчёты внутри
бота, правки задним числом (кроме `/undo` последней записи), групповые чаты. Всё это
либо есть в приложении FatSecret, либо не заказывалось.
