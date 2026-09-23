import time

import httpx

from sre_reflex.models.base import Answer, ModelError, Question, build_answer


class OpenJevModel:
    name = "openjev"

    def __init__(self, client: httpx.AsyncClient, base_url: str, timeout_s: float):
        self.client = client
        self.url = base_url.rstrip("/") + "/decide"
        self.timeout_s = timeout_s

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]:
        start = time.perf_counter()
        try:
            r = await self.client.post(
                self.url,
                json={"state": state, "questions": [q.model_dump() for q in questions]},
                timeout=self.timeout_s,
            )
            r.raise_for_status()
            by_id = {a["question_id"]: a["distribution"] for a in r.json()["answers"]}
        except (httpx.HTTPError, KeyError, ValueError) as e:
            raise ModelError(f"openjev: {e}") from e
        latency_ms = int((time.perf_counter() - start) * 1000)
        missing = [q.id for q in questions if q.id not in by_id]
        if missing:
            raise ModelError(f"openjev: no answer for {missing}")
        return [build_answer(q, by_id[q.id], latency_ms) for q in questions]
