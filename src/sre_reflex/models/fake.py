import hashlib

from sre_reflex.models.base import Answer, Question, build_answer


class FakeModel:
    """Deterministic answers derived from a hash of the state. For tests and CI."""

    name = "fake"

    async def decide(self, state: str, questions: list[Question]) -> list[Answer]:
        answers = []
        for q in questions:
            options = ["yes", "no"] if q.type == "noul" else q.options
            digest = hashlib.sha256(f"{state}|{q.id}".encode()).digest()
            weights = [b + 1 for b in digest[: len(options)]]
            total = sum(weights)
            distribution = {o: w / total for o, w in zip(options, weights)}
            answers.append(build_answer(q, distribution, latency_ms=0))
        return answers
