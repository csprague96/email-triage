"""Fetch -> thread check -> assess -> tag -> (draft) -> notify. Used by the CLI, the watcher, and the dashboard."""

from __future__ import annotations

import threading
import time
import traceback
from datetime import datetime, timedelta, timezone

from . import digest, jev, shared_tags, store
from .config import load_style, settings
from .drafter import draft_reply, render_draft_html
from .mail import get_backend
from .models import Assessment, DraftResult, Email, ThreadInfo
from .notify import toast
from .text import domain_of

CATEGORY_PREFIX = "Priority: "
_run_lock = threading.Lock()


def _owner() -> tuple[str, str]:
    name, addr = get_backend().owner()
    return name or "Me", addr or ""


def _fmt(dt: datetime) -> str:
    return dt.astimezone().strftime("%d %b %H:%M")


# ---- thread awareness ---------------------------------------------------------

def analyze_thread(email: Email, owner_email: str) -> ThreadInfo:
    """Look at the whole conversation to see whether this email still needs anything."""
    info = ThreadInfo()
    try:
        msgs = get_backend().thread_messages(email)
    except Exception as err:
        info.note = f"Thread lookup failed: {err}"
        return info
    if not msgs:
        return info
    info.messages = len(msgs)
    owner_domain = domain_of(owner_email)
    after = [m for m in msgs if m.received > email.received + timedelta(seconds=30)]
    newest = msgs[-1]
    if newest.received > email.received + timedelta(seconds=30):
        info.is_latest = False
        info.newer_from = "you" if newest.from_owner else (newest.sender_name or newest.sender_email)
    owner_after = [m for m in after if m.from_owner]
    if owner_after:
        info.owner_replied_after = owner_after[0].received.isoformat()
    colleague_after = [m for m in after if not m.from_owner and owner_domain and domain_of(m.sender_email) == owner_domain]
    if colleague_after:
        m = colleague_after[0]
        info.colleague_replied_after = f"{m.sender_name or m.sender_email} ({m.received.isoformat()})"

    notes = []
    if info.owner_replied_after:
        notes.append(f"You replied on this thread at {_fmt(owner_after[0].received)}.")
    if colleague_after:
        notes.append(f"{colleague_after[0].sender_name or 'A colleague'} replied at {_fmt(colleague_after[0].received)}.")
    if not info.is_latest and not notes:
        notes.append(f"Thread has moved on: newest message is from {info.newer_from}.")
    info.note = " ".join(notes)
    return info


def apply_thread_rules(assessment: Assessment, email: Email, owner_email: str, thread: ThreadInfo) -> bool:
    """Adjust priority for thread state. Returns True when a draft should still be considered."""
    assessment.thread = thread
    owner_in_to = any(owner_email.lower() == t.lower() for t in email.to) if owner_email else True
    if thread.owner_replied_after:
        if assessment.priority in ("medium", "high", "urgent"):
            assessment.adjusted_by_rule = (assessment.adjusted_by_rule + " " if assessment.adjusted_by_rule else "") + \
                "Dropped to low: you already replied on this thread."
            assessment.priority = "low"
        return False
    if thread.colleague_replied_after and not owner_in_to:
        assessment.adjusted_by_rule = (assessment.adjusted_by_rule + " " if assessment.adjusted_by_rule else "") + \
            "No draft: a colleague replied and you were only copied."
        if assessment.priority in ("high", "urgent"):
            assessment.priority = "medium"
        return False
    if not thread.is_latest:
        assessment.adjusted_by_rule = (assessment.adjusted_by_rule + " " if assessment.adjusted_by_rule else "") + \
            f"No draft: a newer message from {thread.newer_from} is on this thread."
        return False
    return True


def supersede_older_drafts(email: Email) -> int:
    """A new message on a thread makes any unsent draft for an older message stale."""
    stale = store.ready_drafts_in_conversation(email.conversation_id, exclude_email_id=email.id)
    for d in stale:
        store.set_draft_status(d["id"], "superseded")
    return len(stale)


def reconcile_drafts() -> int:
    """Mark ready drafts done when the owner has since replied on the thread. Called by the watcher."""
    backend = get_backend()
    _, owner_email = _owner()
    done = 0
    for d in store.list_ready_drafts_with_conversation(limit=50):
        em = backend.get(d["email_id"])
        if not em:
            continue
        try:
            created = datetime.fromisoformat(d["created_at"])
        except Exception:
            created = em.received
        try:
            msgs = backend.thread_messages(em)
        except Exception:
            continue
        if any(m.from_owner and m.received > created for m in msgs):
            store.set_draft_status(d["id"], "done")
            done += 1
    return done


# ---- core steps ------------------------------------------------------------------

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
        assessment = Assessment(priority="low", confidence=1.0, probabilities={"low": 1.0}, signals={},
                                time_sensitivity=0.0, tags=[], tag_scores={}, jev_priority="low",
                                adjusted_by_rule="Sent by the mailbox owner; skipped.", needs_review=False)
        draft_ok = False
    else:
        assessment = jev.assess(email, owner_email, owner_name)
        thread = analyze_thread(email, owner_email)
        draft_ok = apply_thread_rules(assessment, email, owner_email, thread)

    store.save_email(email)
    store.save_assessment(email.id, assessment)
    apply_categories(email.id, assessment)
    superseded = supersede_older_drafts(email)
    if superseded:
        print(f"[thread] {superseded} older draft(s) superseded by '{email.subject[:50]}'", flush=True)

    if draft is None:
        should_draft = (
            draft_ok
            and settings.drafting_enabled
            and assessment.priority in settings.draft_for_priorities
            and assessment.signals.get("automated", 0) < 0.7
        )
    else:
        should_draft = draft
    if should_draft:
        try:
            make_draft(email)
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
        # Oldest first so a reply on a thread supersedes the draft for the message before it.
        batch = list(backend.inbox_since(since, limit=limit))
        batch.sort(key=lambda e: e.received)
        for email in batch:
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
                thread = f"  {{{a.thread.note}}}" if a.thread and a.thread.note else ""
                print(f"  {a.priority:<7} {a.confidence:.2f}{flag}  {email.sender_name[:22]:<22} {email.subject[:60]}{tags}{thread}", flush=True)
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


def housekeeping() -> None:
    """Periodic chores that ride along with the watcher: shared tags, draft reconciliation, digest."""
    try:
        res = shared_tags.sync(force=False)
        if res.get("error"):
            print(f"[shared-tags] {res['error']}", flush=True)
    except Exception as err:
        print(f"[shared-tags] {err}", flush=True)
    try:
        done = reconcile_drafts()
        if done:
            print(f"[thread] marked {done} draft(s) done because you replied", flush=True)
    except Exception as err:
        print(f"[thread] reconcile failed: {err}", flush=True)
    try:
        if digest.due_now():
            digest.send_now()
            print("[digest] posted to Slack", flush=True)
    except Exception as err:
        print(f"[digest] failed: {err}", flush=True)


def watch(poll_seconds: int | None = None, stop_event: threading.Event | None = None) -> None:
    stop_event = stop_event or threading.Event()
    print(f"[watch] checking inbox every {poll_seconds or settings.poll_seconds}s (Ctrl+C to stop)", flush=True)
    while not stop_event.is_set():
        try:
            run_once(verbose=True)
        except Exception as err:
            print(f"[watch] run failed: {err}", flush=True)
            if "sign in" not in str(err).lower():
                traceback.print_exc()
        housekeeping()
        # Re-read each loop so a change in Settings takes effect without a restart.
        stop_event.wait(poll_seconds or settings.poll_seconds)
