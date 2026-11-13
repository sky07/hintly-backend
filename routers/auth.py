"""Authentication API routes."""

from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status, Request, Response
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel, EmailStr
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User
from services.auth_service import auth_service, AuthError
from services.email_service import send_password_reset_email
from services import credit_service
from config import settings
from limiter import limiter

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/auth", tags=["auth"])

# HTTP Bearer token security scheme
security = HTTPBearer(auto_error=False)


# Request/Response models
class SignupRequest(BaseModel):
    """Signup request body."""
    email: EmailStr
    password: str
    name: str


class LoginRequest(BaseModel):
    """Login request body."""
    email: EmailStr
    password: str


class VerifyEmailRequest(BaseModel):
    """Verify email request body."""
    token: str


class ResendVerificationRequest(BaseModel):
    """Resend verification email request body."""
    email: EmailStr


class ForgotPasswordRequest(BaseModel):
    """Forgot password request body."""
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    """Reset password request body."""
    token: str
    new_password: str


class SignupResponse(BaseModel):
    """Signup response (no token - user must verify email first)."""
    message: str
    requires_verification: bool = True


class AuthResponse(BaseModel):
    """Authentication response."""
    access_token: str
    token_type: str = "bearer"
    user: dict


class UserResponse(BaseModel):
    """User info response."""
    id: str
    email: str
    name: str
    subscription_tier: str
    created_at: str


def user_to_dict(user: User, credit_balance: dict = None) -> dict:
    """Convert User model to dictionary for response."""
    result = {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "subscription_tier": user.subscription_tier.value,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat(),
    }
    if credit_balance is not None:
        result["credit_balance"] = credit_balance
    return result


async def get_current_user(
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
    db: AsyncSession = Depends(get_db),
) -> Optional[User]:
    """Get current user from JWT token. Returns None if not authenticated.

    Uses a short-lived in-memory cache to avoid a DB round-trip on every request.
    """
    if not credentials:
        return None

    user_id = auth_service.decode_token(credentials.credentials)
    if not user_id:
        return None

    # Try cache first (avoids ~500ms Supabase round-trip)
    cached_user = auth_service.get_user_by_id_cached(user_id)
    if cached_user is not None:
        return cached_user

    # Cache miss — fetch from DB and cache for subsequent requests
    user = await auth_service.get_user_by_id(db, user_id)
    if user:
        auth_service.cache_user(user)
    return user


async def require_auth(
    user: Optional[User] = Depends(get_current_user),
) -> User:
    """Require authenticated user. Raises 401 if not authenticated."""
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


@router.post("/signup", response_model=SignupResponse)
@limiter.limit("3/minute")
async def signup(
    request: Request,
    body: SignupRequest,
    db: AsyncSession = Depends(get_db),
):
    """Register a new user with email and password.

    Does not return a token. User must verify email first.
    """
    try:
        user, email_sent = await auth_service.register_user(
            db=db,
            email=body.email,
            password=body.password,
            name=body.name,
        )

        return SignupResponse(
            message="Account created. Please check your email to verify your account."
            if email_sent
            else "Account created. Verification email could not be sent — please use the resend option.",
            requires_verification=True,
        )
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/verify-email")
async def verify_email(
    body: VerifyEmailRequest,
    db: AsyncSession = Depends(get_db),
):
    """Verify a user's email address with the token from the verification email."""
    try:
        user = await auth_service.verify_email(db, body.token)
        return {"message": "Email verified successfully", "email": user.email}
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/resend-verification")
@limiter.limit("2/minute")
async def resend_verification(
    request: Request,
    body: ResendVerificationRequest,
    db: AsyncSession = Depends(get_db),
):
    """Resend the verification email."""
    try:
        await auth_service.resend_verification_email(db, body.email)
        return {"message": "If an account exists with that email, a verification email has been sent."}
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/login", response_model=AuthResponse)
@limiter.limit("5/minute")
async def login(
    request: Request,
    body: LoginRequest,
    db: AsyncSession = Depends(get_db),
):
    """Login with email and password."""
    try:
        user, token = await auth_service.login_user(
            db=db,
            email=body.email,
            password=body.password,
        )

        # Grant free trial on first login if not yet granted
        await credit_service.grant_free_trial(db, user.id)
        balance = await credit_service.get_balance_summary(db, user.id)

        return AuthResponse(
            access_token=token,
            user=user_to_dict(user, credit_balance=balance),
        )
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
        )


@router.post("/forgot-password")
@limiter.limit("3/minute")
async def forgot_password(
    request: Request,
    body: ForgotPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Request a password reset email. Always returns success to prevent email enumeration."""
    token = await auth_service.request_password_reset(db, body.email)
    if token:
        # Look up user name for the email
        user = await auth_service.get_user_by_email(db, body.email)
        if user:
            await send_password_reset_email(user.email, user.name, token)
    return {"message": "If an account exists with that email, we've sent a password reset link."}


@router.post("/reset-password")
@limiter.limit("5/minute")
async def reset_password(
    request: Request,
    body: ResetPasswordRequest,
    db: AsyncSession = Depends(get_db),
):
    """Reset password using a valid reset token."""
    try:
        await auth_service.reset_password(db, body.token, body.new_password)
        return {"message": "Password reset successfully. You can now log in with your new password."}
    except AuthError as e:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(e),
        )


@router.post("/logout")
async def logout():
    """Logout the current user.

    Note: JWT tokens are stateless, so logout is handled client-side
    by removing the token. This endpoint exists for API completeness.
    """
    return {"message": "Logged out successfully"}


@router.post("/refresh", response_model=AuthResponse)
async def refresh_token(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Refresh the JWT token."""
    token = auth_service.create_access_token(user.id)
    balance = await credit_service.get_balance_summary(db, user.id)

    return AuthResponse(
        access_token=token,
        user=user_to_dict(user, credit_balance=balance),
    )


@router.get("/me")
async def get_current_user_info(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get current user information."""
    balance = await credit_service.get_balance_summary(db, user.id)
    return {
        "id": user.id,
        "email": user.email,
        "name": user.name,
        "subscription_tier": user.subscription_tier.value,
        "is_admin": user.is_admin,
        "created_at": user.created_at.isoformat(),
        "credit_balance": balance,
    }


# Google OAuth endpoints
@router.get("/google")
async def google_oauth_start(request: Request):
    """Start Google OAuth flow.

    Returns the Google OAuth URL to redirect the user to.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth not configured",
        )

    # Build Google OAuth URL
    google_auth_url = (
        "https://accounts.google.com/o/oauth2/v2/auth"
        f"?client_id={settings.google_client_id}"
        f"&redirect_uri={settings.google_redirect_uri}"
        "&response_type=code"
        "&scope=email profile"
        "&access_type=offline"
        "&prompt=consent"
    )

    return {"url": google_auth_url}


class GoogleCallbackRequest(BaseModel):
    """Google OAuth callback request."""
    code: str


@router.post("/google/callback", response_model=AuthResponse)
async def google_oauth_callback(
    request: GoogleCallbackRequest,
    db: AsyncSession = Depends(get_db),
):
    """Handle Google OAuth callback.

    Exchanges the authorization code for tokens and creates/updates user.
    """
    if not settings.google_client_id or not settings.google_client_secret:
        raise HTTPException(
            status_code=status.HTTP_501_NOT_IMPLEMENTED,
            detail="Google OAuth not configured",
        )

    try:
        import httpx

        # Exchange code for tokens
        async with httpx.AsyncClient() as client:
            token_response = await client.post(
                "https://oauth2.googleapis.com/token",
                data={
                    "code": request.code,
                    "client_id": settings.google_client_id,
                    "client_secret": settings.google_client_secret,
                    "redirect_uri": settings.google_redirect_uri,
                    "grant_type": "authorization_code",
                },
            )

            if token_response.status_code != 200:
                logger.error(f"Google token exchange failed: {token_response.text}")
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to exchange authorization code",
                )

            tokens = token_response.json()
            access_token = tokens.get("access_token")

            # Get user info from Google
            userinfo_response = await client.get(
                "https://www.googleapis.com/oauth2/v2/userinfo",
                headers={"Authorization": f"Bearer {access_token}"},
            )

            if userinfo_response.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="Failed to get user info from Google",
                )

            google_user = userinfo_response.json()

        # Get or create user
        user = await auth_service.get_or_create_google_user(
            db=db,
            google_id=google_user.get("id"),
            email=google_user.get("email"),
            name=google_user.get("name", google_user.get("email")),
        )

        # Create our JWT token
        token = auth_service.create_access_token(user.id)

        # Grant free trial on first OAuth login if not yet granted
        await credit_service.grant_free_trial(db, user.id)
        balance = await credit_service.get_balance_summary(db, user.id)

        return AuthResponse(
            access_token=token,
            user=user_to_dict(user, credit_balance=balance),
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Google OAuth error: {e}")
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="OAuth authentication failed",
        )
