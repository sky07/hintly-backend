"""WebSocket message schemas."""

from typing import Literal, Optional, Union

from pydantic import BaseModel


# === Client -> Server Messages ===


class SessionContextPayload(BaseModel):
    """Context payload for session.start message."""

    jobDescription: str
    resume: str
    workExperience: str


class SessionStartMessage(BaseModel):
    """Message to start a new session."""

    type: Literal["session.start"]
    context: SessionContextPayload
    verbosity: Literal["concise", "moderate", "detailed"]


class SessionEndMessage(BaseModel):
    """Message to end the current session."""

    type: Literal["session.end"]


class VerbosityChangeMessage(BaseModel):
    """Message to change verbosity setting."""

    type: Literal["verbosity.change"]
    verbosity: Literal["concise", "moderate", "detailed"]


class PingMessage(BaseModel):
    """Ping message for latency measurement."""

    type: Literal["ping"]
    timestamp: int


# Union of all client message types
ClientMessage = Union[
    SessionStartMessage,
    SessionEndMessage,
    VerbosityChangeMessage,
    PingMessage,
]


# === Server -> Client Messages ===


class SessionReadyMessage(BaseModel):
    """Message indicating session is ready."""

    type: Literal["session.ready"] = "session.ready"
    sttMode: str = "chunked"  # "chunked" (Whisper) or "streaming" (Deepgram)


class TranscriptDeltaMessage(BaseModel):
    """Message containing transcription update."""

    type: Literal["transcript.delta"] = "transcript.delta"
    id: str
    speaker: Literal["user", "interviewer"]
    text: str
    isFinal: bool
    isNewTurn: bool = False  # True if this starts a new turn (after silence)


class SuggestionMessage(BaseModel):
    """Message containing AI suggestion."""

    type: Literal["suggestion"] = "suggestion"
    id: str
    response: str
    keyPoints: list[str]
    followUp: str
    intent: Optional[str] = None       # Classified intent (new_question, follow_up, etc.)
    question: Optional[str] = None     # The interviewer question this responds to


class ConnectionStatusMessage(BaseModel):
    """Message indicating connection status."""

    type: Literal["connection.status"] = "connection.status"
    status: Literal["connected", "reconnecting"]
    latency: Optional[int] = None


class ErrorMessage(BaseModel):
    """Error message."""

    type: Literal["error"] = "error"
    code: str
    message: str
    recoverable: bool


class PongMessage(BaseModel):
    """Pong response to ping."""

    type: Literal["pong"] = "pong"
    timestamp: int
    serverTime: int


# Union of all server message types
ServerMessage = Union[
    SessionReadyMessage,
    TranscriptDeltaMessage,
    SuggestionMessage,
    ConnectionStatusMessage,
    ErrorMessage,
    PongMessage,
]
