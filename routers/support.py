"""Support ticket API routes — user and admin endpoints."""

import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User
from routers.auth import require_auth
from routers.admin import require_admin
from services import support_service

logger = logging.getLogger(__name__)

router = APIRouter(tags=["support"])


# --- Request models ---

class CreateTicketRequest(BaseModel):
    category: str
    subject: str
    message: str


class AddMessageRequest(BaseModel):
    content: str


class UpdateStatusRequest(BaseModel):
    status: str


# ===== User endpoints =====

@router.post("/api/support/tickets")
async def create_ticket(
    data: CreateTicketRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Create a new support ticket."""
    try:
        ticket = await support_service.create_ticket(
            db=db,
            user_id=user.id,
            category=data.category,
            subject=data.subject,
            message=data.message,
        )
        return ticket
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/api/support/tickets")
async def list_user_tickets(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List the current user's tickets."""
    tickets = await support_service.get_user_tickets(db, user.id)
    return {"tickets": tickets}


@router.get("/api/support/tickets/{ticket_id}")
async def get_ticket(
    ticket_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get ticket detail with messages (user must own the ticket)."""
    ticket = await support_service.get_ticket_detail(db, ticket_id, user_id=user.id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.post("/api/support/tickets/{ticket_id}/messages")
async def add_user_message(
    ticket_id: str,
    data: AddMessageRequest,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Add a message to a ticket thread (user)."""
    msg = await support_service.add_message(
        db=db, ticket_id=ticket_id, sender_id=user.id, content=data.content, is_admin=False
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return msg


# ===== Admin endpoints =====

@router.get("/api/admin/support/tickets")
async def admin_list_tickets(
    status_filter: Optional[str] = None,
    category: Optional[str] = None,
    limit: int = 50,
    offset: int = 0,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """List all tickets (admin, filterable)."""
    return await support_service.get_all_tickets(
        db, status_filter=status_filter, category_filter=category, limit=limit, offset=offset
    )


@router.get("/api/admin/support/tickets/{ticket_id}")
async def admin_get_ticket(
    ticket_id: str,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Get any ticket detail (admin)."""
    ticket = await support_service.get_ticket_detail(db, ticket_id)
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket


@router.post("/api/admin/support/tickets/{ticket_id}/messages")
async def admin_add_message(
    ticket_id: str,
    data: AddMessageRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Add an admin reply to a ticket."""
    msg = await support_service.add_message(
        db=db, ticket_id=ticket_id, sender_id=admin.id, content=data.content, is_admin=True
    )
    if not msg:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return msg


@router.patch("/api/admin/support/tickets/{ticket_id}/status")
async def admin_update_status(
    ticket_id: str,
    data: UpdateStatusRequest,
    admin: User = Depends(require_admin),
    db: AsyncSession = Depends(get_db),
):
    """Change ticket status (admin)."""
    try:
        ticket = await support_service.update_ticket_status(db, ticket_id, data.status)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not ticket:
        raise HTTPException(status_code=404, detail="Ticket not found")
    return ticket
