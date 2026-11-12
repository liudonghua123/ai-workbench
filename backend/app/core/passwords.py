"""Password and token primitives — standard library only.

``scrypt`` ships with Python, is memory-hard, and needs no wheel on any platform.
That matters here: the workbench must install on a bare Windows box with a
``.venv`` and nothing else, and "you need to compile bcrypt" is exactly the kind
of friction that ends with hardcoded passwords.

Token format: ``wbs_<43 chars of base64url>`` — prefixed so a leaked string is
recognisable in logs, random enough that guessing is not the threat model
(``secrets.token_urlsafe(32)`` is 256 bits).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

# scrypt cost: 2**14 blocks × 8 × 1. ~16 MB per hash, ~40 ms on a laptop.
_N, _R, _P = 2**14, 8, 1
_PREFIX = "scrypt"


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("密码不能为空")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return "$".join(
        (
            _PREFIX,
            str(_N),
            str(_R),
            str(_P),
            base64.b64encode(salt).decode(),
            base64.b64encode(digest).decode(),
        )
    )


def verify_password(password: str | None, stored: str | None) -> bool:
    if not password or not stored:
        return False
    try:
        prefix, n_raw, r_raw, p_raw, salt_raw, digest_raw = stored.split("$")
        if prefix != _PREFIX:
            return False
        salt = base64.b64decode(salt_raw)
        expected = base64.b64decode(digest_raw)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_raw),
            r=int(r_raw),
            p=int(p_raw),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    # constant-time: a length leak is still a leak
    return hmac.compare_digest(actual, expected)


def new_token(prefix: str = "wbs") -> str:
    return f"{prefix}_{secrets.token_urlsafe(32)}"


def token_hash(token: str) -> str:
    """Sessions store this, never the token itself."""
    return hashlib.sha256(token.strip().encode("utf-8")).hexdigest()


def new_state() -> str:
    return secrets.token_urlsafe(24)


def new_pkce_pair() -> tuple[str, str]:
    """(verifier, S256 challenge) for the authorization-code flow."""
    verifier = secrets.token_urlsafe(48)
    challenge = (
        base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")
    )
    return verifier, challenge
