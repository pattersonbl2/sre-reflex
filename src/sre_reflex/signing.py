import hashlib
import hmac
from datetime import datetime, timedelta
from urllib.parse import urlencode

LABEL_TTL = timedelta(days=7)


def sign(key: str, alert_id: int, value: str, exp: int) -> str:
    msg = f"{alert_id}|{value}|{exp}".encode()
    return hmac.new(key.encode(), msg, hashlib.sha256).hexdigest()


def verify(key: str, alert_id: int, value: str, exp: int, sig: str, now: datetime) -> bool:
    if exp < int(now.timestamp()):
        return False
    return hmac.compare_digest(sign(key, alert_id, value, exp), sig)


def label_url(base_url: str, key: str, alert_id: int, value: str, now: datetime) -> str:
    exp = int((now + LABEL_TTL).timestamp())
    query = urlencode({"a": alert_id, "v": value, "exp": exp, "sig": sign(key, alert_id, value, exp)})
    return f"{base_url.rstrip('/')}/label?{query}"
