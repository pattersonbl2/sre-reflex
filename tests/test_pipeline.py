from stubs import BrokenModel, MemoryStore, RecordingNotifier, StaticCollector

from sre_reflex.alerts import AlertmanagerWebhook
from sre_reflex.models.fake import FakeModel
from sre_reflex.pipeline import Pipeline


def make(models=None, store=None):
    store = store or MemoryStore()
    notifier = RecordingNotifier()
    pipeline = Pipeline(
        collectors=[StaticCollector("alert", ["a1"]), StaticCollector("logs", ["l1"])],
        models=models or [FakeModel()],
        store=store,
        notifier=notifier,
        label_base_url="https://x.example",
        label_key="k",
    )
    return pipeline, store, notifier


async def test_firing_alert_is_stored_and_notified(alert, now):
    pipeline, store, notifier = make()
    await pipeline.handle(AlertmanagerWebhook(status="firing", alerts=[alert]), now)
    assert store.states[0][1].text == "## alert\na1\n\n## logs\nl1"
    assert [(d[1], d[2], len(d[3])) for d in store.decisions] == [("fake", 1, 3)]
    title, body, actions = notifier.sent[0]
    assert title == "HighErrorRate · n8n"
    assert body.endswith("(fake)")
    assert len(actions) == 2 and "a=1" in actions[0]["url"]


async def test_duplicate_delivery_is_ignored(alert, now):
    pipeline, _, notifier = make()
    await pipeline.handle_alert(alert, now)
    await pipeline.handle_alert(alert, now)
    assert len(notifier.sent) == 1


async def test_resolved_alert_updates_store_only(alert, now):
    pipeline, store, notifier = make()
    await pipeline.handle_alert(alert, now)
    alert.status = "resolved"
    alert.endsAt = now
    await pipeline.handle_alert(alert, now)
    assert store.resolved == {1: now}
    assert len(notifier.sent) == 1


async def test_self_alerts_are_skipped(alert, now):
    pipeline, store, notifier = make()
    alert.labels["alertname"] = "SreReflexDown"
    await pipeline.handle_alert(alert, now)
    assert notifier.sent == [] and store.keys == {}


async def test_store_down_still_notifies_without_buttons(alert, now):
    store = MemoryStore()
    store.fail = True
    pipeline, _, notifier = make(store=store)
    await pipeline.handle_alert(alert, now)
    assert notifier.sent[0][2] is None


async def test_failed_model_marked_unavailable(alert, now):
    pipeline, store, notifier = make(models=[FakeModel(), BrokenModel()])
    await pipeline.handle_alert(alert, now)
    assert "broken: unavailable" in notifier.sent[0][1]
    assert [d[1] for d in store.decisions] == ["fake"]
