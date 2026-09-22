"""Command line entry point.

  python -m emailtriage check          verify Outlook/Graph, Jev and OpenAI are reachable
  python -m emailtriage login          sign in to Microsoft 365 (MAIL_BACKEND=graph only)
  python -m emailtriage learn-style    build config/style.json from your Sent Items
  python -m emailtriage run [--hours N] [--limit N] [--force]   triage recent inbox mail once
  python -m emailtriage sample [--hours N]  dry run: print what Jev would decide, store nothing, no drafts
  python -m emailtriage digest [--send]  print today's digest, or post it to Slack
  python -m emailtriage sync-tags      refresh the shared tag set now
  python -m emailtriage watch          keep polling the inbox
  python -m emailtriage serve          dashboard + watcher (the normal way to run it)
"""

from __future__ import annotations

import argparse
import sys
import webbrowser
from datetime import datetime, timedelta, timezone


def cmd_check(_args) -> int:
    from .config import settings
    from .mail import get_backend

    ok = True
    print(f"Mail backend: {settings.mail_backend}")
    try:
        print("  " + get_backend().check())
    except Exception as err:
        ok = False
        print(f"  FAILED: {err}")
        if settings.mail_backend == "outlook":
            print("  Is classic Outlook installed and open? The 'new Outlook' app is not supported; set MAIL_BACKEND=graph instead.")

    print("Jev (TypeSafe):")
    if not settings.typesafe_api_key:
        ok = False
        print("  FAILED: TYPESAFE_API_KEY is empty in .env")
    else:
        try:
            from typesafe_sdk import Noul

            from .jev import client

            r = client().system_one(state="Server is down and customers cannot pay.", questions={"urgent": Noul(instructions="Is this urgent?")})
            print(f"  ok, model {r.model}, test noul={r.nouls['urgent'].noul:.2f}")
        except Exception as err:
            ok = False
            print(f"  FAILED: {err}")

    print("OpenAI (drafting):")
    if not settings.openai_api_key:
        print("  not configured; drafting disabled (set OPENAI_API_KEY to enable)")
    else:
        try:
            from openai import OpenAI

            OpenAI(api_key=settings.openai_api_key).models.retrieve(settings.openai_model)
            print(f"  ok, model {settings.openai_model}")
        except Exception as err:
            ok = False
            print(f"  FAILED: {err}")

    from .config import load_style

    prof = load_style()
    print("Writing style: " + (f"learned from {prof.get('emails_analysed')} sent emails" if prof else "not learned yet, run: python -m emailtriage learn-style"))
    print("\nAll good." if ok else "\nFix the items marked FAILED, then run check again.")
    return 0 if ok else 1


def cmd_login(_args) -> int:
    import time

    from .config import settings
    from .mail import get_backend

    if settings.mail_backend != "graph":
        print("MAIL_BACKEND is 'outlook': classic Outlook is used as-is, no sign-in needed.")
        return 0
    backend = get_backend()
    state = backend.auth_state()
    if state["signed_in"]:
        print(f"Already signed in as {state['account']}. Run `python -m emailtriage check` to test the connection.")
        return 0
    flow = backend.start_sign_in()
    print("\n" + flow["message"] + "\n", flush=True)
    try:
        webbrowser.open(flow["verification_uri"])
    except Exception:
        pass
    while backend.auth_state()["pending"]:
        time.sleep(2)
    state = backend.auth_state()
    if state["signed_in"]:
        print(f"Signed in as {state['account']}.")
        return 0
    print(f"Sign-in failed: {state['error']}")
    return 1


def cmd_learn_style(args) -> int:
    from . import style

    prof = style.learn(limit=args.limit)
    print(f"Analysed {prof['emails_analysed']} sent emails.")
    print(f"Greetings: {prof['greetings']}")
    print(f"Sign-offs: {prof['signoffs']}")
    print(f"Median length: {prof['median_words']} words")
    print(f"Signature detected: {prof['signature_lines'] or '(none)'}")
    print(f"Kept {len(prof['samples'])} sample emails as voice references in config/style.json")
    return 0


def _since(args) -> datetime | None:
    return datetime.now(timezone.utc) - timedelta(hours=args.hours) if args.hours else None


def cmd_run(args) -> int:
    from . import pipeline

    res = pipeline.run_once(since=_since(args), limit=args.limit, force=args.force)
    print(res)
    return 0


def cmd_sample(args) -> int:
    """Dry run: assess without storing, categorising, or drafting."""
    from . import jev
    from .mail import get_backend

    backend = get_backend()
    owner_name, owner_email = backend.owner()
    since = _since(args) or datetime.now(timezone.utc) - timedelta(hours=24)
    total_tokens = 0
    for em in backend.inbox_since(since, limit=args.limit):
        a = jev.assess(em, owner_email, owner_name)
        total_tokens += a.input_tokens
        flag = "*" if a.needs_review else " "
        tags = ", ".join(a.tags)
        print(f"{a.priority:<7}{flag} {a.confidence:.2f}  ts={a.time_sensitivity:.1f}  {em.sender_name[:20]:<20} {em.subject[:55]:<55} {tags}")
        if a.adjusted_by_rule:
            print(f"         note: {a.adjusted_by_rule}")
    print(f"\nJev input tokens used: {total_tokens} (about ${total_tokens * 0.042 / 1_000_000:.4f})")
    return 0


def cmd_digest(args) -> int:
    from . import digest

    if args.send:
        d = digest.send_now(args.hours)
        print(f"Posted to Slack: {d['counts']}")
    else:
        print(digest.render_text(digest.build(args.hours)))
    return 0


def cmd_sync_tags(_args) -> int:
    from . import shared_tags

    res = shared_tags.sync(force=True)
    print(res if res.get("enabled") else "SHARED_TAGS_SOURCE is not set in .env")
    return 0 if not res.get("error") else 1


def cmd_watch(args) -> int:
    from . import pipeline

    try:
        pipeline.watch(poll_seconds=args.poll)
    except KeyboardInterrupt:
        pass
    return 0


def _log_to_file_if_headless() -> None:
    """Under pythonw.exe (the background job) there is no console; write output to data/watcher.log."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    from .config import DATA_DIR, ensure_dirs

    ensure_dirs()
    log = DATA_DIR / "watcher.log"
    if log.exists() and log.stat().st_size > 5_000_000:  # keep the log from growing forever
        log.replace(log.with_suffix(".log.1"))

    class _Stream:
        def __init__(self):
            self.fh = open(log, "a", encoding="utf-8", buffering=1)

        def write(self, s):
            if s.strip():
                self.fh.write(datetime.now().strftime("%Y-%m-%d %H:%M:%S ") + s.rstrip("\n") + "\n")

        def flush(self):
            self.fh.flush()

        def isatty(self):
            return False

    sys.stdout = sys.stderr = _Stream()
    print(f"[serve] started headless, logging to {log}")


def cmd_serve(args) -> int:
    import uvicorn

    from . import server
    from .config import settings

    _log_to_file_if_headless()
    if not args.no_watch:
        server.start_watcher()
    url = f"http://127.0.0.1:{settings.port}"
    print(f"Dashboard: {url}")
    if not args.no_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    uvicorn.run(server.app, host="127.0.0.1", port=settings.port, log_level="warning", log_config=None)
    return 0


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows consoles default to cp1252; email text is not.
        try:
            if stream is not None:
                stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    p = argparse.ArgumentParser(prog="emailtriage", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check").set_defaults(fn=cmd_check)
    sub.add_parser("login").set_defaults(fn=cmd_login)
    ls = sub.add_parser("learn-style")
    ls.add_argument("--limit", type=int, default=300)
    ls.set_defaults(fn=cmd_learn_style)
    for name, fn in (("run", cmd_run), ("sample", cmd_sample)):
        sp = sub.add_parser(name)
        sp.add_argument("--hours", type=float, default=None)
        sp.add_argument("--limit", type=int, default=300)
        sp.add_argument("--force", action="store_true", help="re-assess emails already processed")
        sp.set_defaults(fn=fn)
    dg = sub.add_parser("digest")
    dg.add_argument("--hours", type=float, default=None)
    dg.add_argument("--send", action="store_true", help="post to Slack instead of printing")
    dg.set_defaults(fn=cmd_digest)
    sub.add_parser("sync-tags").set_defaults(fn=cmd_sync_tags)
    w = sub.add_parser("watch")
    w.add_argument("--poll", type=int, default=None)
    w.set_defaults(fn=cmd_watch)
    s = sub.add_parser("serve")
    s.add_argument("--no-watch", action="store_true")
    s.add_argument("--no-browser", action="store_true")
    s.set_defaults(fn=cmd_serve)
    args = p.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
