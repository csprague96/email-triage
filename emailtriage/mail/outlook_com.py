"""Classic Outlook desktop backend via COM automation (pywin32).

No credentials needed: it talks to the Outlook that is already signed in on this PC.
Requires classic Outlook (the "new Outlook" app does not expose COM).
"""

from __future__ import annotations

import re
import threading
from datetime import datetime, timedelta, timezone
from typing import Iterator

from ..models import Email
from ..text import html_to_text, normalize_whitespace, strip_quoted
from .base import MailBackend

OL_FOLDER_DRAFTS = 16
OL_FOLDER_INBOX = 6
OL_FOLDER_SENT = 5
OL_MAIL_ITEM = 43

PR_SMTP_ADDRESS = "http://schemas.microsoft.com/mapi/proptag/0x39FE001F"
PR_SENT_REPRESENTING_SMTP = "http://schemas.microsoft.com/mapi/proptag/0x5D01001F"

CATEGORY_PREFIX = "Priority: "
CATEGORY_COLORS = {"junk": 15, "low": 4, "medium": 5, "high": 2, "urgent": 1}  # Outlook OlCategoryColor indexes
TAG_COLOR = 8  # blue

_local = threading.local()
_lock = threading.RLock()


def _app():
    """Per-thread Outlook.Application with COM initialised."""
    import pythoncom
    import win32com.client

    if not getattr(_local, "initialised", False):
        pythoncom.CoInitialize()
        _local.initialised = True
    app = getattr(_local, "app", None)
    if app is None:
        app = win32com.client.Dispatch("Outlook.Application")
        _local.app = app
    return app


def _ns():
    return _app().GetNamespace("MAPI")


def _to_datetime(value) -> datetime:
    # pywintypes datetimes are tz-aware in recent pywin32; older ones are naive local time.
    try:
        dt = datetime(value.year, value.month, value.day, value.hour, value.minute, value.second)
    except Exception:
        return datetime.now(timezone.utc)
    tz = getattr(value, "tzinfo", None)
    if tz is not None:
        try:
            return datetime(value.year, value.month, value.day, value.hour, value.minute, value.second, tzinfo=tz).astimezone(timezone.utc)
        except Exception:
            pass
    return dt.astimezone(timezone.utc)


def _smtp_of_sender(item) -> str:
    try:
        if item.SenderEmailType == "EX":
            sender = item.Sender
            if sender is not None:
                exu = sender.GetExchangeUser()
                if exu is not None and exu.PrimarySmtpAddress:
                    return exu.PrimarySmtpAddress
            try:
                return item.PropertyAccessor.GetProperty(PR_SENT_REPRESENTING_SMTP) or item.SenderEmailAddress or ""
            except Exception:
                pass
        return item.SenderEmailAddress or ""
    except Exception:
        return ""


def _recipients(item) -> tuple[list[str], list[str]]:
    to, cc = [], []
    try:
        for r in item.Recipients:
            addr = ""
            try:
                addr = r.PropertyAccessor.GetProperty(PR_SMTP_ADDRESS)
            except Exception:
                addr = r.Address or ""
            if not addr or "/O=" in addr.upper():
                try:
                    exu = r.AddressEntry.GetExchangeUser()
                    if exu is not None:
                        addr = exu.PrimarySmtpAddress or addr
                except Exception:
                    pass
            addr = addr or (r.Name or "")
            if r.Type == 2:
                cc.append(addr)
            elif r.Type == 1:
                to.append(addr)
    except Exception:
        pass
    return to, cc


class OutlookBackend(MailBackend):
    name = "outlook"

    def __init__(self) -> None:
        self._owner: tuple[str, str] | None = None
        self._known_categories: set[str] | None = None

    # ---- helpers -------------------------------------------------------

    def _item_to_email(self, item, include_full: bool = True) -> Email | None:
        try:
            if item.Class != OL_MAIL_ITEM:
                return None
        except Exception:
            return None
        try:
            html_body = item.HTMLBody or ""
        except Exception:
            html_body = ""
        try:
            plain = item.Body or ""
        except Exception:
            plain = ""
        full_text = html_to_text(html_body) if html_body else normalize_whitespace(plain)
        if not full_text.strip() and plain:
            full_text = normalize_whitespace(plain)
        newest = strip_quoted(full_text)
        to, cc = _recipients(item)
        try:
            unread = bool(item.UnRead)
        except Exception:
            unread = False
        return Email(
            id=item.EntryID,
            subject=item.Subject or "(no subject)",
            sender_name=item.SenderName or "",
            sender_email=_smtp_of_sender(item),
            to=to,
            cc=cc,
            received=_to_datetime(item.ReceivedTime),
            body_text=newest,
            body_full=full_text if include_full else "",
            has_attachments=(item.Attachments.Count > 0) if hasattr(item, "Attachments") else False,
            conversation_id=getattr(item, "ConversationID", "") or "",
            is_read=not unread,
        )

    def _iter_folder(self, folder_const: int, since: datetime | None, limit: int, sent: bool) -> Iterator[Email]:
        with _lock:
            folder = _ns().GetDefaultFolder(folder_const)
            items = folder.Items
            items.Sort("[SentOn]" if sent else "[ReceivedTime]", True)
            count = 0
            item = items.GetFirst()
            while item is not None and count < limit:
                try:
                    stamp = _to_datetime(item.SentOn if sent else item.ReceivedTime)
                except Exception:
                    stamp = None
                if since is not None and stamp is not None and stamp < since:
                    break
                email = self._item_to_email(item)
                if email is not None:
                    count += 1
                    yield email
                item = items.GetNext()

    # ---- interface -----------------------------------------------------

    def owner(self) -> tuple[str, str]:
        if self._owner is None:
            with _lock:
                ns = _ns()
                addr, name = "", ""
                try:
                    cu = ns.CurrentUser
                    name = cu.Name or ""  # display name, e.g. "Jane Doe"
                    exu = cu.AddressEntry.GetExchangeUser()
                    if exu is not None:
                        addr = exu.PrimarySmtpAddress or ""
                except Exception:
                    pass
                if not addr or "@" not in addr:
                    try:
                        acct = ns.Accounts.Item(1)
                        addr = acct.SmtpAddress or addr
                        name = name or acct.UserName or acct.DisplayName or ""
                    except Exception:
                        pass
                self._owner = (name, addr)
        return self._owner

    def inbox_since(self, since: datetime, limit: int = 500) -> Iterator[Email]:
        if since.tzinfo is None:
            since = since.astimezone(timezone.utc)
        yield from self._iter_folder(OL_FOLDER_INBOX, since, limit, sent=False)

    def sent_items(self, limit: int = 300) -> Iterator[Email]:
        yield from self._iter_folder(OL_FOLDER_SENT, None, limit, sent=True)

    def get(self, email_id: str) -> Email | None:
        with _lock:
            try:
                item = _ns().GetItemFromID(email_id)
            except Exception:
                return None
            return self._item_to_email(item)

    def _ensure_category(self, name: str, color: int) -> None:
        ns = _ns()
        if self._known_categories is None:
            self._known_categories = set()
            try:
                for c in ns.Categories:
                    self._known_categories.add(c.Name)
            except Exception:
                pass
        if name not in self._known_categories:
            try:
                ns.Categories.Add(name, color)
            except Exception:
                pass
            self._known_categories.add(name)

    def set_categories(self, email_id: str, categories: list[str]) -> None:
        with _lock:
            item = _ns().GetItemFromID(email_id)
            existing = [c.strip() for c in (item.Categories or "").split(",") if c.strip()]
            # Drop our own previous priority category; keep user categories.
            kept = [c for c in existing if not c.startswith(CATEGORY_PREFIX)]
            for cat in categories:
                if cat.startswith(CATEGORY_PREFIX):
                    level = cat[len(CATEGORY_PREFIX):].strip().lower()
                    self._ensure_category(cat, CATEGORY_COLORS.get(level, 0))
                else:
                    self._ensure_category(cat, TAG_COLOR)
                if cat not in kept:
                    kept.append(cat)
            item.Categories = ", ".join(kept)
            item.Save()

    def create_reply_draft(self, email_id: str, body_html: str, reply_all: bool = False) -> str:
        with _lock:
            item = _ns().GetItemFromID(email_id)
            reply = item.ReplyAll() if reply_all else item.Reply()
            existing = reply.HTMLBody or ""
            m = re.search(r"<body[^>]*>", existing, re.I)
            if m:
                reply.HTMLBody = existing[: m.end()] + body_html + existing[m.end():]
            else:
                reply.HTMLBody = body_html + existing
            reply.Save()
            return reply.EntryID

    def delete_draft(self, draft_id: str) -> None:
        with _lock:
            try:
                item = _ns().GetItemFromID(draft_id)
                item.Delete()
            except Exception:
                pass

    def open_item(self, item_id: str) -> None:
        with _lock:
            item = _ns().GetItemFromID(item_id)
            item.Display()

    def check(self) -> str:
        name, addr = self.owner()
        with _lock:
            inbox = _ns().GetDefaultFolder(OL_FOLDER_INBOX)
            n = inbox.Items.Count
        return f"outlook: connected as {name} <{addr}>, inbox has {n} items"
