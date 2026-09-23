import pytest

from sre_reflex.eval.report import agreement, compute_metrics, render_report
from sre_reflex.store import AnswerRow, EvalRow


def row(p, label, latency, model="a", complete=True):
    return EvalRow(1, model, p, label, latency, 0.001, complete)


ROWS = [row(0.9, "real", 10), row(0.8, "noise", 20), row(0.2, "real", 30), row(0.1, "noise", 40)]


def test_compute_metrics():
    [m] = compute_metrics(ROWS)
    assert (m.model, m.n) == ("a", 4)
    assert (m.precision, m.recall, m.f1) == (0.5, 0.5, 0.5)
    assert m.brier == pytest.approx(0.325)
    assert (m.p50_ms, m.p95_ms) == (20, 40)
    assert m.cost_usd == pytest.approx(0.004)


def test_no_positive_predictions_gives_zero_precision():
    [m] = compute_metrics([row(0.1, "real", 1)])
    assert (m.precision, m.recall, m.f1) == (0.0, 0.0, 0.0)


def test_agreement():
    rows = [
        AnswerRow(1, "openjev", "severity", 2), AnswerRow(1, "ollama", "severity", 2),
        AnswerRow(2, "openjev", "severity", 3), AnswerRow(2, "ollama", "severity", 4),
        AnswerRow(1, "openjev", "self_resolving", 0.9), AnswerRow(1, "ollama", "self_resolving", 0.6),
    ]
    assert agreement(rows) == {"severity": 0.5, "self_resolving": 1.0}


def test_render_report_sections():
    md = render_report(ROWS + [row(0.9, "real", 5, complete=False)], [], "Test")
    assert md.startswith("# Test")
    assert "## Actionable vs labels (all states)" in md
    assert "## Actionable vs labels (complete context only)" in md
    assert "States with missing collectors: 1" in md
    assert "| a | 5 |" in md and "| a | 4 |" in md
