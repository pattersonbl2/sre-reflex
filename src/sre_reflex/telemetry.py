from prometheus_client import Counter, Histogram

ALERTS_PROCESSED = Counter("sre_reflex_alerts_processed", "Alerts triaged")
ADAPTER_LATENCY = Histogram(
    "sre_reflex_adapter_latency_seconds", "Decision model call latency", ["model"]
)
ADAPTER_ERRORS = Counter("sre_reflex_adapter_errors", "Decision model failures", ["model"])
COLLECTOR_ERRORS = Counter("sre_reflex_collector_errors", "Collector failures", ["collector"])
LABELS = Counter("sre_reflex_labels", "Labels recorded", ["source"])
