import json

import httpx
import pytest
import respx

from sre_reflex.models.base import ModelError
from sre_reflex.models.ollama import OllamaModel, response_schema
from sre_reflex.questions import QUESTIONS

OL = "http://ollama:11434"
SEVERITY = QUESTIONS[1].options


def _chat(content: dict) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": json.dumps(content)}})


def test_schema_requires_every_question_and_option():
    schema = response_schema(QUESTIONS)
    assert schema["required"] == ["actionable", "severity", "self_resolving"]
    assert schema["properties"]["actionable"] == {"type": "number"}
    assert schema["properties"]["severity"]["required"] == SEVERITY


@respx.mock
async def test_normalises_and_clamps():
    route = respx.post(f"{OL}/api/chat").mock(return_value=_chat({
        "actionable": 1.3,
        "severity": {SEVERITY[0]: 0.5, SEVERITY[2]: 1.5},
        "self_resolving": 0.25,
    }))
    async with httpx.AsyncClient() as client:
        answers = await OllamaModel(client, OL, "qwen3:8b", 30).decide("ctx", QUESTIONS)

    assert answers[0].value == 1.0
    assert answers[1].value == 3
    assert answers[1].distribution[SEVERITY[2]] == pytest.approx(0.75)
    assert answers[2].value == pytest.approx(0.25)
    sent = json.loads(route.calls.last.request.content)
    assert sent["model"] == "qwen3:8b"
    assert sent["stream"] is False and sent["think"] is False
    assert sent["options"] == {"temperature": 0}
    assert sent["format"] == response_schema(QUESTIONS)
    assert "ctx" in sent["messages"][1]["content"]


@respx.mock
async def test_invalid_json_raises_model_error():
    respx.post(f"{OL}/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "not json"}})
    )
    async with httpx.AsyncClient() as client:
        with pytest.raises(ModelError):
            await OllamaModel(client, OL, "m", 30).decide("s", QUESTIONS)
