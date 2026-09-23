import pytest

from sre_reflex.config import Settings, check_startup_security


def test_defaults(monkeypatch):
    monkeypatch.delenv("ENABLED_MODELS", raising=False)
    s = Settings(_env_file=None)
    assert s.state_token_cap == 800
    assert s.ntfy_topic == "sre-reflex"
    assert s.ntfy_url == ""
    assert s.label_hmac_key == "change-me"
    assert s.model_names == ["openjev", "ollama"]
    assert (s.collector_timeout_s, s.openjev_timeout_s, s.ollama_timeout_s) == (3.0, 5.0, 30.0)


def test_model_names_from_env(monkeypatch):
    monkeypatch.setenv("ENABLED_MODELS", "fake, openjev ,")
    assert Settings(_env_file=None).model_names == ["fake", "openjev"]


def _settings(**overrides):
    defaults = {"label_hmac_key": "a" * 32, "ntfy_url": "https://ntfy.internal.example"}
    defaults.update(overrides)
    return Settings(_env_file=None, **defaults)


@pytest.mark.parametrize("key", ["", "change-me", "short-key"])
def test_check_startup_security_rejects_weak_hmac_key(key):
    with pytest.raises(ValueError, match="LABEL_HMAC_KEY"):
        check_startup_security(_settings(label_hmac_key=key))


def test_check_startup_security_rejects_empty_ntfy_url():
    with pytest.raises(ValueError, match="NTFY_URL"):
        check_startup_security(_settings(ntfy_url=""))


def test_check_startup_security_accepts_strong_config():
    check_startup_security(_settings())
