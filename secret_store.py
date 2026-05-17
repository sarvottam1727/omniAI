"""Encrypted credential store for OmniAI Email Shooter.

Replaces the in-memory ``SECRET_STORE`` dict with a Fernet-encrypted on-disk
store. Each provider's password is encrypted independently and never written
to ``state.json``, never returned by any API, never logged.

Key bootstrap:
1. If ``OMNIAI_SECRET_KEY`` is set in the environment, use it (must be a
   urlsafe-base64 32-byte key — generate one with
   ``python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"``).
2. Otherwise, look for ``local_data/.secret.key``. If present, use it.
3. Otherwise, generate a fresh key, write it to ``local_data/.secret.key``,
   and use that. (Suitable for single-user local installs; for production,
   set ``OMNIAI_SECRET_KEY`` explicitly so secrets remain accessible after
   wiping ``local_data/``.)

On disk, ``local_data/secrets.bin`` is a single JSON dict mapping
``{provider_id: base64_token}`` where ``base64_token`` is the Fernet
ciphertext. Atomic write via temp-file rename.
"""
from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken


_LOG = logging.getLogger("omniai.secrets")
_LOCK = threading.Lock()


def _data_dir() -> Path:
    p = Path(__file__).parent / "local_data"
    p.mkdir(exist_ok=True)
    return p


def _load_or_create_key() -> bytes:
    env = os.environ.get("OMNIAI_SECRET_KEY")
    if env:
        return env.encode() if isinstance(env, str) else env
    key_path = _data_dir() / ".secret.key"
    if key_path.exists():
        return key_path.read_bytes().strip()
    new = Fernet.generate_key()
    key_path.write_bytes(new)
    try:
        # Best-effort tighten permissions on POSIX. Windows ACLs are not
        # touched here — OMNIAI_SECRET_KEY env var is the recommended path
        # there.
        os.chmod(key_path, 0o600)
    except OSError:
        pass
    _LOG.warning("Generated new encryption key at %s — back this up or set OMNIAI_SECRET_KEY", key_path)
    return new


class SecretStore:
    """Thread-safe Fernet-backed key/value store for provider passwords."""

    def __init__(self) -> None:
        self._fernet = Fernet(_load_or_create_key())
        self._path = _data_dir() / "secrets.bin"
        self._cache: dict[str, str] = {}
        self._load()

    def _load(self) -> None:
        with _LOCK:
            if not self._path.exists():
                self._cache = {}
                return
            try:
                self._cache = json.loads(self._path.read_text(encoding="utf-8"))
            except Exception:
                _LOG.warning("Couldn't read secrets.bin — starting fresh")
                self._cache = {}

    def _save(self) -> None:
        tmp = self._path.with_suffix(".bin.tmp")
        tmp.write_text(json.dumps(self._cache, indent=0), encoding="utf-8")
        os.replace(tmp, self._path)

    def set(self, provider_id: str, password: str) -> None:
        """Encrypt and persist a password for the given provider id."""
        if not password:
            self.delete(provider_id)
            return
        token = self._fernet.encrypt(password.encode("utf-8")).decode("ascii")
        with _LOCK:
            self._cache[provider_id] = token
            self._save()

    def get(self, provider_id: str) -> str | None:
        """Return the plaintext password for the given provider id, or None."""
        with _LOCK:
            token = self._cache.get(provider_id)
        if not token:
            return None
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except InvalidToken:
            _LOG.warning("Could not decrypt secret for provider %s (key rotated?)", provider_id)
            return None

    def has(self, provider_id: str) -> bool:
        with _LOCK:
            return provider_id in self._cache

    def delete(self, provider_id: str) -> None:
        with _LOCK:
            self._cache.pop(provider_id, None)
            self._save()

    def list_ids(self) -> list[str]:
        with _LOCK:
            return list(self._cache.keys())

    def count(self) -> int:
        with _LOCK:
            return len(self._cache)

    def items(self):
        """Yield (provider_id, plaintext_password). Used by callers that need
        the decrypted set for re-encryption / migration. Don't log the result."""
        with _LOCK:
            ids = list(self._cache.keys())
        for pid in ids:
            val = self.get(pid)
            if val is not None:
                yield pid, val


_STORE: SecretStore | None = None


def store() -> SecretStore:
    """Lazy-initialised singleton."""
    global _STORE
    if _STORE is None:
        _STORE = SecretStore()
    return _STORE
