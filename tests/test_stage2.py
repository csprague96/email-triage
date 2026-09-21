from datetime import datetime, timedelta, timezone

from emailtriage import digest
from emailtriage.models import Assessment, Email, ThreadInfo
from emailtriage.pipeline import apply_thread_rules
from emailtriage.shared_tags import _fetch


def _assessment(priority="high"):
    return Assessment(priority=priority, confidence=0.9, probabilities={priority: 0.9}, signals={},
                      time_sensitivity=2.0, tags=[], tag_scores={}, jev_priority=priority)


def _email(to=("me@example.com",), cc=()):
    return Email(id="x", subject="RE: thing", sender_name="Sam", sender_email="sam@partner.com", to=list(to), cc=list(cc),
                 received=datetime.now(timezone.utc), body_text="Can you confirm?")


def test_owner_reply_drops_priority_and_blocks_draft():
    a = _assessment("urgent")
    ok = apply_thread_rules(a, _email(), "me@example.com", ThreadInfo(messages=3, is_latest=False, owner_replied_after="2026-09-21T10:00:00+00:00"))
    assert ok is False and a.priority == "low" and "already replied" in a.adjusted_by_rule


def test_colleague_reply_when_only_cc_blocks_draft():
    a = _assessment("high")
    ok = apply_thread_rules(a, _email(to=("other@example.com",), cc=("me@example.com",)), "me@example.com",
                            ThreadInfo(messages=2, is_latest=False, colleague_replied_after="Andy (2026-09-21T10:00:00+00:00)"))
    assert ok is False and a.priority == "medium"


def test_colleague_reply_when_addressed_still_drafts_if_latest():
    a = _assessment("high")
    ok = apply_thread_rules(a, _email(), "me@example.com", ThreadInfo(messages=1, is_latest=True))
    assert ok is True and a.priority == "high"


def test_newer_message_blocks_draft_but_keeps_priority():
    a = _assessment("high")
    ok = apply_thread_rules(a, _email(), "me@example.com", ThreadInfo(messages=2, is_latest=False, newer_from="Sam"))
    assert ok is False and a.priority == "high" and "newer message" in a.adjusted_by_rule


def test_digest_text_renders_sections():
    d = {"hours": 24, "counts": {"urgent": 1, "high": 2, "low": 5},
         "urgent": [{"sender_name": "Sam", "subject": "Broken", "tags": ["Chargebacks"]}],
         "high": [], "drafts": [{"sender_name": "Pat", "subject": "Question", "tags": []}], "needs_review": []}
    text = digest.render_text(d)
    assert "URGENT (1)" in text and "Sam | Broken  [Chargebacks]" in text and "DRAFTS WAITING" in text
    slack = digest.render_slack(d)
    assert slack["blocks"][0]["type"] == "header" and any("Urgent" in str(b) for b in slack["blocks"])


def test_shared_tags_fetch_from_file(tmp_path):
    f = tmp_path / "tags.json"
    f.write_text('{"tags":[{"name":"A","description":"about a","color":"#000","threshold":0.7},{"name":"","description":"bad"}]}', encoding="utf-8")
    data = _fetch(str(f))
    assert [t["name"] for t in data["tags"]] == ["A"] and data["tags"][0]["threshold"] == 0.7
