"""Microsoft Graph backend (device-code sign in).

Use this when classic Outlook is not available (new Outlook, Mac, a server).
Setup once per organisation: register an app in Entra ID with these delegated
permissions: Mail.ReadWrite, User.Read; enable "Allow public client flows".
Put its Application (client) ID in GRAPH_CLIENT_ID. Each colleague then signs
in once with the device code printed in the terminal.

Written against the Graph v1.0 REST API but NOT exercised against a live tenant
in this build. Treat it as a starting point and test before relying on it.
"""

from __future__ import annotations

import json
import re
import webbrowser
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

import httpx

from ..config import ROOT, settings
from ..models import Email
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


class GraphBackend(MailBackend):
    name = "graph"

    def __init__(self) -> None:
        if not settings.graph_client_id:
            raise RuntimeError("GRAPH_CLIENT_ID is required for MAIL_BACKEND=graph")
        self._token: str | None = None
        self._owner: tuple[str, str] | None = None
        self._http = httpx.Client(timeout=30)

    # ---- auth ----------------------------------------------------------

    def _acquire_token(self) -> str:
        import msal

        cache = msal.SerializableTokenCache()
        if TOKEN_CACHE.exists():
            cache.deserialize(TOKEN_CACHE.read_text(encoding="utf-8"))
        app = msal.PublicClientApplication(
            settings.graph_client_id,
            authority=f"https://login.microsoftonline.com/{settings.graph_tenant_id}",
            token_cache=cache,
        )
        result = None
        accounts = app.get_accounts()
        if accounts:
            result = app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result:
            flow = app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Device flow failed: {json.dumps(flow)}")
            print("\n" + flow["message"] + "\n", flush=True)
            try:
                webbrowser.open(flow["verification_uri"])
            except Exception:
                pass
            result = app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Graph sign-in failed: {result.get('error_description', result)}")
        if cache.has_state_changed:
            TOKEN_CACHE.write_text(cache.serialize(), encoding="utf-8")
        return result["access_token"]

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
