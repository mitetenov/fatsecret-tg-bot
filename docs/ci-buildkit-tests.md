# Тесты CI внутри BuildKit

Нативная матрица AMD64/ARM64 сохранена. Вместо экспорта всего `test` image в
Docker daemon CI выполняет проверки в BuildKit и экспортирует только бинарник.
Прежняя загрузка тестового образа занимала 24–33 секунды в прогретом запуске.
В слое зависимостей удаляются только ZIP-дубликаты уже распакованных Go-модулей;
исходники, `.ziphash`, `.mod` и `.info` сохраняются для offline-сборки. Для race
detector установлены `gcc` и `musl-dev` вместо полного `build-base` с C++ tooling.

## Путь проверки

1. `actions/cache` восстанавливает GOCACHE отдельно для архитектур.
2. Закреплённый по SHA `reproducible-containers/buildkit-cache-dance` переносит
   данные в cache mount `/go-build` того же Buildx builder. UID/GID 10001
   задаются и при импорте, и при создании mount. Для операции используется
   существующая зависимость Alpine 3.23.
3. `ci-test` запускает прежний `tools/ci-go.sh` с `--network=none`, без доступа
   к внешним API, под uid/gid 10001. Сохраняются gofmt, vet, все race-тесты с
   `-count=1 -timeout=5m` и статическая production-сборка. Перед проверками
   сравниваются нативный `go env GOARCH` и целевой `TARGETARCH`.
4. `CI_TEST_RUN=<github.run_id>-<github.run_attempt>` меняется при каждом запуске
   и повторной попытке. Его проверка находится в том же RUN, что и тесты:
   BuildKit не может заменить выполнение тестов сохранённым результатом слоя.
5. `ci-artifacts` экспортирует только `/fsbot` в `.ci-bin`. Сборщик, модули и
   содержимое GOCACHE не входят в этот экспорт.
6. `ci-runtime` упаковывает бинарник. Проверяются SHA версии, миграция SQLite,
   root-owned том, сброс привилегий, heartbeat и отрицательный healthcheck.
   `smoke-fixture` содержит Alpine и ту же `python-state.sqlite` вместо SDK.
7. При неполном попадании cache-dance извлекает GOCACHE до очистки builder,
   затем `actions/cache` сохраняет его. При точном попадании извлечение и
   повторная загрузка кеша пропускаются.

Импорт cache mount нужен отдельно от `cache-from/cache-to: type=gha`: backend
GHA сохраняет слои, но не содержимое cache mounts. Дополнительный restore-prefix
по Linux и архитектуре позволяет использовать прежний кеш при изменении
Dockerfile. Go проверяет собственные ключи компиляции; несовместимые записи
не заменяют компиляцию новой версией SDK или с новыми флагами.

## Локальная проверка

```sh
# Указать отдельный builder, если нужен экспорт без загрузки SDK в Docker.
docker buildx build --target ci-artifacts --platform linux/arm64 \
  --build-arg VERSION=local --build-arg CI_TEST_RUN=local-1 \
  --output type=local,dest=.ci-bin .

# CI_TEST_RUN должен меняться, чтобы проверять реальное повторное выполнение.
docker buildx build --target ci-artifacts --platform linux/arm64 \
  --build-arg VERSION=local --build-arg CI_TEST_RUN=local-2 \
  --output type=local,dest=.ci-bin .

docker build --target ci-runtime -t fsbot:runtime .
docker build --target smoke-fixture -t fsbot:fixture .
sh tools/verify-image.sh fsbot:runtime fsbot:fixture
```

Для AMD64 нужна нативная AMD64-среда; обе платформы полностью проверяются в CI.
Обычный `docker build --target test` остаётся доступен для интерактивной работы.

Локально на ARM64 прошли холодный и повторный offline-запуски, импорт кеша
закреплённым cache-dance и smoke tests нового fixture-образа. Экспортировалось
11,8 MB бинарника за 0,1–0,4 секунды. В том же Docker daemon fixture-образ занял
15,1 MB вместо 1,37 GB тестового SDK. Это локальные размеры и время экспорта,
а не обещание соответствующего сокращения всего GitHub CI.

Источники: [Docker exporters](https://docs.docker.com/build/exporters/),
[сохранение cache mounts в GitHub Actions](https://docs.docker.com/build/ci/github-actions/cache/),
[исходники закреплённого cache-dance](https://github.com/reproducible-containers/buildkit-cache-dance/tree/5422eac04292c961a382e0f584ea0f03ad9da723).
