"""Webhook receiver for local demonstrations.

Standard library only, so the demo needs no extra image: it verifies the HMAC
signature of every request, stores the payloads and can be switched into
"always fail" mode to demonstrate the retry ladder and the dead letter queue.

Run it directly or through `docker compose --profile demo up`:

    python scripts/webhook_sink.py
    curl http://127.0.0.1:9000/received
    curl -X POST 'http://127.0.0.1:9000/mode?fail=1'
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

SIGNATURE_HEADER = "X-Webhook-Signature"
REQUEST_ID_HEADER = "X-Webhook-Id"
ATTEMPT_HEADER = "X-Webhook-Attempt"
DEFAULT_PORT = 9000
MAX_BODY_BYTES = 64 * 1024

_state_lock = threading.Lock()
_received: list[dict[str, Any]] = []
_fail_mode = False


def _verify(secret: str, body: bytes, signature: str | None) -> bool:
    if signature is None:
        return False
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


class _Handler(BaseHTTPRequestHandler):
    server_version = "webhook-sink/1.0"

    def _respond(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        path = urlparse(self.path).path
        if path == "/health":
            self._respond(200, {"status": "ok"})
            return
        if path == "/received":
            with _state_lock:
                events = list(_received)
            self._respond(200, {"fail_mode": _fail_mode, "count": len(events), "events": events})
            return
        self._respond(404, {"error": "not_found"})

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/mode":
            query = parse_qs(parsed.query)
            global _fail_mode
            with _state_lock:
                _fail_mode = query.get("fail", ["0"])[0] in {"1", "true", "yes"}
                current = _fail_mode
            self._respond(200, {"fail_mode": current})
            return

        if parsed.path == "/reset":
            with _state_lock:
                _received.clear()
            self._respond(200, {"count": 0})
            return

        if parsed.path != "/hook":
            self._respond(404, {"error": "not_found"})
            return

        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(min(length, MAX_BODY_BYTES))
        secret = os.environ.get("WEBHOOK_SIGNING_SECRET", "")
        signature = self.headers.get(SIGNATURE_HEADER)
        signed = _verify(secret, body, signature) if secret else True
        with _state_lock:
            failing = _fail_mode
            _received.append(
                {
                    "signature_valid": signed,
                    "event_id": self.headers.get(REQUEST_ID_HEADER),
                    "attempt": self.headers.get(ATTEMPT_HEADER),
                    "payload": json.loads(body or b"{}"),
                }
            )
        if failing:
            self._respond(500, {"error": "sink_is_in_fail_mode"})
            return
        self._respond(200, {"accepted": True, "signature_valid": signed})

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[webhook-sink] {format % args}", flush=True)


def main() -> None:
    """Serve until interrupted."""
    port = int(os.environ.get("SINK_PORT", DEFAULT_PORT))
    server = ThreadingHTTPServer(("0.0.0.0", port), _Handler)
    print(f"[webhook-sink] listening on :{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
