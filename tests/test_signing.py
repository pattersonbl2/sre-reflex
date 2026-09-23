from datetime import timedelta
from urllib.parse import parse_qs, urlparse

from sre_reflex.signing import label_url, sign, verify


def test_roundtrip(now):
    exp = int((now + timedelta(days=1)).timestamp())
    assert verify("k", 7, "real", exp, sign("k", 7, "real", exp), now)


def test_rejects_tampered_value_key_and_expired(now):
    exp = int((now + timedelta(days=1)).timestamp())
    sig = sign("k", 7, "real", exp)
    assert not verify("k", 7, "noise", exp, sig, now)
    assert not verify("other", 7, "real", exp, sig, now)
    past = int((now - timedelta(seconds=1)).timestamp())
    assert not verify("k", 7, "real", past, sign("k", 7, "real", past), now)


def test_label_url_expires_in_seven_days(now):
    url = label_url("https://x.example/", "k", 7, "noise", now)
    parsed = urlparse(url)
    q = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert parsed.path == "/label"
    assert (q["a"], q["v"]) == ("7", "noise")
    assert int(q["exp"]) == int((now + timedelta(days=7)).timestamp())
    assert verify("k", 7, "noise", int(q["exp"]), q["sig"], now)
