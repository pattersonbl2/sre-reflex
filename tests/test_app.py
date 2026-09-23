from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient
from stubs import MemoryStore, RecordingPipeline

from sre_reflex.app import create_app
from sre_reflex.config import Settings
from sre_reflex.signing import sign

WEBHOOK = {
    "version": "4",
    "status": "firing",
    "receiver": "sre-reflex",
    "alerts": [{
        "status": "firing",
        "labels": {"alertname": "X", "namespace": "demo"},
        "annotations": {},
        "startsAt": "2026-09-23T11:56:00Z",
        "endsAt": "0001-01-01T00:00:00Z",
        "generatorURL": "",
        "fingerprint": "f1",
    }],
}


def client_for(pipeline=None, store=None):
    app = create_app(
        Settings(_env_file=None, label_hmac_key="k"),
        pipeline=pipeline or RecordingPipeline(),
        store=store or MemoryStore(),
    )
    return TestClient(app)


def test_webhook_accepted_and_handled():
    pipeline = RecordingPipeline()
    with client_for(pipeline=pipeline) as c:
        r = c.post("/alertmanager", json=WEBHOOK)
    assert r.status_code == 202
    assert pipeline.webhooks[0].alerts[0].fingerprint == "f1"


def test_label_ok_then_reused_then_tampered():
    store = MemoryStore()
    exp = int((datetime.now(UTC) + timedelta(days=1)).timestamp())
    params = {"a": 1, "v": "real", "exp": exp, "sig": sign("k", 1, "real", exp)}
    with client_for(store=store) as c:
        assert c.post("/label", params=params).status_code == 200
        assert c.get("/label", params=params).status_code == 409
        assert c.post("/label", params={**params, "v": "noise"}).status_code == 403
    assert store.labels == [(1, "real", "hand")]


def test_health_and_metrics():
    with client_for() as c:
        assert c.get("/healthz").json() == {"status": "ok"}
        assert "sre_reflex_alerts_processed_total" in c.get("/metrics").text
