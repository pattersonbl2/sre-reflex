import re

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
# 20+ chars of base64/hex-ish text containing at least one digit and one letter, no hyphens.
_TOKEN = re.compile(r"\b(?=[A-Za-z0-9+/=_]*\d)(?=[A-Za-z0-9+/=_]*[A-Za-z])[A-Za-z0-9+/=_]{20,}")


def scrub(text: str) -> str:
    text = _EMAIL.sub("<email>", text)
    text = _IPV4.sub("<ip>", text)
    return _TOKEN.sub("<token>", text)
