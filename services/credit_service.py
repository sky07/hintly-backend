"""Credit management service — balance tracking, deduction, and transaction logging."""

import logging
from datetime import datetime, timedelta
from typing import Optional, Tuple, List, Dict, Any

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import (
    CreditBalance, CreditTransaction, User,
    CreditType, CreditSourceType,
)
from config import settings

logger = logging.getLogger(__name__)


async def get_or_create_balance(db: AsyncSession, user_id: str) -> CreditBalance:
    """Get existing credit balance or create a new one for the user."""
    result = await db.execute(
        select(CreditBalance).where(CreditBalance.user_id == user_id)
    )
    balance = result.scalar_one_or_none()

    if balance is None:
        balance = CreditBalance(user_id=user_id)
        db.add(balance)
        await db.flush()
        logger.info(f"[CREDITS] Created new CreditBalance for user {user_id}")

    return balance


async def grant_free_trial(db: AsyncSession, user_id: str) -> bool:
    """Grant free trial credits to a user. Idempotent — skips if already granted.

    Returns True if trial was granted, False if already granted.
    """
    # Check user flag
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()
    if not user:
        logger.warning(f"[CREDITS] User {user_id} not found for free trial grant")
        return False

    if user.free_trial_granted:
        logger.info(f"[CREDITS] Free trial already granted for user {user_id}")
        return False

    trial_seconds = settings.free_trial_minutes * 60
    expiry = datetime.utcnow() + timedelta(days=settings.free_trial_expiry_days)

    balance = await get_or_create_balance(db, user_id)
    balance.free_trial_seconds = trial_seconds
    balance.free_trial_expires_at = expiry

    # Mark user as having received trial
    user.free_trial_granted = True

    # Log transaction
    txn = CreditTransaction(
        user_id=user_id,
        credit_type=CreditType.BYO_KEY.value,  # Free trial works for both types
        source_type=CreditSourceType.FREE_TRIAL.value,
        seconds_amount=trial_seconds,
        balance_after=trial_seconds,
        description=f"Free trial: {settings.free_trial_minutes} minutes, expires {expiry.isoformat()}",
    )
    db.add(txn)
    await db.flush()

    logger.info(f"[CREDITS] Granted free trial ({trial_seconds}s) to user {user_id}, expires {expiry}")
    return True


def _free_trial_available(balance: CreditBalance) -> int:
    """Get available free trial seconds (0 if expired)."""
    if balance.free_trial_seconds <= 0:
        return 0
    if balance.free_trial_expires_at and balance.free_trial_expires_at < datetime.utcnow():
        return 0
    return balance.free_trial_seconds


async def get_effective_balance(db: AsyncSession, user_id: str, credit_type: str) -> int:
    """Get total available seconds for a credit type (purchased + valid free trial)."""
    balance = await get_or_create_balance(db, user_id)

    trial_seconds = _free_trial_available(balance)

    if credit_type == CreditType.BYO_KEY.value:
        return balance.byo_key_seconds + trial_seconds
    elif credit_type == CreditType.PLATFORM_AI.value:
        return balance.platform_ai_seconds + trial_seconds
    else:
        return trial_seconds


async def can_start_session(
    db: AsyncSession, user_id: str, credit_type: str
) -> Tuple[bool, str, int]:
    """Check if user has enough credits to start a session.

    Returns (can_start, message, available_seconds). Minimum 60s required.
    """
    available = await get_effective_balance(db, user_id, credit_type)

    if available < 60:
        return (
            False,
            f"Insufficient credits. You have {available}s remaining, minimum 60s required.",
            available,
        )

    return (True, "OK", available)


async def add_credits(
    db: AsyncSession,
    user_id: str,
    credit_type: str,
    seconds: int,
    source: str,
    payment_id: Optional[str] = None,
    description: Optional[str] = None,
) -> int:
    """Add credits to a user's balance. Returns new balance for the credit type."""
    balance = await get_or_create_balance(db, user_id)

    if credit_type == CreditType.BYO_KEY.value:
        balance.byo_key_seconds += seconds
        new_balance = balance.byo_key_seconds
    elif credit_type == CreditType.PLATFORM_AI.value:
        balance.platform_ai_seconds += seconds
        new_balance = balance.platform_ai_seconds
    else:
        logger.error(f"[CREDITS] Unknown credit type: {credit_type}")
        return 0

    # Log transaction
    txn = CreditTransaction(
        user_id=user_id,
        credit_type=credit_type,
        source_type=source,
        seconds_amount=seconds,
        balance_after=new_balance,
        payment_id=payment_id,
        description=description or f"Added {seconds}s ({seconds // 60}min) {credit_type}",
    )
    db.add(txn)
    await db.flush()

    logger.info(f"[CREDITS] Added {seconds}s to {credit_type} for user {user_id}, new balance: {new_balance}s")
    return new_balance


async def deduct_credits(
    db: AsyncSession,
    user_id: str,
    credit_type: str,
    seconds: int,
    session_id: Optional[str] = None,
) -> Tuple[bool, int]:
    """Deduct credits atomically. Uses free trial first, then purchased.

    Uses SELECT FOR UPDATE for PostgreSQL row-level locking.
    Returns (success, remaining_seconds).
    """
    # Use FOR UPDATE to lock the row (PostgreSQL only, safe no-op for SQLite)
    result = await db.execute(
        select(CreditBalance)
        .where(CreditBalance.user_id == user_id)
        .with_for_update()
    )
    balance = result.scalar_one_or_none()

    if not balance:
        return (False, 0)

    remaining_to_deduct = seconds
    trial_available = _free_trial_available(balance)

    # Deduct from free trial first
    if trial_available > 0 and remaining_to_deduct > 0:
        trial_deduct = min(trial_available, remaining_to_deduct)
        balance.free_trial_seconds -= trial_deduct
        remaining_to_deduct -= trial_deduct

    # Deduct from purchased credits
    if remaining_to_deduct > 0:
        if credit_type == CreditType.BYO_KEY.value:
            if balance.byo_key_seconds >= remaining_to_deduct:
                balance.byo_key_seconds -= remaining_to_deduct
                remaining_to_deduct = 0
            else:
                remaining_to_deduct -= balance.byo_key_seconds
                balance.byo_key_seconds = 0
        elif credit_type == CreditType.PLATFORM_AI.value:
            if balance.platform_ai_seconds >= remaining_to_deduct:
                balance.platform_ai_seconds -= remaining_to_deduct
                remaining_to_deduct = 0
            else:
                remaining_to_deduct -= balance.platform_ai_seconds
                balance.platform_ai_seconds = 0

    if remaining_to_deduct > 0:
        # Not enough credits — partial deduction happened
        effective_deducted = seconds - remaining_to_deduct
    else:
        effective_deducted = seconds

    # Calculate remaining
    remaining = await get_effective_balance(db, user_id, credit_type)

    # Log transaction
    txn = CreditTransaction(
        user_id=user_id,
        credit_type=credit_type,
        source_type=CreditSourceType.PURCHASE.value,  # deduction source
        seconds_amount=-effective_deducted,
        balance_after=remaining,
        session_id=session_id,
        description=f"Session deduction: {effective_deducted}s",
    )
    db.add(txn)
    await db.flush()

    success = remaining_to_deduct == 0
    if not success:
        logger.warning(f"[CREDITS] Partial deduction for user {user_id}: wanted {seconds}s, got {effective_deducted}s")

    return (success, remaining)


async def get_balance_summary(db: AsyncSession, user_id: str) -> Dict[str, Any]:
    """Get a formatted balance summary for API response."""
    balance = await get_or_create_balance(db, user_id)
    trial_available = _free_trial_available(balance)

    return {
        "byo_key_seconds": balance.byo_key_seconds,
        "byo_key_minutes": round(balance.byo_key_seconds / 60, 1),
        "platform_ai_seconds": balance.platform_ai_seconds,
        "platform_ai_minutes": round(balance.platform_ai_seconds / 60, 1),
        "free_trial_seconds": trial_available,
        "free_trial_minutes": round(trial_available / 60, 1),
        "free_trial_expires_at": (
            balance.free_trial_expires_at.isoformat()
            if balance.free_trial_expires_at
            else None
        ),
        "total_byo_seconds": balance.byo_key_seconds + trial_available,
        "total_platform_seconds": balance.platform_ai_seconds + trial_available,
    }


async def get_transaction_history(
    db: AsyncSession,
    user_id: str,
    limit: int = 50,
    offset: int = 0,
) -> List[Dict[str, Any]]:
    """Get paginated transaction history for a user."""
    result = await db.execute(
        select(CreditTransaction)
        .where(CreditTransaction.user_id == user_id)
        .order_by(CreditTransaction.created_at.desc())
        .limit(limit)
        .offset(offset)
    )
    transactions = result.scalars().all()

    return [
        {
            "id": txn.id,
            "credit_type": txn.credit_type,
            "source_type": txn.source_type,
            "seconds_amount": txn.seconds_amount,
            "balance_after": txn.balance_after,
            "description": txn.description,
            "payment_id": txn.payment_id,
            "session_id": txn.session_id,
            "created_at": txn.created_at.isoformat(),
        }
        for txn in transactions
    ]
