"""Encryption utilities for secure API key storage."""

from cryptography.fernet import Fernet
from config import settings


def get_fernet() -> Fernet:
    """Get Fernet instance for encryption/decryption."""
    if not settings.encryption_key:
        raise ValueError(
            "ENCRYPTION_KEY not set. Generate with: "
            "python -c \"from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())\""
        )
    return Fernet(settings.encryption_key.encode())


def encrypt_api_key(api_key: str) -> str:
    """Encrypt an API key for storage."""
    fernet = get_fernet()
    return fernet.encrypt(api_key.encode()).decode()


def decrypt_api_key(encrypted_key: str) -> str:
    """Decrypt a stored API key."""
    fernet = get_fernet()
    return fernet.decrypt(encrypted_key.encode()).decode()


def mask_api_key(api_key: str) -> str:
    """Mask an API key for display (e.g., sk-****)."""
    if len(api_key) <= 6:
        return "****"
    return f"{api_key[:3]}****"
