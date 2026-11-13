"""Conversation history tracking for interview sessions.

Stores recent turns (interviewer questions + candidate answers) in-memory
for injection into LLM context. Enables follow-up coherence, challenge
detection, and conversation-aware responses.

Feature flag: settings.enable_conversation_memory
"""

import time
from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class ConversationTurn:
    """A single turn in the conversation."""
    role: str          # "interviewer" or "candidate"
    text: str          # Verbatim text
    intent: str        # Classified intent (e.g., "new_question", "follow_up") or "unknown"
    timestamp: float   # time.time() when recorded


@dataclass
class ConversationHistory:
    """In-memory conversation history for a single interview session.

    Maintains a rolling buffer of recent turns (Tier 1) for injection
    into LLM context. Designed to be attached to WebSocket ConnectionState.
    """
    max_active_turns: int = 5
    turns: List[ConversationTurn] = field(default_factory=list)

    def add_turn(self, role: str, text: str, intent: str = "unknown") -> None:
        """Record a conversation turn."""
        self.turns.append(ConversationTurn(
            role=role,
            text=text.strip(),
            intent=intent,
            timestamp=time.time(),
        ))

    def get_recent_turns(self, n: Optional[int] = None) -> List[ConversationTurn]:
        """Get the last N turns (default: max_active_turns)."""
        count = n if n is not None else self.max_active_turns
        # Each exchange = 2 turns (interviewer + candidate), so we need
        # count * 2 individual turns to get `count` exchanges
        return self.turns[-(count * 2):] if self.turns else []

    def get_formatted_history(self) -> str:
        """Format recent turns for injection into LLM prompt.

        Returns a string like:
            INTERVIEWER: "Tell me about a challenging project"
            YOUR ANSWER: "At Amazon, we had this migration project..."
            INTERVIEWER: "What was the hardest part?"
            YOUR ANSWER: "The tricky thing was the zero-downtime requirement..."
        """
        recent = self.get_recent_turns()
        if not recent:
            return ""

        lines = []
        for turn in recent:
            if turn.role == "interviewer":
                lines.append(f'INTERVIEWER: "{turn.text}"')
            elif turn.role == "candidate":
                lines.append(f'YOUR ANSWER: "{turn.text}"')
        return "\n".join(lines)

    def get_question_count(self) -> int:
        """Count the number of new questions detected so far."""
        return sum(
            1 for t in self.turns
            if t.role == "interviewer" and t.intent in ("new_question", "unknown")
        )

    def get_last_candidate_answer(self) -> Optional[str]:
        """Get the most recent candidate answer text."""
        for turn in reversed(self.turns):
            if turn.role == "candidate":
                return turn.text
        return None

    def get_last_interviewer_question(self) -> Optional[str]:
        """Get the most recent interviewer question text."""
        for turn in reversed(self.turns):
            if turn.role == "interviewer":
                return turn.text
        return None

    def get_interview_phase(self) -> str:
        """Infer interview phase from question count."""
        q_count = self.get_question_count()
        if q_count <= 2:
            return "introduction"
        elif q_count <= 7:
            return "deep_dive"
        else:
            return "closing"

    def get_phase_instruction(self) -> str:
        """Get phase-specific instruction text for LLM prompt."""
        phase = self.get_interview_phase()
        instructions = {
            "introduction": (
                "We are in the INTRODUCTION phase. Keep answers warm, "
                "concise, and personable. Don't over-elaborate."
            ),
            "deep_dive": (
                "We are in the DEEP DIVE phase. Provide detailed, "
                "substantive answers with concrete examples."
            ),
            "closing": (
                "We are in the CLOSING phase. Keep answers brief and "
                "forward-looking. Wrap up cleanly."
            ),
        }
        return instructions.get(phase, "")

    def update_last_interviewer_intent(self, intent: str) -> None:
        """Update the intent of the most recent interviewer turn."""
        for turn in reversed(self.turns):
            if turn.role == "interviewer":
                turn.intent = intent
                return

    def clear(self) -> None:
        """Clear all conversation history."""
        self.turns.clear()

    def __len__(self) -> int:
        return len(self.turns)
