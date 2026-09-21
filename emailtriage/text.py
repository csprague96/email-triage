"""HTML to text, quoted-reply stripping, and signature removal. Standard library only."""

from __future__ import annotations

import html as htmllib
import re
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table"}
_SKIP_TAGS = {"script", "style", "head", "title", "meta"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0
        self._in_li = False

    def handle_starttag(self, tag, attrs):
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag == "li":
            self.parts.append("\n- ")
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in _SKIP_TAGS:
            self._skip = max(0, self._skip - 1)
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    if not html:
        return ""
    parser = _TextExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html)
    text = "".join(parser.parts)
    text = htmllib.unescape(text)
    return normalize_whitespace(text)


def normalize_whitespace(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n").replace("\xa0", " ").replace("​", "")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"^-\s*$", "", text, flags=re.M)  # empty list items from nested <ul><li> wrappers
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# Markers that begin a quoted earlier message in Outlook / Gmail style threads.
_QUOTE_PATTERNS = [
    re.compile(r"^-{2,}\s*Original (Message|Appointment)\s*-{2,}\s*$", re.I | re.M),
    re.compile(r"^_{10,}\s*$", re.M),
    re.compile(r"^From:\s.+\n(?:Sent|Date):\s.+", re.I | re.M),
    re.compile(r"^From:\s.+<[^>]+>\s*$", re.I | re.M),
    re.compile(r"^On .{5,120}? wrote:\s*$", re.I | re.M),
    re.compile(r"^Le .{5,120}? a écrit\s*:\s*$", re.I | re.M),
    re.compile(r"^\s*>\s?.*$", re.M),  # leading '>' quoted lines
]


def strip_quoted(text: str) -> str:
    """Keep only the newest message of a thread."""
    if not text:
        return ""
    cut = len(text)
    for pat in _QUOTE_PATTERNS:
        m = pat.search(text)
        if m and m.start() < cut:
            # ignore a '>' quote line if it's the very first line (unlikely a reply marker)
            if pat.pattern.startswith(r"^\s*>") and m.start() == 0:
                continue
            cut = m.start()
    return text[:cut].strip()


_SIGNOFF_RE = re.compile(
    r"^(thanks|thank you|many thanks|thanks so much|cheers|best|best regards|kind regards|regards|"
    r"warm regards|sincerely|talk soon|speak soon|have a great (day|weekend)|ty|thx)[\s,!.]*$",
    re.I,
)

_SIG_NOISE_RE = re.compile(
    r"(^\[EXTERNAL EMAIL\]$|^Get Outlook for (iOS|Android)$|^Sent from my (iPhone|iPad|Galaxy|Android)|"
    r"^(Phone|Mobile|Office|Email|Tel|Cell|Fax|Web|www\.):?\s|^https?://\S+$|^\S+@\S+\.\S+$)",
    re.I,
)


def strip_signature(text: str, owner_name: str = "", signature_lines: list[str] | None = None) -> str:
    """Remove the trailing signature block. Uses a learned signature when provided,
    otherwise trims from a known sign-off / name line downward."""
    if not text:
        return ""
    lines = text.split("\n")

    if signature_lines:
        first_sig = signature_lines[0].strip().lower()
        for i, line in enumerate(lines):
            if line.strip().lower() == first_sig and i > 0:
                lines = lines[:i]
                break

    # Trim from the owner's name line if it appears near the end.
    if owner_name:
        name_l = owner_name.strip().lower()
        for i in range(len(lines) - 1, max(-1, len(lines) - 12), -1):
            if lines[i].strip().lower() == name_l:
                lines = lines[:i]
                break

    # Drop trailing noise lines (contact details, disclaimers, mobile footers).
    while lines and (not lines[-1].strip() or _SIG_NOISE_RE.search(lines[-1].strip())):
        lines.pop()

    return "\n".join(lines).strip()


def body_only(text: str) -> str:
    """Body without greeting or sign-off, for style statistics."""
    lines = [l for l in text.split("\n") if l.strip()]
    if lines and re.match(r"^(hi|hello|hey|dear|good (morning|afternoon|evening)|morning|afternoon)\b", lines[0], re.I):
        lines = lines[1:]
    while lines and _SIGNOFF_RE.match(lines[-1].strip()):
        lines.pop()
    return "\n".join(lines).strip()


def first_greeting(text: str) -> str | None:
    for line in text.split("\n"):
        s = line.strip()
        if s:
            m = re.match(r"^(hi|hello|hey|dear|good (?:morning|afternoon|evening)|morning|afternoon|team|all)\b[^\n]{0,40}$", s, re.I)
            return s if m else None
    return None


def last_signoff(text: str) -> str | None:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in reversed(lines[-4:]):
        if _SIGNOFF_RE.match(line):
            return line
    return None


def truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return text[: limit - 20].rstrip() + "\n[... truncated ...]"


def domain_of(email: str) -> str:
    return email.rsplit("@", 1)[-1].lower() if "@" in email else ""
