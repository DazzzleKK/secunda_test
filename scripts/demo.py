"""End-to-end demonstration of the service.

Runs the seven scenarios from the brief against a running `docker compose` stack
and prints a PASS/FAIL line for each of them:

1. readiness of the API;
2. a payment is accepted and processed, the webhook arrives signed;
3. a repeated request is an idempotent replay;
4. the same key with another body is a conflict;
5. missing credentials are rejected;
6. a webhook receiver that always fails lands in the dead letter queue;
7. with the broker down the event waits in the outbox and is published later.

Usage:

    uv run python scripts/demo.py
    uv run python scripts/demo.py --skip-broker-scenario
"""

from __future__ import annotations

import argparse
import asyncio
import subprocess
import sys
import time
import uuid
from typing import Any

import asyncpg
import httpx

DEFAULT_API_URL = "http://127.0.0.1:8000"
DEFAULT_SINK_URL = "http://127.0.0.1:9000"
DEFAULT_RABBIT_URL = "http://127.0.0.1:15672"
DEFAULT_DATABASE_URL = "postgresql://payments:payments@127.0.0.1:5432/payments"
DEFAULT_API_KEY = "local-dev-api-key"
DEFAULT_WEBHOOK_URL = "http://webhook-sink:9000/hook"
RABBITMQ_USER = "guest"
RABBITMQ_PASSWORD = "guest"
DLQ_NAME = "payments.dlq"
TERMINAL_STATUSES = {"succeeded", "failed"}
POLL_INTERVAL_SECONDS = 1.0


class Report:
    """Collects scenario results and prints them as they happen."""

    def __init__(self) -> None:
        self.failures: list[str] = []

    def passed(self, title: str, note: str = "") -> None:
        """Record a successful scenario."""
        print(f"[ PASS ] {title}{f' — {note}' if note else ''}", flush=True)

    def failed(self, title: str, note: str) -> None:
        """Record a failed scenario."""
        self.failures.append(title)
        print(f"[ FAIL ] {title} — {note}", flush=True)

    def skipped(self, title: str, note: str) -> None:
        """Record a scenario that could not run here."""
        print(f"[ SKIP ] {title} — {note}", flush=True)


def payment_body(amount: str, *, webhook_url: str) -> dict[str, Any]:
    """A valid create-payment body."""
    return {
        "amount": amount,
        "currency": "RUB",
        "description": "demo payment",
        "metadata": {"demo": True, "run": str(uuid.uuid4())},
        "webhook_url": webhook_url,
    }


def headers(api_key: str, idempotency_key: str | None) -> dict[str, str]:
    """Authenticated headers, with an optional idempotency key."""
    result = {"X-API-Key": api_key}
    if idempotency_key is not None:
        result["Idempotency-Key"] = idempotency_key
    return result


async def wait_until_final(
    client: httpx.AsyncClient,
    api_key: str,
    payment_id: str,
    *,
    timeout: float,
) -> dict[str, Any] | None:
    """Poll a payment until it reaches a terminal status."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        response = await client.get(
            f"/api/v1/payments/{payment_id}", headers=headers(api_key, None)
        )
        if response.status_code == 200:
            document = response.json()
            if document["status"] in TERMINAL_STATUSES:
                return document
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    return None


async def queue_depth(client: httpx.AsyncClient, rabbit_url: str, queue: str) -> int | None:
    """Read the message count of a queue through the management API."""
    response = await client.get(
        f"{rabbit_url}/api/queues/%2F/{queue}",
        auth=(RABBITMQ_USER, RABBITMQ_PASSWORD),
    )
    if response.status_code != 200:
        return None
    return int(response.json().get("messages", 0))


async def scenario_readiness(client: httpx.AsyncClient, report: Report, *, timeout: float) -> bool:
    """Wait for the API to report a healthy database."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            response = await client.get("/ready")
        except httpx.HTTPError:
            await asyncio.sleep(POLL_INTERVAL_SECONDS)
            continue
        if response.status_code == 200:
            report.passed("1. API готов", response.text.strip())
            return True
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    report.failed("1. API готов", "GET /ready не ответил 200")
    return False


async def scenario_happy_path(
    client: httpx.AsyncClient,
    report: Report,
    sink: httpx.AsyncClient,
    sink_url: str,
    *,
    api_key: str,
    webhook_url: str,
    timeout: float,
) -> None:
    """A payment is accepted, processed and delivered to the receiver."""
    await sink.post(f"{sink_url}/reset")
    response = await client.post(
        "/api/v1/payments",
        json=payment_body("100.50", webhook_url=webhook_url),
        headers=headers(api_key, f"demo-happy-{uuid.uuid4()}"),
    )
    if response.status_code != 202:
        report.failed("2. Платёж обработан и webhook доставлен", response.text)
        return
    payment_id = response.json()["payment_id"]
    payment = await wait_until_final(client, api_key, payment_id, timeout=timeout)
    if payment is None:
        report.failed("2. Платёж обработан и webhook доставлен", "статус не финальный")
        return
    received = (await sink.get(f"{sink_url}/received")).json()
    if received["count"] < 1:
        report.failed(
            "2. Платёж обработан и webhook доставлен",
            f"статус {payment['status']}, но sink ничего не получил",
        )
        return
    event = received["events"][-1]
    report.passed(
        "2. Платёж обработан и webhook доставлен",
        f"status={payment['status']} signature_valid={event['signature_valid']}",
    )


async def scenario_idempotent_replay(
    client: httpx.AsyncClient, report: Report, *, api_key: str, webhook_url: str
) -> None:
    """The same key with the same body returns the stored payment."""
    key = f"demo-replay-{uuid.uuid4()}"
    body = payment_body("250.00", webhook_url=webhook_url)
    first = await client.post("/api/v1/payments", json=body, headers=headers(api_key, key))
    second = await client.post("/api/v1/payments", json=body, headers=headers(api_key, key))
    same_payment = first.json().get("payment_id") == second.json().get("payment_id")
    if first.status_code == 202 and second.status_code == 202 and same_payment:
        report.passed(
            "3. Идемпотентный повтор",
            f"Idempotency-Replayed={second.headers.get('Idempotency-Replayed')}",
        )
        return
    report.failed(
        "3. Идемпотентный повтор",
        f"{first.status_code}/{second.status_code}, same={same_payment}",
    )


async def scenario_conflict(
    client: httpx.AsyncClient, report: Report, *, api_key: str, webhook_url: str
) -> None:
    """The same key with another body is refused."""
    key = f"demo-conflict-{uuid.uuid4()}"
    await client.post(
        "/api/v1/payments",
        json=payment_body("300.00", webhook_url=webhook_url),
        headers=headers(api_key, key),
    )
    response = await client.post(
        "/api/v1/payments",
        json=payment_body("301.00", webhook_url=webhook_url),
        headers=headers(api_key, key),
    )
    code = response.json().get("code") if response.status_code == 409 else None
    if response.status_code == 409 and code == "idempotency_conflict":
        report.passed("4. Конфликт по ключу идемпотентности", "409 idempotency_conflict")
        return
    report.failed("4. Конфликт по ключу идемпотентности", f"{response.status_code} {response.text}")


async def scenario_auth(
    client: httpx.AsyncClient, report: Report, *, api_key: str, webhook_url: str
) -> None:
    """Both the API key and the idempotency key are mandatory."""
    without_key = await client.post(
        "/api/v1/payments",
        json=payment_body("10.00", webhook_url=webhook_url),
        headers={"Idempotency-Key": "demo-no-key"},
    )
    without_idempotency = await client.post(
        "/api/v1/payments",
        json=payment_body("10.00", webhook_url=webhook_url),
        headers={"X-API-Key": api_key},
    )
    if without_key.status_code == 401 and without_idempotency.status_code == 400:
        report.passed("5. Обязательные заголовки", "401 без X-API-Key, 400 без Idempotency-Key")
        return
    report.failed(
        "5. Обязательные заголовки",
        f"{without_key.status_code} и {without_idempotency.status_code}",
    )


async def scenario_dead_letter_queue(
    client: httpx.AsyncClient,
    report: Report,
    sink: httpx.AsyncClient,
    sink_url: str,
    *,
    api_key: str,
    webhook_url: str,
    rabbit_url: str,
    timeout: float,
) -> None:
    """A permanently failing webhook ends up in the dead letter queue."""
    await sink.post(f"{sink_url}/mode?fail=1")
    try:
        before = await queue_depth(client, rabbit_url, DLQ_NAME)
        response = await client.post(
            "/api/v1/payments",
            json=payment_body("400.00", webhook_url=webhook_url),
            headers=headers(api_key, f"demo-dlq-{uuid.uuid4()}"),
        )
        payment_id = response.json()["payment_id"]
        payment = await wait_until_final(client, api_key, payment_id, timeout=timeout)

        deadline = time.monotonic() + timeout
        depth = before or 0
        while time.monotonic() < deadline:
            current = await queue_depth(client, rabbit_url, DLQ_NAME)
            if current is not None and current > (before or 0):
                depth = current
                break
            await asyncio.sleep(POLL_INTERVAL_SECONDS)

        if depth > (before or 0) and payment is not None:
            report.passed(
                "6. DLQ после трёх попыток",
                f"в {DLQ_NAME} {depth} сообщений, платёж={payment['status']}",
            )
            return
        report.failed(
            "6. DLQ после трёх попыток",
            f"глубина {depth} (было {before}), платёж={payment}",
        )
    finally:
        await sink.post(f"{sink_url}/mode?fail=0")


async def scenario_outbox_survives_broker_outage(
    client: httpx.AsyncClient,
    report: Report,
    database: asyncpg.Connection,
    *,
    api_key: str,
    webhook_url: str,
    timeout: float,
) -> None:
    """With the broker down the payment is stored and the event waits in the outbox."""
    if not _compose("stop", "rabbitmq"):
        report.skipped(
            "7. Outbox при недоступном брокере",
            "не удалось выполнить docker compose stop rabbitmq",
        )
        return
    try:
        await asyncio.sleep(3)
        response = await client.post(
            "/api/v1/payments",
            json=payment_body("500.00", webhook_url=webhook_url),
            headers=headers(api_key, f"demo-outbox-{uuid.uuid4()}"),
        )
        if response.status_code != 202:
            report.failed(
                "7. Outbox при недоступном брокере",
                f"POST вернул {response.status_code} вместо 202",
            )
            return
        payment_id = response.json()["payment_id"]
        pending = await database.fetchval(
            "SELECT count(*) FROM outbox WHERE published_at IS NULL AND aggregate_id = $1",
            uuid.UUID(payment_id),
        )
        if pending != 1:
            report.failed(
                "7. Outbox при недоступном брокере",
                f"неопубликованных событий: {pending}",
            )
            return
        report.passed(
            "7. Outbox при недоступном брокере",
            "202 при мёртвом брокере, событие ждёт в outbox",
        )
    finally:
        _compose("start", "rabbitmq")

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        published = await database.fetchval(
            "SELECT count(*) FROM outbox WHERE published_at IS NOT NULL AND aggregate_id = $1",
            uuid.UUID(payment_id),
        )
        if published == 1:
            payment = await wait_until_final(client, api_key, payment_id, timeout=timeout)
            note = f"событие опубликовано, платёж={payment['status'] if payment else 'pending'}"
            report.passed("7b. Событие уехало после подъёма брокера", note)
            return
        await asyncio.sleep(POLL_INTERVAL_SECONDS)
    report.failed("7b. Событие уехало после подъёма брокера", "published_at не выставлен")


def _compose(*args: str) -> bool:
    """Run a docker compose command, returning False when it is unavailable."""
    try:
        completed = subprocess.run(
            ["docker", "compose", *args],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return completed.returncode == 0


async def run(args: argparse.Namespace) -> int:
    """Run every scenario and return the process exit code."""
    report = Report()
    api_key = args.api_key
    async with (
        httpx.AsyncClient(
            base_url=args.api_url, timeout=httpx.Timeout(10.0), trust_env=False
        ) as client,
        httpx.AsyncClient(timeout=httpx.Timeout(10.0), trust_env=False) as sink,
    ):
        if not await scenario_readiness(client, report, timeout=args.startup_timeout):
            return 1
        await scenario_happy_path(
            client,
            report,
            sink,
            args.sink_url,
            api_key=api_key,
            webhook_url=args.webhook_url,
            timeout=args.timeout,
        )
        await scenario_idempotent_replay(
            client, report, api_key=api_key, webhook_url=args.webhook_url
        )
        await scenario_conflict(client, report, api_key=api_key, webhook_url=args.webhook_url)
        await scenario_auth(client, report, api_key=api_key, webhook_url=args.webhook_url)
        await scenario_dead_letter_queue(
            client,
            report,
            sink,
            args.sink_url,
            api_key=api_key,
            webhook_url=args.webhook_url,
            rabbit_url=args.rabbit_url,
            timeout=args.dlq_timeout,
        )
        if args.skip_broker_scenario:
            report.skipped("7. Outbox при недоступном брокере", "--skip-broker-scenario")
        else:
            database = await asyncpg.connect(args.database_url)
            try:
                await scenario_outbox_survives_broker_outage(
                    client,
                    report,
                    database,
                    api_key=api_key,
                    webhook_url=args.webhook_url,
                    timeout=args.timeout,
                )
            finally:
                await database.close()

    print(flush=True)
    if report.failures:
        print(f"ИТОГ: провалено сценариев — {len(report.failures)}", flush=True)
        return 1
    print("ИТОГ: все сценарии прошли", flush=True)
    return 0


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse the command line of the demo."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--api-url", default=DEFAULT_API_URL)
    parser.add_argument("--sink-url", default=DEFAULT_SINK_URL)
    parser.add_argument("--rabbit-url", default=DEFAULT_RABBIT_URL)
    parser.add_argument("--database-url", default=DEFAULT_DATABASE_URL)
    parser.add_argument("--api-key", default=DEFAULT_API_KEY)
    parser.add_argument("--webhook-url", default=DEFAULT_WEBHOOK_URL)
    parser.add_argument("--timeout", type=float, default=60.0)
    parser.add_argument("--dlq-timeout", type=float, default=200.0)
    parser.add_argument("--startup-timeout", type=float, default=90.0)
    parser.add_argument("--skip-broker-scenario", action="store_true")
    return parser.parse_args(argv)


def main() -> int:
    """Entry point."""
    args = parse_args()
    if args.skip_broker_scenario is False:
        print(
            "Подсказка: чтобы сценарий 6 проходил быстрее, "
            "задайте RETRY_DELAYS_MS=1000,2000 (и пересоздайте очереди)",
            flush=True,
        )
    try:
        return asyncio.run(run(args))
    except KeyboardInterrupt:
        return 130
    except (httpx.HTTPError, asyncpg.PostgresError, OSError) as exc:
        print(f"демо прервано: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
        return 2


if __name__ == "__main__":
    sys.exit(main())
