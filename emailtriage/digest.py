"""Daily digest: what landed, what is urgent or high, which drafts are waiting. Posted to Slack.

Slack setup (once per person or per channel): create an Incoming Webhook at
https://api.slack.com/messaging/webhooks and put the URL in SLACK_WEBHOOK_URL.
DIGEST_TIME (local, HH:MM) says when the watcher posts it; DIGEST_WEEKDAYS_ONLY skips weekends.
`python -m emailtriage digest` prints it; `--send` posts it now.

Only subjects, sender names, and tags go to Slack, never message bodies.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import httpx

from . import store
from .config import settings

MAX_ITEMS = 12


def build(hours: float | None = None) -> dict:
    hours = hours or settings.digest_lookback_hours
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    since_iso = since.isoformat()
    urgent = store.emails_since(since_iso, priorities=["urgent"], limit=MAX_ITEMS)
    high = store.emails_since(since_iso, priorities=["high"], limit=MAX_ITEMS)
    review = store.emails_since(since_iso, review_only=True, limit=MAX_ITEMS)
    drafts = store.list_drafts(status="ready", limit=MAX_ITEMS)
    counts = store.counts_since(since_iso)
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "since": since_iso,
        "hours": hours,
        "counts": counts,
        "urgent": urgent,
        "high": high,
        "needs_review": review,
        "drafts": drafts,
    }


def _item_line(e: dict, mrkdwn: bool = True) -> str:
    who = e.get("sender_name") or e.get("sender_email") or "unknown sender"
    subj = (e.get("subject") or "(no subject)").strip()
    tags = ", ".join(e.get("tags") or [])
    tag_part = f"  _{tags}_" if (tags and mrkdwn) else (f"  [{tags}]" if tags else "")
    if mrkdwn:
        return f"• *{who}* · {subj}{tag_part}"
    return f"- {who} | {subj}{tag_part}"


def render_text(d: dict) -> str:
    c = d["counts"]
    total = sum(c.values())
    lines = [f"Email digest, last {int(d['hours'])}h: {total} emails triaged"]
    lines.append("  " + ", ".join(f"{k} {c.get(k, 0)}" for k in ("urgent", "high", "medium", "low", "junk")))
    for label, key in (("URGENT", "urgent"), ("HIGH", "high")):
        if d[key]:
            lines.append(f"\n{label} ({len(d[key])})")
            lines += [_item_line(e, mrkdwn=False) for e in d[key]]
    if d["drafts"]:
        lines.append(f"\nDRAFTS WAITING FOR REVIEW ({len(d['drafts'])})")
        lines += [_item_line(e, mrkdwn=False) for e in d["drafts"]]
    if d["needs_review"]:
        lines.append(f"\nLOW-CONFIDENCE, WORTH A LOOK ({len(d['needs_review'])})")
        lines += [_item_line(e, mrkdwn=False) for e in d["needs_review"]]
    if not (d["urgent"] or d["high"] or d["drafts"] or d["needs_review"]):
        lines.append("\nNothing needs you. Nice.")
    return "\n".join(lines)


def render_slack(d: dict) -> dict:
    c = d["counts"]
    total = sum(c.values())
    day = datetime.now().strftime("%A %d %b")
    blocks: list[dict] = [
        {"type": "header", "text": {"type": "plain_text", "text": f"Inbox digest, {day}"}},
        {"type": "section", "text": {"type": "mrkdwn", "text":
            f"*{total}* emails triaged in the last {int(d['hours'])}h: "
            f"urgent *{c.get('urgent', 0)}*, high *{c.get('high', 0)}*, medium {c.get('medium', 0)}, low {c.get('low', 0)}, junk {c.get('junk', 0)}"}},
    ]

    def section(title: str, items: list[dict]) -> None:
        if not items:
            return
        text = f"*{title}* ({len(items)})\n" + "\n".join(_item_line(e) for e in items)
        blocks.append({"type": "divider"})
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}})

    section("Urgent", d["urgent"])
    section("High", d["high"])
    section("Drafts waiting in Outlook", d["drafts"])
    section("Low confidence, worth a look", d["needs_review"])
    if not (d["urgent"] or d["high"] or d["drafts"] or d["needs_review"]):
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": "Nothing needs you. Nice."}})
    blocks.append({"type": "context", "elements": [{"type": "mrkdwn", "text": "Sent by Email Triage. Open the dashboard on your PC for details and drafts."}]})
    return {"text": f"Inbox digest: {c.get('urgent', 0)} urgent, {c.get('high', 0)} high", "blocks": blocks}


def send_slack(payload: dict, webhook: str | None = None) -> None:
    url = webhook or settings.slack_webhook_url
    if not url:
        raise RuntimeError("SLACK_WEBHOOK_URL is not set")
    resp = httpx.post(url, json=payload, timeout=20)
    if resp.status_code >= 300:
        raise RuntimeError(f"Slack webhook returned {resp.status_code}: {resp.text[:200]}")


def send_now(hours: float | None = None) -> dict:
    d = build(hours)
    send_slack(render_slack(d))
    store.set_meta("last_digest_at", datetime.now(timezone.utc).isoformat())
    store.set_meta("last_digest_date", datetime.now().strftime("%Y-%m-%d"))
    return d


def due_now(now: datetime | None = None) -> bool:
    """True when the scheduled digest for today has not been sent and the time has passed."""
    if not settings.digest_enabled:
        return False
    now = now or datetime.now()
    if settings.digest_weekdays_only and now.weekday() >= 5:
        return False
    try:
        hh, mm = (int(x) for x in settings.digest_time.split(":"))
    except ValueError:
        return False
    if (now.hour, now.minute) < (hh, mm):
        return False
    return store.get_meta("last_digest_date") != now.strftime("%Y-%m-%d")
