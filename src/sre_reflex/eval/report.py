import math
from collections import defaultdict
from dataclasses import dataclass

from sre_reflex.store import AnswerRow, EvalRow


@dataclass
class ModelMetrics:
    model: str
    n: int
    precision: float
    recall: float
    f1: float
    brier: float
    p50_ms: int
    p95_ms: int
    cost_usd: float


def _percentile(values: list[int], q: float) -> int:
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, math.ceil(q * len(ordered)) - 1)]


def compute_metrics(rows: list[EvalRow], threshold: float = 0.5) -> list[ModelMetrics]:
    by_model: dict[str, list[EvalRow]] = defaultdict(list)
    for r in rows:
        by_model[r.model].append(r)
    out = []
    for model in sorted(by_model):
        rs = by_model[model]
        tp = sum(r.p_actionable >= threshold and r.label == "real" for r in rs)
        fp = sum(r.p_actionable >= threshold and r.label == "noise" for r in rs)
        fn = sum(r.p_actionable < threshold and r.label == "real" for r in rs)
        precision = tp / (tp + fp) if tp + fp else 0.0
        recall = tp / (tp + fn) if tp + fn else 0.0
        f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
        brier = sum((r.p_actionable - (r.label == "real")) ** 2 for r in rs) / len(rs)
        latencies = [r.latency_ms for r in rs]
        out.append(ModelMetrics(
            model, len(rs), precision, recall, f1, brier,
            _percentile(latencies, 0.5), _percentile(latencies, 0.95),
            sum(r.cost_usd for r in rs),
        ))
    return out


def agreement(rows: list[AnswerRow]) -> dict[str, float | None]:
    grouped: dict[str, dict[int, dict[str, float]]] = defaultdict(lambda: defaultdict(dict))
    for r in rows:
        grouped[r.question_id][r.state_id][r.model] = r.value
    result: dict[str, float | None] = {}
    for question, states in grouped.items():
        compared = agreed = 0
        for by_model in states.values():
            if len(by_model) < 2:
                continue
            compared += 1
            values = list(by_model.values())
            if question == "severity":
                agreed += len(set(values)) == 1
            else:
                agreed += len({v > 0.5 for v in values}) == 1
        result[question] = agreed / compared if compared else None
    return result


def _table(metrics: list[ModelMetrics]) -> str:
    lines = [
        "| model | n | precision | recall | F1 | Brier | p50 ms | p95 ms | cost $ |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for m in metrics:
        lines.append(
            f"| {m.model} | {m.n} | {m.precision:.2f} | {m.recall:.2f} | {m.f1:.2f} | "
            f"{m.brier:.3f} | {m.p50_ms} | {m.p95_ms} | {m.cost_usd:.4f} |"
        )
    return "\n".join(lines)


def render_report(rows: list[EvalRow], answer_rows: list[AnswerRow], title: str) -> str:
    incomplete = len({r.alert_id for r in rows if not r.complete})
    parts = [
        f"# {title}",
        "## Actionable vs labels (all states)",
        _table(compute_metrics(rows)),
        "## Actionable vs labels (complete context only)",
        _table(compute_metrics([r for r in rows if r.complete])),
        f"States with missing collectors: {incomplete}",
    ]
    if answer_rows:
        parts.append("## Inter-model agreement (no ground truth)")
        for question, rate in agreement(answer_rows).items():
            parts.append(f"- {question}: " + ("n/a" if rate is None else f"{rate:.0%}"))
    return "\n\n".join(parts) + "\n"
