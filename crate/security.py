"""Password hashing and session signing.

PBKDF2-HMAC-SHA256 out of the standard library: no bcrypt wheel to build, no
argon2 dependency, and entirely adequate for one shared password guarding one
person's sample library.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

ITERATIONS = 240_000
PREFIX = "pbkdf2_sha256"


def hash_password(password: str, *, iterations: int = ITERATIONS) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return "$".join(
        [PREFIX, str(iterations), base64.b64encode(salt).decode(),
         base64.b64encode(digest).decode()]
    )


def verify_password(password: str, encoded: str) -> bool:
    try:
        prefix, iterations, salt_b64, digest_b64 = encoded.split("$")
        if prefix != PREFIX:
            return False
        expected = base64.b64decode(digest_b64)
        got = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(iterations)
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(expected, got)


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def sign_session(secret: str, *, ttl: int = 30 * 24 * 3600) -> str:
    payload = _b64(json.dumps({"exp": int(time.time()) + ttl}).encode())
    mac = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(mac)}"


def verify_session(secret: str, token: str) -> bool:
    try:
        payload, mac = token.split(".")
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _unb64(mac)):
            return False
        return int(json.loads(_unb64(payload))["exp"]) > time.time()
    except (ValueError, KeyError, TypeError):
        return False


def random_secret() -> str:
    return _b64(os.urandom(32))
