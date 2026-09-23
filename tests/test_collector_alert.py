from sre_reflex.collectors.alert import AlertCollector


async def test_describes_alert_and_scrubs_annotations(alert, now):
    lines = await AlertCollector().collect(alert, now)
    assert lines == [
        (
            "Alert HighErrorRate (severity=warning) on namespace=n8n, pod=n8n-7d9f8c6b5-x2k4p, "
            "firing 4m."
        ),
        "Summary: n8n 5xx rate above 5%",
        "Description: Contact <email>",
    ]


async def test_handles_no_extra_labels(alert, now):
    alert.labels = {"alertname": "Watchdog"}
    alert.annotations = {}
    lines = await AlertCollector().collect(alert, now)
    assert lines == ["Alert Watchdog (severity=none) on no labels, firing 4m."]


async def test_scrubs_ip_in_label_values(alert, now):
    alert.labels = {"alertname": "Watchdog", "instance": "10.1.2.3:9100"}
    alert.annotations = {}
    lines = await AlertCollector().collect(alert, now)
    assert "<ip>" in lines[0]
    assert "10.1.2.3" not in lines[0]
