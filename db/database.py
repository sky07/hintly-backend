"""Async database connection and session management using SQLAlchemy 2.0."""

from typing import AsyncGenerator
from sqlalchemy import NullPool
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine, async_sessionmaker
from sqlalchemy.orm import DeclarativeBase

from config import settings


class Base(DeclarativeBase):
    """Base class for SQLAlchemy models."""
    pass


# Create async engine
engine = create_async_engine(
    settings.database_url,
    echo=settings.database_echo,
    future=True,
    poolclass=NullPool, # Highly recommended for Supabase Pooler
    # This line prevents the error by disabling the problematic cache
    pool_pre_ping=True,
    #connect_args={"statement_cache_size": 0} if "sqlite" in settings.database_url else {}
    connect_args={"statement_cache_size": 0}
)

# Create async session factory
AsyncSessionLocal = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def init_db() -> None:
    """Initialize the database by creating all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        # Migrations: ADD COLUMN IF NOT EXISTS (safe for PostgreSQL transactional DDL)
        migrations = [
            "ALTER TABLE approved_answers ADD COLUMN IF NOT EXISTS role_type VARCHAR(100)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verified BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_token VARCHAR(255)",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS email_verification_expires TIMESTAMP",
            # Credit system migrations
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS is_admin BOOLEAN DEFAULT FALSE",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS free_trial_granted BOOLEAN DEFAULT FALSE",
            "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS credit_type_used VARCHAR(20)",
            "ALTER TABLE interview_sessions ADD COLUMN IF NOT EXISTS seconds_charged INTEGER DEFAULT 0",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS credit_type VARCHAR(20)",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS credit_seconds INTEGER",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS pricing_tier_id VARCHAR(36)",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS phonepe_merchant_transaction_id VARCHAR(255)",
            "ALTER TABLE payments ADD COLUMN IF NOT EXISTS phonepe_transaction_id VARCHAR(255)",
        ]
        for sql in migrations:
            await conn.execute(__import__('sqlalchemy').text(sql))


async def close_db() -> None:
    """Close database connections."""
    await engine.dispose()


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency that provides a database session."""
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()
