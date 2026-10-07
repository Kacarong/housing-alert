"""비밀번호 해시(scrypt)와 서명된 세션 쿠키. 외부 의존성 없이 표준 라이브러리만 쓴다."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import time

SESSION_TTL = 14 * 24 * 3600


def hash_password(password: str, *, n: int = 2 ** 14, r: int = 8, p: int = 1) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p, dklen=32)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def verify_password(password: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    try:
        _, n, r, p, salt_hex, digest_hex = stored.split("$")
        digest = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt_hex), n=int(n), r=int(r), p=int(p), dklen=32)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(digest.hex(), digest_hex)


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_session(secret: str, pw_version: str, ttl: int = SESSION_TTL) -> str:
    """pw_version: 비밀번호 해시 일부. 비밀번호를 바꾸면 기존 세션이 모두 무효가 된다."""
    payload = {"sid": secrets.token_hex(8), "exp": int(time.time()) + ttl, "pv": pw_version}
    body = _b64(json.dumps(payload, separators=(",", ":")).encode())
    sig = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    return f"{body}.{sig}"


def read_session(secret: str, token: str | None, pw_version: str) -> dict | None:
    if not token or "." not in token or not secret:
        return None
    body, sig = token.rsplit(".", 1)
    expected = _b64(hmac.new(secret.encode(), body.encode(), hashlib.sha256).digest())
    if not hmac.compare_digest(sig, expected):
        return None
    try:
        payload = json.loads(_unb64(body))
    except ValueError:
        return None
    if payload.get("exp", 0) < time.time() or payload.get("pv") != pw_version:
        return None
    return payload


def csrf_token(secret: str, session: dict) -> str:
    return hmac.new(secret.encode(), f"csrf:{session.get('sid')}".encode(), hashlib.sha256).hexdigest()[:32]


def password_version(stored_hash: str | None) -> str:
    return hashlib.sha256((stored_hash or "").encode()).hexdigest()[:12]


def generate_password(length: int = 16) -> str:
    alphabet = "abcdefghijkmnopqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(length))
