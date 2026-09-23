import pytest

from sre_reflex.models.base import Question, build_answer, normalize_options, noul_distribution


def test_noul_answer_value_is_yes_probability():
    q = Question(id="a", type="noul", text="t")
    a = build_answer(q, noul_distribution(0.8), latency_ms=12)
    assert a.value == pytest.approx(0.8)
    assert a.distribution == pytest.approx({"yes": 0.8, "no": 0.2})
    assert a.latency_ms == 12 and a.cost_usd == 0.0


def test_score_answer_value_is_one_based_level():
    q = Question(id="s", type="score", text="t", options=["low", "mid", "high"])
    a = build_answer(q, {"low": 0.1, "mid": 0.7, "high": 0.2}, latency_ms=0)
    assert a.value == 2


def test_choice_answer_value_is_option():
    q = Question(id="c", type="choice", text="t", options=["a", "b"])
    assert build_answer(q, {"a": 0.3, "b": 0.7}, latency_ms=0).value == "b"


def test_noul_distribution_clamps():
    assert noul_distribution(1.4) == {"yes": 1.0, "no": 0.0}
    assert noul_distribution(-0.2) == {"yes": 0.0, "no": 1.0}


def test_normalize_options_fills_missing_and_rescales():
    assert normalize_options(["x", "y", "z"], {"x": 1.0, "y": 3.0}) == {"x": 0.25, "y": 0.75, "z": 0.0}


def test_normalize_options_all_zero_is_uniform():
    assert normalize_options(["x", "y"], {"x": -1}) == {"x": 0.5, "y": 0.5}
