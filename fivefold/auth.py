from __future__ import annotations

import base64
import hashlib
import hmac
import os
import time
from collections import defaultdict, deque

from fastapi import HTTPException, Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from fivefold.config import Settings

SESSION_COOKIE = "fivefold_session"
_login_attempts: dict[str, deque[float]] = defaultdict(deque)


def hash_password(password: str, iterations: int = 310_000) -> str:
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations)
    return f"pbkdf2_sha256${iterations}${base64.urlsafe_b64encode(salt).decode()}${base64.urlsafe_b64encode(digest).decode()}"


def verify_password(password: str, encoded: str | None) -> bool:
    if not encoded:
        return False
    try:
        algorithm, iterations_text, salt_text, digest_text = encoded.split("$", 3)
        if algorithm != "pbkdf2_sha256":
            return False
        salt = base64.urlsafe_b64decode(salt_text.encode())
        expected = base64.urlsafe_b64decode(digest_text.encode())
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), salt, int(iterations_text)
        )
        return hmac.compare_digest(actual, expected)
    except (ValueError, TypeError):
        return False


def serializer(settings: Settings, salt: str) -> URLSafeTimedSerializer:
    return URLSafeTimedSerializer(settings.session_secret, salt=salt)


def create_session(settings: Settings) -> str:
    return serializer(settings, "session").dumps({"role": "admin"})


def read_session(settings: Settings, token: str | None) -> bool:
    if not token:
        return False
    try:
        payload = serializer(settings, "session").loads(token, max_age=12 * 60 * 60)
        return payload.get("role") == "admin"
    except (BadSignature, SignatureExpired):
        return False


def csrf_token(settings: Settings, subject: str = "admin") -> str:
    return serializer(settings, "csrf").dumps({"subject": subject})


def verify_csrf(settings: Settings, token: str | None, subject: str = "admin") -> bool:
    if not token:
        return False
    try:
        payload = serializer(settings, "csrf").loads(token, max_age=12 * 60 * 60)
        return payload.get("subject") == subject
    except (BadSignature, SignatureExpired):
        return False


def require_admin(request: Request, settings: Settings) -> None:
    if not read_session(settings, request.cookies.get(SESSION_COOKIE)):
        raise HTTPException(status_code=401, detail="Admin authentication required")


def check_login_rate_limit(client_key: str) -> None:
    now = time.monotonic()
    attempts = _login_attempts[client_key]
    while attempts and now - attempts[0] > 300:
        attempts.popleft()
    if len(attempts) >= 8:
        raise HTTPException(status_code=429, detail="Too many login attempts")
    attempts.append(now)
