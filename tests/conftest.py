from datetime import UTC, datetime

import pytest

from sre_reflex.alerts import AlertmanagerAlert

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.fixture
def now() -> datetime:
    return NOW


@pytest.fixture
def alert() -> AlertmanagerAlert:
    return AlertmanagerAlert(
        status="firing",
        labels={
            "alertname": "HighErrorRate",
            "severity": "warning",
            "namespace": "n8n",
            "pod": "n8n-7d9f8c6b5-x2k4p",
        },
        annotations={"summary": "n8n 5xx rate above 5%", "description": "Contact ops@example.com"},
        startsAt=datetime(2026, 9, 23, 11, 56, tzinfo=UTC),
        fingerprint="abc123",
        generatorURL=(
            "http://prometheus:9090/graph?g0.expr="
            "rate%28http_requests_total%7Bcode%3D~%225..%22%7D%5B5m%5D%29&g0.tab=1"
        ),
    )
