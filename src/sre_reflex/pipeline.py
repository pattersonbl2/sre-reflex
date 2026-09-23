import asyncio
import logging
import time
from datetime import UTC, datetime

from sre_reflex.alerts import AlertmanagerAlert, AlertmanagerWebhook
from sre_reflex.collectors.base import run_collector
from sre_reflex.models.base import Answer
from sre_reflex.notify import format_message, label_actions
from sre_reflex.questions import Q_VERSION, QUESTIONS
from sre_reflex.state import build_state
from sre_reflex.telemetry import ADAPTER_ERRORS, ADAPTER_LATENCY, ALERTS_PROCESSED

log = logging.getLogger(__name__)

SKIP_ALERTS = frozenset({"SreReflexDown", "SreReflexModelServerUnreachable"})


class Pipeline:
    def __init__(
        self,
        *,
        collectors,
        models,
        store,
        notifier,
        label_base_url: str,
        label_key: str,
        collector_timeout_s: float = 3.0,
        token_cap: int = 250,
    ):
        self.collectors = collectors
        self.models = models
        self.store = store
        self.notifier = notifier
        self.label_base_url = label_base_url
        self.label_key = label_key
        self.collector_timeout_s = collector_timeout_s
        self.token_cap = token_cap

    async def handle(self, webhook: AlertmanagerWebhook, now: datetime | None = None) -> None:
        for alert in webhook.alerts:
            try:
                await self.handle_alert(alert, now or datetime.now(UTC))
            except Exception:
                log.exception("failed to triage %s", alert.fingerprint)

    async def handle_alert(self, alert: AlertmanagerAlert, now: datetime) -> None:
        if alert.alertname in SKIP_ALERTS:
            return
        if alert.status == "resolved":
            await self.store.resolve_alert(alert.fingerprint, alert.startsAt, alert.endsAt or now)
            return

        alert_id: int | None = None
        try:
            alert_id, created = await self.store.upsert_alert(alert)
            if not created:
                return
        except Exception:
            log.exception("store unavailable; notifying without label buttons")

        results = await asyncio.gather(
            *(run_collector(c, alert, now, self.collector_timeout_s) for c in self.collectors)
        )
        state = build_state(list(results), self.token_cap)
        outputs = await asyncio.gather(*(self._decide(m, state.text) for m in self.models))
        answers_by_model = {m.name: out for m, out in zip(self.models, outputs)}

        if alert_id is not None:
            try:
                state_id = await self.store.insert_state(alert_id, state)
                for name, answers in answers_by_model.items():
                    if answers is not None:
                        await self.store.insert_decisions(state_id, name, Q_VERSION, answers)
            except Exception:
                log.exception("failed to persist decisions for %s", alert.fingerprint)
                alert_id = None

        title, body = format_message(alert, answers_by_model, state.collectors_ok)
        actions = (
            label_actions(self.label_base_url, self.label_key, alert_id, now)
            if alert_id is not None
            else None
        )
        await self.notifier.send(title, body, actions)
        ALERTS_PROCESSED.inc()

    async def _decide(self, model, state_text: str) -> list[Answer] | None:
        start = time.perf_counter()
        try:
            return await model.decide(state_text, QUESTIONS)
        except Exception:
            log.warning("model %s failed", model.name, exc_info=True)
            ADAPTER_ERRORS.labels(model=model.name).inc()
            return None
        finally:
            ADAPTER_LATENCY.labels(model=model.name).observe(time.perf_counter() - start)
