"""Write a reply draft in the owner's voice with an LLM, then have Jev check it.

Flow: build a style brief from style.json -> ask the model for a reply ->
regex lint for AI tells -> Jev verification (sounds_like_ai, answers_the_ask,
invents_facts, matches_voice, quality) -> one revision pass if it fails.
"""

from __future__ import annotations

import html as htmllib
import re

from . import jev
from .config import load_style, settings
from .models import DraftResult, Email
from .style import describe
from .text import truncate

# Distilled from Wikipedia's "Signs of AI writing" plus common email boilerplate.
BANNED_PHRASES = [
    "i hope this email finds you well", "i hope this finds you well", "i hope you are doing well", "i hope you're doing well",
    "i wanted to reach out", "i wanted to touch base", "just wanted to follow up", "i appreciate your patience",
    "thank you for your patience", "please don't hesitate", "please do not hesitate", "feel free to reach out",
    "i understand your frustration", "i completely understand", "rest assured", "i apologize for any inconvenience",
    "apologies for any inconvenience", "at your earliest convenience", "moving forward", "going forward",
    "to ensure", "in order to", "it's important to note", "it is important to note", "it's worth noting",
    "as a valued", "we value", "i'd be happy to", "i would be happy to", "happy to help", "absolutely",
    "great question", "thank you for bringing this to", "thank you for reaching out", "thanks for reaching out",
    "i'm reaching out", "circle back", "touch base", "leverage", "streamline", "seamless", "robust",
    "delve", "crucial", "pivotal", "landscape", "tapestry", "testament", "underscore", "highlights the",
    "showcase", "foster", "enhance", "align with", "vibrant", "meticulous", "navigate", "journey", "empower",
    "not just", "not only", "it's not about", "rather than simply", "additionally,", "furthermore,", "moreover,",
    "in conclusion", "to summarize", "in summary", "overall,", "ultimately,", "best regards", "warm regards", "kind regards",
]
BANNED_REGEX = [
    (re.compile(r"—"), "em dash"),
    (re.compile(r"\bnot (?:just|only) \w+[^.]{0,60}\bbut (?:also )?\b", re.I), "not X but Y framing"),
    (re.compile(r"^\s*(?:\*\*|#)", re.M), "markdown formatting"),
    (re.compile(r"[\U0001F300-\U0001FAFF☀-➿]"), "emoji"),
]

SYSTEM_PROMPT = """You draft email replies on behalf of {owner_name}. The reply must read as if {owner_name} typed it quickly between meetings, not as if an assistant wrote it.

How {owner_name} writes:
{style_brief}

Real examples of {owner_name}'s emails (match this register exactly):
{samples}

Hard rules:
- Answer only what the sender actually asked or needs. No recap of their message, no preamble about the email itself.
- Plain sentences. No headings, bold, markdown, emoji, or em dashes. Bullets only if the sender asked several distinct questions.
- Never invent facts, dates, numbers, decisions, or promises. If you would need information {owner_name} has not given you, write a short placeholder in square brackets, like [confirm date] or [add ticket link]. Keep placeholders rare and short.
- Do not use these phrases or their cousins: {banned_sample}.
- Do not open with thanks for reaching out, hope you are well, or an apology unless the thread really calls for one.
- Do not close with an offer of further help or a summary. A sign-off like the ones above is enough.
- Do not write a name, title, or signature after the sign-off. It is added automatically.
- Keep it to roughly the typical length. A one-line reply is fine when that is what a person would send.
- Match the sender's first-name form of address ("Hi Hutch," if they signed as Hutch).

Output only the email body text, starting with the greeting."""

REVISION_PROMPT = """Your previous draft was checked and needs another pass. Problems found:
{problems}

Rewrite the reply fixing those problems while keeping everything else. Output only the email body text."""


def _lint(text: str) -> list[str]:
    low = text.lower()
    hits = [p for p in BANNED_PHRASES if p in low]
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


def draft_reply(email: Email, owner_name: str, owner_email: str, context: str = "") -> DraftResult:
    profile = load_style() or {}
    samples = profile.get("samples") or []
    style_brief = describe(profile) if profile else (
        "Short, friendly, direct. Opens with 'Hi {name},' and closes with 'Thanks,'. Uses contractions. Around 60 words."
    )
    sample_block = "\n\n---\n\n".join(truncate(s, 900) for s in samples[:8]) if samples else "(no samples learned yet; run `learn-style`)"
    instructions = SYSTEM_PROMPT.format(
        owner_name=owner_name or "the mailbox owner",
        style_brief=style_brief,
        samples=sample_block,
        banned_sample=", ".join(BANNED_PHRASES[:18]),
    )
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
        lint = _lint(text)
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
            {"role": "user", "content": REVISION_PROMPT.format(problems="\n".join(f"- {p}" for p in problems))},
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
