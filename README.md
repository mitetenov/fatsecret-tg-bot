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

Всё живёт в Docker; локальный Python нужен только если хочется без него.

```bash
cp .env.example .env      # заполнить ключи FatSecret; .env не коммитится
docker compose build
```

```bash
docker compose run --rm test     # тесты
docker compose run --rm spike    # проверки FatSecret, PIN вводится в терминале
docker compose up bot            # сам бот (пока заглушка)
```

Состояние — SQLite и кеш access-токена — лежит в `./data`, примонтированном в `/data`,
и переживает пересборку образа. Сервис `test` запускается без ключей и без сети
(`network_mode: none`): тесты покрывают чистую логику, наружу им ходить незачем.

Спайк по FatSecret умеет три режима:

```bash
docker compose run --rm spike python spike/check_fatsecret.py --two-legged-only  # только чтение
docker compose run --rm spike                                                    # + PIN-авторизация
docker compose run --rm spike python spike/check_fatsecret.py --write            # + создаёт продукт
```

Без `--write` скрипт ничего в аккаунте FatSecret не меняет.

### Мультиплатформенный образ

Один тег на VPS (amd64) и Raspberry Pi (arm64) — см. [ADR 0003](docs/adr/0003-tolko-oauth-1-0-dlya-vsego-fatsecret.md)
и решение о хостинге. Сборка идёт отдельным билдером, чтобы не подменять дефолтный,
которым пользуется `docker compose`:

```bash
docker buildx create --name fsbot-multi        # один раз
docker buildx build --builder fsbot-multi \
  --platform linux/amd64,linux/arm64 \
  -t <registry>/fsbot:latest --push .
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
