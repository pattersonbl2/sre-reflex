"""Every DecisionModel adapter must pass this suite.

`fake` always runs. Real adapters run with: LIVE_MODELS=openjev,ollama uv run pytest -m live
"""
import math
import os

import httpx
import pytest

from sre_reflex.config import Settings
from sre_reflex.models import build_model
from sre_reflex.questions import QUESTIONS

STATE = "## alert\nAlert HighErrorRate (severity=warning) on namespace=demo, firing 4m."
LIVE = [m for m in os.environ.get("LIVE_MODELS", "").split(",") if m]


@pytest.fixture(params=["fake", *[pytest.param(m, marks=pytest.mark.live) for m in LIVE]])
async def model(request):
    async with httpx.AsyncClient() as client:
        yield build_model(request.param, client, Settings())


async def test_one_answer_per_question_in_order(model):
    answers = await model.decide(STATE, QUESTIONS)
    assert [a.question_id for a in answers] == [q.id for q in QUESTIONS]


async def test_distributions_sum_to_one(model):
    for a in await model.decide(STATE, QUESTIONS):
        assert math.isclose(sum(a.distribution.values()), 1.0, abs_tol=0.01)


async def test_values_in_range(model):
    for q, a in zip(QUESTIONS, await model.decide(STATE, QUESTIONS)):
        if q.type == "noul":
            assert 0.0 <= a.value <= 1.0
        elif q.type == "score":
            assert 1 <= a.value <= len(q.options)
        else:
            assert a.value in q.options


async def test_latency_and_cost_non_negative(model):
    for a in await model.decide(STATE, QUESTIONS):
        assert a.latency_ms >= 0 and a.cost_usd >= 0


async def test_fake_is_deterministic():
    async with httpx.AsyncClient() as client:
        m = build_model("fake", client, Settings())
        assert await m.decide(STATE, QUESTIONS) == await m.decide(STATE, QUESTIONS)


async def test_unknown_model_raises():
    async with httpx.AsyncClient() as client:
        with pytest.raises(ValueError):
            build_model("nope", client, Settings())
