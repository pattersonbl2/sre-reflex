"""HTTP wrapper around open-jev (typed_decisions.OpenJev)."""
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal

from fastapi import FastAPI, Request, Response
from prometheus_client import CONTENT_TYPE_LATEST, Histogram, generate_latest
from pydantic import BaseModel, Field

MODEL_ID = os.environ.get("OPENJEV_MODEL", "com-kotobalabs/open-jev-deberta-v3-large")
DECIDE_SECONDS = Histogram("openjev_decide_seconds", "open-jev decide latency")


MAX_STATE_CHARS = 20_000
MAX_QUESTIONS = 16
MAX_OPTIONS = 32


class Question(BaseModel):
    id: str = Field(max_length=64)
    type: Literal["noul", "score", "choice"]
    text: str = Field(max_length=2_000)
    options: list[Annotated[str, Field(max_length=200)]] = Field(
        default=[], max_length=MAX_OPTIONS
    )


class DecideRequest(BaseModel):
    state: str = Field(max_length=MAX_STATE_CHARS)
    questions: list[Question] = Field(max_length=MAX_QUESTIONS)


class AnswerOut(BaseModel):
    question_id: str
    distribution: dict[str, float]


class DecideResponse(BaseModel):
    answers: list[AnswerOut]


def to_openjev(q: Question) -> dict:
    d: dict = {"type": q.type, "instructions": q.text}
    if q.type != "noul":
        d["options"] = q.options
    return d


def from_openjev(q: Question, out: dict) -> dict[str, float]:
    if q.type == "noul":
        p = min(1.0, max(0.0, float(out["noul"])))
        return {"yes": p, "no": 1.0 - p}
    probs = {o: max(0.0, float(out["probabilities"].get(o, 0.0))) for o in q.options}
    total = sum(probs.values())
    if total == 0:
        return {o: 1.0 / len(q.options) for o in q.options}
    return {o: v / total for o, v in probs.items()}


def load_model():
    import torch
    from typed_decisions.open_jev import OpenJev

    model = OpenJev.from_pretrained(MODEL_ID)
    if torch.cuda.is_available() and hasattr(model, "to"):
        model = model.to("cuda")
    return model


def create_app(model=None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.model = model if model is not None else load_model()
        yield

    app = FastAPI(title="open-jev model server", lifespan=lifespan)

    @app.post("/decide")
    def decide(req: DecideRequest, request: Request) -> DecideResponse:
        with DECIDE_SECONDS.time():
            outs = request.app.state.model.decide(
                req.state, [to_openjev(q) for q in req.questions]
            )
        return DecideResponse(answers=[
            AnswerOut(question_id=q.id, distribution=from_openjev(q, o))
            for q, o in zip(req.questions, outs, strict=True)
        ])

    @app.get("/healthz")
    def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    def metrics() -> Response:
        return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)

    return app


app = create_app()
