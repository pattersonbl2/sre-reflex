import pytest
from pydantic import ValidationError

from sre_reflex.alerts import (
    MAX_ALERTS_PER_WEBHOOK,
    MAX_KEY_LEN,
    MAX_MAP_ENTRIES,
    MAX_VALUE_LEN,
    AlertmanagerWebhook,
)


def _alert(**over):
    base = {
        "status": "firing", "labels": {"alertname": "X"}, "annotations": {},
        "startsAt": "2026-09-23T11:56:00Z", "fingerprint": "f",
    }
    return {**base, **over}


def test_alerts_truncated_to_cap():
    wh = AlertmanagerWebhook.model_validate(
        {"status": "firing", "alerts": [_alert(fingerprint=str(i)) for i in range(120)]}
    )
    assert len(wh.alerts) == MAX_ALERTS_PER_WEBHOOK
    assert wh.alerts[0].fingerprint == "0"


@pytest.mark.parametrize(
    "over",
    [
        {"labels": {"k" * (MAX_KEY_LEN + 1): "v"}},
        {"labels": {"alertname": "v" * (MAX_VALUE_LEN + 1)}},
        {"annotations": {"summary": "v" * (MAX_VALUE_LEN + 1)}},
        {"annotations": {f"k{i}": "v" for i in range(MAX_MAP_ENTRIES + 1)}},
        {"fingerprint": "f" * 129},
    ],
)
def test_oversized_fields_rejected(over):
    with pytest.raises(ValidationError):
        AlertmanagerWebhook.model_validate({"status": "firing", "alerts": [_alert(**over)]})


def test_limits_accept_boundary_values():
    AlertmanagerWebhook.model_validate({
        "status": "firing",
        "alerts": [_alert(labels={"k" * MAX_KEY_LEN: "v" * MAX_VALUE_LEN})],
    })
