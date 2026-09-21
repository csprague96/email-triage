"""Local dashboard: FastAPI JSON API + a single static page. Runs the inbox watcher in a thread."""

from __future__ import annotations

import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel

from . import __version__, digest, pipeline, shared_tags, store, style
from .config import (
    PRIORITY_ORDER,
    load_priority_config,
    load_style,
    load_tags_config,
    save_priority_config,
    save_style,
    save_tags_config,
    settings,
)
from .mail import get_backend

STATIC = Path(__file__).parent / "static"
app = FastAPI(title="Email Triage", version=__version__)
_stop = threading.Event()
_watcher: threading.Thread | None = None


def start_watcher() -> None:
    global _watcher
    if _watcher is None or not _watcher.is_alive():
        _watcher = threading.Thread(target=pipeline.watch, kwargs={"stop_event": _stop}, daemon=True, name="inbox-watcher")
        _watcher.start()


@app.on_event("shutdown")
def _shutdown() -> None:
    _stop.set()


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


# ---- status / runs -------------------------------------------------------

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


# ---- emails ---------------------------------------------------------------

@app.get("/api/emails")
def emails(priority: str | None = None, tag: str | None = None, review: bool = False, q: str | None = None, limit: int = 200):
    return store.list_emails(priority=priority, tag=tag, review=review or None, search=q, limit=limit)


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
        raise HTTPException(400, "SHARED_TAGS_SOURCE is not set in .env")
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
        raise HTTPException(400, "SLACK_WEBHOOK_URL is not set in .env")
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
