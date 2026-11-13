"""Admin API routes — pricing management, user credits, analytics."""

import logging
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status, Query
from pydantic import BaseModel
from sqlalchemy import select, func, case, and_
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import (
    User, Payment, CreditTransaction, InterviewSession,
    PaymentStatus, CreditSourceType, CreditBalance,
)
from routers.auth import require_auth
from services import credit_service, pricing_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])


# --- Admin dependency ---

async def require_admin(user: User = Depends(require_auth)) -> User:
    """Require the user to be an admin."""
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user


# --- Request models ---

class PricingTierCreate(BaseModel):
    credit_type: str
    minutes: int
    base_price_inr: float
    discount_percent: float = 0
    final_price_inr: float
    price_usd: Optional[float] = None
    is_active: bool = True


class PricingTierUpdate(BaseModel):
    credit_type: Optional[str] = None
    minutes: Optional[int] = None
    base_price_inr: Optional[float] = None
    discount_percent: Optional[float] = None
    final_price_inr: Optional[float] = None
    price_usd: Optional[float] = None
    is_active: Optional[bool] = None


class CreditAdjustRequest(BaseModel):
    credit_type: str
    seconds: int
    reason: str


class SystemConfigUpdate(BaseModel):
    updates: dict  # key-value pairs


# --- Pricing CRUD ---

@router.get("/pricing")
async def list_pricing(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all pricing tiers (including inactive)."""
    tiers = await pricing_service.get_all_pricing_tiers(db)
    return {"tiers": tiers}


@router.post("/pricing")
async def create_pricing(
    data: PricingTierCreate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Create a new pricing tier."""
    tier = await pricing_service.create_pricing_tier(db, data.model_dump())
    return tier


@router.put("/pricing/{tier_id}")
async def update_pricing(
    tier_id: str,
    data: PricingTierUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update an existing pricing tier."""
    updates = {k: v for k, v in data.model_dump().items() if v is not None}
    tier = await pricing_service.update_pricing_tier(db, tier_id, updates)
    if not tier:
        raise HTTPException(status_code=404, detail="Pricing tier not found")
    return tier


@router.delete("/pricing/{tier_id}")
async def delete_pricing(
    tier_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Delete a pricing tier."""
    success = await pricing_service.delete_pricing_tier(db, tier_id)
    if not success:
        raise HTTPException(status_code=404, detail="Pricing tier not found")
    return {"status": "deleted"}


# --- User credit management ---

@router.get("/users/{user_id}/credits")
async def get_user_credits(
    user_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """View a user's credit balance."""
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    summary = await credit_service.get_balance_summary(db, user_id)
    return {
        "user_id": user_id,
        "email": target_user.email,
        "name": target_user.name,
        **summary,
    }


@router.post("/users/{user_id}/credits/adjust")
async def adjust_user_credits(
    user_id: str,
    data: CreditAdjustRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Manually adjust a user's credits (admin grant/deduction)."""
    result = await db.execute(select(User).where(User.id == user_id))
    target_user = result.scalar_one_or_none()
    if not target_user:
        raise HTTPException(status_code=404, detail="User not found")

    if data.seconds > 0:
        new_balance = await credit_service.add_credits(
            db=db,
            user_id=user_id,
            credit_type=data.credit_type,
            seconds=data.seconds,
            source=CreditSourceType.ADMIN_GRANT.value,
            description=f"Admin adjustment: {data.reason}",
        )
    elif data.seconds < 0:
        success, remaining = await credit_service.deduct_credits(
            db=db,
            user_id=user_id,
            credit_type=data.credit_type,
            seconds=abs(data.seconds),
        )
        new_balance = remaining
    else:
        raise HTTPException(status_code=400, detail="Seconds must be non-zero")

    summary = await credit_service.get_balance_summary(db, user_id)
    return {"status": "adjusted", **summary}


# --- System config ---

@router.get("/config")
async def get_config(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all system config values."""
    config = await pricing_service.get_system_config(db)
    return {"config": config}


@router.put("/config")
async def update_config(
    data: SystemConfigUpdate,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Update system config values."""
    config = await pricing_service.update_system_config(db, data.updates)
    return {"config": config}


# --- Analytics ---

@router.get("/analytics/revenue")
async def revenue_analytics(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get revenue summary."""
    result = await db.execute(
        select(
            func.count(Payment.id).label("total_payments"),
            func.sum(Payment.amount).label("total_revenue_inr"),
        ).where(Payment.status == PaymentStatus.COMPLETED)
    )
    row = result.one()

    return {
        "total_payments": row.total_payments or 0,
        "total_revenue_inr": float(row.total_revenue_inr or 0),
    }


@router.get("/analytics/usage")
async def usage_analytics(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get usage summary."""
    # Total users
    user_count = await db.execute(select(func.count(User.id)))
    total_users = user_count.scalar() or 0

    # Total sessions
    session_count = await db.execute(select(func.count(InterviewSession.id)))
    total_sessions = session_count.scalar() or 0

    # Total seconds charged
    seconds_result = await db.execute(
        select(func.sum(InterviewSession.seconds_charged))
    )
    total_seconds = seconds_result.scalar() or 0

    return {
        "total_users": total_users,
        "total_sessions": total_sessions,
        "total_seconds_charged": total_seconds,
        "total_minutes_charged": round(total_seconds / 60, 1),
    }


@router.get("/transactions")
async def all_transactions(
    limit: int = 50,
    offset: int = 0,
    user_id: Optional[str] = None,
    credit_type: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get all transactions (paginated, filterable)."""
    query = select(CreditTransaction).order_by(CreditTransaction.created_at.desc())

    if user_id:
        query = query.where(CreditTransaction.user_id == user_id)
    if credit_type:
        query = query.where(CreditTransaction.credit_type == credit_type)

    query = query.limit(min(limit, 100)).offset(offset)
    result = await db.execute(query)
    transactions = result.scalars().all()

    return {
        "transactions": [
            {
                "id": t.id,
                "user_id": t.user_id,
                "credit_type": t.credit_type,
                "source_type": t.source_type,
                "seconds_amount": t.seconds_amount,
                "balance_after": t.balance_after,
                "description": t.description,
                "created_at": t.created_at.isoformat(),
            }
            for t in transactions
        ],
        "limit": limit,
        "offset": offset,
    }


# --- Rich Analytics ---

@router.get("/analytics/signups")
async def signups_over_time(
    days: int = Query(default=30, le=365),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get daily signup counts for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(
            func.date(User.created_at).label("day"),
            func.count(User.id).label("count"),
        )
        .where(User.created_at >= since)
        .group_by(func.date(User.created_at))
        .order_by(func.date(User.created_at))
    )
    rows = result.all()
    return {
        "days": days,
        "data": [{"date": str(r.day), "count": r.count} for r in rows],
    }


@router.get("/analytics/sessions-over-time")
async def sessions_over_time(
    days: int = Query(default=30, le=365),
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get daily session counts for the last N days."""
    since = datetime.utcnow() - timedelta(days=days)
    result = await db.execute(
        select(
            func.date(InterviewSession.created_at).label("day"),
            func.count(InterviewSession.id).label("count"),
        )
        .where(InterviewSession.created_at >= since)
        .group_by(func.date(InterviewSession.created_at))
        .order_by(func.date(InterviewSession.created_at))
    )
    rows = result.all()
    return {
        "days": days,
        "data": [{"date": str(r.day), "count": r.count} for r in rows],
    }


@router.get("/analytics/active-users")
async def active_users(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get active user counts: today, 7-day, 30-day (based on sessions)."""
    now = datetime.utcnow()
    day_ago = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)

    result = await db.execute(
        select(
            func.count(func.distinct(
                case((InterviewSession.created_at >= day_ago, InterviewSession.user_id))
            )).label("daily"),
            func.count(func.distinct(
                case((InterviewSession.created_at >= week_ago, InterviewSession.user_id))
            )).label("weekly"),
            func.count(func.distinct(
                case((InterviewSession.created_at >= month_ago, InterviewSession.user_id))
            )).label("monthly"),
        )
    )
    row = result.one()
    return {
        "daily_active": row.daily or 0,
        "weekly_active": row.weekly or 0,
        "monthly_active": row.monthly or 0,
    }


@router.get("/analytics/funnel")
async def user_funnel(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get conversion funnel: registered -> verified -> had session -> paid."""
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0
    verified_users = (await db.execute(
        select(func.count(User.id)).where(User.email_verified == True)
    )).scalar() or 0
    users_with_sessions = (await db.execute(
        select(func.count(func.distinct(InterviewSession.user_id)))
    )).scalar() or 0
    paid_users = (await db.execute(
        select(func.count(func.distinct(Payment.user_id)))
        .where(Payment.status == PaymentStatus.COMPLETED)
    )).scalar() or 0

    return {
        "registered": total_users,
        "email_verified": verified_users,
        "had_session": users_with_sessions,
        "paid": paid_users,
    }


@router.get("/users")
async def list_users(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, le=100),
    search: Optional[str] = None,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all users with session count and credit info."""
    offset = (page - 1) * limit

    # Subquery for session counts per user
    session_counts = (
        select(
            InterviewSession.user_id,
            func.count(InterviewSession.id).label("session_count"),
            func.max(InterviewSession.created_at).label("last_session_at"),
        )
        .group_by(InterviewSession.user_id)
        .subquery()
    )

    query = (
        select(
            User.id,
            User.email,
            User.name,
            User.is_admin,
            User.email_verified,
            User.created_at,
            func.coalesce(session_counts.c.session_count, 0).label("session_count"),
            session_counts.c.last_session_at,
        )
        .outerjoin(session_counts, User.id == session_counts.c.user_id)
        .order_by(User.created_at.desc())
    )

    if search:
        query = query.where(
            User.email.ilike(f"%{search}%") | User.name.ilike(f"%{search}%")
        )

    # Total count
    count_query = select(func.count(User.id))
    if search:
        count_query = count_query.where(
            User.email.ilike(f"%{search}%") | User.name.ilike(f"%{search}%")
        )
    total = (await db.execute(count_query)).scalar() or 0

    result = await db.execute(query.limit(limit).offset(offset))
    rows = result.all()

    return {
        "users": [
            {
                "id": r.id,
                "email": r.email,
                "name": r.name,
                "is_admin": r.is_admin,
                "email_verified": r.email_verified,
                "created_at": r.created_at.isoformat(),
                "session_count": r.session_count,
                "last_session_at": r.last_session_at.isoformat() if r.last_session_at else None,
            }
            for r in rows
        ],
        "total": total,
        "page": page,
        "limit": limit,
        "total_pages": (total + limit - 1) // limit,
    }


@router.get("/analytics/overview")
async def analytics_overview(
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Combined overview for the admin dashboard — all key metrics in one call."""
    now = datetime.utcnow()
    today = now - timedelta(days=1)
    week_ago = now - timedelta(days=7)

    # Total users
    total_users = (await db.execute(select(func.count(User.id)))).scalar() or 0

    # New users today
    new_today = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= today)
    )).scalar() or 0

    # New users this week
    new_this_week = (await db.execute(
        select(func.count(User.id)).where(User.created_at >= week_ago)
    )).scalar() or 0

    # Total sessions
    total_sessions = (await db.execute(
        select(func.count(InterviewSession.id))
    )).scalar() or 0

    # Sessions today
    sessions_today = (await db.execute(
        select(func.count(InterviewSession.id))
        .where(InterviewSession.created_at >= today)
    )).scalar() or 0

    # Sessions this week
    sessions_this_week = (await db.execute(
        select(func.count(InterviewSession.id))
        .where(InterviewSession.created_at >= week_ago)
    )).scalar() or 0

    # Total revenue
    revenue_result = await db.execute(
        select(func.sum(Payment.amount))
        .where(Payment.status == PaymentStatus.COMPLETED)
    )
    total_revenue = float(revenue_result.scalar() or 0)

    # Total minutes used
    minutes_result = await db.execute(
        select(func.sum(InterviewSession.seconds_charged))
    )
    total_seconds = minutes_result.scalar() or 0

    return {
        "total_users": total_users,
        "new_users_today": new_today,
        "new_users_this_week": new_this_week,
        "total_sessions": total_sessions,
        "sessions_today": sessions_today,
        "sessions_this_week": sessions_this_week,
        "total_revenue_inr": total_revenue,
        "total_minutes_used": round(total_seconds / 60, 1),
    }
