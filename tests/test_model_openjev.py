import json

import httpx
import pytest
import respx

from sre_reflex.models.base import ModelError
from sre_reflex.models.openjev import OpenJevModel
from sre_reflex.questions import QUESTIONS

MS = "http://ms:8000"
SEVERITY = QUESTIONS[1].options


@respx.mock
async def test_posts_state_and_builds_answers():
    route = respx.post(f"{MS}/decide").mock(
        return_value=httpx.Response(200, json={"answers": [
            {"question_id": "severity", "distribution": {o: float(o.startswith("2")) for o in SEVERITY}},
            {"question_id": "actionable", "distribution": {"yes": 0.3, "no": 0.7}},
            {"question_id": "self_resolving", "distribution": {"yes": 0.9, "no": 0.1}},
        ]})
    )
    async with httpx.AsyncClient() as client:
        answers = await OpenJevModel(client, MS, 5).decide("state text", QUESTIONS)

    assert [a.question_id for a in answers] == ["actionable", "severity", "self_resolving"]
    assert [a.value for a in answers] == [pytest.approx(0.3), 2, pytest.approx(0.9)]
    sent = json.loads(route.calls.last.request.content)
    assert sent["state"] == "state text"
    assert [q["id"] for q in sent["questions"]] == ["actionable", "severity", "self_resolving"]


@respx.mock
async def test_http_error_raises_model_error():
    respx.post(f"{MS}/decide").mock(return_value=httpx.Response(500))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelError):
            await OpenJevModel(client, MS, 5).decide("s", QUESTIONS)


@respx.mock
async def test_missing_answer_raises_model_error():
    respx.post(f"{MS}/decide").mock(return_value=httpx.Response(200, json={"answers": []}))
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelError):
            await OpenJevModel(client, MS, 5).decide("s", QUESTIONS)
