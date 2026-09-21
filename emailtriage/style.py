"""Learn a writing-style profile from the mailbox owner's Sent Items.

Produces config/style.json: greetings, sign-offs, typical length, habits, a
detected signature block, and a set of representative excerpts used as
few-shot samples when drafting. Everything stays on the local machine.
"""

from __future__ import annotations

import re
import statistics
from collections import Counter
from datetime import datetime, timezone

from .config import load_style, save_style
from .mail import get_backend
from .text import body_only, first_greeting, last_signoff, strip_signature

MIN_WORDS, MAX_WORDS = 8, 700
_SIGNOFF_WORD = re.compile(r"^(thanks|thank you|many thanks|cheers|best|regards|kind regards|best regards)[\s,!.]*$", re.I)
_GREETING_RE = re.compile(r"^(hi|hello|hey|dear|good morning|good afternoon|good evening|morning|afternoon)\b\s*(.*?)([,!:]?)\s*$", re.I)


def _normalise_greeting(greeting: str) -> str:
    """'Hi Stacy,' -> 'Hi {name},' ; 'Hi Stacy, Kayla,' -> 'Hi {names},' ; 'Hi team,' stays."""
    m = _GREETING_RE.match(greeting.strip())
    if not m:
        return greeting.strip()
    word, rest, punct = m.group(1), m.group(2).strip().rstrip(","), m.group(3)
    if not rest:
        return f"{word}{punct}"
    if rest.lower() in {"team", "all", "both", "everyone", "there"}:
        return f"{word} {rest}{punct}"
    names = [n for n in re.split(r",|\band\b|&", rest) if n.strip()]
    return f"{word} " + ("{name}" if len(names) == 1 else "{names}") + punct
SKIP_SUBJECT_PREFIXES = ("accepted:", "declined:", "tentative:", "canceled:", "cancelled:", "automatic reply", "fw:", "fwd:")


def _detect_signature(texts: list[str], owner_name: str) -> list[str]:
    """Find lines that recur at the end of many emails (the signature block)."""
    counter: Counter[str] = Counter()
    for t in texts:
        tail = [l.strip() for l in t.split("\n") if l.strip()][-8:]
        for line in set(tail):
            counter[line] += 1
    n = max(1, len(texts))
    common = [
        line for line, c in counter.items()
        if c / n >= 0.25 and 3 <= len(line) < 80 and re.search(r"[A-Za-z]{2}", line) and not _SIGNOFF_WORD.match(line)
    ]
    if not common:
        return []
    # order as they appear in a typical email
    for t in texts:
        tail = [l.strip() for l in t.split("\n") if l.strip()][-8:]
        ordered = [l for l in tail if l in common]
        if ordered:
            # start the signature at the owner's name if present
            if owner_name:
                for i, l in enumerate(ordered):
                    if l.lower() == owner_name.lower():
                        return ordered[i:]
            return ordered
    return common


def learn(limit: int = 300) -> dict:
    backend = get_backend()
    owner_name, owner_email = backend.owner()
    raw: list[str] = []
    subjects: list[str] = []
    for em in backend.sent_items(limit=limit):
        subj = (em.subject or "").strip().lower()
        if subj.startswith(SKIP_SUBJECT_PREFIXES) and not subj.startswith(("fw:", "fwd:")):
            continue
        if not em.body_text.strip():
            continue
        if "microsoft teams meeting" in em.body_text.lower() and len(em.body_text) < 1200:
            continue
        raw.append(em.body_text)
        subjects.append(em.subject)

    signature = _detect_signature(raw, owner_name)
    cleaned: list[str] = []
    for t in raw:
        c = strip_signature(t, owner_name=owner_name, signature_lines=signature)
        words = len(c.split())
        alpha_ratio = sum(ch.isalpha() for ch in c) / max(1, len(c))
        if MIN_WORDS <= words <= MAX_WORDS and alpha_ratio >= 0.5:
            cleaned.append(c)

    greetings = Counter()
    signoffs = Counter()
    lengths = []
    exclam = 0
    questions = 0
    bullets = 0
    contractions = 0
    total_sentences = 0
    dash_usage = 0
    for c in cleaned:
        g = first_greeting(c)
        if g:
            # normalise "Hi Stacy," -> "Hi {name},"
            greetings[_normalise_greeting(g)] += 1
        s = last_signoff(c)
        if s:
            signoffs[s] += 1
        core = body_only(c)
        w = len(core.split())
        lengths.append(w)
        exclam += core.count("!")
        questions += core.count("?")
        bullets += len(re.findall(r"^\s*[-•*·]\s", core, re.M))
        contractions += len(re.findall(r"\b\w+'(?:s|t|re|ve|ll|d|m)\b", core, re.I))
        dash_usage += core.count(" – ") + core.count(" - ")
        total_sentences += max(1, len(re.findall(r"[.!?](\s|$)", core)))

    n = max(1, len(cleaned))
    median_words = int(statistics.median(lengths)) if lengths else 60

    # Representative samples: spread across lengths, prefer ones with a greeting and a sign-off.
    scored = sorted(cleaned, key=lambda c: len(c.split()))
    samples: list[str] = []
    if scored:
        buckets = [scored[i * len(scored) // 10:(i + 1) * len(scored) // 10] for i in range(10)]
        for b in buckets:
            if not b:
                continue
            best = max(b, key=lambda c: (bool(first_greeting(c)), bool(last_signoff(c)), -abs(len(c.split()) - median_words)))
            if best not in samples:
                samples.append(best)
        samples = samples[:12]

    profile = {
        "learned_at": datetime.now(timezone.utc).isoformat(),
        "owner_name": owner_name,
        "owner_email": owner_email,
        "emails_analysed": len(cleaned),
        "signature_lines": signature,
        "greetings": [g for g, _ in greetings.most_common(4)],
        "signoffs": [s for s, _ in signoffs.most_common(4)],
        "median_words": median_words,
        "habits": {
            "exclamations_per_email": round(exclam / n, 2),
            "questions_per_email": round(questions / n, 2),
            "bullets_per_email": round(bullets / n, 2),
            "contractions_per_sentence": round(contractions / max(1, total_sentences), 2),
            "dash_asides_per_email": round(dash_usage / n, 2),
        },
        "samples": samples,
        "extra_instructions": (load_style() or {}).get("extra_instructions", ""),
    }
    save_style(profile)
    return profile


def describe(profile: dict) -> str:
    """Turn the profile into prose instructions for the drafting model."""
    h = profile.get("habits", {})
    lines = [
        f"Typical greeting: {', '.join(profile.get('greetings') or ['Hi {name},'])}.",
        f"Typical sign-off: {', '.join(profile.get('signoffs') or ['Thanks,'])} (the signature block is added automatically, never write a name or title after the sign-off).",
        f"Typical length: about {profile.get('median_words', 60)} words. Short replies are normal.",
    ]
    if h.get("exclamations_per_email", 0) >= 0.5:
        lines.append("Uses an exclamation mark now and then, for warmth, not hype.")
    else:
        lines.append("Rarely uses exclamation marks.")
    if h.get("contractions_per_sentence", 0) >= 0.2:
        lines.append("Uses contractions (I'm, we're, that's).")
    if h.get("bullets_per_email", 0) >= 0.5:
        lines.append("Answers multi-part questions with short bullet points under each question.")
    if h.get("dash_asides_per_email", 0) >= 0.4:
        lines.append("Sometimes joins a short aside with a spaced hyphen or en dash, never an em dash.")
    if profile.get("extra_instructions"):
        lines.append(profile["extra_instructions"])
    return "\n".join(lines)
