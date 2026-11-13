"""Session data models."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Literal


@dataclass
class SessionContext:
    """Context provided by the user for the interview session."""

    job_description: str = ""
    resume: str = ""
    work_experience: str = ""


@dataclass
class TranscriptEntry:
    """A single entry in the conversation transcript."""

    id: str
    timestamp: datetime
    speaker: Literal["user", "interviewer"]
    text: str
    is_final: bool = False


@dataclass
class Session:
    """An active interview session."""

    id: str
    created_at: datetime
    context: SessionContext
    verbosity: str = "moderate"
    transcript: list[TranscriptEntry] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    last_summary_at: datetime = field(default_factory=datetime.now)
