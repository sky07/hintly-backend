"""Database models using SQLAlchemy 2.0 ORM."""

import uuid
from datetime import datetime
from typing import Optional, List
from sqlalchemy import String, Text, Integer, Float, Boolean, DateTime, ForeignKey, JSON, Enum as SQLEnum
from sqlalchemy.orm import Mapped, mapped_column, relationship
from sqlalchemy.dialects.sqlite import JSON as SQLiteJSON
import enum

from .database import Base


def generate_uuid() -> str:
    """Generate a new UUID string."""
    return str(uuid.uuid4())


class SubscriptionTier(str, enum.Enum):
    """User subscription tier (legacy — kept for backwards compatibility)."""
    FREE = "free"
    PRO = "pro"


class PaymentStatus(str, enum.Enum):
    """Payment status."""
    PENDING = "pending"
    COMPLETED = "completed"
    FAILED = "failed"


class PaymentMethod(str, enum.Enum):
    """Payment method."""
    UPI = "upi"
    CARD = "card"
    MOCK = "mock"
    PHONEPE = "phonepe"


class CreditType(str, enum.Enum):
    """Credit type — determines pricing model."""
    BYO_KEY = "byo_key"
    PLATFORM_AI = "platform_ai"


class CreditSourceType(str, enum.Enum):
    """Source of credit transaction."""
    PURCHASE = "purchase"
    FREE_TRIAL = "free_trial"
    ADMIN_GRANT = "admin_grant"
    REFUND = "refund"


class TicketStatus(str, enum.Enum):
    """Support ticket status."""
    OPEN = "open"
    IN_PROGRESS = "in_progress"
    RESOLVED = "resolved"
    CLOSED = "closed"


class TicketCategory(str, enum.Enum):
    """Support ticket category."""
    GENERAL_INQUIRY = "general_inquiry"
    FEEDBACK = "feedback"
    TECHNICAL_ISSUE = "technical_issue"
    BILLING = "billing"
    OTHERS = "others"


class LLMProvider(str, enum.Enum):
    """LLM provider types."""
    OPENAI = "openai"
    GEMINI = "gemini"
    GROQ = "groq"
    ANTHROPIC = "anthropic"


class InterviewRound(str, enum.Enum):
    """Interview round types."""
    BEHAVIORAL = "behavioral"
    TECHNICAL = "technical"
    SYSTEM_DESIGN = "system_design"
    SCREENING = "screening"
    CULTURE_FIT = "culture_fit"


class User(Base):
    """User account model."""
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False, index=True)
    password_hash: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # nullable for OAuth
    google_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True, index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    email_verified: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    email_verification_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email_verification_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    password_reset_token: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    password_reset_expires: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    subscription_tier: Mapped[SubscriptionTier] = mapped_column(
        SQLEnum(SubscriptionTier),
        default=SubscriptionTier.FREE,
        nullable=False
    )
    is_admin: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    free_trial_granted: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relationships
    sessions: Mapped[List["InterviewSession"]] = relationship(
        "InterviewSession",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    api_keys: Mapped[List["UserAPIKey"]] = relationship(
        "UserAPIKey",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    usage_records: Mapped[List["UsageRecord"]] = relationship(
        "UsageRecord",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    payments: Mapped[List["Payment"]] = relationship(
        "Payment",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    credit_balance: Mapped[Optional["CreditBalance"]] = relationship(
        "CreditBalance",
        back_populates="user",
        uselist=False,
        cascade="all, delete-orphan"
    )
    credit_transactions: Mapped[List["CreditTransaction"]] = relationship(
        "CreditTransaction",
        back_populates="user",
        cascade="all, delete-orphan"
    )
    support_tickets: Mapped[List["SupportTicket"]] = relationship(
        "SupportTicket",
        back_populates="user",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<User(id={self.id}, email={self.email})>"


class InterviewSession(Base):
    """Interview session model."""
    __tablename__ = "interview_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    job_description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    resume: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    work_experience: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    company_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    round_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    transcript: Mapped[Optional[dict]] = mapped_column(SQLiteJSON, nullable=True, default=list)
    suggestions: Mapped[Optional[dict]] = mapped_column(SQLiteJSON, nullable=True, default=list)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    provider_used: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    credit_type_used: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    seconds_charged: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="sessions")
    usage_records: Mapped[List["UsageRecord"]] = relationship(
        "UsageRecord",
        back_populates="session",
        cascade="all, delete-orphan"
    )
    approved_answers: Mapped[List["ApprovedAnswer"]] = relationship(
        "ApprovedAnswer",
        back_populates="session",
        cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"<InterviewSession(id={self.id}, user_id={self.user_id})>"


class UserAPIKey(Base):
    """User's stored API keys for LLM providers."""
    __tablename__ = "user_api_keys"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    provider: Mapped[LLMProvider] = mapped_column(SQLEnum(LLMProvider), nullable=False)
    encrypted_key: Mapped[str] = mapped_column(Text, nullable=False)  # Fernet encrypted
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        default=datetime.utcnow,
        onupdate=datetime.utcnow,
        nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="api_keys")

    # Unique constraint: one key per provider per user
    __table_args__ = (
        {"sqlite_autoincrement": True},
    )

    def __repr__(self) -> str:
        return f"<UserAPIKey(id={self.id}, provider={self.provider})>"


class UsageRecord(Base):
    """Usage tracking for billing."""
    __tablename__ = "usage_records"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id"),
        nullable=True,
        index=True
    )
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    charged_amount: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    recorded_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="usage_records")
    session: Mapped[Optional["InterviewSession"]] = relationship(
        "InterviewSession",
        back_populates="usage_records"
    )

    def __repr__(self) -> str:
        return f"<UsageRecord(id={self.id}, user_id={self.user_id})>"


class Payment(Base):
    """Payment records."""
    __tablename__ = "payments"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    amount: Mapped[float] = mapped_column(Float, nullable=False)
    currency: Mapped[str] = mapped_column(String(10), default="INR", nullable=False)
    payment_method: Mapped[PaymentMethod] = mapped_column(SQLEnum(PaymentMethod), nullable=False)
    status: Mapped[PaymentStatus] = mapped_column(
        SQLEnum(PaymentStatus),
        default=PaymentStatus.PENDING,
        nullable=False
    )
    transaction_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True, unique=True)
    credit_type: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    credit_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    pricing_tier_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("pricing_tiers.id"), nullable=True
    )
    phonepe_merchant_transaction_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    phonepe_transaction_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="payments")
    pricing_tier: Mapped[Optional["PricingTier"]] = relationship("PricingTier")

    def __repr__(self) -> str:
        return f"<Payment(id={self.id}, amount={self.amount}, status={self.status})>"


class CreditBalance(Base):
    """Per-user credit balance. Single row per user, supports row-level locking."""
    __tablename__ = "credit_balances"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, unique=True, index=True
    )
    byo_key_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    platform_ai_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    free_trial_seconds: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    free_trial_expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="credit_balance")

    def __repr__(self) -> str:
        return f"<CreditBalance(user_id={self.user_id}, byo={self.byo_key_seconds}s, platform={self.platform_ai_seconds}s, trial={self.free_trial_seconds}s)>"


class CreditTransaction(Base):
    """Append-only audit log for all credit changes."""
    __tablename__ = "credit_transactions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    credit_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CreditType value
    source_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CreditSourceType value
    seconds_amount: Mapped[int] = mapped_column(Integer, nullable=False)  # positive=add, negative=deduct
    balance_after: Mapped[int] = mapped_column(Integer, nullable=False)
    payment_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("payments.id"), nullable=True
    )
    session_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("interview_sessions.id"), nullable=True
    )
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="credit_transactions")
    payment: Mapped[Optional["Payment"]] = relationship("Payment")
    session: Mapped[Optional["InterviewSession"]] = relationship("InterviewSession")

    def __repr__(self) -> str:
        return f"<CreditTransaction(id={self.id}, type={self.credit_type}, amount={self.seconds_amount}s)>"


class PricingTier(Base):
    """Admin-configurable pricing tiers for credit packages."""
    __tablename__ = "pricing_tiers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    credit_type: Mapped[str] = mapped_column(String(20), nullable=False)  # CreditType value
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    base_price_inr: Mapped[float] = mapped_column(Float, nullable=False)
    discount_percent: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    final_price_inr: Mapped[float] = mapped_column(Float, nullable=False)
    price_usd: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<PricingTier(id={self.id}, type={self.credit_type}, {self.minutes}min @ ₹{self.final_price_inr})>"


class SystemConfig(Base):
    """Key-value store for system-wide configuration."""
    __tablename__ = "system_config"

    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, nullable=False)
    description: Mapped[Optional[str]] = mapped_column(String(500), nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    def __repr__(self) -> str:
        return f"<SystemConfig(key={self.key}, value={self.value})>"


class ApprovedAnswer(Base):
    """Pre-prepared approved answers for interview questions."""
    __tablename__ = "approved_answers"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    session_id: Mapped[Optional[str]] = mapped_column(
        String(36),
        ForeignKey("interview_sessions.id"),
        nullable=True,
        index=True
    )
    question_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)  # MongoDB question ID
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    company_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    role_type: Mapped[Optional[str]] = mapped_column(String(100), nullable=True)
    round_type: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    answer_data: Mapped[Optional[dict]] = mapped_column(SQLiteJSON, nullable=True)
    is_approved: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    user: Mapped["User"] = relationship("User")
    session: Mapped[Optional["InterviewSession"]] = relationship(
        "InterviewSession",
        back_populates="approved_answers"
    )

    def __repr__(self) -> str:
        return f"<ApprovedAnswer(id={self.id}, question={self.question_text[:50]})>"


class SupportTicket(Base):
    """Support ticket model."""
    __tablename__ = "support_tickets"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    ticket_number: Mapped[str] = mapped_column(String(10), unique=True, nullable=False, index=True)
    user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False, index=True)
    category: Mapped[TicketCategory] = mapped_column(SQLEnum(TicketCategory), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[TicketStatus] = mapped_column(
        SQLEnum(TicketStatus), default=TicketStatus.OPEN, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False
    )

    # Relationships
    user: Mapped["User"] = relationship("User", back_populates="support_tickets")
    messages: Mapped[List["TicketMessage"]] = relationship(
        "TicketMessage", back_populates="ticket", cascade="all, delete-orphan",
        order_by="TicketMessage.created_at"
    )

    def __repr__(self) -> str:
        return f"<SupportTicket(id={self.id}, number={self.ticket_number}, status={self.status})>"


class TicketMessage(Base):
    """Message within a support ticket thread."""
    __tablename__ = "ticket_messages"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=generate_uuid)
    ticket_id: Mapped[str] = mapped_column(String(36), ForeignKey("support_tickets.id"), nullable=False, index=True)
    sender_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id"), nullable=False)
    is_admin_reply: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow, nullable=False)

    # Relationships
    ticket: Mapped["SupportTicket"] = relationship("SupportTicket", back_populates="messages")
    sender: Mapped["User"] = relationship("User")

    def __repr__(self) -> str:
        return f"<TicketMessage(id={self.id}, ticket_id={self.ticket_id})>"
