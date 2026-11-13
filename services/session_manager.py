"""Session manager for tracking active sessions in memory."""

import uuid
from datetime import datetime
from typing import Optional

from models.session import Session, SessionContext, TranscriptEntry


class SessionManager:
    """Manages active interview sessions in memory."""

    def __init__(self):
        self._sessions: dict[str, Session] = {}

    def create_session(
        self,
        job_description: str = "",
        resume: str = "",
        work_experience: str = "",
        verbosity: str = "moderate",
    ) -> Session:
        """Create a new session with a unique ID."""
        session_id = str(uuid.uuid4())
        context = SessionContext(
            job_description=job_description,
            resume=resume,
            work_experience=work_experience,
        )
        session = Session(
            id=session_id,
            created_at=datetime.now(),
            context=context,
            verbosity=verbosity,
        )
        self._sessions[session_id] = session
        return session

    def get_session(self, session_id: str) -> Optional[Session]:
        """Retrieve a session by ID."""
        return self._sessions.get(session_id)

    def update_context(
        self,
        session_id: str,
        job_description: Optional[str] = None,
        resume: Optional[str] = None,
        work_experience: Optional[str] = None,
    ) -> bool:
        """Update the context for a session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        if job_description is not None:
            session.context.job_description = job_description
        if resume is not None:
            session.context.resume = resume
        if work_experience is not None:
            session.context.work_experience = work_experience

        return True

    def set_verbosity(self, session_id: str, verbosity: str) -> bool:
        """Update the verbosity setting for a session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        session.verbosity = verbosity
        return True

    def add_transcript_entry(
        self,
        session_id: str,
        entry_id: str,
        speaker: str,
        text: str,
        is_final: bool = False,
    ) -> bool:
        """Add a transcript entry to the session."""
        session = self._sessions.get(session_id)
        if not session:
            return False

        # Check if we're updating an existing entry
        for entry in session.transcript:
            if entry.id == entry_id:
                entry.text = text
                entry.is_final = is_final
                return True

        # Create new entry
        entry = TranscriptEntry(
            id=entry_id,
            timestamp=datetime.now(),
            speaker=speaker,  # type: ignore
            text=text,
            is_final=is_final,
        )
        session.transcript.append(entry)
        return True

    def clear_session(self, session_id: str) -> bool:
        """Clear and remove a session on disconnect."""
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def get_all_sessions(self) -> list[Session]:
        """Get all active sessions (for debugging)."""
        return list(self._sessions.values())


# Global session manager instance
session_manager = SessionManager()
