"""Живая проверка вебхука Meta: все сообщения, повторы, статусы."""

import hashlib
import hmac
import http.client
import json
import sys

SECRET = b"secret-123"
HOST, PORT = "localhost", 8086


def post(payload: dict, *, sign_secret: bytes = SECRET) -> tuple[int, str]:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    signature = "sha256=" + hmac.new(sign_secret, body, hashlib.sha256).hexdigest()
    conn = http.client.HTTPConnection(HOST, PORT, timeout=30)
    conn.request(
        "POST", "/webhook", body,
        {"Content-Type": "application/json", "X-Hub-Signature-256": signature},
    )
    response = conn.getresponse()
    data = response.read().decode("utf-8", "replace")
    conn.close()
    return response.status, data


def message(mid: str, stamp: str, body: str) -> dict:
    return {
        "id": mid, "from": "79999999999", "timestamp": stamp,
        "text": {"body": body},
    }


def main() -> int:
    two = {"entry": [{"changes": [{"value": {"messages": [
        message("wamid.T1", "10", "first"),
        message("wamid.T2", "20", "second"),
    ]}}]}]}

    print("1. два сообщения в одном вебхуке:", post(two))
    print("2. повтор того же вебхука:", post(two))
    print("3. третье новое сообщение:", post({"entry": [{"changes": [{"value": {
        "messages": [message("wamid.T3", "30", "third")]}}]}]}))
    print("4. только статусы доставки:", post({"entry": [{"changes": [{"value": {
        "statuses": [{"id": "wamid.T1", "status": "delivered"}]}}]}]}))
    print("5. без подписи (секрет не тот):", post(two, sign_secret=b"wrong"))
    return 0


if __name__ == "__main__":
    sys.exit(main())