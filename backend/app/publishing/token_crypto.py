"""
Encrypted-at-rest OAuth token storage (section 30).

Access/refresh tokens are Fernet-encrypted (AES-128-CBC + HMAC) before they
ever touch the database. TOKEN_ENCRYPTION_KEY MUST be set via environment
variable in any real deployment -- if it's missing, a random key is
generated for this process only, which means every token becomes
permanently undecryptable the moment the process restarts. That's a loud,
safe failure mode (forces reauthorization) rather than storing plaintext.

Never log a token, encrypted or not, alongside anything that could be used
to correlate it back to a plaintext value logged elsewhere.
"""
from __future__ import annotations

import logging

from cryptography.fernet import Fernet, InvalidToken

from app.config import settings

logger = logging.getLogger("clip_pipeline.publishing")

if settings.TOKEN_ENCRYPTION_KEY:
    _key = settings.TOKEN_ENCRYPTION_KEY.encode("utf-8")
else:
    _key = Fernet.generate_key()
    logger.warning(
        "TOKEN_ENCRYPTION_KEY is not set -- generated an ephemeral key for this process only. "
        "Any stored OAuth tokens will become undecryptable after a restart. Set "
        "TOKEN_ENCRYPTION_KEY in your environment for real deployments."
    )

_fernet = Fernet(_key)


def encrypt_token(plaintext: str) -> str:
    return _fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")


def decrypt_token(ciphertext: str) -> str | None:
    """Returns None (never raises) if the token can't be decrypted -- e.g. the
    encryption key changed since it was stored. Callers must treat that as
    "reauthorization required", not crash the worker."""
    try:
        return _fernet.decrypt(ciphertext.encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return None
