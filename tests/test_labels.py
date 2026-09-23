from datetime import timedelta

from stubs import MemoryStore

from sre_reflex.labels import LabelResult, infer_labels, record_hand_label
from sre_reflex.signing import sign


async def test_valid_label_recorded_once(now):
    store = MemoryStore()
    exp = int((now + timedelta(days=1)).timestamp())
    sig = sign("k", 3, "real", exp)
    assert await record_hand_label(store, "k", 3, "real", exp, sig, now) is LabelResult.OK
    assert await record_hand_label(store, "k", 3, "real", exp, sig, now) is LabelResult.REUSED
    assert store.labels == [(3, "real", "hand")]


async def test_bad_signature_or_value_invalid(now):
    store = MemoryStore()
    exp = int((now + timedelta(days=1)).timestamp())
    assert await record_hand_label(store, "k", 3, "real", exp, "bad", now) is LabelResult.INVALID
    sig = sign("k", 3, "maybe", exp)
    assert await record_hand_label(store, "k", 3, "maybe", exp, sig, now) is LabelResult.INVALID
    assert store.labels == []


async def test_infer_labels_marks_noise(now):
    store = MemoryStore()
    store.to_infer = [4, 9]
    assert await infer_labels(store, now) == 2
    assert store.labels == [(4, "noise", "inferred"), (9, "noise", "inferred")]
