from sre_reflex.config import Settings


def test_defaults(monkeypatch):
    monkeypatch.delenv("ENABLED_MODELS", raising=False)
    s = Settings(_env_file=None)
    assert s.state_token_cap == 800
    assert s.ntfy_topic == "sre-reflex"
    assert s.model_names == ["openjev", "ollama"]
    assert (s.collector_timeout_s, s.openjev_timeout_s, s.ollama_timeout_s) == (3.0, 5.0, 30.0)


def test_model_names_from_env(monkeypatch):
    monkeypatch.setenv("ENABLED_MODELS", "fake, openjev ,")
    assert Settings(_env_file=None).model_names == ["fake", "openjev"]
