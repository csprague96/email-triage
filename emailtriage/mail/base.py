"""Interface every mail backend implements."""

from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Iterator

from ..models import Email


class MailBackend(ABC):
    name: str = "base"

    @abstractmethod
    def owner(self) -> tuple[str, str]:
        """(display name, smtp address) of the mailbox owner."""

    @abstractmethod
    def inbox_since(self, since: datetime, limit: int = 500) -> Iterator[Email]:
        """Inbox mail received after `since`, newest first."""

    @abstractmethod
    def sent_items(self, limit: int = 300) -> Iterator[Email]:
        """Recent sent mail, newest first, for learning the writing style."""

    @abstractmethod
    def get(self, email_id: str) -> Email | None:
        ...

    @abstractmethod
    def set_categories(self, email_id: str, categories: list[str]) -> None:
        """Replace this tool's categories on the item, keeping any others the user set."""

    @abstractmethod
    def create_reply_draft(self, email_id: str, body_html: str, reply_all: bool = False) -> str:
        """Create a saved reply draft with `body_html` inserted above the quoted original. Returns draft id."""

    @abstractmethod
    def delete_draft(self, draft_id: str) -> None:
        ...

    @abstractmethod
    def open_item(self, item_id: str) -> None:
        """Show the item to the user (opens Outlook window, or a browser link)."""

    def check(self) -> str:
        """Return a short human-readable status line, raise on failure."""
        name, addr = self.owner()
        return f"{self.name}: connected as {name} <{addr}>"
