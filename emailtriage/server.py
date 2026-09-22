"""Local dashboard: FastAPI JSON API + static front end. Runs the inbox watcher in a thread."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import __version__, digest, pipeline, shared_tags, store, style
from . import jev as jev_mod
from .config import (
    PRIORITY_ORDER,
    apply_settings,
    load_drafting,
    load_priority_config,
    load_style,
    load_tags_config,
    reset_drafting,
    save_drafting,
    save_priority_config,
    save_style,
    save_tags_config,
    settings,
    settings_view,
)
from .drafter import build_instructions
from .mail import get_backend, reset_backend

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Email Triage", version=__version__)
_stop = threading.Event()
_watcher: threading.Thread | None = None
REQUEST_HEADER = "x-requested-with"  # the front end sends this; cross-site pages cannot without a CORS preflight


def start_watcher() -> None:
    global _watcher
    if _watcher is None or not _watcher.is_alive():
        _watcher = threading.Thread(target=pipeline.watch, kwargs={"stop_event": _stop}, daemon=True, name="inbox-watcher")
        _watcher.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop.set()


@app.middleware("http")
async def _same_origin_guard(request: Request, call_next):
    """Mutating API calls must carry the dashboard's custom header. A web page on another origin cannot add it
    without a CORS preflight, which this server never approves, so it cannot change settings or send digests."""
    if request.url.path.startswith("/api/") and request.method not in ("GET", "HEAD", "OPTIONS"):
        if request.headers.get(REQUEST_HEADER, "").lower() != "emailtriage":
            return JSONResponse(status_code=403, content={"detail": "missing X-Requested-With header"})
    return await call_next(request)


app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


# ---- status / runs -------------------------------------------------------

def _local_midnight_utc_iso() -> str:
    now = datetime.now()
    return now.replace(hour=0, minute=0, second=0, microsecond=0).astimezone(timezone.utc).isoformat()


def _auth_state() -> dict:
    """Sign-in state for the front door. Outlook: whatever is signed in on this PC. Graph: cached Microsoft account."""
    if settings.mail_backend == "graph":
        if not settings.graph_client_id:
            return {"backend": "graph", "signed_in": False, "needs_client_id": True, "error": ""}
        try:
            st = get_backend().auth_state()
        except Exception as err:
            return {"backend": "graph", "signed_in": False, "error": str(err)}
        st["backend"] = "graph"
        return st
    try:
        name, addr = get_backend().owner()
        return {"backend": "outlook", "signed_in": True, "account": addr, "name": name, "error": ""}
    except Exception as err:
        return {"backend": "outlook", "signed_in": False, "error": str(err)}


@app.get("/api/status")
def status():
    s = store.stats()
    prof = load_style()
    try:
        owner_name, owner_email = get_backend().owner()
        backend_ok, backend_msg = True, f"{settings.mail_backend}: {owner_name}"
    except Exception as err:
        owner_name, owner_email = "", ""
        backend_ok, backend_msg = False, str(err)
    s.update({
        "version": __version__,
        "backend": settings.mail_backend,
        "backend_ok": backend_ok,
        "backend_msg": backend_msg,
        "owner_name": owner_name,
        "owner_email": owner_email,
        "jev_configured": bool(settings.typesafe_api_key),
        "drafting_enabled": settings.drafting_enabled,
        "openai_model": settings.openai_model,
        "draft_for_priorities": settings.draft_for_priorities,
        "style_learned": bool(prof),
        "style_emails": (prof or {}).get("emails_analysed", 0),
        "watcher_alive": bool(_watcher and _watcher.is_alive()),
        "poll_seconds": settings.poll_seconds,
        "priorities": PRIORITY_ORDER,
        "shared_tags_source": settings.shared_tags_source,
        "digest_enabled": settings.digest_enabled,
        "digest_time": settings.digest_time,
        "digest_weekdays_only": settings.digest_weekdays_only,
        "slack_configured": bool(settings.slack_webhook_url),
        "last_digest_at": store.get_meta("last_digest_at"),
        "handled_today": store.handled_count_since(_local_midnight_utc_iso()),
    })
    return s


class RunBody(BaseModel):
    hours: float | None = None
    limit: int = 300
    force: bool = False


@app.post("/api/run")
def run(body: RunBody):
    since = datetime.now(timezone.utc) - timedelta(hours=body.hours) if body.hours else None
    return pipeline.run_once(since=since, limit=body.limit, force=body.force, verbose=True)


# ---- sign in ----------------------------------------------------------------

@app.get("/api/auth")
def auth_status():
    return _auth_state()


@app.post("/api/auth/connect")
def auth_connect():
    """Outlook: retry the connection. Graph: start the Microsoft device-code sign-in and return the code to show."""
    reset_backend()
    if settings.mail_backend == "graph":
        if not settings.graph_client_id:
            raise HTTPException(400, "Add the Entra app's client ID in Settings > Account first.")
        try:
            flow = get_backend().start_sign_in()
        except Exception as err:
            raise HTTPException(500, str(err))
        st = _auth_state()
        st["pending"] = {k: v for k, v in flow.items() if k != "expires_at"}
        return st
    st = _auth_state()
    if not st["signed_in"]:
        raise HTTPException(503, st.get("error") or "Could not reach Outlook. Is classic Outlook installed and open?")
    return st


@app.post("/api/auth/signout")
def auth_signout():
    if settings.mail_backend != "graph":
        raise HTTPException(400, "Outlook mode uses the account signed in to Outlook on this PC; there is nothing to sign out of here.")
    try:
        get_backend().sign_out()
    except Exception as err:
        raise HTTPException(500, str(err))
    reset_backend()
    return _auth_state()


# ---- today (focus view) -----------------------------------------------------

def _rank(e: dict) -> tuple:
    return (0 if e["priority"] == "urgent" else 1, -(e.get("time_sensitivity") or 0), e["received"])


@app.get("/api/today")
def today(hours: float = 72):
    now = datetime.now(timezone.utc)
    since = (now - timedelta(hours=hours)).isoformat()
    since_day = (now - timedelta(hours=24)).isoformat()
    open_items = store.list_emails(priorities=["urgent", "high"], since_iso=since, handled=False, limit=100)
    needs_you = [e for e in open_items if not (e.get("thread") or {}).get("owner_replied_after")]
    needs_you.sort(key=_rank)
    seen = {e["id"] for e in needs_you}
    review = [e for e in store.list_emails(review=True, since_iso=since, handled=False, limit=60)
              if e["priority"] not in ("junk",) and e["id"] not in seen][:8]
    later = [e for e in store.list_emails(priorities=["medium"], since_iso=since, handled=False, limit=60)
             if not (e.get("thread") or {}).get("owner_replied_after")]
    return {
        "generated_at": now.isoformat(),
        "hours": hours,
        "needs_you": needs_you,
        "review": review,
        "later": later[:6],
        "later_total": len(later),
        "drafts": store.list_drafts(status="ready", limit=20),
        "counts_24h": store.counts_since(since_day),
        "handled_today": store.handled_count_since(_local_midnight_utc_iso()),
    }


# ---- emails ---------------------------------------------------------------

@app.get("/api/emails")
def emails(priority: str | None = None, tag: str | None = None, review: bool = False, q: str | None = None,
           limit: int = 200, handled: bool | None = None, hours: float | None = None):
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat() if hours else None
    return store.list_emails(priority=priority, tag=tag, review=review or None, search=q, limit=limit,
                             handled=handled, since_iso=since)


@app.get("/api/emails/{email_id}")
def email_detail(email_id: str):
    d = store.get_email(email_id)
    if not d:
        raise HTTPException(404, "unknown email")
    return d


class PriorityBody(BaseModel):
    priority: str


@app.post("/api/emails/{email_id}/priority")
def set_priority(email_id: str, body: PriorityBody):
    if body.priority not in PRIORITY_ORDER:
        raise HTTPException(400, f"priority must be one of {PRIORITY_ORDER}")
    try:
        store.record_correction(email_id, body.priority)
    except KeyError:
        raise HTTPException(404, "unknown email")
    d = store.get_email(email_id)
    if settings.apply_outlook_categories and d:
        try:
            get_backend().set_categories(email_id, [f"Priority: {body.priority.capitalize()}"] + d["tags"])
        except Exception:
            pass
    return {"ok": True}


class TagsBody(BaseModel):
    tags: list[str]


@app.post("/api/emails/{email_id}/tags")
def set_tags(email_id: str, body: TagsBody):
    store.set_user_tags(email_id, body.tags)
    d = store.get_email(email_id)
    if settings.apply_outlook_categories and d:
        try:
            get_backend().set_categories(email_id, [f"Priority: {d['priority'].capitalize()}"] + body.tags)
        except Exception:
            pass
    return {"ok": True}


class HandledBody(BaseModel):
    handled: bool = True


@app.post("/api/emails/{email_id}/handled")
def set_handled(email_id: str, body: HandledBody):
    if not store.get_email(email_id):
        raise HTTPException(404, "unknown email")
    store.set_handled(email_id, body.handled)
    return {"ok": True, "handled": body.handled}


@app.post("/api/emails/{email_id}/reassess")
def reassess(email_id: str):
    em = get_backend().get(email_id)
    if not em:
        raise HTTPException(404, "email not found in mailbox")
    a = pipeline.process_email(em, force=True, draft=False)
    return a.to_dict() if a else {}


class DraftBody(BaseModel):
    context: str = ""
    reply_all: bool = False


@app.post("/api/emails/{email_id}/draft")
def create_draft(email_id: str, body: DraftBody):
    if not settings.drafting_enabled:
        raise HTTPException(400, "OPENAI_API_KEY is not set; drafting is disabled")
    em = get_backend().get(email_id)
    if not em:
        raise HTTPException(404, "email not found in mailbox")
    draft_id, result = pipeline.make_draft(em, context=body.context, reply_all=body.reply_all)
    d = result.to_dict()
    d["id"] = draft_id
    return d


@app.post("/api/emails/{email_id}/open")
def open_email(email_id: str):
    try:
        get_backend().open_item(email_id)
    except Exception as err:
        raise HTTPException(500, str(err))
    return {"ok": True}


# ---- drafts ---------------------------------------------------------------

@app.get("/api/drafts")
def drafts(status: str | None = "ready"):
    return store.list_drafts(status=None if status == "all" else status)


@app.post("/api/drafts/{draft_id}/open")
def open_draft(draft_id: int):
    d = store.get_draft(draft_id)
    if not d:
        raise HTTPException(404, "unknown draft")
    if not d["outlook_draft_id"]:
        raise HTTPException(400, "this draft was not saved to the mailbox")
    try:
        get_backend().open_item(d["outlook_draft_id"])
    except Exception as err:
        raise HTTPException(500, f"could not open (was it already sent or deleted?): {err}")
    store.set_draft_status(draft_id, "opened")
    return {"ok": True}


@app.post("/api/drafts/{draft_id}/done")
def draft_done(draft_id: int):
    store.set_draft_status(draft_id, "done")
    return {"ok": True}


@app.delete("/api/drafts/{draft_id}")
def discard_draft(draft_id: int):
    d = store.get_draft(draft_id)
    if not d:
        raise HTTPException(404, "unknown draft")
    if d["outlook_draft_id"]:
        get_backend().delete_draft(d["outlook_draft_id"])
    store.set_draft_status(draft_id, "discarded")
    return {"ok": True}


# ---- settings (.env) --------------------------------------------------------

@app.get("/api/settings")
def get_settings():
    return settings_view()


@app.put("/api/settings")
def put_settings(body: dict):
    try:
        changed = apply_settings(body)
    except ValueError as err:
        raise HTTPException(400, str(err))
    if any(k in changed for k in ("MAIL_BACKEND", "GRAPH_CLIENT_ID", "GRAPH_TENANT_ID")):
        reset_backend()
    if any(k in changed for k in ("TYPESAFE_API_KEY", "TYPESAFE_MODEL")):
        jev_mod.reset_client()
    return {"changed": changed, "settings": settings_view()}


# ---- config ---------------------------------------------------------------

@app.get("/api/tags")
def get_tags():
    return load_tags_config()


@app.put("/api/tags")
def put_tags(body: dict):
    try:
        save_tags_config(body)
    except ValueError as err:
        raise HTTPException(400, str(err))
    return load_tags_config()


@app.post("/api/tags/sync")
def sync_tags():
    res = shared_tags.sync(force=True)
    if not res.get("enabled"):
        raise HTTPException(400, "Set a shared tags source first (Settings > Tags)")
    return res


# ---- digest -----------------------------------------------------------------

@app.get("/api/digest/preview")
def digest_preview(hours: float | None = None):
    d = digest.build(hours)
    d["text"] = digest.render_text(d)
    return d


@app.post("/api/digest/send")
def digest_send(hours: float | None = None):
    if not settings.slack_webhook_url:
        raise HTTPException(400, "Add a Slack webhook URL first (Settings > Digest)")
    d = digest.send_now(hours)
    return {"ok": True, "counts": d["counts"]}


@app.get("/api/priority-config")
def get_priority_config():
    return load_priority_config()


@app.put("/api/priority-config")
def put_priority_config(body: dict):
    try:
        save_priority_config(body)
    except ValueError as err:
        raise HTTPException(400, str(err))
    return load_priority_config()


# ---- drafting: prompt, banned phrases, style --------------------------------

@app.get("/api/drafting")
def get_drafting():
    return load_drafting()


@app.put("/api/drafting")
def put_drafting(body: dict):
    try:
        save_drafting(body)
    except ValueError as err:
        raise HTTPException(400, str(err))
    return load_drafting()


@app.post("/api/drafting/reset")
def reset_drafting_prompt():
    reset_drafting()
    return load_drafting()


@app.get("/api/drafting/preview")
def drafting_preview():
    """The system prompt exactly as the drafting model will receive it right now."""
    owner = (load_style() or {}).get("owner_name") or ""
    if not owner:
        try:
            owner = get_backend().owner()[0]
        except Exception:
            owner = "the mailbox owner"
    instructions, samples, conf = build_instructions(owner)
    return {"instructions": instructions, "samples": len(samples), "banned_phrases": len(conf["banned_phrases"])}


@app.get("/api/style")
def get_style():
    return load_style() or {}


class StyleNotes(BaseModel):
    extra_instructions: str


@app.put("/api/style/notes")
def put_style_notes(body: StyleNotes):
    prof = load_style() or {}
    prof["extra_instructions"] = body.extra_instructions
    save_style(prof)
    return prof


@app.post("/api/style/learn")
def learn_style(limit: int = 300):
    prof = style.learn(limit=limit)
    return {"emails_analysed": prof["emails_analysed"], "greetings": prof["greetings"], "signoffs": prof["signoffs"],
            "median_words": prof["median_words"], "signature_lines": prof["signature_lines"]}


@app.exception_handler(Exception)
async def _errors(_, exc: Exception):
    return JSONResponse(status_code=500, content={"detail": str(exc)})
