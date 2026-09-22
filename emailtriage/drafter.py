"""Write a reply draft in the owner's voice with an LLM, then have Jev check it.

Flow: build a style brief from style.json -> ask the model for a reply ->
regex lint for AI tells -> Jev verification (sounds_like_ai, answers_the_ask,
invents_facts, matches_voice, quality) -> one revision pass if it fails.
"""

from __future__ import annotations

import html as htmllib
import re

from . import jev
from .config import load_drafting, load_style, settings
from .models import DraftResult, Email
from .prompts import DEFAULT_BANNED_PHRASES, DEFAULT_REVISION_PROMPT, DEFAULT_SYSTEM_PROMPT
from .style import describe
from .text import truncate

# Kept for backwards compatibility; the live list comes from config/drafting.json (Settings > Drafting).
BANNED_PHRASES = DEFAULT_BANNED_PHRASES
SYSTEM_PROMPT = DEFAULT_SYSTEM_PROMPT
REVISION_PROMPT = DEFAULT_REVISION_PROMPT

BANNED_REGEX = [
    (re.compile(r"—"), "em dash"),
    (re.compile(r"\bnot (?:just|only) \w+[^.]{0,60}\bbut (?:also )?\b", re.I), "not X but Y framing"),
    (re.compile(r"^\s*(?:\*\*|#)", re.M), "markdown formatting"),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "emoji"),
]




def _lint(text: str, phrases: list[str] | None = None) -> list[str]:
    low = text.lower()
    hits = [p for p in (phrases if phrases is not None else load_drafting()["banned_phrases"]) if p in low]
    for rx, label in BANNED_REGEX:
        if rx.search(text):
            hits.append(label)
    return hits


def _openai_client():
    from openai import OpenAI

    if not settings.openai_api_key:
        raise RuntimeError("OPENAI_API_KEY is not set; drafting is disabled")
    return OpenAI(api_key=settings.openai_api_key)


def _complete(client, instructions: str, messages: list[dict]) -> str:
    """Call the Responses API. Falls back gracefully if the model rejects reasoning options."""
    kwargs = dict(model=settings.openai_model, instructions=instructions, input=messages)
    try:
        resp = client.responses.create(reasoning={"effort": "low"}, **kwargs)
    except Exception as first:
        msg = str(first).lower()
        if "reasoning" in msg or "unsupported" in msg or "invalid" in msg:
            resp = client.responses.create(**kwargs)
        else:
            raise
    text = getattr(resp, "output_text", None)
    if not text:
        # older SDK shapes
        parts = []
        for item in getattr(resp, "output", []) or []:
            for c in getattr(item, "content", []) or []:
                t = getattr(c, "text", None)
                if t:
                    parts.append(t)
        text = "\n".join(parts)
    return (text or "").strip()


def _clean_output(text: str) -> str:
    text = text.strip().strip("`").strip()
    text = re.sub(r"^(subject:.*\n)+", "", text, flags=re.I).strip()
    text = text.replace("—", " - ").replace("–", " - ").replace("  ", " ")
    return text


def build_instructions(owner_name: str) -> tuple[str, list[str], dict]:
    """The system prompt exactly as the drafting model receives it, plus the writer samples and drafting config."""
    conf = load_drafting()
    profile = load_style() or {}
    samples = profile.get("samples") or []
    style_brief = describe(profile) if profile else (
        "Short, friendly, direct. Opens with 'Hi {name},' and closes with 'Thanks,'. Uses contractions. Around 60 words."
    )
    sample_block = "\n\n---\n\n".join(truncate(s, 900) for s in samples[:8]) if samples else "(no samples learned yet; run `learn-style`)"
    instructions = conf["system_prompt"].format_map({
        "owner_name": owner_name or "the mailbox owner",
        "style_brief": style_brief,
        "samples": sample_block,
        "banned_sample": ", ".join(conf["banned_phrases"][:18]) or "(none)",
    })
    return instructions, samples, conf


def draft_reply(email: Email, owner_name: str, owner_email: str, context: str = "") -> DraftResult:
    instructions, samples, conf = build_instructions(owner_name)
    thread_note = ""
    if email.body_full and len(email.body_full) > len(email.body_text) + 40:
        thread_note = "\n\nEarlier messages in the thread (context only):\n" + truncate(email.body_full[len(email.body_text):], 3000)
    user_msg = (
        f"Incoming email to reply to.\nFrom: {email.sender_name} <{email.sender_email}>\nSubject: {email.subject}\n\n"
        f"{truncate(email.body_text, 6000)}{thread_note}"
    )
    if context:
        user_msg += f"\n\nThings {owner_name} knows that you may use: {context}"

    client = _openai_client()
    messages = [{"role": "user", "content": user_msg}]
    attempts = 0
    checks: dict[str, float] = {}
    notes: list[str] = []
    text = ""
    passed = False

    while attempts < 2:
        attempts += 1
        text = _clean_output(_complete(client, instructions, messages))
        problems: list[str] = []
        lint = _lint(text, conf["banned_phrases"])
        if lint:
            problems.append("Contains AI-sounding wording: " + ", ".join(lint[:6]))
        try:
            checks = jev.check_draft(text, email, samples, known_context=context)
        except Exception as err:  # Jev check is a safety net, not a hard dependency
            notes.append(f"Jev check skipped: {err}")
            checks = {}
        if checks:
            if checks.get("sounds_like_ai", 0) >= 0.6:
                problems.append("Reads like an AI template; make it plainer and more specific to this email.")
            if checks.get("answers_the_ask", 1) < 0.5:
                problems.append("Does not actually answer what the sender asked.")
            if checks.get("invents_facts", 0) >= 0.5:
                problems.append("States facts or commitments not supported by the email; use short [placeholders] instead.")
            if checks.get("matches_voice", 1) < 0.4:
                problems.append("Tone does not match the writer's samples.")
        if not problems:
            passed = True
            break
        notes.append(f"Attempt {attempts}: " + " | ".join(problems))
        messages = messages + [
            {"role": "assistant", "content": text},
            {"role": "user", "content": conf["revision_prompt"].format_map({"problems": "\n".join(f"- {p}" for p in problems)})},
        ]

    return DraftResult(body_text=text, attempts=attempts, checks=checks, passed=passed, notes="\n".join(notes))



def render_draft_html(body_text: str, signature_lines: list[str] | None = None) -> str:
    """Draft text (plus the learned signature block) as simple HTML for insertion above a quoted reply."""
    paras = []
    for p in body_text.strip().split("\n\n"):
        inner = "<br>".join(htmllib.escape(line) for line in p.split("\n"))
        paras.append(f'<p style="margin:0 0 12px 0">{inner}</p>')
    sig = ""
    if signature_lines:
        rendered = []
        for i, line in enumerate(signature_lines):
            line = re.sub(r"\s*<mailto:[^>]+>", "", line).strip()  # text-conversion artefact
            if not line:
                continue
            m = re.match(r"^(Email:\s*)(\S+@\S+)$", line, re.I)
            if m:
                rendered.append(f'{htmllib.escape(m.group(1))}<a href="mailto:{htmllib.escape(m.group(2))}">{htmllib.escape(m.group(2))}</a>')
            elif i == 0:
                rendered.append(f"<b>{htmllib.escape(line)}</b>")
            else:
                rendered.append(htmllib.escape(line))
        if rendered:
            sig = '<p style="margin:12px 0 0 0">' + "<br>".join(rendered) + "</p>"
    return (
        '<div id="emailtriage-draft" style="font-family:Calibri,Arial,sans-serif;font-size:11pt">'
        + "".join(paras) + sig + "</div>"
    )
