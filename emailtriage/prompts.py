"""Default prompt text for the drafter. Users can override these in config/drafting.json (Settings > Drafting)."""

from __future__ import annotations

# Distilled from Wikipedia's "Signs of AI writing" plus common email boilerplate.
DEFAULT_BANNED_PHRASES = [
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

# Placeholders filled in at draft time. {owner_name} is the mailbox owner, {style_brief} is the prose description of
# the learned writing style (including any extra instructions), {samples} are real sent emails, {banned_sample} is a
# short list from the banned phrases.
PROMPT_PLACEHOLDERS = {
    "owner_name": "The mailbox owner's display name.",
    "style_brief": "Prose description of the learned writing style, including your extra instructions.",
    "samples": "A handful of your real sent emails, used as voice references.",
    "banned_sample": "The first few entries from the banned phrase list.",
}

DEFAULT_SYSTEM_PROMPT = """You draft email replies on behalf of {owner_name}. The reply must read as if {owner_name} typed it quickly between meetings, not as if an assistant wrote it.

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

DEFAULT_REVISION_PROMPT = """Your previous draft was checked and needs another pass. Problems found:
{problems}

Rewrite the reply fixing those problems while keeping everything else. Output only the email body text."""
