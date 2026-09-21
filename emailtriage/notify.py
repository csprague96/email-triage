"""User notifications: Windows toast (winotify) with a console fallback."""

from __future__ import annotations

import sys

from .config import settings


def toast(title: str, message: str, url: str | None = None) -> None:
    print(f"[notify] {title}: {message}", flush=True)
    if not settings.toast_notifications or not sys.platform.startswith("win"):
        return
    try:
        from winotify import Notification, audio

        n = Notification(app_id="Email Triage", title=title, msg=message[:200], duration="long")
        n.set_audio(audio.Default, loop=False)
        if url:
            n.add_actions(label="Open dashboard", launch=url)
        n.show()
    except Exception as err:  # never let a notification failure break the pipeline
        print(f"[notify] toast failed: {err}", flush=True)
