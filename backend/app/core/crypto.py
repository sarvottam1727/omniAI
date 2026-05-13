import base64
import hashlib

from cryptography.fernet import Fernet

from app.core.config import get_settings


def _fernet() -> Fernet:
    raw = get_settings().credential_encryption_key.encode()
    key = base64.urlsafe_b64encode(hashlib.sha256(raw).digest())
    return Fernet(key)


def encrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(value: str | None) -> str | None:
    if not value:
        return value
    return _fernet().decrypt(value.encode()).decode()
