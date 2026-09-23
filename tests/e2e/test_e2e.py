import json
import os
import time
from datetime import UTC, datetime
from uuid import uuid4

import httpx
import psycopg
import pytest

pytestmark = pytest.mark.e2e
BOT = os.environ.get("E2E_BOT_URL", "http://localhost:8080")
NTFY = os.environ.get("E2E_NTFY_URL", "http://localhost:8081")
DB = os.environ.get("E2E_DATABASE_URL", "postgresql://sre_reflex:sre_reflex@localhost:5432/sre_reflex")


def test_webhook_to_ntfy_and_database():
    fp = uuid4().hex
    started = datetime.now(UTC).isoformat()
    payload = {
        "version": "4", "status": "firing", "receiver": "sre-reflex",
        "alerts": [{
            "status": "firing",
            "labels": {"alertname": f"E2E{fp[:6]}", "severity": "warning", "namespace": "e2e"},
            "annotations": {"summary": "end to end"},
            "startsAt": started, "endsAt": "0001-01-01T00:00:00Z",
            "generatorURL": "", "fingerprint": fp,
        }],
    }
    assert httpx.post(f"{BOT}/alertmanager", json=payload).status_code == 202

    message = None
    deadline = time.time() + 30
    while time.time() < deadline and message is None:
        r = httpx.get(f"{NTFY}/sre-reflex/json", params={"poll": "1", "since": "all"})
        for line in r.text.splitlines():
            m = json.loads(line)
            if m.get("title", "").startswith(f"E2E{fp[:6]}"):
                message = m
        time.sleep(1)

    assert message is not None, "no ntfy message within 30s"
    assert "(fake)" in message["message"]
    # history and logs hit unreachable hosts; metrics has no query and no expression, so it
    # succeeds with no lines.
    assert "missing: history, logs" in message["message"]
    assert len(message["actions"]) == 2

    with psycopg.connect(DB) as conn:
        count = conn.execute(
            "SELECT count(*) FROM decisions d JOIN states s ON s.id = d.state_id "
            "JOIN alerts a ON a.id = s.alert_id WHERE a.fingerprint = %s",
            (fp,),
        ).fetchone()[0]
    assert count == 3
