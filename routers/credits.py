"""Credits API routes — balance, transactions, purchase, pricing, PhonePe webhook."""

import base64
import json
import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, Payment, PaymentMethod, PaymentStatus, CreditType
from routers.auth import require_auth
from services import credit_service, phonepe_service, pricing_service
from config import settings

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/credits", tags=["credits"])


# --- Request/Response Models ---

class PurchaseRequest(BaseModel):
    pricing_tier_id: str
    redirect_url: str  # Frontend URL to redirect after payment


class PurchaseResponse(BaseModel):
    payment_id: str
    redirect_url: str


# --- Endpoints ---

@router.get("/balance")
async def get_balance(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get current user's credit balance."""
    # Grant free trial on first balance check if not yet granted
    await credit_service.grant_free_trial(db, user.id)

    summary = await credit_service.get_balance_summary(db, user.id)
    return summary


@router.get("/transactions")
async def get_transactions(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get paginated transaction history."""
    transactions = await credit_service.get_transaction_history(
        db, user.id, limit=min(limit, 100), offset=offset
    )
    return {"transactions": transactions, "limit": limit, "offset": offset}


@router.post("/purchase", response_model=PurchaseResponse)
async def purchase_credits(
    request: PurchaseRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Initiate a credit purchase via PhonePe."""
    # Get pricing tier
    tier = await pricing_service.get_pricing_tier_by_id(db, request.pricing_tier_id)
    if not tier or not tier.is_active:
        raise HTTPException(status_code=400, detail="Invalid or inactive pricing tier")

    # Create merchant transaction ID
    merchant_txn_id = f"IA_{uuid.uuid4().hex[:20]}"

    # Create PENDING payment record
    payment = Payment(
        user_id=user.id,
        amount=tier.final_price_inr,
        currency="INR",
        payment_method=PaymentMethod.PHONEPE,
        status=PaymentStatus.PENDING,
        credit_type=tier.credit_type,
        credit_seconds=tier.minutes * 60,
        pricing_tier_id=tier.id,
        phonepe_merchant_transaction_id=merchant_txn_id,
    )
    db.add(payment)
    await db.flush()

    # Call PhonePe to create payment order
    callback_url = f"{settings.backend_url}/api/credits/webhook/phonepe"
    amount_paise = int(tier.final_price_inr * 100)

    redirect_url = await phonepe_service.create_payment_order(
        merchant_txn_id=merchant_txn_id,
        amount_paise=amount_paise,
        user_id=user.id,
        redirect_url=request.redirect_url,
        callback_url=callback_url,
    )

    if not redirect_url:
        payment.status = PaymentStatus.FAILED
        await db.flush()
        raise HTTPException(status_code=502, detail="Failed to create payment order with PhonePe")

    return PurchaseResponse(payment_id=payment.id, redirect_url=redirect_url)


@router.get("/pricing")
async def get_pricing(db: AsyncSession = Depends(get_db)):
    """Get active pricing tiers. Public endpoint — no auth required."""
    tiers = await pricing_service.get_active_pricing_tiers(db)
    return {"tiers": tiers}


@router.post("/webhook/phonepe")
async def phonepe_webhook(request: Request, db: AsyncSession = Depends(get_db)):
    """PhonePe server-to-server callback. No auth — checksum verified."""
    body = await request.body()
    x_verify = request.headers.get("X-VERIFY", "")

    try:
        body_json = json.loads(body)
        response_base64 = body_json.get("response", "")
    except (json.JSONDecodeError, AttributeError):
        logger.error("[PHONEPE-WEBHOOK] Invalid request body")
        raise HTTPException(status_code=400, detail="Invalid request body")

    # Verify checksum
    if not phonepe_service.verify_callback_checksum(response_base64, x_verify):
        logger.warning("[PHONEPE-WEBHOOK] Checksum verification failed")
        raise HTTPException(status_code=400, detail="Invalid checksum")

    # Decode response
    try:
        response_data = json.loads(base64.b64decode(response_base64))
    except Exception:
        logger.error("[PHONEPE-WEBHOOK] Failed to decode response")
        raise HTTPException(status_code=400, detail="Invalid response data")

    merchant_txn_id = response_data.get("data", {}).get("merchantTransactionId")
    payment_state = response_data.get("code")
    phonepe_txn_id = response_data.get("data", {}).get("transactionId")

    logger.info(f"[PHONEPE-WEBHOOK] Callback: txn={merchant_txn_id}, code={payment_state}")

    if not merchant_txn_id:
        raise HTTPException(status_code=400, detail="Missing transaction ID")

    # Find payment by merchant transaction ID
    from sqlalchemy import select
    result = await db.execute(
        select(Payment).where(
            Payment.phonepe_merchant_transaction_id == merchant_txn_id
        )
    )
    payment = result.scalar_one_or_none()

    if not payment:
        logger.error(f"[PHONEPE-WEBHOOK] Payment not found for txn {merchant_txn_id}")
        raise HTTPException(status_code=404, detail="Payment not found")

    if payment.status == PaymentStatus.COMPLETED:
        logger.info(f"[PHONEPE-WEBHOOK] Payment already completed: {merchant_txn_id}")
        return {"status": "already_processed"}

    if payment_state == "PAYMENT_SUCCESS":
        # Update payment
        payment.status = PaymentStatus.COMPLETED
        payment.phonepe_transaction_id = phonepe_txn_id
        payment.transaction_id = phonepe_txn_id

        # Add credits atomically
        await credit_service.add_credits(
            db=db,
            user_id=payment.user_id,
            credit_type=payment.credit_type,
            seconds=payment.credit_seconds,
            source="purchase",
            payment_id=payment.id,
            description=f"Purchase: {payment.credit_seconds // 60}min {payment.credit_type}",
        )

        logger.info(
            f"[PHONEPE-WEBHOOK] Payment SUCCESS: {merchant_txn_id}, "
            f"added {payment.credit_seconds}s {payment.credit_type} to user {payment.user_id}"
        )
    else:
        payment.status = PaymentStatus.FAILED
        logger.info(f"[PHONEPE-WEBHOOK] Payment FAILED: {merchant_txn_id}, code={payment_state}")

    await db.flush()
    return {"status": "processed"}
