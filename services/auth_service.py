"""Authentication service for user registration, login, and JWT management."""

import re
import time
import uuid
from datetime import datetime, timedelta
from typing import Optional
import logging

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from db.models import User, SubscriptionTier
from services.email_service import send_verification_email
import hashlib

logger = logging.getLogger(__name__)

# Short-lived user cache to avoid DB round-trip on every authenticated request.
# Key: user_id, Value: (User object, expiry timestamp)
_user_cache: dict[str, tuple] = {}
_USER_CACHE_TTL = 120  # seconds

# Password hashing context
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


class AuthError(Exception):
    """Authentication error."""
    pass


class AuthService:
    """Service for authentication operations."""

    @staticmethod
    def hash_password(password: str) -> str:
        """Normalize → bytes → SHA256 → hex (64 chars)"""
        sha = hashlib.sha256(password.encode("utf-8")).hexdigest()
        """Hash a password using bcrypt."""
        return pwd_context.hash(sha)

    @staticmethod
    def verify_password(plain_password: str, hashed_password: str) -> bool:
        sha = hashlib.sha256(plain_password.encode("utf-8")).hexdigest()
        """Verify a password against its hash."""
        return pwd_context.verify(sha, hashed_password)

    @staticmethod
    def create_access_token(user_id: str, expires_delta: Optional[timedelta] = None) -> str:
        """Create a JWT access token."""
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)

        payload = {
            "sub": user_id,
            "exp": expire,
            "iat": datetime.utcnow(),
        }
        return jwt.encode(payload, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)

    @staticmethod
    def decode_token(token: str) -> Optional[str]:
        """Decode and validate a JWT token. Returns user_id if valid."""
        try:
            payload = jwt.decode(
                token,
                settings.jwt_secret_key,
                algorithms=[settings.jwt_algorithm]
            )
            user_id: str = payload.get("sub")
            if user_id is None:
                return None
            return user_id
        except JWTError as e:
            logger.debug(f"JWT decode error: {e}")
            return None

    @staticmethod
    def _validate_password(password: str) -> None:
        """Validate password meets requirements."""
        if len(password) < 8:
            raise AuthError("Password must be at least 8 characters")
        if not re.search(r"\d", password):
            raise AuthError("Password must contain at least 1 number")

    async def register_user(
        self,
        db: AsyncSession,
        email: str,
        password: str,
        name: str,
    ) -> tuple[User, bool]:
        """Register a new user with email and password.

        Returns (user, email_sent) tuple.
        """
        email = email.strip().lower()
        name = name.strip()

        # Check if email already exists
        result = await db.execute(select(User).where(User.email == email))
        existing_user = result.scalar_one_or_none()
        if existing_user:
            raise AuthError("Email already registered")

        self._validate_password(password)

        # Generate verification token
        token = str(uuid.uuid4())
        expires = datetime.utcnow() + timedelta(hours=settings.email_verification_expiry_hours)

        # Create user
        user = User(
            email=email,
            password_hash=self.hash_password(password),
            name=name,
            subscription_tier=SubscriptionTier.FREE,
            email_verified=False,
            email_verification_token=token,
            email_verification_expires=expires,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

        # Send verification email
        email_sent = await send_verification_email(email, name, token)

        logger.info(f"New user registered: {email} (verification email sent: {email_sent})")
        return user, email_sent

    async def login_user(
        self,
        db: AsyncSession,
        email: str,
        password: str,
    ) -> tuple[User, str]:
        """Login a user and return user + JWT token."""
        email = email.strip().lower()

        # Find user by email
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            raise AuthError("Invalid email or password")

        if not user.password_hash:
            raise AuthError("Please use Google sign-in for this account")

        if not self.verify_password(password, user.password_hash):
            raise AuthError("Invalid email or password")

        # Check email verification
        if not user.email_verified:
            raise AuthError("Please verify your email before logging in")

        # Create access token
        token = self.create_access_token(user.id)

        logger.info(f"User logged in: {email}")
        return user, token

    async def verify_email(self, db: AsyncSession, token: str) -> User:
        """Verify a user's email with the given token."""
        result = await db.execute(
            select(User).where(User.email_verification_token == token)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise AuthError("Invalid verification token")

        if user.email_verified:
            return user  # Already verified

        if user.email_verification_expires and user.email_verification_expires < datetime.utcnow():
            raise AuthError("Verification token has expired. Please request a new one.")

        user.email_verified = True
        user.email_verification_token = None
        user.email_verification_expires = None
        await db.flush()
        await db.refresh(user)

        logger.info(f"Email verified: {user.email}")
        return user

    async def resend_verification_email(self, db: AsyncSession, email: str) -> bool:
        """Resend verification email. Returns True if sent. Enforces 60s cooldown."""
        email = email.strip().lower()

        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user:
            # Don't reveal whether email exists
            return True

        if user.email_verified:
            raise AuthError("Email is already verified")

        # Rate limit: check if token was generated less than 60s ago
        if user.email_verification_expires:
            token_created = user.email_verification_expires - timedelta(
                hours=settings.email_verification_expiry_hours
            )
            if (datetime.utcnow() - token_created).total_seconds() < 60:
                raise AuthError("Please wait before requesting another email")

        # Generate new token
        token = str(uuid.uuid4())
        user.email_verification_token = token
        user.email_verification_expires = datetime.utcnow() + timedelta(
            hours=settings.email_verification_expiry_hours
        )
        await db.flush()

        email_sent = await send_verification_email(user.email, user.name, token)
        logger.info(f"Resent verification email to {email} (sent: {email_sent})")
        return email_sent

    async def request_password_reset(self, db: AsyncSession, email: str) -> Optional[str]:
        """Generate a password reset token for the user. Returns token or None if user not found."""
        email = email.strip().lower()
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if not user or not user.password_hash:
            # Silent fail to prevent email enumeration
            return None

        token = str(uuid.uuid4())
        user.password_reset_token = token
        user.password_reset_expires = datetime.utcnow() + timedelta(hours=1)
        await db.flush()

        logger.info(f"Password reset requested for {email}")
        return token

    async def reset_password(self, db: AsyncSession, token: str, new_password: str) -> User:
        """Reset a user's password using a valid reset token."""
        result = await db.execute(
            select(User).where(User.password_reset_token == token)
        )
        user = result.scalar_one_or_none()

        if not user:
            raise AuthError("Invalid or expired reset token")

        if user.password_reset_expires and user.password_reset_expires < datetime.utcnow():
            raise AuthError("Reset token has expired. Please request a new one.")

        self._validate_password(new_password)

        user.password_hash = self.hash_password(new_password)
        user.password_reset_token = None
        user.password_reset_expires = None
        await db.flush()
        await db.refresh(user)

        logger.info(f"Password reset completed for {user.email}")
        return user

    async def get_user_by_id(self, db: AsyncSession, user_id: str) -> Optional[User]:
        """Get a user by their ID."""
        result = await db.execute(select(User).where(User.id == user_id))
        return result.scalar_one_or_none()

    def get_user_by_id_cached(self, user_id: str) -> Optional[User]:
        """Get a cached user by ID (no DB hit). Returns None on cache miss."""
        now = time.monotonic()
        cached = _user_cache.get(user_id)
        if cached:
            user_obj, expiry = cached
            if now < expiry:
                return user_obj
            del _user_cache[user_id]
        return None

    def cache_user(self, user: User) -> None:
        """Cache a user object after DB fetch."""
        from sqlalchemy.orm import make_transient
        make_transient(user)
        _user_cache[user.id] = (user, time.monotonic() + _USER_CACHE_TTL)

    async def get_user_by_email(self, db: AsyncSession, email: str) -> Optional[User]:
        """Get a user by their email."""
        result = await db.execute(select(User).where(User.email == email))
        return result.scalar_one_or_none()

    async def get_or_create_google_user(
        self,
        db: AsyncSession,
        google_id: str,
        email: str,
        name: str,
    ) -> User:
        """Get or create a user from Google OAuth data."""
        # First try to find by google_id
        result = await db.execute(select(User).where(User.google_id == google_id))
        user = result.scalar_one_or_none()

        if user:
            return user

        # Try to find by email (user might have registered with email first)
        result = await db.execute(select(User).where(User.email == email))
        user = result.scalar_one_or_none()

        if user:
            # Link Google account to existing user
            user.google_id = google_id
            user.email_verified = True  # Google already verified the email
            if not user.name or user.name == email:
                user.name = name
            await db.flush()
            await db.refresh(user)
            logger.info(f"Linked Google account to existing user: {email}")
            return user

        # Create new user (Google-verified email)
        user = User(
            email=email,
            google_id=google_id,
            name=name,
            subscription_tier=SubscriptionTier.FREE,
            email_verified=True,
        )
        db.add(user)
        await db.flush()
        await db.refresh(user)

        logger.info(f"New user registered via Google: {email}")
        return user


# Singleton instance
auth_service = AuthService()
