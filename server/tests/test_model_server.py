import pytest
from fastapi.testclient import TestClient
from model_server import create_app


class FakeOpenJev:
    def __init__(self):
        self.calls = []

    def decide(self, text, questions):
        self.calls.append((text, questions))
        out = []
        for q in questions:
            if q["type"] == "noul":
                out.append({"noul": 0.9})
            else:
                probs = {o: 1.0 for o in q["options"]}
                out.append({q["type"]: q["options"][0], "probabilities": probs, "confidence": 0.5})
        return out


def test_decide_translates_both_ways():
    fake = FakeOpenJev()
    with TestClient(create_app(fake)) as client:
        r = client.post("/decide", json={
            "state": "ctx",
            "questions": [
                {"id": "actionable", "type": "noul", "text": "Needs action."},
                {"id": "severity", "type": "score", "text": "How bad?", "options": ["low", "high"]},
            ],
        })
    assert r.status_code == 200
    answers = r.json()["answers"]
    assert answers[0]["question_id"] == "actionable"
    assert answers[0]["distribution"] == pytest.approx({"yes": 0.9, "no": 0.1})
    assert answers[1]["distribution"] == {"low": 0.5, "high": 0.5}
    text, sent = fake.calls[0]
    assert text == "ctx"
    assert sent == [
        {"type": "noul", "instructions": "Needs action."},
        {"type": "score", "instructions": "How bad?", "options": ["low", "high"]},
    ]


def test_healthz_and_metrics():
    with TestClient(create_app(FakeOpenJev())) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        assert "openjev_decide_seconds" in client.get("/metrics").text
