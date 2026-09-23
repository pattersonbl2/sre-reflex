from typing import Literal, Protocol

from pydantic import BaseModel


class Question(BaseModel):
    id: str
    type: Literal["noul", "score", "choice"]
    text: str
    options: list[str] = []


class Answer(BaseModel):
    question_id: str
    value: float | int | str
    distribution: dict[str, float]
    latency_ms: int
    cost_usd: float = 0.0


class ModelError(Exception):
    """An adapter could not produce answers."""


class DecisionModel(Protocol):
    name: str

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]: ...


def noul_distribution(p: float) -> dict[str, float]:
    p = min(1.0, max(0.0, float(p)))
    return {"yes": p, "no": 1.0 - p}


def normalize_options(options: list[str], raw: dict) -> dict[str, float]:
    values = {o: max(0.0, float(raw.get(o, 0.0))) for o in options}
    total = sum(values.values())
    if total == 0:
        return {o: 1.0 / len(options) for o in options}
    return {o: v / total for o, v in values.items()}


def build_answer(
    q: Question, distribution: dict[str, float], latency_ms: int, cost_usd: float = 0.0
) -> Answer:
    if q.type == "noul":
        value: float | int | str = distribution["yes"]
    else:
        best = max(q.options, key=lambda o: distribution.get(o, 0.0))
        value = q.options.index(best) + 1 if q.type == "score" else best
    return Answer(
        question_id=q.id,
        value=value,
        distribution=distribution,
        latency_ms=latency_ms,
        cost_usd=cost_usd,
    )
