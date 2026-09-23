import math
from dataclasses import dataclass

from sre_reflex.collectors.base import CollectorResult

SECTION_ORDER = ["alert", "history", "metrics", "logs"]
TRIM_ORDER = ["logs", "metrics"]


@dataclass
class State:
    text: str
    collectors_ok: dict[str, bool]
    token_count: int


def estimate_tokens(text: str) -> int:
    return math.ceil(len(text) / 4)


def build_state(results: list[CollectorResult], cap: int) -> State:
    by_name = {r.name: r for r in results}
    sections = {n: list(by_name[n].lines) if n in by_name else [] for n in SECTION_ORDER}

    def render() -> str:
        return "\n\n".join(
            f"## {n}\n" + "\n".join(sections[n]) for n in SECTION_ORDER if sections[n]
        )

    text = render()
    for name in TRIM_ORDER:
        while estimate_tokens(text) > cap and sections[name]:
            sections[name].pop()
            text = render()
    return State(text, {r.name: r.ok for r in results}, estimate_tokens(text))
