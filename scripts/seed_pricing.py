"""Seed pricing tiers and default system config values."""

import asyncio
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import select
from db.database import AsyncSessionLocal, init_db
from db.models import PricingTier, SystemConfig, CreditType


# BYO Key: ₹450/60min base rate
# Platform+AI: ₹900/60min base rate
PRICING_TIERS = [
    # BYO Key tiers
    {"credit_type": CreditType.BYO_KEY.value, "minutes": 60, "base_price_inr": 450, "discount_percent": 0},
    {"credit_type": CreditType.BYO_KEY.value, "minutes": 120, "base_price_inr": 900, "discount_percent": 10},
    {"credit_type": CreditType.BYO_KEY.value, "minutes": 300, "base_price_inr": 2250, "discount_percent": 20},
    {"credit_type": CreditType.BYO_KEY.value, "minutes": 600, "base_price_inr": 4500, "discount_percent": 30},
    # Platform+AI tiers
    {"credit_type": CreditType.PLATFORM_AI.value, "minutes": 60, "base_price_inr": 900, "discount_percent": 0},
    {"credit_type": CreditType.PLATFORM_AI.value, "minutes": 120, "base_price_inr": 1800, "discount_percent": 10},
    {"credit_type": CreditType.PLATFORM_AI.value, "minutes": 300, "base_price_inr": 4500, "discount_percent": 20},
    {"credit_type": CreditType.PLATFORM_AI.value, "minutes": 600, "base_price_inr": 9000, "discount_percent": 30},
]

DEFAULT_SYSTEM_CONFIG = [
    {"key": "usd_to_inr_rate", "value": "83.0", "description": "USD to INR exchange rate for display"},
    {"key": "free_trial_minutes", "value": "10", "description": "Free trial credit minutes for new users"},
    {"key": "free_trial_expiry_days", "value": "7", "description": "Days until free trial credits expire"},
    {"key": "grace_period_seconds", "value": "60", "description": "Grace period after credits exhausted"},
    {"key": "deduction_interval_seconds", "value": "30", "description": "Credit deduction interval during session"},
]


async def seed():
    await init_db()

    async with AsyncSessionLocal() as db:
        # Seed pricing tiers (skip if already exist)
        existing = await db.execute(select(PricingTier))
        if existing.scalars().first():
            print("Pricing tiers already exist, skipping...")
        else:
            for tier_data in PRICING_TIERS:
                final_price = tier_data["base_price_inr"] * (1 - tier_data["discount_percent"] / 100)
                tier = PricingTier(
                    credit_type=tier_data["credit_type"],
                    minutes=tier_data["minutes"],
                    base_price_inr=tier_data["base_price_inr"],
                    discount_percent=tier_data["discount_percent"],
                    final_price_inr=final_price,
                    price_usd=round(final_price / 83.0, 2),
                    is_active=True,
                )
                db.add(tier)
            print(f"Seeded {len(PRICING_TIERS)} pricing tiers")

        # Seed system config (upsert)
        for config_data in DEFAULT_SYSTEM_CONFIG:
            existing_config = await db.execute(
                select(SystemConfig).where(SystemConfig.key == config_data["key"])
            )
            if existing_config.scalar_one_or_none():
                print(f"  Config '{config_data['key']}' already exists, skipping...")
            else:
                config = SystemConfig(**config_data)
                db.add(config)
                print(f"  Seeded config: {config_data['key']} = {config_data['value']}")

        await db.commit()
        print("Seeding complete!")


if __name__ == "__main__":
    asyncio.run(seed())
