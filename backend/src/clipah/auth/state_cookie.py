"""Carry one pending login ceremony in an encrypted, authenticated browser cookie."""

from __future__ import annotations

import base64
import hashlib
import json
from datetime import datetime
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from clipah.auth.models import AuthorizationError, PendingAuthorization

# Domain separation keeps this key independent of every other use of the session secret.
CEREMONY_KEY_CONTEXT = "clipah-oidc-ceremony-v1:"


def seal_pending_authorization(pending: PendingAuthorization, *, secret: str) -> str:
    """Encrypt one ceremony so the browser can hold it without reading or forging it."""
    payload = json.dumps(
        {
            "state": pending.state,
            "nonce": pending.nonce,
            "code_verifier": pending.code_verifier,
            "redirect_uri": pending.redirect_uri,
            "created_at": pending.created_at.isoformat(),
        }
    )
    return _cipher(secret).encrypt(payload.encode("utf-8")).decode("ascii")


def open_pending_authorization(sealed: str, *, secret: str) -> PendingAuthorization:
    """Recover a ceremony only while it is intact and unforged.

    Freshness is not decided here: the login flow measures the recovered
    ``created_at`` against its own authorization lifetime and injected clock.
    """
    try:
        plaintext = _cipher(secret).decrypt(sealed.encode("ascii"))
        payload: Any = json.loads(plaintext)
        pending = PendingAuthorization(
            state=str(payload["state"]),
            nonce=str(payload["nonce"]),
            code_verifier=str(payload["code_verifier"]),
            redirect_uri=str(payload["redirect_uri"]),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )
    except (InvalidToken, ValueError, TypeError, KeyError) as error:
        # A tampered, stale, or malformed ceremony is refused without explanation.
        raise AuthorizationError("pending authorization is not usable") from error
    return pending


def _cipher(secret: str) -> Fernet:
    """Derive the ceremony cipher from the deployment's session secret."""
    digest = hashlib.sha256(f"{CEREMONY_KEY_CONTEXT}{secret}".encode()).digest()
    return Fernet(base64.urlsafe_b64encode(digest))
