"""Session management API routes."""

import json
from datetime import datetime, timedelta
from typing import Optional
import logging

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import PlainTextResponse, JSONResponse
from pydantic import BaseModel
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from db.database import get_db
from db.models import User, InterviewSession
from routers.auth import require_auth

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/sessions", tags=["sessions"])


class SessionSummary(BaseModel):
    """Session summary for list view."""
    id: str
    job_description_snippet: Optional[str]
    duration_seconds: int
    provider_used: Optional[str]
    created_at: str
    ended_at: Optional[str]


class SessionDetail(BaseModel):
    """Full session details."""
    id: str
    job_description: Optional[str]
    resume: Optional[str]
    work_experience: Optional[str]
    transcript: list
    suggestions: list
    duration_seconds: int
    provider_used: Optional[str]
    created_at: str
    ended_at: Optional[str]


@router.get("/stats")
async def get_session_stats(
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get aggregated session stats (total sessions, practice time, this week)."""
    week_ago = datetime.utcnow() - timedelta(days=7)

    # Single query: total count + total duration
    total_result = await db.execute(
        select(
            func.count(InterviewSession.id),
            func.coalesce(func.sum(InterviewSession.duration_seconds), 0),
        ).where(InterviewSession.user_id == user.id)
    )
    total_row = total_result.one()

    # Single query: this week count + duration
    week_result = await db.execute(
        select(
            func.count(InterviewSession.id),
            func.coalesce(func.sum(InterviewSession.duration_seconds), 0),
        ).where(
            InterviewSession.user_id == user.id,
            InterviewSession.created_at >= week_ago,
        )
    )
    week_row = week_result.one()

    return {
        "totalSessions": total_row[0],
        "totalMinutes": total_row[1] // 60,
        "thisWeekSessions": week_row[0],
        "thisWeekMinutes": week_row[1] // 60,
    }


@router.get("", response_model=list[SessionSummary])
async def list_sessions(
    limit: int = 50,
    offset: int = 0,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """List user's interview sessions."""
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.user_id == user.id)
        .order_by(desc(InterviewSession.created_at))
        .limit(limit)
        .offset(offset)
    )
    sessions = result.scalars().all()

    return [
        SessionSummary(
            id=s.id,
            job_description_snippet=s.job_description[:100] if s.job_description else None,
            duration_seconds=s.duration_seconds,
            provider_used=s.provider_used,
            created_at=s.created_at.isoformat(),
            ended_at=s.ended_at.isoformat() if s.ended_at else None,
        )
        for s in sessions
    ]


@router.get("/{session_id}", response_model=SessionDetail)
async def get_session(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Get session details."""
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .where(InterviewSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    return SessionDetail(
        id=session.id,
        job_description=session.job_description,
        resume=session.resume,
        work_experience=session.work_experience,
        transcript=session.transcript or [],
        suggestions=session.suggestions or [],
        duration_seconds=session.duration_seconds,
        provider_used=session.provider_used,
        created_at=session.created_at.isoformat(),
        ended_at=session.ended_at.isoformat() if session.ended_at else None,
    )


@router.delete("/{session_id}")
async def delete_session(
    session_id: str,
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Delete a session."""
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .where(InterviewSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    await db.delete(session)
    logger.info(f"Session {session_id} deleted by user {user.id}")

    return {"message": "Session deleted"}


@router.get("/{session_id}/transcript")
async def download_transcript(
    session_id: str,
    format: str = "text",
    user: User = Depends(require_auth),
    db: AsyncSession = Depends(get_db),
):
    """Download session transcript as text or JSON."""
    result = await db.execute(
        select(InterviewSession)
        .where(InterviewSession.id == session_id)
        .where(InterviewSession.user_id == user.id)
    )
    session = result.scalar_one_or_none()

    if not session:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Session not found",
        )

    if format == "json":
        return JSONResponse(
            content={
                "session_id": session.id,
                "created_at": session.created_at.isoformat(),
                "duration_seconds": session.duration_seconds,
                "job_description": session.job_description,
                "transcript": session.transcript or [],
                "suggestions": session.suggestions or [],
            },
            headers={
                "Content-Disposition": f'attachment; filename="transcript-{session_id}.json"'
            },
        )

    # Plain text format
    lines = [
        f"Interview Session Transcript",
        f"Date: {session.created_at.strftime('%Y-%m-%d %H:%M')}",
        f"Duration: {session.duration_seconds // 60} minutes",
        "",
        "=" * 50,
        "",
    ]

    if session.job_description:
        lines.extend([
            "JOB DESCRIPTION:",
            session.job_description,
            "",
            "=" * 50,
            "",
        ])

    lines.append("TRANSCRIPT:\n")

    for entry in (session.transcript or []):
        speaker = entry.get("speaker", "Unknown")
        text = entry.get("text", "")
        lines.append(f"[{speaker}] {text}\n")

    lines.extend([
        "",
        "=" * 50,
        "",
        "AI SUGGESTIONS:\n",
    ])

    for i, suggestion in enumerate(session.suggestions or [], 1):
        lines.append(f"Suggestion {i}:")
        if isinstance(suggestion, dict):
            response = suggestion.get("response", "")
            key_points = suggestion.get("keyPoints", [])
            lines.append(f"  Response: {response}")
            if key_points:
                lines.append(f"  Key Points: {', '.join(key_points)}")
        else:
            lines.append(f"  {suggestion}")
        lines.append("")

    content = "\n".join(lines)

    return PlainTextResponse(
        content=content,
        headers={
            "Content-Disposition": f'attachment; filename="transcript-{session_id}.txt"'
        },
    )
