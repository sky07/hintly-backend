"""Email service using Resend for sending verification emails."""

import logging
from config import settings

logger = logging.getLogger(__name__)


def _verification_html(user_name: str, verify_url: str) -> str:
    """Build a simple HTML verification email."""
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
        <h2 style="color: #1e293b; margin-bottom: 8px;">Verify your email</h2>
        <p style="color: #475569; font-size: 15px; line-height: 1.6;">
            Hi {user_name},<br><br>
            Thanks for signing up for Hintly. Please click the button below to verify your email address.
        </p>
        <a href="{verify_url}"
           style="display: inline-block; background: #2563eb; color: #fff; text-decoration: none;
                  padding: 12px 32px; border-radius: 8px; font-weight: 600; font-size: 15px; margin: 24px 0;">
            Verify Email
        </a>
        <p style="color: #94a3b8; font-size: 13px; line-height: 1.5;">
            Or copy this link into your browser:<br>
            <a href="{verify_url}" style="color: #2563eb; word-break: break-all;">{verify_url}</a>
        </p>
        <p style="color: #94a3b8; font-size: 13px;">This link expires in 24 hours.</p>
    </div>
    """


async def send_verification_email(to_email: str, user_name: str, token: str) -> bool:
    """Send a verification email via Resend. Returns True on success."""
    verify_url = f"{settings.clean_frontend_url}/auth/verify-email?token={token}"

    if not settings.resend_api_key:
        logger.warning(
            f"RESEND_API_KEY not set — skipping email to {to_email}. "
            f"Verification URL: {verify_url}"
        )
        return False

    try:
        import resend
        resend.api_key = settings.resend_api_key

        resend.Emails.send({
            "from": settings.email_from,
            "to": [to_email],
            "subject": "Verify your email - Hintly",
            "html": _verification_html(user_name, verify_url),
        })
        logger.info(f"Verification email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send verification email to {to_email}: {e}")
        return False


def _password_reset_html(user_name: str, reset_url: str) -> str:
    """Build a simple HTML password reset email."""
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 480px; margin: 0 auto; padding: 40px 20px;">
        <h2 style="color: #1e293b; margin-bottom: 8px;">Reset your password</h2>
        <p style="color: #475569; font-size: 15px; line-height: 1.6;">
            Hi {user_name},<br><br>
            We received a request to reset your password. Click the button below to choose a new password.
        </p>
        <a href="{reset_url}"
           style="display: inline-block; background: #2563eb; color: #fff; text-decoration: none;
                  padding: 12px 32px; border-radius: 8px; font-weight: 600; font-size: 15px; margin: 24px 0;">
            Reset Password
        </a>
        <p style="color: #94a3b8; font-size: 13px; line-height: 1.5;">
            Or copy this link into your browser:<br>
            <a href="{reset_url}" style="color: #2563eb; word-break: break-all;">{reset_url}</a>
        </p>
        <p style="color: #94a3b8; font-size: 13px;">This link expires in 1 hour. If you didn't request this, you can safely ignore this email.</p>
    </div>
    """


async def send_password_reset_email(to_email: str, user_name: str, token: str) -> bool:
    """Send a password reset email via Resend. Returns True on success."""
    reset_url = f"{settings.clean_frontend_url}/auth/reset-password?token={token}"

    if not settings.resend_api_key:
        logger.warning(
            f"RESEND_API_KEY not set — skipping email to {to_email}. "
            f"Reset URL: {reset_url}"
        )
        return False

    try:
        import resend
        resend.api_key = settings.resend_api_key

        resend.Emails.send({
            "from": settings.email_from,
            "to": [to_email],
            "subject": "Reset your password - Hintly",
            "html": _password_reset_html(user_name, reset_url),
        })
        logger.info(f"Password reset email sent to {to_email}")
        return True
    except Exception as e:
        logger.error(f"Failed to send password reset email to {to_email}: {e}")
        return False


def _support_ticket_html(user_name: str, ticket_number: str, subject: str, message: str, is_new: bool = True) -> str:
    """Build HTML for support ticket notifications."""
    heading = "New Support Ticket" if is_new else f"Reply on {ticket_number}"
    return f"""
    <div style="font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 520px; margin: 0 auto; padding: 40px 20px;">
        <h2 style="color: #1e293b; margin-bottom: 8px;">{heading}</h2>
        <p style="color: #64748b; font-size: 13px; margin-bottom: 16px;">Ticket: <strong>{ticket_number}</strong> &mdash; {subject}</p>
        <p style="color: #475569; font-size: 15px; line-height: 1.6;">
            From: <strong>{user_name}</strong>
        </p>
        <div style="background: #f8fafc; border-left: 3px solid #2563eb; padding: 16px; margin: 16px 0; border-radius: 0 8px 8px 0;">
            <p style="color: #334155; font-size: 14px; line-height: 1.6; margin: 0; white-space: pre-wrap;">{message}</p>
        </div>
        <p style="color: #94a3b8; font-size: 12px;">Hintly Support</p>
    </div>
    """


async def send_support_ticket_email(
    to_email: str,
    ticket_number: str,
    subject: str,
    message: str,
    user_name: str,
    is_new: bool = True,
) -> bool:
    """Send a support ticket notification email via Resend. Returns True on success."""
    if not settings.resend_api_key:
        logger.warning(f"RESEND_API_KEY not set — skipping support email to {to_email}")
        return False

    email_subject = f"[{ticket_number}] {subject}" if is_new else f"Re: [{ticket_number}] {subject}"

    try:
        import resend
        resend.api_key = settings.resend_api_key

        resend.Emails.send({
            "from": settings.email_from,
            "to": [to_email],
            "subject": email_subject,
            "html": _support_ticket_html(user_name, ticket_number, subject, message, is_new),
        })
        logger.info(f"Support email sent to {to_email} for {ticket_number}")
        return True
    except Exception as e:
        logger.error(f"Failed to send support email to {to_email}: {e}")
        return False
