# fsbot — еда в FatSecret через Telegram

Telegram-бот, который добавляет продукты в **личные аккаунты FatSecret** нескольких
человек: текстом, по фото (тарелка или упаковка) и по штрих-коду. Дневник смотрят в
мобильном приложении FatSecret — второго интерфейса к нему бот не строит.

- Термины предметной области: [CONTEXT.md](CONTEXT.md)
- Решения, которые иначе выглядят необъяснимо: [docs/adr/](docs/adr/)

## Состояние

Тариф — **Premier Free** (US-датасет, английский). Доступно:

| Способ ввода | Состояние |
|---|---|
| Текст («творог 5% 200г и овсянка 60г») | ✅ работает |
| Фото тарелки — блюда и оценка веса | ✅ работает |
| Фото упаковки — бренд, название, КБЖУ | ✅ ищет продукт, иначе предлагает создать |
| Штрих-код: фото кода читается локально (zbar) | ✅ без обращения к LLM |
| Кода нет в базе | ✅ разбирает то же фото и предлагает создать продукт |
| Создание Своего продукта из этикетки | ✅ по явной кнопке — удалить через API нельзя |
| Связка штрих-кода: код → свой продукт | ✅ второе сканирование мгновенное |
| Кириллица в базе продуктов | ❌ датасет US/английский, поэтому нужен слой перевода |

Запись в Дневник идёт legacy-методом `food_entry.create` с пересчётом граммов в
`serving_id` + `number_of_units`: базовый путь нового REST на нашем аккаунте пока не
подтверждён (`docker compose run --rm spike` это выяснит).

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

Состояние — SQLite и heartbeat — лежит в именованном томе `fsbot-data` и переживает
обновление образа. Именно том, а не каталог `./data`: bind-mount на чистом сервере
создаётся от root, процесс работает под uid 10001, и SQLite падает с `unable to open
database file`. Том наследует владельца из образа. На macOS этого не видно — Docker
Desktop подменяет владельца, поэтому баг ловится только на настоящем Linux-сервере.

Бэкап и перенос состояния:

```bash
# выгрузить
docker run --rm -v fatsecret-tg-bot_fsbot-data:/data -v "$PWD:/backup" \
  alpine tar czf /backup/fsbot-data.tgz -C /data .

# залить обратно (или перенести со старой машины)
docker run --rm -v fatsecret-tg-bot_fsbot-data:/data -v "$PWD:/backup" \
  alpine sh -c 'tar xzf /backup/fsbot-data.tgz -C /data'
```

Если состояние уже накоплено в `./data` (до перехода на том), перенеси его один раз:

```bash
docker run --rm -v "$PWD/data:/from" -v fatsecret-tg-bot_fsbot-data:/to \
  alpine sh -c 'cp -a /from/. /to/'
```

> ⚠️ Не запускай `docker compose config`: команда разворачивает `env_file` и печатает
> ключи в терминал. Для проверки синтаксиса есть `docker compose config --quiet`.

### Тесты и спайк

Тесты, pytest и спайк живут в отдельной стадии `test` и в публикуемый образ не
попадают — он от этого на 35 МБ легче. Стадия стоит на том же venv и том же базовом
образе, так что окружение совпадает с прод-образом:

```bash
docker build --target test -t fsbot:test .
docker run --rm --network none fsbot:test                       # тесты
docker run --rm -it --env-file .env -v "$PWD/data:/data" fsbot:test \
  python spike/check_fatsecret.py --two-legged-only             # спайк
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
