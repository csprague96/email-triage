"""Plain data types shared across the package."""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime


@dataclass
class Email:
    id: str  # backend-specific stable id (Outlook EntryID or Graph message id)
    subject: str
    sender_name: str
    sender_email: str
    to: list[str]
    cc: list[str]
    received: datetime
    body_text: str  # cleaned plain text of the newest message only
    body_full: str = ""  # full plain text including quoted thread
    has_attachments: bool = False
    conversation_id: str = ""
    is_read: bool = False
    web_link: str = ""

    def to_dict(self) -> dict:
        d = asdict(self)
        d["received"] = self.received.isoformat()
        return d


@dataclass
class Assessment:
    priority: str
    confidence: float
    probabilities: dict[str, float]
    signals: dict[str, float]  # noul name -> probability
    time_sensitivity: float  # 0..3 expected score
    tags: list[str]
    tag_scores: dict[str, float]
    jev_priority: str  # what Jev picked before any rule adjustments
    adjusted_by_rule: str = ""  # human readable explanation when rules changed the answer
    needs_review: bool = False
    model: str = ""
    input_tokens: int = 0

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class DraftResult:
    body_text: str
    attempts: int
    checks: dict[str, float] = field(default_factory=dict)
    passed: bool = True
    notes: str = ""
    outlook_draft_id: str = ""

    def to_dict(self) -> dict:
        return asdict(self)
