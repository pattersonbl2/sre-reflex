"""In-memory stand-ins shared by pipeline, labels and app tests."""
from sre_reflex.models.base import ModelError


class MemoryStore:
    def __init__(self):
        self.keys: dict = {}
        self.resolved: dict[int, object] = {}
        self.states: list = []
        self.decisions: list = []
        self.labels: list = []
        self.sigs: set[str] = set()
        self.to_infer: list[int] = []
        self.fail = False

    async def upsert_alert(self, alert):
        if self.fail:
            raise RuntimeError("db down")
        key = (alert.fingerprint, alert.startsAt)
        if key in self.keys:
            return self.keys[key], False
        self.keys[key] = len(self.keys) + 1
        return self.keys[key], True

    async def resolve_alert(self, fingerprint, fired_at, resolved_at):
        alert_id = self.keys.get((fingerprint, fired_at))
        if alert_id:
            self.resolved[alert_id] = resolved_at

    async def insert_state(self, alert_id, state):
        self.states.append((alert_id, state))
        return len(self.states)

    async def insert_decisions(self, state_id, model, q_version, answers):
        self.decisions.append((state_id, model, q_version, answers))

    async def add_label(self, alert_id, value, source, sig=None):
        if sig is not None and sig in self.sigs:
            return False
        if sig is not None:
            self.sigs.add(sig)
        self.labels.append((alert_id, value, source))
        return True

    async def alerts_to_infer(self, now):
        return list(self.to_infer)


class RecordingNotifier:
    def __init__(self):
        self.sent: list = []

    async def send(self, title, body, actions=None):
        self.sent.append((title, body, actions))


class StaticCollector:
    def __init__(self, name, lines):
        self.name = name
        self.lines = lines

    async def collect(self, alert, now):
        return list(self.lines)


class BrokenModel:
    name = "broken"

    async def decide(self, state, questions):
        raise ModelError("down")


class RecordingPipeline:
    def __init__(self):
        self.webhooks: list = []

    async def handle(self, webhook, now=None):
        self.webhooks.append(webhook)
