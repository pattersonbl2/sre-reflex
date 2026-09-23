import json
import time

import httpx

from sre_reflex.models.base import (
    Answer,
    ModelError,
    Question,
    build_answer,
    normalize_options,
    noul_distribution,
)

SYSTEM = (
    "You are an SRE triaging a Prometheus alert. Answer only with the requested JSON. "
    "Give calibrated probabilities between 0 and 1."
)


def response_schema(questions: list[Question]) -> dict:
    props: dict = {}
    for q in questions:
        if q.type == "noul":
            props[q.id] = {"type": "number"}
        else:
            props[q.id] = {
                "type": "object",
                "properties": {o: {"type": "number"} for o in q.options},
                "required": list(q.options),
            }
    return {"type": "object", "properties": props, "required": [q.id for q in questions]}


def build_prompt(state: str, questions: list[Question]) -> str:
    lines = ["Alert context:", state, "", "Answer every question:"]
    for q in questions:
        if q.type == "noul":
            lines.append(f'- "{q.id}": probability that this statement is true: {q.text}')
        else:
            opts = ", ".join(f'"{o}"' for o in q.options)
            lines.append(
                f'- "{q.id}": {q.text} Give a probability for each option, summing to 1: {opts}'
            )
    return "\n".join(lines)


class OllamaModel:
    name = "ollama"

    def __init__(self, client: httpx.AsyncClient, base_url: str, model: str, timeout_s: float):
        self.client = client
        self.url = base_url.rstrip("/") + "/api/chat"
        self.model = model
        self.timeout_s = timeout_s

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]:
        start = time.perf_counter()
        try:
            r = await self.client.post(
                self.url,
                json={
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": SYSTEM},
                        {"role": "user", "content": build_prompt(state, questions)},
                    ],
                    "format": response_schema(questions),
                    "stream": False,
                    "think": False,
                    "options": {"temperature": 0},
                },
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            content = json.loads(r.json()["message"]["content"])
            latency_ms = int((time.perf_counter() - start) * 1000)
            answers = []
            for q in questions:
                raw = content[q.id]
                dist = (
                    noul_distribution(raw)
                    if q.type == "noul"
                    else normalize_options(q.options, raw)
                )
                answers.append(build_answer(q, dist, latency_ms))
            return answers
        except (httpx.HTTPError, KeyError, ValueError, TypeError, AttributeError) as e:
            raise ModelError(f"ollama: {e}") from e
