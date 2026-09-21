"""Fetch -> assess -> tag -> (draft) -> notify. Used by the CLI, the watcher, and the dashboard."""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import jev, store
from .config import settings
from .config import load_style
from .drafter import draft_reply, render_draft_html
from .mail import get_backend
from .models import Assessment, DraftResult, Email
from .notify import toast

CATEGORY_PREFIX = "Priority: "
_run_lock = threading.Lock()


def _owner() -> tuple[str, str]:
    name, addr = get_backend().owner()
    return name or "Me", addr or ""


def apply_categories(email_id: str, assessment: Assessment) -> None:
    if not settings.apply_outlook_categories:
        return
    cats = [f"{CATEGORY_PREFIX}{assessment.priority.capitalize()}"] + list(assessment.tags)
    try:
        get_backend().set_categories(email_id, cats)
    except Exception as err:
        print(f"[categories] could not set on '{email_id[:12]}...': {err}", flush=True)


def make_draft(email: Email, context: str = "", reply_all: bool = False) -> tuple[int, DraftResult]:
    owner_name, owner_email = _owner()
    result = draft_reply(email, owner_name, owner_email, context=context)
    try:
        signature = (load_style() or {}).get("signature_lines") or []
        html = render_draft_html(result.body_text, signature)
        result.outlook_draft_id = get_backend().create_reply_draft(email.id, html, reply_all=reply_all)
    except Exception as err:
        result.notes = (result.notes + "\n" if result.notes else "") + f"Could not create mail draft: {err}"
    draft_id = store.save_draft(email.id, result)
    return draft_id, result


def process_email(email: Email, force: bool = False, draft: bool | None = None) -> Assessment | None:
    if not force and store.is_processed(email.id):
        return None
    owner_name, owner_email = _owner()
    if email.sender_email and owner_email and email.sender_email.lower() == owner_email.lower() and not force:
        # Mail we sent to ourselves (meeting invites etc.): store as low without spending a Jev call.
        assessment = Assessment(priority="low", confidence=1.0, probabilities={"low": 1.0}, signals={},
                                time_sensitivity=0.0, tags=[], tag_scores={}, jev_priority="low",
                                adjusted_by_rule="Sent by the mailbox owner; skipped.", needs_review=False)
    else:
        assessment = jev.assess(email, owner_email, owner_name)
    store.save_email(email)
    store.save_assessment(email.id, assessment)
    apply_categories(email.id, assessment)

    should_draft = draft if draft is not None else (
        settings.drafting_enabled and assessment.priority in settings.draft_for_priorities
        and assessment.signals.get("automated", 0) < 0.7
    )
    if should_draft:
        try:
            draft_id, result = make_draft(email)
            toast(
                f"Draft ready: {assessment.priority.upper()} email",
                f"{email.sender_name}: {email.subject}",
                url=f"http://127.0.0.1:{settings.port}/#drafts",
            )
        except Exception as err:
            print(f"[draft] failed for '{email.subject}': {err}", flush=True)
            traceback.print_exc()
    elif assessment.priority == "urgent":
        toast("Urgent email", f"{email.sender_name}: {email.subject}", url=f"http://127.0.0.1:{settings.port}/")
    return assessment


def run_once(since: datetime | None = None, limit: int = 300, force: bool = False, verbose: bool = True) -> dict:
    """Process inbox mail received after `since` (default: since the last run, or 24h)."""
    if not _run_lock.acquire(blocking=False):
        return {"skipped": True, "reason": "a run is already in progress"}
    try:
        backend = get_backend()
        if since is None:
            last = store.get_meta("last_seen_received")
            since = datetime.fromisoformat(last) - timedelta(minutes=5) if last else datetime.now(timezone.utc) - timedelta(hours=24)
        processed, skipped, errors = 0, 0, 0
        newest: datetime | None = None
        counts: dict[str, int] = {}
        t0 = time.time()
        for email in backend.inbox_since(since, limit=limit):
            if newest is None or email.received > newest:
                newest = email.received
            try:
                a = process_email(email, force=force)
            except Exception as err:
                errors += 1
                print(f"[run] error on '{email.subject}': {err}", flush=True)
                continue
            if a is None:
                skipped += 1
                continue
            processed += 1
            counts[a.priority] = counts.get(a.priority, 0) + 1
            if verbose:
                flag = " (review)" if a.needs_review else ""
                tags = f"  [{', '.join(a.tags)}]" if a.tags else ""
                print(f"  {a.priority:<7} {a.confidence:.2f}{flag}  {email.sender_name[:22]:<22} {email.subject[:60]}{tags}", flush=True)
        if newest is not None:
            store.set_meta("last_seen_received", newest.isoformat())
        note = f"processed {processed}, skipped {skipped}, errors {errors} in {time.time() - t0:.1f}s"
        store.set_meta("last_run", datetime.now(timezone.utc).isoformat())
        store.set_meta("last_run_note", note)
        if verbose:
            print(f"[run] {note}", flush=True)
        return {"processed": processed, "skipped": skipped, "errors": errors, "by_priority": counts, "note": note}
    finally:
        _run_lock.release()


def watch(poll_seconds: int | None = None, stop_event: threading.Event | None = None) -> None:
    poll = poll_seconds or settings.poll_seconds
    stop_event = stop_event or threading.Event()
    print(f"[watch] checking inbox every {poll}s (Ctrl+C to stop)", flush=True)
    while not stop_event.is_set():
        try:
            run_once(verbose=True)
        except Exception as err:
            print(f"[watch] run failed: {err}", flush=True)
            traceback.print_exc()
        stop_event.wait(poll)
