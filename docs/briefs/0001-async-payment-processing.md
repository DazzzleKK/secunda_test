# Brief 0001 — Асинхронный сервис процессинга платежей (тестовое задание)

## Мета

- **Проект / репозиторий:** `secunda_test` (greenfield, `git init` — по решению оператора)
- **Источник требований:** `secunda_tz.pdf` (тестовое задание, 4 стр.)
- **Стек:** Python 3.12, FastAPI, Pydantic v2, SQLAlchemy 2.0 (async, asyncpg),
  PostgreSQL 16, RabbitMQ 4 + FastStream, Alembic, Dishka (DI), httpx, uv, Docker Compose
- **Тип:** фича (новый сервис с нуля)
- **Класс изменения:** **крупное** — новый домен и приложение целиком
- **Триггеры ревью:** новый слой/домен/приложение; внешние сервисы и необратимые
  операции (деньги, webhook-вызовы); объём >400 строк → `layer-reviewer` обязателен
  на каждой волне и один раз на весь diff
- **Статус:** черновик → ожидает ответов рекрутера → **подтверждено**

## 1. Что требует ТЗ (дословная сводка)

**Сущность Payment:** id, amount (decimal), currency (RUB/USD/EUR), description,
metadata (JSON), status (pending | succeeded | failed), idempotency_key, webhook_url,
created_at / processed_at.

**API**

| Метод | Путь | Вход | Выход |
|-------|------|------|-------|
| POST | `/api/v1/payments` | header `Idempotency-Key` (обязательный), body: amount, currency, description, metadata, webhook_url | 202 Accepted: payment_id, status, created_at |
| GET | `/api/v1/payments/{payment_id}` | — | детальная информация о платеже |

Аутентификация: статический API-ключ в заголовке `X-API-Key` — **для всех эндпоинтов**.

**Брокер:** при создании платежа публикуется событие в очередь `payments.new`.
Один consumer: получает сообщение → эмулирует обработку (2–5 сек, 90% успех, 10% ошибка)
→ обновляет статус в БД → отправляет webhook на указанный URL → повторные попытки
при ошибках отправки.

**Гарантии:** Outbox pattern, Idempotency key, DLQ после 3 попыток.

**Результат:** таблицы `payments` и `outbox`; 2 эндпоинта; один consumer; outbox;
retry 3 попытки с экспоненциальной задержкой; DLQ; docker-compose с postgres,
rabbitmq, api, consumer; README с запуском и примерами.

**Критерии оценки:** архитектура и чистота кода; корректность Outbox; работа с
RabbitMQ (очереди, обменники, DLQ); идемпотентность; обработка ошибок и retry;
работоспособность Docker-окружения.

### Карта критериев оценки (главный фокус)

Всё, что делаем, привязываем к шести критериям из ТЗ. Всё, что баллов не даёт, — вне scope.

| Критерий из ТЗ | Что реально проверяет рекрутер | Чем закрываем | Чем доказываем (файл / команда) |
|---|---|---|---|
| **Архитектура и чистота кода** | домен без импортов ORM/HTTP/брокера; use case без SQLAlchemy; тонкие роутеры и subscriber; явные мапперы; строгая типизация | четыре слоя, порты + Dishka, мапперы ORM↔domain↔DTO↔API-схема, никаких `dict` между слоями | `lint-imports` (контракты import-linter), `mypy --strict`, `docs/ARCHITECTURE.md` со схемой и таблицей «куда класть логику» |
| **Корректная реализация Outbox** | `payments` и `outbox` пишутся **одной** транзакцией; публикует только relay; `published_at` — после подтверждения брокера; relay масштабируется; есть метрика отставания | `CreatePaymentUseCase` — один `commit()` на две вставки; `PublishOutboxUseCase` + relay с publisher confirms и `FOR UPDATE SKIP LOCKED` | тест «брокер лежит → POST всё равно 202, событие уезжает после подъёма брокера, ничего не потеряно»; тест двух параллельных relay на одной пачке |
| **Работа с RabbitMQ** | топология объявлена кодом (не руками в UI); durable exchanges/queues; DLX→retry→DLQ реально работают; prefetch; ack после commit; нет `requeue=True` | `topology.py` — единственное место объявления; topic exchange, 3 retry-тира с TTL и DLX, `payments.dlq`, prefetch=10, manual ack после фиксации в БД | e2e: сбой → 3 попытки → ровно **1** сообщение в `payments.dlq` с `x-error`/`x-attempt`; проверка `durable` и аргументов очередей через management API |
| **Идемпотентность** | UNIQUE в БД, а не `SELECT`-then-`INSERT`; конкурентные дубли без 500; повтор отдаёт исходный результат; повторная доставка не списывает дважды | `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING`, `request_fingerprint`, guarded `UPDATE ... WHERE status='pending'`, наш `payment_id` как ключ идемпотентности в шлюз | тест: 20 параллельных POST с одним ключом → 1 строка, 1 событие, все ответы 202 с одним `payment_id`; тест повторной доставки события → 1 webhook |
| **Обработка ошибок и retry** | бизнес-отказ ≠ техническая ошибка; envelope; backoff с лимитом; таймауты внешних вызовов; нет проглоченных исключений | `AppError`→`DomainError`→`PaymentsDomainError`→конкретный тип с контекстом; `ErrorEnvelope`; тир-очереди 5/25/125 с; httpx timeout=5s; логи с `payment_id`/`event_id` | unit-тесты маппинга `code`→HTTP; e2e «sink падает 2 раза, на 3-й отдаёт 200 → платёж `succeeded`, webhook доставлен» |
| **Работоспособность Docker** | `up` с нуля без ручных шагов; healthcheck'и; миграции автоматически; не root; образ без мусора | compose: postgres, rabbitmq, migrate, api, consumer (+ `webhook-sink` под профилем `demo`); healthchecks и `--wait`; multi-stage Dockerfile; `.env.example` | `docker compose down -v && docker compose up -d --wait` на чистой машине + `scripts/demo` с 7 сценариями и печатью результата |

**Сознательно не делаем** (баллов не даёт, съедает бюджет): Prometheus/Grafana, Kubernetes,
возвраты и отмены, список платежей с пагинацией, мультитенантность, свой UI, внешний CI,
полная защита от SSRF (фиксируем риск и путь решения текстом).

**Распределение усилий:** гарантии доставки (outbox + идемпотентность + retry/DLQ) ≈ 40 %,
слоистость и типизация ≈ 25 %, топология RabbitMQ ≈ 15 %, Docker и демо ≈ 10 %,
README/ADR ≈ 10 %.

**Сценарий демонстрации** (в README и в `scripts/demo`):

1. `docker compose up -d --wait` — все healthcheck'и зелёные.
2. POST → 202; GET → `pending`; через ~3 с → `succeeded`; webhook пришёл в sink.
3. Повтор того же POST с тем же ключом → 202 + `Idempotency-Replayed: true`, в БД одна строка.
4. Тот же ключ, другая сумма → 409 `idempotency_conflict`; без `X-API-Key` → 401; без `Idempotency-Key` → 400.
5. Sink в режиме «всегда 500» → 3 попытки с ростом задержки → одно сообщение в `payments.dlq`.
6. `docker compose stop rabbitmq` → POST отдаёт 202, строка в `outbox` ждёт → `start rabbitmq` → relay публикует, платёж обрабатывается (доказательство outbox).
7. `SELECT status, count(*) FROM payments GROUP BY status` и `SELECT ... FROM outbox` — состояние на экране.

Задержки тиров конфигурируемы (`RETRY_DELAYS_MS`), демо-скрипт ставит 1/2/4 с, чтобы
пункт 5 занимал секунды, а не 2.5 минуты.

## 2. Карта архитектуры проекта

Репозиторий пустой — слои берём из профиля clean-architecture скилла
(`stack-profiles/clean-architecture.md`), домен — один bounded context `payments`.

| Слой | Пути | Содержимое |
|------|------|------------|
| Domain | `src/payments/domain/` | сущности, value objects, enum'ы, доменные исключения; zero imports из внешних слоёв |
| Application | `src/payments/application/` | use cases, DTO, ports (Protocol), сервис политики |
| Infrastructure | `src/payments/infrastructure/` | SQLAlchemy-модели, репозитории, UoW, шлюз-эмулятор, RabbitMQ (FastStream), httpx webhook-сендер, config |
| Presentation | `src/payments/presentation/` | FastAPI-роутеры, схемы, DI-провайдеры, exception handlers, FastStream subscriber |
| Shared kernel | `src/shared/exceptions/` | `AppError` → `DomainError` (без импорта домена) |

**Правило зависимостей:** `presentation → application → domain ← infrastructure`.
Инфраструктура **реализует** порты из application. Контроль — `import-linter`
(запрет `infrastructure`/`presentation` в `domain`/`application`), запускается в
`make lint` и в финальной проверке.

**Домены:** один — `payments`. Порт `PaymentGateway` — единственная точка внешнего мира.

**DI / wiring:** Dishka (`dishka.integrations.fastapi` + `dishka.integrations.faststream`),
один `AppProvider` со scope: APP → REQUEST (API) / APP (consumer). Роутеры и
subscriber получают use case через `FromDishka[...]`. Composition root:
`src/payments/presentation/api/app.py` (API) и `src/payments/presentation/consumer.py`
(consumer).

```
src/
├── shared/exceptions/{base.py,domain.py}
└── payments/
    ├── domain/{enums.py,value_objects.py,entities.py,exceptions.py,services.py}
    ├── application/
    │   ├── dto/                       # пакет: один DTO на файл
    │   ├── ports/                     # пакет: один порт на файл
    │   └── use_cases/{create_payment.py,get_payment.py,process_payment.py,
    │                  deliver_webhook.py,publish_outbox.py}
    ├── infrastructure/
    │   ├── config.py
    │   ├── db/{models.py,mappers.py,session.py,repositories.py,unit_of_work.py}
    │   ├── gateway/fake_gateway.py
    │   ├── messaging/{topology.py,broker.py,publisher.py,consumer.py,outbox_relay.py}
    │   └── webhook/{http_sender.py,signer.py}
    └── presentation/
        ├── api/{app.py,dependencies.py,security.py,schemas.py,error_handlers.py,
        │        routers/payments.py,routers/health.py}
        └── consumer_entrypoint.py
```

## 3. Цель (зачем)

Тестовое задание проверяет не «умею ли я FastAPI», а три вещи: (1) понимание
проблемы dual-write и Outbox pattern; (2) умение сделать идемпотентную обработку
при at-least-once доставке; (3) дисциплину слоёв и обработки ошибок. Поэтому
сервис строится так, чтобы каждое из этих мест было явным, проверяемым тестом и
объяснимым на собеседовании.

## 4. Ключевые решения (что спросят на собеседовании — и что отвечаем)

| # | Вопрос интервьюера | Решение в коде |
|---|--------------------|----------------|
| 1 | Зачем outbox, если у RabbitMQ есть publisher confirms? | Confirms не убирают окно между `COMMIT` и публикацией (краш процесса, недоступный брокер). Источник истины — БД: строка `payments` и строка `outbox` пишутся в одной транзакции, публикацию догоняет relay. Confirms **включаем** — чтобы не помечать `published_at` при потерянном сообщении. |
| 2 | Exactly-once? | Нет. At-least-once доставка + идемпотентная обработка = exactly-once **эффект**. Реализация: условный `UPDATE ... WHERE status='pending' RETURNING` + `webhook_delivered_at IS NULL`. |
| 3 | Relay упал между publish и `mark_published` — дубль? | Да, дубль доставки. Consumer на дубле видит финальный статус и `webhook_delivered_at` → no-op + ack. Дубль webhook'а гасится на стороне получателя по `X-Webhook-Id` (event_id). |
| 4 | Два API-пода с одним `Idempotency-Key` одновременно | `INSERT ... ON CONFLICT (idempotency_key) DO NOTHING RETURNING id` (не exception-driven). Если строки нет — читаем существующую и сравниваем fingerprint: совпал → replay, не совпал → 409. |
| 5 | Тот же ключ, другой body | Храним `request_fingerprint` = sha256 от канонизированных полей. Расхождение → `IdempotencyConflictError` → 409 (поведение Stripe). |
| 6 | Почему не `SELECT ... FOR UPDATE` на платеже? | Блокировка строки на время 2–5 сек эмуляции = держим транзакцию и соединение. Условный UPDATE без удержания блокировки + idempotent guarded переход. |
| 7 | 10% «ошибка» шлюза — это retry или бизнес-провал? | Бизнес-провал: `status=failed`, webhook со `failed`, ack без retry. Retry — только на **технические** сбои (таймаут/5xx/недоступность БД/брокера). Иначе ретраим списание денег. |
| 8 | Как не списать дважды при retry на реальном шлюзе? | `PaymentGateway.charge(payment, idempotency_key=payment.id)` — наш payment_id как ключ идемпотентности PSP; повторный вызов с тем же ключом возвращает тот же результат. |
| 9 | Где считать попытки? | Свой заголовок `x-attempt` (relay ставит 0, consumer инкрементирует при отправке в retry-очередь) + `x-death` как внешняя сверка. Не полагаемся на `requeue=True` — он даёт бесконечный цикл. |
| 10 | Экспоненциальная задержка: `sleep` в обработчике? | Нет: удержание сообщения блокирует слот и рвёт heartbeat. Три retry-очереди с фиксированным TTL (5s / 25s / 125s) и DLX обратно в рабочую очередь. Per-message TTL в одной очереди даёт head-of-line blocking — поэтому тиры. |
| 11 | Кто читает DLQ? | Никто автоматически — это свалка для разбора. `payments.dlq` с заголовками `x-error`, `x-attempt`, `x-failed-at`; CLI `python -m payments.cli dlq peek/replay` + метрика глубины. |
| 12 | Битое сообщение (poison) | Валидация payload падает → сразу DLQ + ack, лимит попыток не тратится. Никогда `requeue=True`. Платёж не найден → то же. |
| 13 | Порядок сообщений | Не гарантируем и не нужен: платежи независимы. Если понадобится — consistent-hash exchange по payment_id или single-active-consumer. |
| 14 | Slow/hanging webhook | httpx c `timeout=5s`, connection pool, никаких бесконечных ретраев внутри процесса. При росте нагрузки webhook уезжает в отдельную очередь, чтобы медленный клиент не тормозил процессинг. |
| 15 | SSRF через `webhook_url` | Разрешаем только `http/https`, хост обязателен; в README фиксируем риск (внутренние адреса) и путь решения: allowlist/deny приватных диапазонов. |
| 16 | Подпись webhook'а | HMAC-SHA256(`WEBHOOK_SIGNING_SECRET`, body) в `X-Webhook-Signature`, плюс `X-Webhook-Id` (event_id) и `X-Webhook-Attempt`. Требование ТЗ не содержит, но стоит ~20 строк и снимает вопрос «а как получатель проверит подлинность». |
| 17 | Деньги | `NUMERIC(18,2)` + `CHECK (amount > 0)`, Pydantic `Decimal` с `max_digits=18, decimal_places=2, gt=0`; в JSON — **строкой** (`"100.50"`), чтобы не потерять точность на float. Никогда `float`. |
| 18 | Транзакции и миграции в compose | Alembic async-шаблон; миграции выполняет отдельный сервис `migrate` (или `alembic upgrade head` в entrypoint **с pg advisory lock**) — иначе N контейнеров мигрируют параллельно. |
| 19 | Почему ключ в заголовке, а не в теле? | Так требует ТЗ; заголовок не участвует в fingerprint, что и нужно — иначе replay с тем же ключом всегда конфликтовал бы. |
| 20 | Что если шлюз вернул ошибку, а мы уже отправили webhook? | Невозможно: webhook отправляется только после фиксации финального статуса в БД; любая ошибка до этого → retry, статус остаётся `pending`. |
| 21 | Ретраи webhook'а vs ретраи сообщения — не удвоение? | Один механизм: попытка сообщения = одна попытка webhook'а. Внутри процесса backoff не крутим, лимит 3 попытки → DLQ. Это закрывает и «повторные попытки при ошибках отправки», и «DLQ после 3 попыток» из ТЗ без двойного счётчика. |
| 22 | Наблюдаемость | structlog JSON с `payment_id`, `event_id`, `attempt`; `/health` (liveness) и `/ready` (БД + брокер); метрики outbox lag (возраст самой старой неопубликованной записи) и глубины DLQ. |

## 5. Схема данных (2 таблицы, как требует ТЗ)

**payments**

| Колонка | Тип | Примечание |
|---------|-----|------------|
| id | UUID PK | генерируется приложением |
| amount | NUMERIC(18,2) NOT NULL | CHECK (amount > 0) |
| currency | VARCHAR(3) NOT NULL | CHECK IN (RUB, USD, EUR) |
| description | TEXT NOT NULL | ≤1024 символа (валидация) |
| metadata | JSONB NOT NULL DEFAULT '{}' | ≤8 КБ, только объект |
| status | VARCHAR(16) NOT NULL | CHECK IN (pending, succeeded, failed) |
| idempotency_key | VARCHAR(255) NOT NULL | **UNIQUE** |
| request_fingerprint | CHAR(64) NOT NULL | sha256 канонизированного запроса |
| webhook_url | TEXT NOT NULL | |
| failure_reason | TEXT NULL | причина `failed` от шлюза |
| webhook_attempts | INT NOT NULL DEFAULT 0 | |
| webhook_delivered_at | TIMESTAMPTZ NULL | NULL = не доставлен |
| webhook_last_error | TEXT NULL | |
| created_at / updated_at | TIMESTAMPTZ NOT NULL | UTC, server default `now()` |
| processed_at | TIMESTAMPTZ NULL | момент финального статуса |

Индексы: UNIQUE(`idempotency_key`); (`status`, `created_at`) — поиск «зависших»;
(`created_at`).

**outbox**

| Колонка | Тип | Примечание |
|---------|-----|------------|
| id | UUID PK | |
| aggregate_id | UUID NOT NULL | payment_id |
| event_type | VARCHAR(64) NOT NULL | `payment.created` |
| routing_key | VARCHAR(128) NOT NULL | `payments.new` |
| payload | JSONB NOT NULL | тело события |
| attempts | INT NOT NULL DEFAULT 0 | попытки публикации |
| last_error | TEXT NULL | |
| created_at | TIMESTAMPTZ NOT NULL | |
| published_at | TIMESTAMPTZ NULL | NULL = ждёт публикации |

Индекс: partial (`created_at`) WHERE `published_at IS NULL` — выборка relay
(`ORDER BY created_at LIMIT n FOR UPDATE SKIP LOCKED`).

## 6. Топология RabbitMQ

```
exchange payments (topic, durable)
  rk payments.new ──► queue payments.new        (durable, quorum, prefetch=10)
                        │ nack/DLX
  retry-tier exchange payments.retry (direct, durable)
  rk retry.1 ──► queue payments.retry.1  (x-message-ttl=5000,   DLX→payments/payments.new)
  rk retry.2 ──► queue payments.retry.2  (x-message-ttl=25000,  DLX→payments/payments.new)
  rk retry.3 ──► queue payments.retry.3  (x-message-ttl=125000, DLX→payments/payments.new)
  rk payments.dlq ──► queue payments.dlq (durable, без consumer'а)
```

- Публикует: outbox relay (publisher confirms = on, `mandatory=True`).
- Потребляет: один consumer, `prefetch_count=10`, `ack` после успешного шага.
- Исчерпание 3 попыток → публикация в `payments.dlq` с `x-error`, `x-attempt` + ack оригинала.
- Имена/тип обменника — открытый вопрос рекрутеру (в ТЗ названа только `payments.new`).

## 7. Интерфейсы

### `src/payments/domain/enums.py` — Domain

```python
class PaymentStatus(StrEnum):
    PENDING = 'pending'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'


class Currency(StrEnum):
    RUB = 'RUB'
    USD = 'USD'
    EUR = 'EUR'


class EventType(StrEnum):
    PAYMENT_CREATED = 'payment.created'
```

### `src/payments/domain/value_objects.py` — Domain

```python
@dataclass(frozen=True, slots=True)
class Money:
    amount: Decimal          # > 0, ровно 2 знака
    currency: Currency

    def as_string(self) -> str: ...          # '100.50' для JSON


@dataclass(frozen=True, slots=True)
class WebhookTarget:
    url: str                                 # только http/https, есть host


@dataclass(frozen=True, slots=True)
class IdempotencyKey:
    value: str                               # 1..255 символов
```

### `src/payments/domain/entities.py` — Domain

```python
@dataclass(slots=True)
class Payment:
    id: UUID
    money: Money
    description: str
    metadata: Mapping[str, Any]
    idempotency_key: str
    request_fingerprint: str
    webhook_url: str
    status: PaymentStatus
    created_at: datetime
    updated_at: datetime
    processed_at: datetime | None
    failure_reason: str | None
    webhook_attempts: int
    webhook_delivered_at: datetime | None
    webhook_last_error: str | None

    @property
    def is_final(self) -> bool: ...

    @property
    def webhook_pending(self) -> bool: ...

    def mark_succeeded(self, now: datetime) -> bool: ...   # False, если переход уже совершён
    def mark_failed(self, reason: str, now: datetime) -> bool: ...


@dataclass(frozen=True, slots=True)
class OutboxEvent:
    id: UUID
    aggregate_id: UUID
    event_type: EventType
    routing_key: str
    payload: Mapping[str, Any]
    created_at: datetime
    attempts: int = 0
    last_error: str | None = None
    published_at: datetime | None = None

    @classmethod
    def payment_created(cls, payment: Payment, *, now: datetime) -> 'OutboxEvent': ...
```

### `src/payments/domain/exceptions.py` — Domain

```python
class PaymentsDomainError(DomainError): ...            # code = 'payments_error'
class PaymentNotFoundError(PaymentsDomainError): ...   # code = 'payment_not_found',  payment_id
class IdempotencyConflictError(PaymentsDomainError): ...# code = 'idempotency_conflict', idempotency_key
class InvalidAmountError(PaymentsDomainError): ...     # code = 'invalid_amount',       amount
class WebhookDeliveryError(DomainError): ...           # code = 'webhook_delivery_failed', attempts (→ retry/DLQ)
class PaymentProcessingError(DomainError): ...         # code = 'payment_processing_failed' (технический сбой шлюза → retry)
```

### `src/payments/application/dto/` — Application (пакет)

```python
@dataclass(frozen=True, slots=True)
class CreatePaymentCommand:
    amount: Decimal
    currency: Currency
    description: str
    metadata: Mapping[str, Any]
    webhook_url: str
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class CreatePaymentResult:
    payment: PaymentDto
    replayed: bool                       # True — идемпотентный повтор


@dataclass(frozen=True, slots=True)
class PaymentDto: ...                    # снимок Payment без ORM


@dataclass(frozen=True, slots=True)
class PaymentCreatedEvent:
    event_id: UUID
    payment_id: UUID
    attempt: int


@dataclass(frozen=True, slots=True)
class ChargeResult:
    approved: bool
    reason: str | None


@dataclass(frozen=True, slots=True)
class WebhookPayload:
    event_id: UUID
    event: str
    payment_id: UUID
    status: str
    amount: str
    currency: str
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class WebhookDeliveryResult:
    delivered: bool
    status_code: int | None
    error: str | None
```

### `src/payments/application/ports/` — Application (пакет)

```python
class PaymentRepositoryProtocol(Protocol):
    async def add_if_absent(self, payment: Payment) -> bool: ...
    async def get(self, payment_id: UUID) -> Payment | None: ...
    async def get_by_idempotency_key(self, key: str) -> Payment | None: ...
    async def apply_outcome(self, payment_id: UUID, outcome: PaymentOutcome) -> bool: ...   # guarded UPDATE
    async def register_webhook_attempt(self, payment_id: UUID, result: WebhookDeliveryResult,
                                       *, now: datetime) -> None: ...


class OutboxRepositoryProtocol(Protocol):
    async def add(self, event: OutboxEvent) -> None: ...
    async def fetch_unpublished(self, limit: int) -> Sequence[OutboxEvent]: ...   # FOR UPDATE SKIP LOCKED
    async def mark_published(self, event_id: UUID, *, now: datetime) -> None: ...
    async def register_failure(self, event_id: UUID, error: str) -> None: ...
    async def oldest_unpublished_age(self) -> timedelta | None: ...               # метрика lag


class UnitOfWorkProtocol(Protocol):
    payments: PaymentRepositoryProtocol
    outbox: OutboxRepositoryProtocol
    async def __aenter__(self) -> 'UnitOfWorkProtocol': ...
    async def __aexit__(self, *exc_info: object) -> None: ...
    async def commit(self) -> None: ...
    async def rollback(self) -> None: ...


class PaymentGatewayProtocol(Protocol):
    async def charge(self, payment: Payment, *, idempotency_key: UUID) -> ChargeResult: ...


class EventPublisherProtocol(Protocol):
    async def publish(self, event: OutboxEvent) -> None: ...      # ждёт publisher confirm


class WebhookSenderProtocol(Protocol):
    async def send(self, target: WebhookTarget, payload: WebhookPayload, *,
                   attempt: int) -> WebhookDeliveryResult: ...


class ClockProtocol(Protocol):
    def now(self) -> datetime: ...


class IdGeneratorProtocol(Protocol):
    def new_id(self) -> UUID: ...
```

### `src/payments/application/use_cases/` — Application

```python
class CreatePaymentUseCase:
    def __init__(self, uow_factory: UnitOfWorkFactory, clock: ClockProtocol,
                 ids: IdGeneratorProtocol) -> None: ...
    async def execute(self, command: CreatePaymentCommand) -> CreatePaymentResult: ...
    # IdempotencyConflictError | InvalidAmountError


class GetPaymentUseCase:
    def __init__(self, uow_factory: UnitOfWorkFactory) -> None: ...
    async def execute(self, payment_id: UUID) -> PaymentDto: ...     # PaymentNotFoundError


class ProcessPaymentUseCase:
    def __init__(self, uow_factory: UnitOfWorkFactory, gateway: PaymentGatewayProtocol,
                 clock: ClockProtocol) -> None: ...
    async def execute(self, event: PaymentCreatedEvent) -> ProcessPaymentResult: ...
    # PaymentProcessingError (технический сбой → retry)


class DeliverWebhookUseCase:
    def __init__(self, uow_factory: UnitOfWorkFactory, sender: WebhookSenderProtocol,
                 clock: ClockProtocol) -> None: ...
    async def execute(self, event: PaymentCreatedEvent) -> WebhookDeliveryResult: ...
    # WebhookDeliveryError (→ retry/DLQ)


class PublishOutboxUseCase:
    def __init__(self, uow_factory: UnitOfWorkFactory, publisher: EventPublisherProtocol,
                 clock: ClockProtocol) -> None: ...
    async def execute(self, *, batch_size: int = 100) -> int: ...      # сколько опубликовано
```

### `src/payments/infrastructure/messaging/topology.py` — Infrastructure

```python
@dataclass(frozen=True, slots=True)
class TopologyNames:
    exchange: str = 'payments'
    dlq_exchange: str = 'payments.dlx'
    retry_exchange: str = 'payments.retry'
    work_queue: str = 'payments.new'
    dlq: str = 'payments.dlq'
    retry_queues: tuple[str, ...] = ('payments.retry.1', 'payments.retry.2', 'payments.retry.3')
    retry_delays_ms: tuple[int, ...] = (5_000, 25_000, 125_000)
    max_attempts: int = 3
    retry_for_attempt: Callable[[int], str | None]   # attempt -> retry queue | None (-> DLQ)
```

### Presentation (HTTP-контракты)

```python
class PaymentCreateRequest(BaseModel):        # amount: Decimal(max_digits=18, decimal_places=2, gt=0)
    amount: Decimal
    currency: Currency
    description: str = Field(max_length=1024)
    metadata: dict[str, Any] = Field(default_factory=dict)
    webhook_url: str


class PaymentCreateResponse(BaseModel):       # 202
    payment_id: UUID
    status: PaymentStatus
    created_at: datetime


class PaymentResponse(BaseModel):             # 200
    payment_id: UUID
    amount: str
    currency: Currency
    description: str
    metadata: dict[str, Any]
    status: PaymentStatus
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None
    failure_reason: str | None
    webhook_delivered_at: datetime | None
```

**Ошибки → HTTP** (`ErrorEnvelope {code, message, details}` из скилла):

| code | HTTP | Когда |
|------|------|-------|
| `unauthorized` | 401 | нет/неверный `X-API-Key` |
| `missing_idempotency_key` | 400 | нет заголовка `Idempotency-Key` |
| `payment_not_found` | 404 | GET неизвестного id |
| `idempotency_conflict` | 409 | тот же ключ + другой fingerprint |
| `payment_not_visible` | 409 | строка с ключом закоммичена, но ещё не видна — повторить запрос |
| `payment_not_finalized` | 409 | webhook запрошен до финального статуса |
| `validation_error` | 422 | Pydantic (форма запроса, путь, типы) |
| `invalid_amount` | 422 | сумма: не положительная, не 2 знака, больше 18 разрядов |
| `invalid_currency` | 422 | валюта не из RUB/USD/EUR |
| `invalid_metadata` | 422 | metadata не JSON-объект, не сериализуется или больше 8 КБ |
| `invalid_webhook_url` | 422 | webhook_url не абсолютный http(s) с хостом |
| `invalid_idempotency_key` | 422 | ключ пустой или длиннее 255 символов |
| `payment_gateway_unavailable`, `payment_processing_failed` | 503 | технический сбой шлюза (ретраится в очереди) |
| `webhook_delivery_failed` | 503 | webhook не доставлен (ретраится в очереди) |
| `infra_error` | 503 | непереведённый сбой инфраструктуры (БД) |
| `internal_error` | 500 | непойманное |

`details` — только whitelist: `payment_id`, `idempotency_key`, `attempts`, `scheme`, `has_host`,
`size_bytes`, `limit_bytes`, `idempotency_key_length`. Сам `webhook_url` в ответ не попадает:
в userinfo могут быть учётные данные.

## 8. Поведение после изменений

1. `POST /api/v1/payments` с валидным `X-API-Key` и `Idempotency-Key` создаёт
   `payments` + `outbox` в одной транзакции и возвращает `202` с
   `{payment_id, status: "pending", created_at}` (плюс `Location`).
2. Повтор с тем же ключом и тем же телом возвращает `202` с теми же данными и
   заголовком `Idempotency-Replayed: true`; с другим телом — `409`.
3. Relay читает неопубликованные строки `outbox` (`SKIP LOCKED`), публикует в
   `payments.new` и помечает `published_at`.
4. Consumer: эмулирует обработку 2–5 сек с вероятностью успеха 0.9 → финальный
   статус в БД → webhook → при технической ошибке retry через тир-очередь.
5. После 3 неудачных попыток сообщение уходит в `payments.dlq`; платёж остаётся
   в последнем зафиксированном статусе (для `succeeded` webhook-сбой не откатывает
   деньги).
6. `GET /api/v1/payments/{id}` отдаёт детальную информацию, включая webhook-поля.
7. `docker compose up --wait` поднимает postgres, rabbitmq, migrate, api, consumer;
   `/ready` отвечает 200 только при живых БД и брокере.

## 9. Вне scope

- Возвраты/отмены, `GET /payments` со списком и пагинацией, фильтры.
- Мультитенантность, несколько API-ключей, rate limiting.
- Realtime-метрики Prometheus/Grafana (только `/health`, `/ready` и структурированные логи).
- Proof-of-concept «правильный» прод-деплой (k8s, blue/green).
- Полная защита от SSRF (фиксируем риск и путь решения в README).

## 10. Критерии готовности

**Волна 1 — скелет, домен, порты**
- [ ] `uv sync`, `ruff check`, `ruff format --check`, `mypy --strict src` — без ошибок
- [ ] `lint-imports` (import-linter) проходит: domain/application не зависят от внешних слоёв
- [ ] Unit-тесты домена: инварианты Money/IdempotencyKey/WebhookTarget, переходы статусов, `OutboxEvent.payment_created`

**Волна 2 — персистентность**
- [ ] `payments` и `outbox` создаются одной миграцией Alembic (`alembic upgrade head` на пустой БД)
- [ ] `downgrade` работает
- [ ] Тест `add_if_absent` → False на повторе, `apply_outcome` → False на повторном переходе
- [ ] `fetch_unpublished` не отдаёт одну строку двум параллельным relay (тест на `SKIP LOCKED`)

**Волна 3 — API**
- [ ] Интеграционные тесты: 202 / 202-replay / 409 / 200 / 404 / 401 / 422, формат `ErrorEnvelope`
- [ ] `/health` и `/ready` отвечают ожидаемо (ready = 503 при мёртвой БД)
- [ ] OpenAPI (`/docs`) описывает `X-API-Key` и `Idempotency-Key`

**Волна 4 — брокер, consumer, outbox, webhook**
- [ ] Топология объявляется идемпотентно при старте consumer'а; повторный старт не падает
- [ ] Атрибуты очередей проверены через management API: `durable`, TTL тиров, DLX-аргументы
- [ ] E2E на реальных Postgres+RabbitMQ: платёж доходит до `succeeded`, webhook получен локальным sink'ом
- [ ] Детерминированная эмуляция (seed/настраиваемая вероятность): 10% → `failed` + webhook
- [ ] Брокер недоступен в момент создания: POST отдаёт 202, строка `outbox` ждёт, после подъёма брокера событие публикуется (доказательство outbox)
- [ ] Технический сбой → 3 попытки с ростом задержки → ровно одно сообщение в `payments.dlq` с `x-error`/`x-attempt`
- [ ] Sink падает дважды и отдаёт 200 на третий раз → платёж `succeeded`, webhook доставлен (проверка ladder'а)
- [ ] Дубль доставки не приводит к повторному списанию и повторному webhook'у
- [ ] HMAC-подпись webhook'а проверяется тестом
- [ ] `webhook-sink` для локальной демонстрации (профиль `demo`), не влияет на базовый `docker compose up`

**Волна 5 — Docker, документация, финальная проверка**
- [ ] `docker compose down -v && docker compose up -d --wait` поднимает всё с нуля; healthcheck'и проходят
- [ ] `scripts/demo` прогоняет 7 сценариев из карты критериев и печатает результат
- [ ] README: запуск, примеры curl, таблица «критерий → где смотреть в коде», ограничения, путь масштабирования
- [ ] `docs/ARCHITECTURE.md`: схема слоёв, направление зависимостей, поток платежа, таблица «куда класть логику»
- [ ] ADR с тремя ключевыми решениями: outbox вместо dual-write, retry-тиры вместо sleep, идемпотентность через ON CONFLICT
- [ ] Полный прогон `make check` (lint + mypy + import-linter + tests) зелёный
- [ ] Ревью всего diff (`layer-reviewer`) — Critical закрыты

## 11. Решения оператора

Вопросы рекрутеру сняты («по стеку всё понятно, фокус на критериях оценки»).
Работаем по рекомендациям ниже; любое можно отменить до старта волны 1.

| # | Вопрос | Решение |
|---|--------|---------|
| 1 | Ретраи: webhook или всё сообщение | Один механизм: попытка сообщения = попытка webhook'а, лимит 3, дальше DLQ |
| 2 | Что делает 10 % «ошибки» шлюза | Бизнес-отказ: `failed` + webhook, без retry. Retry только на технические сбои |
| 3 | Область уникальности `Idempotency-Key` | Глобально (однотенантный сервис), UNIQUE-индекс в БД |
| 4 | Тот же ключ, другое тело | 409 `idempotency_conflict` по `request_fingerprint` |
| 5 | Ответ на идемпотентный повтор | 202 с тем же телом + заголовок `Idempotency-Replayed: true` |
| 6 | Имена очередей и тип обменника | `payments.new` — очередь и routing key на topic-exchange `payments`; retry-тиры и `payments.dlq` — наши имена |
| 7 | Дополнительные колонки | Да: `request_fingerprint`, `webhook_attempts`, `webhook_delivered_at`, `webhook_last_error`, `failure_reason`. Таблиц ровно две, как в ТЗ |
| 8 | Статус `processing` | Не вводим, строго `pending/succeeded/failed` |
| 9 | Тесты | Да: unit (домен, маппинг ошибок) + интеграционные (API, репозитории, e2e на живых Postgres+RabbitMQ) |
| 10 | Бонусы | Включаем HMAC-подпись webhook'а, CLI для DLQ, `/health` + `/ready`, `docs/ARCHITECTURE.md` + ADR, `webhook-sink` под профилем `demo`, `scripts/demo`. Прометея нет |
| 11 | Язык | README, ADR и `docs/` — по-русски; код, идентификаторы и короткие докстринги — по-английски |
| 12 | Git | `git init`, атомарные коммиты по волнам (коммиты подписываются конфигом Keril) |
| 13 | Нагрузка | Вне критериев: корректность гарантий важнее RPS; путь масштабирования описан в README, код к нему готов (SKIP LOCKED, prefetch, конфигурируемая конкурентность) |

## 12. Отклонения (заполняется по ходу работы)

| # | Что уточнили | Почему |
|---|--------------|--------|
| 1 | Все пять use case'ов перенесены из волн 3–4 в волну 1 | Application — чистый слой без фреймворков: инфраструктурные волны только реализуют порты и не трогают application |
| 2 | `WebhookDeliveryError` и `PaymentProcessingError` наследуют `PaymentsDomainError` | В §7 brief у них по недосмотру указан `DomainError`; единая база нужна для одного handler'а |
| 3 | Добавлен `PaymentNotFinalizedError` (code `payment_not_finalized`) | Инвариант «webhook только для финального платежа» — его не было в списке исключений |
| 4 | `PaymentOutcome.reason` обязателен для `FAILED`, иначе подставляется фиксированный текст | ТЗ требует объяснимую причину отказа, шлюз может её не вернуть |
| 5 | `ProcessPaymentUseCase` оборачивает `PaymentGatewayUnavailableError` в `PaymentProcessingError` (`from exc`) | Транспортная политика retry различает технический сбой и бизнес-отказ |
| 6 | `ProcessPaymentResult = {payment_id, status, applied}` | DTO не был описан в §7, только тип возврата |
| 7 | Нарушения VO `webhook_url`/`idempotency_key` пока дают код `payments_error` | Волна 3 добавляет `InvalidWebhookTargetError`/`InvalidIdempotencyKeyError` и маппинг в 422, иначе такие запросы дадут 500 |
| 8 | `x-attempt` в заголовке сообщения 0-based (число сделанных попыток), `X-Webhook-Attempt` в payload — 1-based | Явно фиксируем смещение, чтобы не разошлись политика ретраев и наблюдаемость |
| 9 | `config.py` перенесён из волны 3 в волну 2 | URL БД нужен `migrations/env.py` уже в волне 2 |
| 10 | `testcontainers[postgres]` в dev-зависимостях | Интеграционные тесты поднимают настоящий PostgreSQL и остаются самодостаточными |
| 11 | `ruff extend-exclude = ["docs"]` | ruff 0.17 форматирует python-блоки внутри Markdown и покушался на brief |
| 12 | `include_external_packages = true` в конфиге import-linter | Иначе контракты на внешние модули (sqlalchemy, fastapi) не проверяются |
| 13 | `make check` дополняется явными командами для Windows | На машине оператора нет `make`; README даёт оба пути |
| 14 | Сигнатура `UnitOfWorkProtocol.__aexit__` — стандартная трёхаргументная | Протокол с `*exc_info: object` несовместим с реализацией; mypy strict это ловит |
| 15 | Новые доменные коды: `invalid_currency`, `invalid_metadata`, `invalid_webhook_url`, `invalid_idempotency_key`, `payment_not_visible` | Ревью волны 1: VO-нарушения уходили в общий `payments_error` → 500; гонка по ключу переиспользовала код технического сбоя шлюза |
| 16 | `metadata` канонизируется в домене: глубоко замораживается, нормализуется JSON и ограничивается 8 КБ; тот же снимок идёт в fingerprint | Ревью I1/I3: shallow-копия позволяла изменить `metadata` после расчёта fingerprint, а размер проверялся только будущей схемой API |
| 17 | Вставки в БД идут через Core-таблицу (`PaymentModel.__table__`), а не через ORM-сущность | ORM-вставка резолвит ключ `metadata` как `Base.metadata` и падает с `AttributeError` |
| 18 | `fetch_unpublished` не отдаёт события, у которых `attempts >= 5` (парковка), а `last_error` обрезается до 500 символов | Ревью I5: «отравленное» событие переопубликовывалось бы вечно, а текст ошибки брокера писался целиком |
| 19 | `PublishOutboxUseCase` не коммитит пустую пачку | Ревью M9: лишний commit на каждом тике relay |
| 20 | `testcontainers.community.postgres` вместо `testcontainers.postgres` | Прежний путь помечен deprecated в testcontainers 4.15 |

## 13. Волны работ

| Волна | Задачи | Слой / каталог | Файлы | Зависит от |
|-------|--------|----------------|-------|------------|
| 1 | Скелет проекта (pyproject, uv, ruff/mypy/import-linter, Makefile), shared exceptions, домен (enum'ы, VO, сущности, исключения), DTO и порты application, unit-тесты домена | `shared/`, `payments/domain/`, `payments/application/`, корень | ~20 | — |
| 2 | SQLAlchemy-модели, мапперы, репозитории, UoW, engine/session, Alembic (async) + первая миграция, тесты репозиториев | `payments/infrastructure/db/`, `migrations/` | ~12 | 1 |
| 3 | Config (pydantic-settings), Dishka-провайдеры, FastAPI app + lifespan, auth `X-API-Key`, схемы, exception handlers, роутеры payments/health, use cases Create/Get, интеграционные тесты API | `payments/infrastructure/config.py`, `payments/presentation/api/` | ~15 | 1, 2 |
| 4 | Топология RabbitMQ, FastStream broker/publisher/consumer, эмулятор шлюза, httpx webhook + HMAC, outbox relay, use cases Process/Deliver/Publish, E2E-тесты на compose-инфраструктуре | `payments/infrastructure/{messaging,gateway,webhook}/`, `payments/presentation/consumer_entrypoint.py` | ~18 | 2, 3 |
| 5 | Dockerfile (multi-stage, non-root, uv), docker-compose (postgres, rabbitmq, migrate, api, consumer + `webhook-sink` под профилем `demo`), `.env.example`, README + `docs/ARCHITECTURE.md` + ADR, `scripts/demo`, финальный `make check` | корень, `docs/`, `scripts/` | ~14 | 1–4 |

> Волны 1→4 последовательны (один каталог, общий контракт); внутри волны
> независимые задачи можно вести параллельно сабагентами. Ревью —
> `layer-reviewer` после каждой волны (триггер: новый домен + внешние сервисы +
> объём) и финальное ревью всего diff.

---

**Подтверждение:** ☐ ожидает / ☑ подтверждено
