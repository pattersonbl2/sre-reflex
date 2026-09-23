import asyncio
import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from sre_reflex.alerts import AlertmanagerAlert
from sre_reflex.telemetry import COLLECTOR_ERRORS

log = logging.getLogger(__name__)


@dataclass
class CollectorResult:
    name: str
    lines: list[str]
    ok: bool


class Collector(Protocol):
    name: str

    async def collect(self, alert: AlertmanagerAlert, now: datetime) -> list[str]: ...


async def run_collector(
    collector: Collector, alert: AlertmanagerAlert, now: datetime, timeout_s: float
) -> CollectorResult:
    try:
        lines = await asyncio.wait_for(collector.collect(alert, now), timeout_s)
        return CollectorResult(collector.name, lines, True)
    except Exception:
        log.warning("collector %s failed for %s", collector.name, alert.fingerprint, exc_info=True)
        COLLECTOR_ERRORS.labels(collector=collector.name).inc()
        return CollectorResult(collector.name, [], False)
