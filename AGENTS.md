# Правила проекта

Тестовое задание: асинхронный сервис процессинга платежей.
**Источник истины по требованиям и контрактам:** `docs/briefs/0001-async-payment-processing.md`.
Читай его целиком перед работой. Исторические документы в `docs/briefs/` не переписываются.

## Слои и зависимости

```
presentation ──► application ──► domain
infrastructure ─► application/domain (реализует порты)
```

| Слой | Каталог | Что можно |
|------|---------|-----------|
| Domain | `src/payments/domain/` | чистая логика и инварианты; **никаких** импортов SQLAlchemy, FastAPI, FastStream, pydantic, httpx, dishka |
| Application | `src/payments/application/` | use cases, DTO, порты (Protocol); **никаких** импортов инфраструктуры и фреймворков |
| Infrastructure | `src/payments/infrastructure/` | адаптеры: SQLAlchemy, FastStream, httpx, config; реализуют порты |
| Presentation | `src/payments/presentation/` | FastAPI-роутеры, схемы, DI, exception handlers, FastStream subscriber; тонкий слой |
| Shared kernel | `src/shared/` | `AppError` → `DomainError`; не импортирует `payments` |

Границы проверяются `import-linter` (`uv run lint-imports`) — контракты в `pyproject.toml`.
Бизнес-логика в роутерах и subscriber'ах не живёт: роутер валидирует и маппит, subscriber
решает только политику ack/nack.

## Ошибки

Иерархия: `AppError` → `DomainError` → `PaymentsDomainError` → конкретный тип с полями
контекста и стабильным `code`. Vendor-исключения (`IntegrityError`, `httpx.*`) переводятся
в доменные **в адаптере** с `raise ... from exc`. HTTP отдаёт `ErrorEnvelope {code, message,
details}`; `details` — явный whitelist. Никогда `detail=str(exc)`.

## Команды

```bash
uv sync                                    # окружение
uv run ruff check . && uv run ruff format --check .
uv run mypy src                            # strict
uv run lint-imports
uv run pytest -q                           # unit + integration
make check                                 # всё вместе
```

Полный прогон запускается один раз в конце волны/работы; во время правки — точечно
(`uv run pytest tests/unit/test_x.py -q`).

## Стиль

- Идентификаторы, докстринги и комментарии — по-английски; README, ADR и `docs/` — по-русски.
- `ruff` (line-length 100) и `mypy --strict` без `# type: ignore` без причины.
- DTO и доменные артефакты — `dataclass(frozen=True, slots=True)`; enum'ы — `StrEnum`.
- ORM-модели наружу из репозитория не уходят: маппинг ORM → domain/DTO внутри адаптера.
- Секреты только через env (`pydantic-settings`), в репозитории — `.env.example`.
- Публичные функции/классы — короткий докстринг на английском.

## Тесты

- `tests/unit/` — домен, use cases на фейковых портах; без сети, БД и брокера.
- `tests/integration/` — API, репозитории, e2e на живых Postgres и RabbitMQ.
- Один тест — одно поведение; имя описывает ожидание, а не метод.
- Детерминированность: эмуляция шлюза с seed, задержки ретраев через настройки.

## Запрещено

- Менять подтверждённый brief молча (уточнения — в раздел «Отклонения»).
- Добавлять статусы платежа сверх `pending/succeeded/failed`.
- Публиковать в RabbitMQ вне outbox relay.
- `requeue=True` при nack.
- Коммитить `.env`, секреты и артефакты окружения.
