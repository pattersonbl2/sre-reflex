import json

import httpx
import respx

from sre_reflex.models.base import Answer
from sre_reflex.notify import Notifier, format_message, label_actions


def ans(p_act, sev, p_self):
    return [
        Answer(question_id="actionable", value=p_act, distribution={}, latency_ms=1),
        Answer(question_id="severity", value=sev, distribution={}, latency_ms=1),
        Answer(question_id="self_resolving", value=p_self, distribution={}, latency_ms=1),
    ]


def test_format_message(alert):
    title, body = format_message(
        alert,
        {"openjev": ans(0.22, 2, 0.87), "ollama": ans(0.35, 2, 0.70), "jev": None},
        {"alert": True, "history": True, "metrics": True, "logs": False},
    )
    assert title == "HighErrorRate · n8n"
    assert body.splitlines() == [
        "actionable 0.22 · severity 2/5 · self-resolving 0.87   (openjev)",
        "ollama: actionable 0.35 · severity 2/5 · self-resolving 0.70",
        "jev: unavailable",
        "missing: logs",
    ]


def test_label_actions(now):
    actions = label_actions("https://x.example", "k", 5, now)
    assert [a["label"] for a in actions] == ["✅ Real", "🔇 Noise"]
    assert all(a["action"] == "http" and a["method"] == "POST" for a in actions)
    assert "v=real" in actions[0]["url"] and "a=5" in actions[0]["url"]


@respx.mock
async def test_notifier_posts_json_with_token():
    route = respx.post("https://ntfy.example/").mock(return_value=httpx.Response(200))
    async with httpx.AsyncClient() as client:
        await Notifier(client, "https://ntfy.example", "sre-reflex", "tok").send("t", "b", [{"x": 1}])
    req = route.calls.last.request
    assert json.loads(req.content) == {
        "topic": "sre-reflex", "title": "t", "message": "b", "actions": [{"x": 1}]
    }
    assert req.headers["authorization"] == "Bearer tok"


@respx.mock
async def test_notifier_error_has_status_but_no_url():
    import pytest

    from sre_reflex.notify import NotifyError

    respx.post("https://ntfy.example/").mock(return_value=httpx.Response(503))
    async with httpx.AsyncClient() as client:
        with pytest.raises(NotifyError) as ei:
            await Notifier(client, "https://ntfy.example", "t", "tok").send("a", "b")
    assert "503" in str(ei.value)
    assert "ntfy.example" not in str(ei.value)
    assert ei.value.__cause__ is None and ei.value.__suppress_context__


@respx.mock
async def test_notifier_connect_error_is_sanitized():
    import pytest

    from sre_reflex.notify import NotifyError

    respx.post("https://ntfy.example/").mock(side_effect=httpx.ConnectError("boom https://ntfy.example/"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(NotifyError) as ei:
            await Notifier(client, "https://ntfy.example", "t").send("a", "b")
    assert str(ei.value) == "ntfy delivery failed: ConnectError"
