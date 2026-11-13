"""Billing API routes."""

import asyncio
import uuid
from datetime import datetime
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, Payment, PaymentStatus, PaymentMethod, SubscriptionTier
from routers.auth import require_auth
from services.usage_service import usage_service

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


class SubscriptionInfo(BaseModel):
    """Current subscription info."""
    tier: str
    session_count: int
    total_duration_seconds: int
    total_duration_formatted: str
    total_charged_inr: float
    remaining_free_sessions: Optional[int]
    session_time_limit_seconds: Optional[int]


class UsageHistoryItem(BaseModel):
    """Usage history item."""
    session_id: str
    duration_seconds: int
    charged_amount: float
    recorded_at: str


class PaymentRequest(BaseModel):
    """Payment request."""
    amount: float
    payment_method: str  # 'upi' or 'card'


class PaymentResponse(BaseModel):
    """Payment response."""
    id: str
    amount: float
    currency: str
    status: str
    transaction_id: Optional[str]
    created_at: str


class PaymentHistoryItem(BaseModel):
    """Payment history item."""
    id: str
    amount: float
    currency: str
    payment_method: str
    status: str
    transaction_id: Optional[str]
    created_at: str


@router.get("/subscription", response_model=SubscriptionInfo)
async def get_subscription(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get current subscription and usage info."""
    stats = await usage_service.get_usage_stats(db, user.id)

    return SubscriptionInfo(
        tier=stats["subscription_tier"],
        session_count=stats["session_count"],
        total_duration_seconds=stats["total_duration_seconds"],
        total_duration_formatted=stats["total_duration_formatted"],
        total_charged_inr=stats["total_charged_inr"],
        remaining_free_sessions=stats["remaining_free_sessions"],
        session_time_limit_seconds=stats["session_time_limit_seconds"],
    )


@router.get("/usage")
async def get_usage_history(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get usage history."""
    from db.models import UsageRecord

    result = await db.execute(
        select(UsageRecord)
        .where(UsageRecord.user_id == user.id)
        .order_by(desc(UsageRecord.recorded_at))
        .limit(limit)
        .offset(offset)
    )
    records = result.scalars().all()

    return [
        UsageHistoryItem(
            session_id=r.session_id or "",
            duration_seconds=r.duration_seconds,
            charged_amount=r.charged_amount,
            recorded_at=r.recorded_at.isoformat(),
        )
        for r in records
    ]


@router.post("/payment", response_model=PaymentResponse)
async def create_payment(
    request: PaymentRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a mock payment.

    This is a mock implementation that simulates payment processing.
    In production, this would integrate with a real payment gateway.
    """
    # Validate payment method
    try:
        method = PaymentMethod(request.payment_method)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid payment method: {request.payment_method}",
        )

    # Create pending payment
    payment = Payment(
        user_id=user.id,
        amount=request.amount,
        currency="INR",
        payment_method=method,
        status=PaymentStatus.PENDING,
    )
    db.add(payment)
    await db.flush()
    await db.refresh(payment)

    logger.info(f"Payment created: {payment.id} for user {user.id}, amount={request.amount}")

    # Simulate payment processing (in production, this would be async)
    # For demo, we'll auto-complete after a delay
    payment.status = PaymentStatus.COMPLETED
    payment.transaction_id = f"TXN_{uuid.uuid4().hex[:12].upper()}"

    # Upgrade user to Pro on successful payment
    user.subscription_tier = SubscriptionTier.PRO
    user.updated_at = datetime.utcnow()

    logger.info(f"Payment {payment.id} completed, user {user.id} upgraded to Pro")

    return PaymentResponse(
        id=payment.id,
        amount=payment.amount,
        currency=payment.currency,
        status=payment.status.value,
        transaction_id=payment.transaction_id,
        created_at=payment.created_at.isoformat(),
    )


@router.get("/payments", response_model=list[PaymentHistoryItem])
async def get_payment_history(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get payment history."""
    result = await db.execute(
        select(Payment)
        .where(Payment.user_id == user.id)
        .order_by(desc(Payment.created_at))
        .limit(limit)
        .offset(offset)
    )
    payments = result.scalars().all()

    return [
        PaymentHistoryItem(
            id=p.id,
            amount=p.amount,
            currency=p.currency,
            payment_method=p.payment_method.value,
            status=p.status.value,
            transaction_id=p.transaction_id,
            created_at=p.created_at.isoformat(),
        )
        for p in payments
    ]


@router.get("/can-start-session")
async def check_can_start_session(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Check if user can start a new session (usage limits)."""
    can_start, message = await usage_service.can_start_session(db, user)
    time_limit = await usage_service.get_session_time_limit(user)

    return {
        "can_start": can_start,
        "message": message,
        "time_limit_seconds": time_limit,
        "tier": user.subscription_tier.value,
    }
