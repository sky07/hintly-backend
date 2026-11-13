"""Pricing tier and system config management service."""

import logging
from typing import Optional, List, Dict, Any

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from db.models import PricingTier, SystemConfig

logger = logging.getLogger(__name__)


# --- Pricing Tier CRUD ---

async def get_active_pricing_tiers(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get all active pricing tiers, ordered by credit type and minutes."""
    result = await db.execute(
        select(PricingTier)
        .where(PricingTier.is_active == True)
        .order_by(PricingTier.credit_type, PricingTier.minutes)
    )
    tiers = result.scalars().all()

    return [_tier_to_dict(t) for t in tiers]


async def get_all_pricing_tiers(db: AsyncSession) -> List[Dict[str, Any]]:
    """Get all pricing tiers (including inactive), for admin."""
    result = await db.execute(
        select(PricingTier).order_by(PricingTier.credit_type, PricingTier.minutes)
    )
    tiers = result.scalars().all()
    return [_tier_to_dict(t) for t in tiers]


async def get_pricing_tier_by_id(db: AsyncSession, tier_id: str) -> Optional[PricingTier]:
    """Get a single pricing tier by ID."""
    result = await db.execute(
        select(PricingTier).where(PricingTier.id == tier_id)
    )
    return result.scalar_one_or_none()


async def create_pricing_tier(db: AsyncSession, data: Dict[str, Any]) -> Dict[str, Any]:
    """Create a new pricing tier."""
    tier = PricingTier(
        credit_type=data["credit_type"],
        minutes=data["minutes"],
        base_price_inr=data["base_price_inr"],
        discount_percent=data.get("discount_percent", 0),
        final_price_inr=data["final_price_inr"],
        price_usd=data.get("price_usd"),
        is_active=data.get("is_active", True),
    )
    db.add(tier)
    await db.flush()
    return _tier_to_dict(tier)


async def update_pricing_tier(
    db: AsyncSession, tier_id: str, data: Dict[str, Any]
) -> Optional[Dict[str, Any]]:
    """Update an existing pricing tier."""
    result = await db.execute(
        select(PricingTier).where(PricingTier.id == tier_id)
    )
    tier = result.scalar_one_or_none()
    if not tier:
        return None

    for key, value in data.items():
        if hasattr(tier, key) and key != "id":
            setattr(tier, key, value)

    await db.flush()
    return _tier_to_dict(tier)


async def delete_pricing_tier(db: AsyncSession, tier_id: str) -> bool:
    """Delete a pricing tier."""
    result = await db.execute(
        select(PricingTier).where(PricingTier.id == tier_id)
    )
    tier = result.scalar_one_or_none()
    if not tier:
        return False

    await db.delete(tier)
    await db.flush()
    return True


def _tier_to_dict(tier: PricingTier) -> Dict[str, Any]:
    return {
        "id": tier.id,
        "credit_type": tier.credit_type,
        "minutes": tier.minutes,
        "base_price_inr": tier.base_price_inr,
        "discount_percent": tier.discount_percent,
        "final_price_inr": tier.final_price_inr,
        "price_usd": tier.price_usd,
        "is_active": tier.is_active,
    }


# --- System Config ---

async def get_system_config(db: AsyncSession) -> Dict[str, str]:
    """Get all system config as a dict."""
    result = await db.execute(select(SystemConfig))
    configs = result.scalars().all()
    return {c.key: c.value for c in configs}


async def get_config_value(db: AsyncSession, key: str) -> Optional[str]:
    """Get a single config value."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    )
    config = result.scalar_one_or_none()
    return config.value if config else None


async def set_config_value(
    db: AsyncSession, key: str, value: str, description: Optional[str] = None
) -> None:
    """Set a config value (upsert)."""
    result = await db.execute(
        select(SystemConfig).where(SystemConfig.key == key)
    )
    config = result.scalar_one_or_none()

    if config:
        config.value = value
        if description is not None:
            config.description = description
    else:
        config = SystemConfig(key=key, value=value, description=description)
        db.add(config)

    await db.flush()


async def update_system_config(db: AsyncSession, updates: Dict[str, str]) -> Dict[str, str]:
    """Update multiple config values at once."""
    for key, value in updates.items():
        await set_config_value(db, key, value)
    return await get_system_config(db)
