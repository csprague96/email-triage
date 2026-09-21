"""Mail backends. `get_backend()` returns the one selected in .env."""

from __future__ import annotations

from ..config import settings
from .base import MailBackend

_backend: MailBackend | None = None


def get_backend() -> MailBackend:
    global _backend
    if _backend is None:
        if settings.mail_backend == "graph":
            from .graph import GraphBackend

            _backend = GraphBackend()
        elif settings.mail_backend == "outlook":
            from .outlook_com import OutlookBackend

            _backend = OutlookBackend()
        else:
            raise ValueError(f"Unknown MAIL_BACKEND '{settings.mail_backend}' (use 'outlook' or 'graph')")
    return _backend
