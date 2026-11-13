"""User settings API routes (API keys, preferences)."""

from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, UserAPIKey, LLMProvider
from routers.auth import require_auth
from services.encryption import encrypt_api_key, decrypt_api_key, mask_api_key
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/settings", tags=["settings"])


class APIKeyInfo(BaseModel):
    """API key info for display (masked)."""
    provider: str
    masked_key: str
    created_at: str
    updated_at: str


class APIKeyCreate(BaseModel):
    """Request to store/update an API key."""
    api_key: str


class APIKeyValidation(BaseModel):
    """API key validation result."""
    valid: bool
    message: str


async def validate_groq_key(api_key: str) -> tuple[bool, str]:
    """Validate a Groq API key by making a test request."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.groq.com/openai/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            if response.status_code == 200:
                return True, "API key is valid"
            elif response.status_code == 401:
                return False, "Invalid API key"
            else:
                return False, f"Validation failed: {response.status_code}"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


async def validate_openai_key(api_key: str) -> tuple[bool, str]:
    """Validate an OpenAI API key."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=10.0,
            )
            if response.status_code == 200:
                return True, "API key is valid"
            elif response.status_code == 401:
                return False, "Invalid API key"
            else:
                return False, f"Validation failed: {response.status_code}"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


async def validate_gemini_key(api_key: str) -> tuple[bool, str]:
    """Validate a Gemini API key."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"https://generativelanguage.googleapis.com/v1/models?key={api_key}",
                timeout=10.0,
            )
            if response.status_code == 200:
                return True, "API key is valid"
            elif response.status_code == 400 or response.status_code == 403:
                return False, "Invalid API key"
            else:
                return False, f"Validation failed: {response.status_code}"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


async def validate_anthropic_key(api_key: str) -> tuple[bool, str]:
    """Validate an Anthropic API key by making a minimal test request."""
    import httpx
    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 1,
                    "messages": [{"role": "user", "content": "hi"}],
                },
                timeout=10.0,
            )
            if response.status_code == 200:
                return True, "API key is valid"
            elif response.status_code == 401:
                return False, "Invalid API key"
            else:
                # Non-auth errors (rate limit, etc.) mean the key is valid
                return True, "API key accepted"
    except Exception as e:
        return False, f"Validation error: {str(e)}"


@router.get("/api-keys", response_model=list[APIKeyInfo])
async def list_api_keys(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List user's stored API keys (masked)."""
    if not settings.encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Encryption not configured",
        )

    result = await db.execute(
        select(UserAPIKey).where(UserAPIKey.user_id == user.id)
    )
    keys = result.scalars().all()

    return [
        APIKeyInfo(
            provider=k.provider.value,
            masked_key=mask_api_key(decrypt_api_key(k.encrypted_key)),
            created_at=k.created_at.isoformat(),
            updated_at=k.updated_at.isoformat(),
        )
        for k in keys
    ]


@router.put("/api-keys/{provider}")
async def store_api_key(
    provider: str,
    request: APIKeyCreate,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Store or update an API key for a provider."""
    if not settings.encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Encryption not configured",
        )

    # Validate provider
    try:
        llm_provider = LLMProvider(provider)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}. Valid options: openai, gemini, groq, anthropic",
        )

    # Validate the API key
    if llm_provider == LLMProvider.GROQ:
        valid, message = await validate_groq_key(request.api_key)
    elif llm_provider == LLMProvider.OPENAI:
        valid, message = await validate_openai_key(request.api_key)
    elif llm_provider == LLMProvider.GEMINI:
        valid, message = await validate_gemini_key(request.api_key)
    elif llm_provider == LLMProvider.ANTHROPIC:
        valid, message = await validate_anthropic_key(request.api_key)
    else:
        valid, message = True, "Validation skipped"

    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=message,
        )

    # Encrypt the key
    encrypted = encrypt_api_key(request.api_key)

    # Check if key already exists
    result = await db.execute(
        select(UserAPIKey)
        .where(UserAPIKey.user_id == user.id)
        .where(UserAPIKey.provider == llm_provider)
    )
    existing = result.scalar_one_or_none()

    if existing:
        existing.encrypted_key = encrypted
        logger.info(f"Updated {provider} API key for user {user.id}")
    else:
        key = UserAPIKey(
            user_id=user.id,
            provider=llm_provider,
            encrypted_key=encrypted,
        )
        db.add(key)
        logger.info(f"Stored {provider} API key for user {user.id}")

    return {"message": f"{provider} API key saved successfully"}


@router.delete("/api-keys/{provider}")
async def delete_api_key(
    provider: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a stored API key."""
    try:
        llm_provider = LLMProvider(provider)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}",
        )

    result = await db.execute(
        select(UserAPIKey)
        .where(UserAPIKey.user_id == user.id)
        .where(UserAPIKey.provider == llm_provider)
    )
    key = result.scalar_one_or_none()

    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    await db.delete(key)
    logger.info(f"Deleted {provider} API key for user {user.id}")

    return {"message": f"{provider} API key deleted"}


@router.post("/api-keys/{provider}/validate", response_model=APIKeyValidation)
async def validate_api_key(
    provider: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Validate a stored API key."""
    if not settings.encryption_key:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Encryption not configured",
        )

    try:
        llm_provider = LLMProvider(provider)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid provider: {provider}",
        )

    result = await db.execute(
        select(UserAPIKey)
        .where(UserAPIKey.user_id == user.id)
        .where(UserAPIKey.provider == llm_provider)
    )
    key = result.scalar_one_or_none()

    if not key:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="API key not found",
        )

    # Decrypt and validate
    api_key = decrypt_api_key(key.encrypted_key)

    if llm_provider == LLMProvider.GROQ:
        valid, message = await validate_groq_key(api_key)
    elif llm_provider == LLMProvider.OPENAI:
        valid, message = await validate_openai_key(api_key)
    elif llm_provider == LLMProvider.GEMINI:
        valid, message = await validate_gemini_key(api_key)
    elif llm_provider == LLMProvider.ANTHROPIC:
        valid, message = await validate_anthropic_key(api_key)
    else:
        valid, message = True, "Validation skipped"

    return APIKeyValidation(valid=valid, message=message)
