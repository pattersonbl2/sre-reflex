from datetime import datetime
from enum import Enum

from sre_reflex.signing import verify
from sre_reflex.telemetry import LABELS

VALUES = {"real", "noise"}


class LabelResult(Enum):
    OK = "ok"
    INVALID = "invalid"
    REUSED = "reused"


async def record_hand_label(
    store, key: str, alert_id: int, value: str, exp: int, sig: str, now: datetime
) -> LabelResult:
    if value not in VALUES or not verify(key, alert_id, value, exp, sig, now):
        return LabelResult.INVALID
    if not await store.add_label(alert_id, value, "hand", sig):
        return LabelResult.REUSED
    LABELS.labels(source="hand").inc()
    return LabelResult.OK


async def infer_labels(store, now: datetime) -> int:
    ids = await store.alerts_to_infer(now)
    for alert_id in ids:
        await store.add_label(alert_id, "noise", "inferred")
        LABELS.labels(source="inferred").inc()
    return len(ids)
