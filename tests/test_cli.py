import pytest

from sre_reflex.cli import build_parser


def test_parser_subcommands():
    p = build_parser()
    assert p.parse_args(["serve", "--port", "9000"]).port == 9000
    assert p.parse_args(["migrate"]).cmd == "migrate"
    assert p.parse_args(["infer-labels"]).cmd == "infer-labels"
    with pytest.raises(SystemExit):
        p.parse_args([])
