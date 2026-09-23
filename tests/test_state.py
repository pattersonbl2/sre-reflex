from sre_reflex.collectors.base import CollectorResult
from sre_reflex.state import build_state, estimate_tokens


def R(name, lines, ok=True):
    return CollectorResult(name, lines, ok)


def test_orders_sections_and_records_collectors():
    s = build_state(
        [R("logs", ["l1"]), R("alert", ["a1"]), R("history", [], ok=False), R("metrics", ["m1"])],
        cap=800,
    )
    assert s.text == "## alert\na1\n\n## metrics\nm1\n\n## logs\nl1"
    assert s.collectors_ok == {"logs": True, "alert": True, "history": False, "metrics": True}
    assert s.token_count == estimate_tokens(s.text)


def test_trims_logs_before_metrics():
    logs = [f"log line {i} " + "x" * 90 for i in range(50)]
    s = build_state([R("alert", ["a1"]), R("metrics", ["m1", "m2"]), R("logs", logs)], cap=200)
    assert s.token_count <= 200
    assert "m1\nm2" in s.text
    assert "log line 0 " in s.text and "log line 49" not in s.text


def test_never_trims_alert_section():
    s = build_state([R("alert", ["a" * 400]), R("metrics", ["m1"]), R("logs", ["l1"])], cap=20)
    assert s.text == "## alert\n" + "a" * 400


def test_estimate_tokens_rounds_up():
    assert estimate_tokens("abcde") == 2
