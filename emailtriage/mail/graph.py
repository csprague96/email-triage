"""Microsoft Graph backend (device-code sign in).

Use this when classic Outlook is not available (new Outlook, Mac, a server).
Setup once per organisation: register an app in Entra ID with these delegated
permissions: Mail.ReadWrite, User.Read; enable "Allow public client flows".
Put its Application (client) ID in GRAPH_CLIENT_ID (or paste it in Settings >
Account). Each colleague then signs in once from the dashboard's sign-in screen
(or with `python -m emailtriage login`), which uses the device code flow.

Written against the Graph v1.0 REST API but NOT exercised against a live tenant
in this build. Treat it as a starting point and test before relying on it.
"""

from __future__ import annotations

import json
import re
import threading
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx

from ..config import ROOT, settings
from ..models import Email, ThreadMessage
from ..text import html_to_text, normalize_whitespace, strip_quoted
from .base import MailBackend

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.ReadWrite", "User.Read"]
TOKEN_CACHE = ROOT / ".graph_token_cache.json"
SELECT = (
    "id,subject,from,toRecipients,ccRecipients,receivedDateTime,sentDateTime,body,hasAttachments,"
    "conversationId,isRead,webLink,categories"
)
CATEGORY_PREFIX = "Priority: "


class NotSignedIn(RuntimeError):
    """No cached Microsoft account. Sign in from the dashboard or run `python -m emailtriage login`."""


class GraphBackend(MailBackend):
    name = "graph"

    def __init__(self) -> None:
        if not settings.graph_client_id:
            raise RuntimeError("GRAPH_CLIENT_ID is required for MAIL_BACKEND=graph. Paste it in Settings > Account.")
        self._token: str | None = None
        self._owner: tuple[str, str] | None = None
        self._http = httpx.Client(timeout=30)
        self._pending: dict | None = None  # device flow in progress: user_code, verification_uri, expires_at
        self._pending_error: str = ""
        self._auth_lock = threading.Lock()

    # ---- auth ----------------------------------------------------------

    def _msal_app(self):
        import msal

        cache = msal.SerializableTokenCache()
        if TOKEN_CACHE.exists():
            try:
                cache.deserialize(TOKEN_CACHE.read_text(encoding="utf-8"))
            except Exception:
                pass
        app = msal.PublicClientApplication(
            settings.graph_client_id,
            authority=f"https://login.microsoftonline.com/{settings.graph_tenant_id}",
            token_cache=cache,
        )
        return app, cache

    @staticmethod
    def _save_cache(cache) -> None:
        if cache.has_state_changed:
            TOKEN_CACHE.write_text(cache.serialize(), encoding="utf-8")

    def _acquire_token(self) -> str:
        """Silent only. Interactive sign-in is driven by start_sign_in() so it can be shown in the dashboard."""
        app, cache = self._msal_app()
        accounts = app.get_accounts()
        result = app.acquire_token_silent(SCOPES, account=accounts[0]) if accounts else None
        if not result or "access_token" not in result:
            raise NotSignedIn("Not signed in to Microsoft 365. Sign in from the dashboard (or run: python -m emailtriage login).")
        self._save_cache(cache)
        return result["access_token"]

    def start_sign_in(self) -> dict:
        """Begin the device code flow. Returns the code and URL for the user; completion happens in a background thread."""
        with self._auth_lock:
            if self._pending and self._pending["expires_at"] > datetime.now(timezone.utc).timestamp():
                return dict(self._pending)
            app, cache = self._msal_app()
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Could not start Microsoft sign-in: {flow.get('error_description') or json.dumps(flow)[:300]}")
            self._pending = {
                "user_code": flow["user_code"],
                "verification_uri": flow["verification_uri"],
                "message": flow["message"],
                "expires_at": datetime.now(timezone.utc).timestamp() + int(flow.get("expires_in", 900)),
            }
            self._pending_error = ""

        def finish() -> None:
            try:
                result = app.acquire_token_by_device_flow(flow)
                if "access_token" in result:
                    self._save_cache(cache)
                    self._token = result["access_token"]
                    self._owner = None
                else:
                    self._pending_error = result.get("error_description") or result.get("error") or "sign-in did not complete"
            except Exception as err:
                self._pending_error = str(err)
            finally:
                with self._auth_lock:
                    self._pending = None

        threading.Thread(target=finish, daemon=True, name="graph-device-flow").start()
        return dict(self._pending)

    def auth_state(self) -> dict:
        """What the sign-in screen needs: signed in?, a flow in progress?, last error."""
        app, _ = self._msal_app()
        accounts = app.get_accounts()
        pending = None
        with self._auth_lock:
            if self._pending and self._pending["expires_at"] > datetime.now(timezone.utc).timestamp():
                pending = {k: v for k, v in self._pending.items() if k != "expires_at"}
        return {
            "signed_in": bool(accounts),
            "account": accounts[0].get("username", "") if accounts else "",
            "pending": pending,
            "error": self._pending_error,
        }

    def sign_out(self) -> None:
        app, cache = self._msal_app()
        for acct in app.get_accounts():
            app.remove_account(acct)
        self._save_cache(cache)
        if TOKEN_CACHE.exists():
            TOKEN_CACHE.unlink()
        self._token = None
        self._owner = None
        self._pending_error = ""

    def _headers(self) -> dict:
        if self._token is None:
            self._token = self._acquire_token()
        return {"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"}

    def _req(self, method: str, url: str, **kw) -> httpx.Response:
        if not url.startswith("http"):
            url = GRAPH + url
        resp = self._http.request(method, url, headers=self._headers(), **kw)
        if resp.status_code == 401:
            self._token = None
            resp = self._http.request(method, url, headers=self._headers(), **kw)
        if resp.status_code >= 400:
            raise RuntimeError(f"Graph {method} {url} -> {resp.status_code}: {resp.text[:500]}")
        return resp

    # ---- mapping -------------------------------------------------------

    @staticmethod
    def _addr(entry: dict) -> str:
        return ((entry or {}).get("emailAddress") or {}).get("address", "") or ""

    def _to_email(self, m: dict) -> Email:
        body = m.get("body") or {}
        content = body.get("content", "") or ""
        full = html_to_text(content) if body.get("contentType", "").lower() == "html" else normalize_whitespace(content)
        received = m.get("receivedDateTime") or m.get("sentDateTime") or ""
        try:
            dt = datetime.fromisoformat(received.replace("Z", "+00:00"))
        except Exception:
            dt = datetime.now(timezone.utc)
        frm = (m.get("from") or {}).get("emailAddress") or {}
        return Email(
            id=m["id"],
            subject=m.get("subject") or "(no subject)",
            sender_name=frm.get("name", "") or "",
            sender_email=frm.get("address", "") or "",
            to=[self._addr(r) for r in m.get("toRecipients") or []],
            cc=[self._addr(r) for r in m.get("ccRecipients") or []],
            received=dt,
            body_text=strip_quoted(full),
            body_full=full,
            has_attachments=bool(m.get("hasAttachments")),
            conversation_id=m.get("conversationId", "") or "",
            is_read=bool(m.get("isRead")),
            web_link=m.get("webLink", "") or "",
        )

    def _list(self, folder: str, params: dict, limit: int) -> Iterator[Email]:
        url = f"/me/mailFolders/{folder}/messages"
        count = 0
        while url and count < limit:
            resp = self._req("GET", url, params=params if url.startswith("/") else None)
            data = resp.json()
            for m in data.get("value", []):
                yield self._to_email(m)
                count += 1
                if count >= limit:
                    return
            url = data.get("@odata.nextLink")
            params = {}

    # ---- interface -----------------------------------------------------

    def owner(self) -> tuple[str, str]:
        if self._owner is None:
            me = self._req("GET", "/me").json()
            self._owner = (me.get("displayName", ""), me.get("mail") or me.get("userPrincipalName", ""))
        return self._owner

    def inbox_since(self, since: datetime, limit: int = 500) -> Iterator[Email]:
        since_utc = since.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        params = {
            "$select": SELECT,
            "$orderby": "receivedDateTime desc",
            "$filter": f"receivedDateTime ge {since_utc}",
            "$top": 50,
        }
        yield from self._list("inbox", params, limit)

    def sent_items(self, limit: int = 300) -> Iterator[Email]:
        params = {"$select": SELECT, "$orderby": "sentDateTime desc", "$top": 50}
        yield from self._list("sentitems", params, limit)

    def get(self, email_id: str) -> Email | None:
        try:
            m = self._req("GET", f"/me/messages/{email_id}", params={"$select": SELECT}).json()
        except RuntimeError:
            return None
        return self._to_email(m)

    def set_categories(self, email_id: str, categories: list[str]) -> None:
        m = self._req("GET", f"/me/messages/{email_id}", params={"$select": "categories"}).json()
        kept = [c for c in m.get("categories", []) if not c.startswith(CATEGORY_PREFIX)]
        for c in categories:
            if c not in kept:
                kept.append(c)
        self._req("PATCH", f"/me/messages/{email_id}", json={"categories": kept})

    def create_reply_draft(self, email_id: str, body_html: str, reply_all: bool = False) -> str:
        action = "createReplyAll" if reply_all else "createReply"
        draft = self._req("POST", f"/me/messages/{email_id}/{action}", json={}).json()
        existing = (draft.get("body") or {}).get("content", "") or ""
        m = re.search(r"<body[^>]*>", existing, re.I)
        new_html = existing[: m.end()] + body_html + existing[m.end():] if m else body_html + existing
        self._req("PATCH", f"/me/messages/{draft['id']}", json={"body": {"contentType": "html", "content": new_html}})
        return draft["id"]

    def thread_messages(self, email: Email) -> list[ThreadMessage]:
        if not email.conversation_id:
            return []
        _, owner_email = self.owner()
        cid = email.conversation_id.replace("'", "''")
        data = self._req(
            "GET", "/me/messages",
            params={"$filter": f"conversationId eq '{cid}' and isDraft eq false",
                    "$select": "from,receivedDateTime,sentDateTime,parentFolderId", "$top": 50},
        ).json()
        out = []
        for m in data.get("value", []):
            frm = (m.get("from") or {}).get("emailAddress") or {}
            addr = frm.get("address", "") or ""
            from_owner = bool(owner_email) and addr.lower() == owner_email.lower()
            stamp = m.get("sentDateTime") if from_owner else m.get("receivedDateTime")
            try:
                dt = datetime.fromisoformat((stamp or "").replace("Z", "+00:00"))
            except Exception:
                continue
            out.append(ThreadMessage(sender_name=frm.get("name", "") or "", sender_email=addr, received=dt, from_owner=from_owner))
        out.sort(key=lambda t: t.received)
        return out

    def delete_draft(self, draft_id: str) -> None:
        try:
            self._req("DELETE", f"/me/messages/{draft_id}")
        except RuntimeError:
            pass

    def open_item(self, item_id: str) -> None:
        m = self._req("GET", f"/me/messages/{item_id}", params={"$select": "webLink"}).json()
        link = m.get("webLink")
        if link:
            webbrowser.open(link)
