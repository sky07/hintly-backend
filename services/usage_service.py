"""Usage tracking service for billing and free tier limits."""

from datetime import datetime, timedelta
from typing import Optional
import logging

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import User, InterviewSession, UsageRecord, SubscriptionTier

logger = logging.getLogger(__name__)

# Free tier limits
FREE_TIER_MAX_SESSIONS = 3
FREE_TIER_MAX_DURATION_SECONDS = 5 * 60  # 5 minutes per session

# Pro tier pricing
PRO_HOURLY_RATE_INR = 50


class UsageLimitError(Exception):
    """Raised when usage limit is exceeded."""
    pass


class UsageService:
    """Service for tracking usage and enforcing limits."""

    async def get_session_count(self, db: AsyncSession, user_id: str) -> int:
        """Get total number of sessions for a user."""
        result = await db.execute(
            select(func.count(InterviewSession.id))
            .where(InterviewSession.user_id == user_id)
        )
        return result.scalar() or 0

    async def get_total_usage_seconds(self, db: AsyncSession, user_id: str) -> int:
        """Get total usage in seconds for a user."""
        result = await db.execute(
            select(func.sum(InterviewSession.duration_seconds))
            .where(InterviewSession.user_id == user_id)
        )
        return result.scalar() or 0

    async def get_remaining_free_sessions(self, db: AsyncSession, user: User) -> int:
        """Get remaining free sessions for a free tier user."""
        if user.subscription_tier != SubscriptionTier.FREE:
            return -1  # Unlimited for pro users

        session_count = await self.get_session_count(db, user.id)
        return max(0, FREE_TIER_MAX_SESSIONS - session_count)

    async def can_start_session(self, db: AsyncSession, user: User) -> tuple[bool, str]:
        """Check if user can start a new session."""
        if user.subscription_tier == SubscriptionTier.PRO:
            return True, "Pro subscription active"

        # Check free tier limits
        session_count = await self.get_session_count(db, user.id)
        if session_count >= FREE_TIER_MAX_SESSIONS:
            return False, f"Free tier limit reached ({FREE_TIER_MAX_SESSIONS} sessions). Upgrade to Pro for unlimited sessions."

        return True, f"Session {session_count + 1} of {FREE_TIER_MAX_SESSIONS}"

    async def get_session_time_limit(self, user: User) -> Optional[int]:
        """Get time limit in seconds for user's session. None if unlimited."""
        if user.subscription_tier == SubscriptionTier.PRO:
            return None  # Unlimited
        return FREE_TIER_MAX_DURATION_SECONDS

    async def record_usage(
        self,
        db: AsyncSession,
        user_id: str,
        session_id: str,
        duration_seconds: int,
    ) -> UsageRecord:
        """Record usage for billing."""
        # Calculate charge (only for pro users, free tier is... free)
        charged_amount = 0.0

        # Get user to check tier
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user and user.subscription_tier == SubscriptionTier.PRO:
            # Pro tier: ₹50/hour
            hours = duration_seconds / 3600
            charged_amount = round(hours * PRO_HOURLY_RATE_INR, 2)

        record = UsageRecord(
            user_id=user_id,
            session_id=session_id,
            duration_seconds=duration_seconds,
            charged_amount=charged_amount,
        )
        db.add(record)
        await db.flush()
        await db.refresh(record)

        logger.info(
            f"Usage recorded: user={user_id}, session={session_id}, "
            f"duration={duration_seconds}s, charged=₹{charged_amount}"
        )

        return record

    async def get_usage_stats(self, db: AsyncSession, user_id: str) -> dict:
        """Get usage statistics for a user."""
        session_count = await self.get_session_count(db, user_id)
        total_seconds = await self.get_total_usage_seconds(db, user_id)

        # Get total charged amount
        result = await db.execute(
            select(func.sum(UsageRecord.charged_amount))
            .where(UsageRecord.user_id == user_id)
        )
        total_charged = result.scalar() or 0.0

        # Get user tier
        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        return {
            "session_count": session_count,
            "total_duration_seconds": total_seconds,
            "total_duration_formatted": f"{total_seconds // 60}:{total_seconds % 60:02d}",
            "total_charged_inr": total_charged,
            "subscription_tier": user.subscription_tier.value if user else "free",
            "remaining_free_sessions": (
                max(0, FREE_TIER_MAX_SESSIONS - session_count)
                if user and user.subscription_tier == SubscriptionTier.FREE
                else None
            ),
            "session_time_limit_seconds": (
                FREE_TIER_MAX_DURATION_SECONDS
                if user and user.subscription_tier == SubscriptionTier.FREE
                else None
            ),
        }

    async def cleanup_expired_free_sessions(self, db: AsyncSession) -> int:
        """Delete free tier sessions older than 24 hours."""
        cutoff = datetime.utcnow() - timedelta(hours=24)

        # Find free tier users with old sessions
        result = await db.execute(
            select(InterviewSession)
            .join(User)
            .where(User.subscription_tier == SubscriptionTier.FREE)
            .where(InterviewSession.created_at < cutoff)
        )
        sessions = result.scalars().all()

        count = len(sessions)
        for session in sessions:
            await db.delete(session)

        if count > 0:
            logger.info(f"Cleaned up {count} expired free tier sessions")

        return count


# Singleton instance
usage_service = UsageService()
