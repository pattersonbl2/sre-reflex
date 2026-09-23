from sre_reflex.scrub import scrub


def test_redacts_email_ip_and_token():
    text = "user ops@example.com from 10.0.0.5 sent Jho74PK1JBwHBsfuTk2mM8OziS9dP8NPv5x"
    assert scrub(text) == "user <email> from <ip> sent <token>"


def test_keeps_hyphenated_names_and_short_words():
    text = "kube-prometheus-stack-alertmanager pod n8n-7d9f8c6b5-x2k4p restarted"
    assert scrub(text) == text
