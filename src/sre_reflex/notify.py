from datetime import datetime

import httpx

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.models.base import Answer
from sre_reflex.signing import label_url


def format_answers(answers: list[Answer]) -> str:
    by_id = {a.question_id: a.value for a in answers}
    parts = []
    if "actionable" in by_id:
        parts.append(f"actionable {by_id['actionable']:.2f}")
    if "severity" in by_id:
        parts.append(f"severity {by_id['severity']}/5")
    if "self_resolving" in by_id:
        parts.append(f"self-resolving {by_id['self_resolving']:.2f}")
    return " · ".join(parts)


def format_message(
    alert: AlertmanagerAlert,
    answers_by_model: dict[str, list[Answer] | None],
    collectors_ok: dict[str, bool],
) -> tuple[str, str]:
    where = alert.labels.get("namespace") or alert.labels.get("instance") or "cluster"
    lines = []
    for i, (model, answers) in enumerate(answers_by_model.items()):
        if answers is None:
            lines.append(f"{model}: unavailable")
        elif i == 0:
            lines.append(f"{format_answers(answers)}   ({model})")
        else:
            lines.append(f"{model}: {format_answers(answers)}")
    missing = [name for name, ok in collectors_ok.items() if not ok]
    if missing:
        lines.append("missing: " + ", ".join(missing))
    return f"{alert.alertname} · {where}", "\n".join(lines)


def label_actions(base_url: str, key: str, alert_id: int, now: datetime) -> list[dict]:
    return [
        {"action": "http", "label": label, "method": "POST", "clear": True,
         "url": label_url(base_url, key, alert_id, value, now)}
        for label, value in (("✅ Real", "real"), ("🔇 Noise", "noise"))
    ]


class Notifier:
    def __init__(self, client: httpx.AsyncClient, url: str, topic: str, token: str = ""):
        self.client = client
        self.url = url.rstrip("/") + "/"
        self.topic = topic
        self.token = token

    async def send(self, title: str, body: str, actions: list[dict] | None = None) -> None:
        payload: dict = {"topic": self.topic, "title": title, "message": body}
        if actions:
            payload["actions"] = actions
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        r = await self.client.post(self.url, json=payload, headers=headers, timeout=10)
        r.raise_for_status()
