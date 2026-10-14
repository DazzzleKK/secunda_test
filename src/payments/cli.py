"""Operator tooling for the dead letter queue.

    uv run python -m payments.cli dlq peek --limit 10
    uv run python -m payments.cli dlq replay --limit 10

Both commands talk to the RabbitMQ management API, so they work from a laptop
against the same broker the service uses. `replay` puts the messages back on the
working queue with a fresh attempt counter: the consumer will process them again,
which is how a payment whose webhook never went through gets its notification.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Final

import httpx

from payments.infrastructure.messaging.topology import (
    ATTEMPT_HEADER,
    DLQ_NAME,
    EXCHANGE_NAME,
    WORK_ROUTING_KEY,
)

DEFAULT_MANAGEMENT_PORT: Final[int] = 15672
DEFAULT_LIMIT: Final[int] = 10
REQUEST_TIMEOUT_SECONDS: Final[float] = 10.0
VIRTUAL_HOST: Final[str] = "%2F"
PERSISTENT_DELIVERY_MODE: Final[int] = 2

DEFAULT_BROKER_HOST: Final[str] = "localhost"
DEFAULT_BROKER_USER: Final[str] = "guest"
DEFAULT_BROKER_PASSWORD: Final[str] = "guest"


def broker_credentials() -> tuple[str, str, str]:
    """Broker connection read from the environment.

    The CLI deliberately does not build the application settings: an operator tool
    that only talks to the management API must work without the service's API key.
    """
    return (
        os.environ.get("RABBITMQ_HOST", DEFAULT_BROKER_HOST),
        os.environ.get("RABBITMQ_USER", DEFAULT_BROKER_USER),
        os.environ.get("RABBITMQ_PASSWORD", DEFAULT_BROKER_PASSWORD),
    )


def peek_messages(
    client: httpx.Client,
    base_url: str,
    queue: str,
    *,
    limit: int,
    remove: bool,
) -> list[dict[str, Any]]:
    """Read messages from a queue, keeping or removing them."""
    response = client.post(
        f"{base_url}/api/queues/{VIRTUAL_HOST}/{queue}/get",
        json={
            "count": limit,
            "ackmode": "ack_requeue_false" if remove else "ack_requeue_true",
            "encoding": "auto",
        },
    )
    response.raise_for_status()
    return list(response.json())


def publish_back(
    client: httpx.Client,
    base_url: str,
    *,
    body: str,
    routing_key: str = WORK_ROUTING_KEY,
) -> bool:
    """Put a message on the working queue with a fresh attempt counter."""
    response = client.post(
        f"{base_url}/api/exchanges/{VIRTUAL_HOST}/{EXCHANGE_NAME}/publish",
        json={
            "properties": {
                "headers": {ATTEMPT_HEADER: 0},
                "delivery_mode": PERSISTENT_DELIVERY_MODE,
            },
            "routing_key": routing_key,
            "payload": body,
            "payload_encoding": "string",
        },
    )
    response.raise_for_status()
    return bool(response.json().get("routed"))


def _describe(message: dict[str, Any]) -> dict[str, Any]:
    """A short, readable view of a message taken from the management API."""
    payload: Any = message.get("payload")
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except ValueError:
            payload = payload[:200]
    headers = (message.get("properties") or {}).get("headers") or {}
    return {
        "payment_id": payload.get("payment_id") if isinstance(payload, dict) else None,
        "attempt": headers.get(ATTEMPT_HEADER),
        "error": headers.get("x-error"),
        "failed_at": headers.get("x-failed-at"),
        "payload": payload,
    }


def command_peek(client: httpx.Client, base_url: str, args: argparse.Namespace) -> int:
    """Show what is parked in the dead letter queue."""
    messages = peek_messages(client, base_url, args.queue, limit=args.limit, remove=False)
    if not messages:
        print(f"{args.queue} is empty", flush=True)
        return 0
    for message in messages:
        print(json.dumps(_describe(message), ensure_ascii=False, indent=2), flush=True)
    return 0


def command_replay(client: httpx.Client, base_url: str, args: argparse.Namespace) -> int:
    """Put the parked messages back on the working queue."""
    messages = peek_messages(client, base_url, args.queue, limit=args.limit, remove=True)
    if not messages:
        print(f"{args.queue} is empty", flush=True)
        return 0
    replayed = 0
    for message in messages:
        body = message.get("payload")
        if not isinstance(body, str):
            body = json.dumps(body, ensure_ascii=False)
        if publish_back(client, base_url, body=body):
            replayed += 1
        else:
            print(
                f"message was not routed, is {WORK_ROUTING_KEY} still bound?",
                file=sys.stderr,
                flush=True,
            )
    print(f"replayed {replayed} of {len(messages)} messages from {args.queue}", flush=True)
    return 0 if replayed == len(messages) else 1


def build_parser() -> argparse.ArgumentParser:
    """Command line of the operator tool."""
    parser = argparse.ArgumentParser(prog="payments.cli", description=__doc__)
    parser.add_argument("--host", default=None, help="broker host (defaults to the settings)")
    parser.add_argument(
        "--management-port", type=int, default=DEFAULT_MANAGEMENT_PORT, help="management API port"
    )
    parser.add_argument("--user", default=None, help="broker user")
    parser.add_argument("--password", default=None, help="broker password")
    subparsers = parser.add_subparsers(dest="command", required=True)
    dlq = subparsers.add_parser("dlq", help="dead letter queue tools")
    dlq_commands = dlq.add_subparsers(dest="dlq_command", required=True)

    for name, handler, help_text in (
        ("peek", command_peek, "show the parked messages"),
        ("replay", command_replay, "publish the parked messages back"),
    ):
        sub = dlq_commands.add_parser(name, help=help_text)
        sub.add_argument("--queue", default=DLQ_NAME, help=f"queue to inspect ({DLQ_NAME})")
        sub.add_argument("--limit", type=int, default=DEFAULT_LIMIT, help="how many messages")
        sub.set_defaults(handler=handler)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Entry point of the operator tool."""
    args = build_parser().parse_args(argv)
    default_host, default_user, default_password = broker_credentials()
    host = args.host or default_host
    user = args.user or default_user
    password = args.password or default_password
    base_url = f"http://{host}:{args.management_port}"

    try:
        with httpx.Client(
            auth=(user, password), timeout=REQUEST_TIMEOUT_SECONDS, trust_env=False
        ) as client:
            return int(args.handler(client, base_url, args))
    except httpx.HTTPStatusError as exc:
        print(
            f"management API answered {exc.response.status_code} for {exc.request.url}",
            file=sys.stderr,
            flush=True,
        )
        return 2
    except httpx.HTTPError as exc:
        print(
            f"could not reach the management API at {base_url}: {exc}", file=sys.stderr, flush=True
        )
        return 2


if __name__ == "__main__":
    sys.exit(main())
