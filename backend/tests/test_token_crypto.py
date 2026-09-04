"""Token encryption at rest (section 30): tokens must round-trip correctly and
must never be readable without the key -- including failing safely (None,
not a crash) when the key has changed since a token was stored."""
from cryptography.fernet import Fernet

from app.publishing.token_crypto import decrypt_token, encrypt_token


def test_roundtrip():
    original = "ya29.some-real-looking-access-token"
    encrypted = encrypt_token(original)
    assert encrypted != original  # never stored in plaintext
    assert decrypt_token(encrypted) == original


def test_ciphertext_is_not_the_plaintext_substring():
    token = "super-secret-refresh-token-value"
    encrypted = encrypt_token(token)
    assert token not in encrypted


def test_garbage_ciphertext_returns_none_not_an_exception():
    assert decrypt_token("not-a-real-fernet-token") is None


def test_token_encrypted_with_a_different_key_fails_safely():
    other_key = Fernet.generate_key()
    foreign_ciphertext = Fernet(other_key).encrypt(b"some-token").decode("ascii")
    assert decrypt_token(foreign_ciphertext) is None
