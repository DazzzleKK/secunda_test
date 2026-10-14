# Асинхронный сервис процессинга платежей

Тестовое задание: микросервис принимает запросы на оплату, складывает их в
PostgreSQL вместе с событием в outbox, обрабатывает через эмуляцию платёжного шлюза
и уведомляет клиента вебхуком. Фоновая обработка — RabbitMQ (FastStream).

**Стек:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async, asyncpg),
PostgreSQL 16, RabbitMQ 4 (FastStream), Alembic, Dishka, httpx, uv, Docker Compose.

---

## Быстрый старт

```bash
cp .env.example .env          # необязательно, compose подставляет те же значения
docker compose up -d --wait   # postgres, rabbitmq, миграции, api, consumer
curl http://127.0.0.1:8000/ready
# {"status":"ok","database":"ok"}
```

Swagger: <http://127.0.0.1:8000/docs>, RabbitMQ management: <http://127.0.0.1:15672>
(guest/guest).

### Демонстрация одной командой

```bash
docker compose --profile demo up -d --wait   # добавляет локальный приёмник webhook'ов
uv run python scripts/demo.py                # 7 сценариев, PASS/FAIL по каждому
```

Скрипт проходит: готовность API → платёж обработан и webhook доставлен с валидной
подписью → идемпотентный повтор → конфликт по ключу → обязательные заголовки →
DLQ после исчерпания попыток → outbox при недоступном брокере. Для быстрого
прохождения сценария с DLQ поставьте в `.env` короткие задержки:
`RETRY_DELAYS_MS=1000,2000` (и пересоздайте очереди: `docker compose down -v`).

---

## API

### `POST /api/v1/payments` — создать платёж

Заголовки: `X-API-Key` (обязательный), `Idempotency-Key` (обязательный).

```bash
curl -i -X POST http://127.0.0.1:8000/api/v1/payments \
  -H 'X-API-Key: local-dev-api-key' \
  -H 'Idempotency-Key: order-42' \
  -H 'Content-Type: application/json' \
  -d '{
        "amount": "100.50",
        "currency": "RUB",
        "description": "Оплата заказа 42",
        "metadata": {"order_id": "42"},
        "webhook_url": "https://example.com/hooks/payments"
      }'
```

```
HTTP/1.1 202 Accepted
Location: /api/v1/payments/0f8f...-...
{"payment_id": "0f8f...", "status": "pending", "created_at": "2026-01-01T12:00:00Z"}
```

Ответ `202` означает «принят и сохранён»: запись в БД и событие в outbox
зафиксированы одной транзакцией, обработка идёт в фоне.

Повтор с тем же ключом и телом — идемпотентный повтор:

```bash
# тот же запрос ещё раз
# 202 Accepted, тот же payment_id, заголовок Idempotency-Replayed: true
```

Тот же ключ с другим телом:

```bash
# 409 Conflict
{"code": "idempotency_conflict", "message": "Idempotency key was reused with a different request payload"}
```

### `GET /api/v1/payments/{payment_id}` — состояние платежа

```bash
curl -s http://127.0.0.1:8000/api/v1/payments/0f8f... -H 'X-API-Key: local-dev-api-key'
```

```json
{
  "payment_id": "0f8f...",
  "amount": "100.50",
  "currency": "RUB",
  "description": "Оплата заказа 42",
  "metadata": {"order_id": "42"},
  "status": "succeeded",
  "webhook_url": "https://example.com/hooks/payments",
  "created_at": "2026-01-01T12:00:00Z",
  "processed_at": "2026-01-01T12:00:04Z",
  "failure_reason": null,
  "webhook_attempts": 1,
  "webhook_delivered_at": "2026-01-01T12:00:04Z"
}
```

### Ошибки

Единый формат: `{"code", "message", "details"}`. Коды и статусы — в таблице brief,
кратко: `unauthorized` 401, `missing_idempotency_key` 400, `payment_not_found` 404,
`idempotency_conflict` / `payment_not_visible` / `payment_not_finalized` 409,
`validation_error` и доменные инварианты (`invalid_amount`, `invalid_currency`,
`invalid_metadata`, `invalid_webhook_url`, `invalid_idempotency_key`) 422,
`infra_error` и технические сбои 503, `internal_error` 500.

### Служебные эндпоинты

`GET /health` — процесс жив (200). `GET /ready` — БД отвечает (200, иначе 503).
Они намеренно без `X-API-Key`: данные платежей не отдают, их опрашивает Docker и
балансировщик.

---

## Как это работает

```
клиент ──POST──► API ──одна транзакция──► payments + outbox
                    │                            │
                    └── 202 Accepted              ▼
                                            outbox relay ──publish──► RabbitMQ
                                                                        │
                                            consumer ◄──────────────────┘
                                                │
                                    эмуляция шлюза (2–5 с, 90% успех)
                                                │
                                    финальный статус в БД (guarded UPDATE)
                                                │
                                    webhook + HMAC ──► URL клиента
                                                │
                              неудача ► retry-тир ► … ► payments.dlq
```

Подробно: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) (слои, поток, транзакционные
границы) и [docs/adr/0001](docs/adr/0001-outbox-retry-and-idempotency.md) (почему
outbox, почему тиры ретраев, почему `ON CONFLICT`).

**Топология RabbitMQ** (объявляется кодом, `src/payments/infrastructure/messaging/topology.py`):

| Обменник | Routing key | Очередь | Назначение |
|---|---|---|---|
| `payments` (topic, durable) | `payments.new` | `payments.new` | рабочая очередь, prefetch 10 |
| `payments.retry` (direct, durable) | `retry.1` | `payments.retry.1` | TTL 5 с, DLX → `payments`/`payments.new` |
| `payments.retry` | `retry.2` | `payments.retry.2` | TTL 25 с |
| `payments.dlx` (direct, durable) | `payments.dlq` | `payments.dlq` | свалка после исчерпания попыток, без consumer'а |

Число тиров задаётся `RETRY_DELAYS_MS`: **N задержек = N повторов, то есть N+1
обработок** (по умолчанию 5 с и 25 с → три обработки, дальше DLQ). Аргументы
очереди в RabbitMQ неизменяемы, поэтому после смены `RETRY_DELAYS_MS` нужно
пересоздать очередь: `docker compose down -v && docker compose up -d --wait`.

**Гарантии.** Доставка at-least-once, обработка идемпотентна, поэтому эффект ровно
один: платёж и событие пишутся одной транзакцией; `ON CONFLICT (idempotency_key)`
гасит конкурентные дубли; `UPDATE ... WHERE status='pending'` делает повторную
доставку no-op; `webhook_delivered_at` и `X-Webhook-Id` защищают получателя от
дублей. Бизнес-отказ шлюза (`declined`) — финальный статус `failed` без ретраев;
ретраятся только технические сбои.

---

## Критерии оценки: где смотреть

| Критерий | Что смотреть | Чем доказано |
|---|---|---|
| Архитектура и чистота | `docs/ARCHITECTURE.md`, `src/payments/{domain,application,infrastructure,presentation}/`, контракты import-linter в `pyproject.toml` | `uv run lint-imports` (4 контракта), `uv run mypy src` (strict), таблица «куда класть логику» |
| Outbox pattern | `application/use_cases/create_payment.py` (один `commit()` на две таблицы), `application/use_cases/publish_outbox.py`, `infrastructure/db/repositories.py` (`SKIP LOCKED`), `infrastructure/messaging/outbox_relay.py` | сценарий 7 демо (`tests/integration/test_outbox_relay.py`): брокер лежит → 202, событие ждёт; поднялся → опубликовано |
| RabbitMQ (очереди, обменники, DLQ) | `infrastructure/messaging/{topology,broker,publisher,consumer}.py` | `tests/integration/test_rabbit_topology.py` (durable и TTL тиров через management API), `tests/integration/test_consumer_flow.py` (ровно одно сообщение в `payments.dlq` после трёх попыток) |
| Идемпотентность | `domain/services.py` (fingerprint), `infrastructure/db/repositories.py` (`ON CONFLICT`, guarded update), `application/use_cases/create_payment.py` | `tests/integration/test_api_payments.py`: повтор → один платёж и одно событие, другое тело → 409 |
| Обработка ошибок и retry | `shared/exceptions/`, `domain/exceptions.py`, `presentation/api/errors.py`, `infrastructure/messaging/consumer.py`, `infrastructure/webhook/http_sender.py` | e2e «приёмник падает → 3 попытки → DLQ»; unit-тесты маппинга кодов в HTTP |
| Docker | `Dockerfile` (multi-stage, non-root, uv), `docker-compose.yml`, `scripts/demo.py` | `docker compose down -v && docker compose up -d --wait` с нуля, healthcheck'и на всех сервисах |

---

## Тесты

```bash
uv run pytest -q                 # всё: 143+ теста
uv run pytest tests/unit -q      # домен, use cases на фейках, адаптеры
uv run pytest tests/integration -q   # живой PostgreSQL и RabbitMQ в контейнерах
uv run pytest -m "not integration" -q  # только то, что не требует Docker
```

Интеграционные тесты поднимают PostgreSQL 16 и RabbitMQ 4 через testcontainers,
прогоняют Alembic и проверяют HTTP-контракты, репозитории, топологию брокера и
полный путь платежа до webhook'а в локальный приёмник.

Проверки целиком:

```bash
make check    # ruff check + ruff format --check + mypy + import-linter + pytest
```

На Windows без `make` те же команды:

```powershell
uv run ruff check . ; uv run ruff format --check . ; uv run mypy src ; uv run lint-imports ; uv run pytest -q
```

---

## Разработка без Docker-стека

```bash
uv sync
docker compose up -d postgres rabbitmq     # только инфраструктура
export DATABASE_URL=postgresql+asyncpg://payments:payments@127.0.0.1:5432/payments
export API_KEY=local-dev-api-key
uv run alembic upgrade head
uv run uvicorn --factory payments.presentation.api.app:create_app --port 8000
```

Consumer и relay (в API он уже работает фоновой задачей):

```bash
uv run python -m payments.presentation.consumer_entrypoint
uv run python -m payments.infrastructure.messaging.outbox_relay   # standalone
```

Новая миграция:

```bash
uv run alembic revision --autogenerate -m "describe the change"
uv run alembic upgrade head
```

---

## Конфигурация

Все параметры читаются из окружения или `.env` (образец — `.env.example`).

| Переменная | По умолчанию | Смысл |
|---|---|---|
| `API_KEY` | `local-dev-api-key` | статический ключ в `X-API-Key` |
| `DATABASE_URL` | собирается из `POSTGRES_*` | строка подключения asyncpg |
| `RABBITMQ_*` | `rabbitmq`/`guest` | подключение к брокеру |
| `OUTBOX_BATCH_SIZE`, `OUTBOX_POLL_INTERVAL_MS` | `100`, `1000` | пачка и период relay |
| `GATEWAY_SUCCESS_RATE`, `GATEWAY_MIN_DELAY_MS`, `GATEWAY_MAX_DELAY_MS`, `GATEWAY_RANDOM_SEED` | `0.9`, `2000`, `5000`, `42` | эмуляция шлюза (seed — для детерминированных тестов) |
| `RETRY_DELAYS_MS` | `5000,25000` | задержки retry-тиров: N значений — N повторов, всего N+1 обработок |
| `WEBHOOK_TIMEOUT_SECONDS`, `WEBHOOK_SIGNING_SECRET` | `5`, `local-dev-signing-secret` | таймаут и HMAC-секрет вебхуков |
| `LOG_LEVEL` | `INFO` | уровень логирования |

**Подпись вебхука:** `X-Webhook-Signature: HMAC-SHA256(secret, raw_body)` в hex,
плюс `X-Webhook-Id` (id события) и `X-Webhook-Attempt` (номер попытки). Пример
проверки — `scripts/webhook_sink.py`.

---

## Структура

```
src/
├── shared/exceptions/          # AppError → DomainError → InfraError
└── payments/
    ├── domain/                 # Payment, Money, инварианты, fingerprint
    ├── application/            # use cases, DTO, порты (Protocol)
    ├── infrastructure/         # SQLAlchemy, RabbitMQ, httpx, gateway, config
    └── presentation/           # FastAPI (api/), consumer entrypoint
migrations/                     # Alembic
scripts/                        # demo.py, webhook_sink.py
docs/                           # ARCHITECTURE.md, ADR, brief
tests/{unit,integration}/
```

---

## Ограничения и что дальше

Осознанно вне рамок тестового задания: возвраты и отмена платежей, список платежей с
пагинацией, мультитенантность и несколько API-ключей, rate limiting, метрики
Prometheus, CI.

Известные риски и направления развития:

- **SSRF через `webhook_url`**: сейчас разрешены любые http(s)-адреса, включая
  внутренние. В продакшене нужен allowlist доменов клиента или запрет приватных
  диапазонов.
- **Webhook на критическом пути обработки**: медленный получатель занимает слот
  consumer'а. При росте нагрузки доставку стоит вынести в отдельную очередь с
  circuit breaker по хосту.
- **Рост нагрузки**: API stateless и масштабируется репликами; обработку ускоряют
  конкурентность consumer'а и `prefetch` (переходы статусов guarded, поэтому дубли
  безопасны); relay масштабируется через `SKIP LOCKED`. Дальше — `LISTEN/NOTIFY` или
  CDC вместо polling, партиционирование `payments` по `created_at`, PgBouncer.
- **Хранение `metadata`**: ограничено 8 КБ и только JSON-объект; вложенные структуры
  замораживаются при создании платежа.
