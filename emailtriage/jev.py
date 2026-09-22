"""Priority + tag assessment with Jev (TypeSafe System One model).

One request per email. The state is a JSON object describing the email; the
questions are: one Choice for priority, one Score for time sensitivity, a Noul
per signal from priority.json, and a Noul per tag from tags.json. Jev answers all
of them in parallel, so adding tags costs almost nothing.
"""

from __future__ import annotations

import threading

from typesafe_sdk import Choice, Noul, Score, TypeSafeClient, TypeSafeAPIError

from . import store
from .config import PRIORITY_ORDER, load_priority_config, load_tags_config, settings
from .models import Assessment, Email
from .text import domain_of, truncate

STATE_BODY_LIMIT = 6000  # characters of the newest message
STATE_THREAD_LIMIT = 2500  # characters of earlier quoted thread for context

_client: TypeSafeClient | None = None
_client_lock = threading.Lock()


def client() -> TypeSafeClient:
    global _client
    with _client_lock:
        if _client is None:
            if not settings.typesafe_api_key:
                raise RuntimeError("TYPESAFE_API_KEY is not set in .env")
            _client = TypeSafeClient(api_key=settings.typesafe_api_key, model=settings.typesafe_model, timeout=30)
        return _client


def reset_client() -> None:
    """Drop the cached client after the API key or model changed."""
    global _client
    with _client_lock:
        _client = None


def build_state(email: Email, owner_email: str, owner_name: str) -> dict:
    owner_domain = domain_of(owner_email)
    sender_domain = domain_of(email.sender_email)
    thread_rest = ""
    if email.body_full and len(email.body_full) > len(email.body_text) + 40:
        thread_rest = truncate(email.body_full[len(email.body_text):].strip(), STATE_THREAD_LIMIT)
    state = {
        "recipient": {"name": owner_name, "email": owner_email, "role": "the mailbox owner being triaged"},
        "email": {
            "from_name": email.sender_name,
            "from_email": email.sender_email,
            "sender_is_colleague": bool(owner_domain) and sender_domain == owner_domain,
            "recipient_in_to": any(owner_email.lower() == t.lower() for t in email.to),
            "recipient_only_cc": any(owner_email.lower() == c.lower() for c in email.cc)
            and not any(owner_email.lower() == t.lower() for t in email.to),
            "to_count": len(email.to),
            "cc_count": len(email.cc),
            "subject": email.subject,
            "received": email.received.isoformat(),
            "has_attachments": email.has_attachments,
            "is_reply_or_forward": email.subject.lower().startswith(("re:", "fw:", "fwd:")),
            "newest_message": truncate(email.body_text, STATE_BODY_LIMIT),
        },
    }
    if thread_rest:
        state["email"]["earlier_thread_excerpt"] = thread_rest
    corrections = store.recent_corrections(limit=6)
    if corrections:
        state["examples_of_past_corrections"] = [
            {"subject": c["subject"], "from_email": c["sender_email"], "snippet": c["snippet"][:200],
             "correct_priority": c["to_priority"]}
            for c in corrections
        ]
    return state


def build_questions(pconf: dict, tconf: dict) -> dict:
    levels = pconf["levels"]
    questions: dict = {
        "priority": Choice(
            instructions=(
                "How should `email` be prioritised for `recipient`? Judge from the recipient's point of view: "
                "what they must do and how soon. Consider `examples_of_past_corrections` if present."
            ),
            criteria={lvl: levels[lvl] for lvl in PRIORITY_ORDER},
        ),
        "time_sensitivity": Score(
            instructions="How time-sensitive is `email.newest_message` for `recipient`?",
            criteria=list(pconf["time_sensitivity_levels"]),
        ),
    }
    for key, question in pconf["signals"].items():
        if key.startswith("_"):
            continue
        questions[f"signal:{key}"] = Noul(instructions=question)
    for tag in tconf["tags"]:
        questions[f"tag:{tag['name']}"] = Noul(
            instructions=f"Does the tag '{tag['name']}' apply to `email`? {tag['description']}",
            criteria={"true": f"The tag '{tag['name']}' applies.", "false": "It does not apply."},
        )
    return questions


def _apply_rules(jev_priority: str, confidence: float, probs: dict, signals: dict, time_sens: float, pconf: dict) -> tuple[str, str, bool]:
    """Confidence-gated adjustments. Returns (priority, explanation, needs_review)."""
    th = pconf.get("thresholds", {})
    min_conf = float(th.get("min_confidence", 0.5))
    urgent_conf = float(th.get("urgent_needs_confidence", 0.6))
    automated = signals.get("automated", 0.0)
    needs_reply = signals.get("needs_reply", 0.0)
    action = signals.get("action_requested", 0.0)
    escalation = signals.get("escalation", 0.0)
    only_cc = signals.get("recipient_only_copied", 0.0)

    priority = jev_priority
    note = ""
    review = False

    if confidence < min_conf:
        review = True
        # Rule-based fallback from the individual signals.
        if automated >= 0.75 and needs_reply < 0.3:
            fallback = "low"
        elif (needs_reply >= 0.6 or action >= 0.6) and (time_sens >= 2.3 or escalation >= 0.7):
            fallback = "urgent"
        elif needs_reply >= 0.6 or action >= 0.6:
            fallback = "high" if time_sens >= 1.5 else "medium"
        elif only_cc >= 0.7:
            fallback = "low"
        else:
            # pick the higher-probability neighbour among the top two options
            top2 = sorted(probs.items(), key=lambda kv: kv[1], reverse=True)[:2]
            fallback = max(top2, key=lambda kv: PRIORITY_ORDER.index(kv[0]))[0] if top2 else jev_priority
        if fallback != jev_priority:
            note = f"Jev picked '{jev_priority}' at low confidence ({confidence:.2f}); signals suggest '{fallback}'."
            priority = fallback
        else:
            note = f"Low confidence ({confidence:.2f}); kept '{jev_priority}'. Worth a look."

    if priority == "urgent" and confidence < urgent_conf and not (escalation >= 0.7 or time_sens >= 2.5):
        note = (note + " " if note else "") + f"Demoted urgent to high: confidence {confidence:.2f} and no strong urgency signals."
        priority = "high"
        review = True

    if priority in ("high", "urgent") and automated >= 0.85 and needs_reply < 0.25 and escalation < 0.5:
        note = (note + " " if note else "") + "Automated notification with no reply expected; capped at medium."
        priority = "medium"
        review = True

    return priority, note.strip(), review


def assess(email: Email, owner_email: str, owner_name: str) -> Assessment:
    pconf = load_priority_config()
    tconf = load_tags_config()
    state = build_state(email, owner_email, owner_name)
    questions = build_questions(pconf, tconf)
    try:
        result = client().system_one(state=state, questions=questions)
    except TypeSafeAPIError as err:
        raise RuntimeError(f"Jev request failed ({err.status}): {err}") from err

    choice = result.choices["priority"]
    time_sens = result.scores["time_sensitivity"].score
    signals = {name.split(":", 1)[1]: ans.noul for name, ans in result.nouls.items() if name.startswith("signal:")}
    default_th = float(tconf.get("default_threshold", 0.6))
    tag_scores: dict[str, float] = {}
    tags: list[str] = []
    for tag in tconf["tags"]:
        ans = result.nouls.get(f"tag:{tag['name']}")
        if ans is None:
            continue
        tag_scores[tag["name"]] = round(ans.noul, 3)
        if ans.noul >= float(tag.get("threshold", default_th)):
            tags.append(tag["name"])

    priority, note, review = _apply_rules(choice.choice, choice.confidence, choice.probabilities, signals, time_sens, pconf)
    return Assessment(
        priority=priority,
        confidence=round(choice.confidence, 3),
        probabilities={k: round(v, 3) for k, v in choice.probabilities.items()},
        signals={k: round(v, 3) for k, v in signals.items()},
        time_sensitivity=round(time_sens, 2),
        tags=tags,
        tag_scores=tag_scores,
        jev_priority=choice.choice,
        adjusted_by_rule=note,
        needs_review=review,
        model=result.model,
        input_tokens=result.usage.input_tokens or 0,
    )


# ---- draft verification ---------------------------------------------------

DRAFT_CHECKS = {
    "sounds_like_ai": Noul(
        instructions=(
            "Does `draft` read like generic AI-generated business text rather than a quick note a real person typed? "
            "Signs: stock phrases (I hope this finds you well, I appreciate your patience, please don't hesitate), "
            "tidy rule-of-three lists, 'not just X but Y' framing, em dashes, over-formal hedging, restating the sender's message back to them."
        ),
        criteria={"true": "Reads like an AI template.", "false": "Reads like something the writer would actually send."},
    ),
    "answers_the_ask": Noul(
        instructions="Does `draft` respond to what `incoming_email` actually asks or needs, rather than talking around it?",
    ),
    "invents_facts": Noul(
        instructions=(
            "Does `draft` state specific facts, dates, decisions, or commitments that are not supported by `incoming_email` "
            "or `known_context`? Placeholders in square brackets do not count as invented facts."
        ),
    ),
    "matches_voice": Noul(
        instructions="Is the tone, length, and phrasing of `draft` consistent with the `writer_samples`?",
    ),
    "quality": Score(
        instructions="Overall, how ready is `draft` to send after a quick human read?",
        criteria=["Needs a rewrite", "Needs real edits", "Minor tweaks only", "Ready to send"],
    ),
}


def check_draft(draft: str, email: Email, writer_samples: list[str], known_context: str = "") -> dict[str, float]:
    state = {
        "incoming_email": {"from": email.sender_name, "subject": email.subject, "body": truncate(email.body_text, 4000)},
        "draft": draft,
        "writer_samples": writer_samples[:6],
        "known_context": known_context or "none",
    }
    result = client().system_one(state=state, questions=DRAFT_CHECKS)
    out = {name: round(ans.noul, 3) for name, ans in result.nouls.items()}
    out["quality"] = round(result.scores["quality"].score, 2)
    return out
