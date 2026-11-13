"""PhonePe payment gateway integration service."""

import base64
import hashlib
import json
import logging
from typing import Optional, Dict, Any

import httpx

from config import settings

logger = logging.getLogger(__name__)


def _generate_checksum(payload_base64: str, endpoint: str) -> str:
    """Generate X-VERIFY checksum for PhonePe API.

    Checksum = SHA256(base64_payload + endpoint + salt_key) + ### + salt_index
    """
    data_to_hash = payload_base64 + endpoint + settings.phonepe_salt_key
    sha256_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
    return f"{sha256_hash}###{settings.phonepe_salt_index}"


async def create_payment_order(
    merchant_txn_id: str,
    amount_paise: int,
    user_id: str,
    redirect_url: str,
    callback_url: str,
) -> Optional[str]:
    """Create a PhonePe payment order and return the redirect URL.

    Args:
        merchant_txn_id: Unique merchant transaction ID
        amount_paise: Amount in paise (1 INR = 100 paise)
        user_id: User ID for PhonePe merchant user ID
        redirect_url: URL to redirect after payment
        callback_url: Server-to-server callback URL

    Returns:
        PhonePe payment page redirect URL, or None on failure
    """
    payload = {
        "merchantId": settings.phonepe_merchant_id,
        "merchantTransactionId": merchant_txn_id,
        "merchantUserId": user_id[:36],  # PhonePe limit
        "amount": amount_paise,
        "redirectUrl": redirect_url,
        "redirectMode": "REDIRECT",
        "callbackUrl": callback_url,
        "paymentInstrument": {
            "type": "PAY_PAGE",
        },
    }

    payload_json = json.dumps(payload)
    payload_base64 = base64.b64encode(payload_json.encode()).decode()

    endpoint = "/pg/v1/pay"
    checksum = _generate_checksum(payload_base64, endpoint)

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.post(
                f"{settings.phonepe_base_url}{endpoint}",
                json={"request": payload_base64},
                headers={
                    "Content-Type": "application/json",
                    "X-VERIFY": checksum,
                },
            )

            response_data = response.json()
            logger.info(f"[PHONEPE] Payment order response: {response.status_code}")

            if response.status_code == 200 and response_data.get("success"):
                redirect = (
                    response_data.get("data", {})
                    .get("instrumentResponse", {})
                    .get("redirectInfo", {})
                    .get("url")
                )
                if redirect:
                    logger.info(f"[PHONEPE] Payment redirect URL obtained for txn {merchant_txn_id}")
                    return redirect

            logger.error(f"[PHONEPE] Payment order failed: {response_data}")
            return None

    except Exception as e:
        logger.error(f"[PHONEPE] Error creating payment order: {e}")
        return None


def verify_callback_checksum(response_base64: str, x_verify_header: str) -> bool:
    """Verify the X-VERIFY checksum from PhonePe callback.

    Args:
        response_base64: Base64 encoded response body
        x_verify_header: X-VERIFY header value from callback

    Returns:
        True if checksum is valid
    """
    data_to_hash = response_base64 + "/pg/v1/status" + settings.phonepe_salt_key
    expected_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
    expected_checksum = f"{expected_hash}###{settings.phonepe_salt_index}"

    is_valid = expected_checksum == x_verify_header
    if not is_valid:
        logger.warning("[PHONEPE] Callback checksum verification failed")
    return is_valid


async def check_payment_status(merchant_txn_id: str) -> Optional[Dict[str, Any]]:
    """Check payment status from PhonePe.

    Args:
        merchant_txn_id: Merchant transaction ID

    Returns:
        Payment status response dict, or None on failure
    """
    endpoint = f"/pg/v1/status/{settings.phonepe_merchant_id}/{merchant_txn_id}"
    data_to_hash = endpoint + settings.phonepe_salt_key
    sha256_hash = hashlib.sha256(data_to_hash.encode()).hexdigest()
    checksum = f"{sha256_hash}###{settings.phonepe_salt_index}"

    try:
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{settings.phonepe_base_url}{endpoint}",
                headers={
                    "Content-Type": "application/json",
                    "X-VERIFY": checksum,
                    "X-MERCHANT-ID": settings.phonepe_merchant_id,
                },
            )

            response_data = response.json()
            logger.info(f"[PHONEPE] Status check for {merchant_txn_id}: {response_data.get('code')}")
            return response_data

    except Exception as e:
        logger.error(f"[PHONEPE] Error checking payment status: {e}")
        return None
