"""Support ticket service — create, list, reply, and manage tickets."""

import logging
import random
import string
from typing import Optional, List, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from db.models import SupportTicket, TicketMessage, User, TicketStatus, TicketCategory
from config import settings
from services.email_service import send_support_ticket_email

logger = logging.getLogger(__name__)


async def generate_ticket_number(db: AsyncSession) -> str:
    """Generate a unique ticket number like TKT-A1B2C3."""
    for _ in range(10):
        chars = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
        number = f"TKT-{chars}"
        result = await db.execute(
            select(SupportTicket).where(SupportTicket.ticket_number == number)
        )
        if result.scalar_one_or_none() is None:
            return number
    raise RuntimeError("Failed to generate unique ticket number")


async def create_ticket(
    db: AsyncSession,
    user_id: str,
    category: str,
    subject: str,
    message: str,
) -> Dict[str, Any]:
    """Create a new support ticket with its initial message."""
    ticket_number = await generate_ticket_number(db)

    # Look up the user for name/email
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one()

    ticket = SupportTicket(
        user_id=user_id,
        ticket_number=ticket_number,
        category=TicketCategory(category),
        subject=subject,
    )
    db.add(ticket)
    await db.flush()

    msg = TicketMessage(
        ticket_id=ticket.id,
        sender_id=user_id,
        is_admin_reply=False,
        content=message,
    )
    db.add(msg)
    await db.flush()

    # Send email notification to support
    if settings.support_email:
        await send_support_ticket_email(
            to_email=settings.support_email,
            ticket_number=ticket_number,
            subject=subject,
            message=message,
            user_name=user.name,
            is_new=True,
        )

    logger.info(f"[SUPPORT] Ticket {ticket_number} created by {user.email}")

    return _ticket_to_dict(ticket, messages=[msg])


async def get_user_tickets(db: AsyncSession, user_id: str) -> List[Dict[str, Any]]:
    """List all tickets for a user, ordered by updated_at desc."""
    result = await db.execute(
        select(SupportTicket)
        .where(SupportTicket.user_id == user_id)
        .order_by(SupportTicket.updated_at.desc())
    )
    tickets = result.scalars().all()
    return [_ticket_to_dict(t) for t in tickets]


async def get_ticket_detail(
    db: AsyncSession, ticket_id: str, user_id: Optional[str] = None
) -> Optional[Dict[str, Any]]:
    """Get ticket with messages. If user_id provided, verify ownership."""
    result = await db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.messages).selectinload(TicketMessage.sender))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return None
    if user_id and ticket.user_id != user_id:
        return None
    return _ticket_to_dict(ticket, messages=ticket.messages)


async def add_message(
    db: AsyncSession,
    ticket_id: str,
    sender_id: str,
    content: str,
    is_admin: bool = False,
) -> Optional[Dict[str, Any]]:
    """Add a message to a ticket thread."""
    result = await db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return None

    # Non-admin users can only message their own tickets
    if not is_admin and ticket.user_id != sender_id:
        return None

    msg = TicketMessage(
        ticket_id=ticket_id,
        sender_id=sender_id,
        is_admin_reply=is_admin,
        content=content,
    )
    db.add(msg)

    # Touch updated_at
    from datetime import datetime
    ticket.updated_at = datetime.utcnow()

    # If admin replies and ticket is open, move to in_progress
    if is_admin and ticket.status == TicketStatus.OPEN:
        ticket.status = TicketStatus.IN_PROGRESS

    await db.flush()

    # Send email notification
    sender_result = await db.execute(select(User).where(User.id == sender_id))
    sender = sender_result.scalar_one()

    if is_admin and ticket.user:
        # Notify the ticket owner
        await send_support_ticket_email(
            to_email=ticket.user.email,
            ticket_number=ticket.ticket_number,
            subject=ticket.subject,
            message=content,
            user_name=sender.name,
            is_new=False,
        )
    elif not is_admin and settings.support_email:
        # Notify support
        await send_support_ticket_email(
            to_email=settings.support_email,
            ticket_number=ticket.ticket_number,
            subject=ticket.subject,
            message=content,
            user_name=sender.name,
            is_new=False,
        )

    return _message_to_dict(msg, sender_name=sender.name)


async def update_ticket_status(
    db: AsyncSession, ticket_id: str, new_status: str
) -> Optional[Dict[str, Any]]:
    """Update ticket status (admin only)."""
    result = await db.execute(
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .where(SupportTicket.id == ticket_id)
    )
    ticket = result.scalar_one_or_none()
    if not ticket:
        return None

    ticket.status = TicketStatus(new_status)
    from datetime import datetime
    ticket.updated_at = datetime.utcnow()
    await db.flush()

    # Notify ticket owner of status change
    if ticket.user:
        status_label = new_status.replace("_", " ").title()
        await send_support_ticket_email(
            to_email=ticket.user.email,
            ticket_number=ticket.ticket_number,
            subject=ticket.subject,
            message=f"Your ticket status has been updated to: {status_label}",
            user_name="Support Team",
            is_new=False,
        )

    logger.info(f"[SUPPORT] Ticket {ticket.ticket_number} status -> {new_status}")
    return _ticket_to_dict(ticket)


async def get_all_tickets(
    db: AsyncSession,
    status_filter: Optional[str] = None,
    category_filter: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
) -> Dict[str, Any]:
    """Get all tickets (admin), with optional filters."""
    query = (
        select(SupportTicket)
        .options(selectinload(SupportTicket.user))
        .order_by(SupportTicket.updated_at.desc())
    )

    if status_filter:
        query = query.where(SupportTicket.status == TicketStatus(status_filter))
    if category_filter:
        query = query.where(SupportTicket.category == TicketCategory(category_filter))

    # Count
    count_query = select(func.count(SupportTicket.id))
    if status_filter:
        count_query = count_query.where(SupportTicket.status == TicketStatus(status_filter))
    if category_filter:
        count_query = count_query.where(SupportTicket.category == TicketCategory(category_filter))
    count_result = await db.execute(count_query)
    total = count_result.scalar() or 0

    query = query.limit(min(limit, 100)).offset(offset)
    result = await db.execute(query)
    tickets = result.scalars().all()

    return {
        "tickets": [_ticket_to_dict(t, include_user=True) for t in tickets],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


def _ticket_to_dict(
    ticket: SupportTicket,
    messages: Optional[List[TicketMessage]] = None,
    include_user: bool = False,
) -> Dict[str, Any]:
    """Serialize a ticket to dict."""
    data: Dict[str, Any] = {
        "id": ticket.id,
        "ticket_number": ticket.ticket_number,
        "category": ticket.category.value,
        "subject": ticket.subject,
        "status": ticket.status.value,
        "created_at": ticket.created_at.isoformat(),
        "updated_at": ticket.updated_at.isoformat(),
    }
    if include_user and ticket.user:
        data["user"] = {
            "id": ticket.user.id,
            "name": ticket.user.name,
            "email": ticket.user.email,
        }
    if messages is not None:
        data["messages"] = [_message_to_dict(m) for m in messages]
    return data


def _message_to_dict(msg: TicketMessage, sender_name: Optional[str] = None) -> Dict[str, Any]:
    """Serialize a ticket message to dict."""
    name = sender_name
    if name is None and hasattr(msg, "sender") and msg.sender:
        name = msg.sender.name
    return {
        "id": msg.id,
        "sender_id": msg.sender_id,
        "sender_name": name or "Unknown",
        "is_admin_reply": msg.is_admin_reply,
        "content": msg.content,
        "created_at": msg.created_at.isoformat(),
    }
