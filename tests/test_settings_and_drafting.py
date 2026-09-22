import json
from datetime import datetime, timezone

import pytest

from emailtriage import config, store
from emailtriage.models import Assessment, Email, ThreadInfo
from emailtriage.prompts import DEFAULT_SYSTEM_PROMPT


# ---- .env writer -----------------------------------------------------------

def test_update_env_keeps_comments_and_order(tmp_path):
    env = tmp_path / ".env"
    env.write_text("# keys\nTYPESAFE_API_KEY=abc\n\n# digest\nDIGEST_TIME=08:00\nSLACK_WEBHOOK_URL=\n", encoding="utf-8")
    config.update_env({"DIGEST_TIME": "07:30", "POLL_SECONDS": "60"}, path=env)
    text = env.read_text(encoding="utf-8")
    assert text.startswith("# keys\nTYPESAFE_API_KEY=abc\n\n# digest\nDIGEST_TIME=07:30\nSLACK_WEBHOOK_URL=\n")
    assert "# ---- Set from the dashboard ----\nPOLL_SECONDS=60\n" in text
    assert not env.with_suffix(".env.tmp").exists()


def test_update_env_quotes_awkward_values(tmp_path):
    env = tmp_path / ".env"
    env.write_text("SHARED_TAGS_SOURCE=\n", encoding="utf-8")
    config.update_env({"SHARED_TAGS_SOURCE": r"\\server\team folder\tags #1.json"}, path=env)
    from dotenv import dotenv_values

    assert dotenv_values(env)["SHARED_TAGS_SOURCE"] == r"\\server\team folder\tags #1.json"


def test_update_env_keeps_crlf_line_endings(tmp_path):
    env = tmp_path / ".env"
    env.write_bytes(b"# keys\r\nPOLL_SECONDS=120\r\nDIGEST_TIME=08:00\r\n")
    config.update_env({"POLL_SECONDS": "90"}, path=env)
    assert env.read_bytes() == b"# keys\r\nPOLL_SECONDS=90\r\nDIGEST_TIME=08:00\r\n"


def test_update_env_creates_file_when_missing(tmp_path):
    env = tmp_path / ".env"
    config.update_env({"DIGEST_ENABLED": "false"}, path=env)
    assert "DIGEST_ENABLED=false" in env.read_text(encoding="utf-8")


# ---- validation ------------------------------------------------------------

def test_coerce_validates_types():
    f = config.SETTINGS_FIELDS
    assert config._coerce("DIGEST_TIME", f["DIGEST_TIME"], "7:05") == "07:05"
    assert config._coerce("DIGEST_WEEKDAYS_ONLY", f["DIGEST_WEEKDAYS_ONLY"], False) == "false"
    assert config._coerce("DRAFT_FOR_PRIORITIES", f["DRAFT_FOR_PRIORITIES"], ["Urgent", " high "]) == "urgent,high"
    with pytest.raises(ValueError):
        config._coerce("DIGEST_TIME", f["DIGEST_TIME"], "25:00")
    with pytest.raises(ValueError):
        config._coerce("POLL_SECONDS", f["POLL_SECONDS"], 5)
    with pytest.raises(ValueError):
        config._coerce("DRAFT_FOR_PRIORITIES", f["DRAFT_FOR_PRIORITIES"], "urgent,whenever")
    with pytest.raises(ValueError):
        config._coerce("SLACK_WEBHOOK_URL", f["SLACK_WEBHOOK_URL"], "https://example.com/hook")
    with pytest.raises(ValueError):
        config._coerce("MAIL_BACKEND", f["MAIL_BACKEND"], "imap")


def test_apply_settings_round_trip(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    env.write_text("POLL_SECONDS=120\nSLACK_WEBHOOK_URL=https://hooks.slack.com/services/keepme\n", encoding="utf-8")
    monkeypatch.setattr(config, "ENV_FILE", env)
    monkeypatch.setattr(config, "_PROCESS_ENV_KEYS", set())
    changed = config.apply_settings({"POLL_SECONDS": 90, "SLACK_WEBHOOK_URL": config.SECRET_MASK, "DIGEST_ENABLED": False})
    assert sorted(changed) == ["DIGEST_ENABLED", "POLL_SECONDS"]
    assert config.settings.poll_seconds == 90
    assert config.settings.digest_on is False
    assert "keepme" in env.read_text(encoding="utf-8")  # masked secret left untouched
    view = config.settings_view()
    assert view["SLACK_WEBHOOK_URL"] == {"set": True, "hint": "epme"}
    assert config.settings.digest_enabled is False
    config.apply_settings({"POLL_SECONDS": 120, "DIGEST_ENABLED": True})


def test_settings_view_never_leaks_secrets():
    view = config.settings_view()
    for key, spec in config.SETTINGS_FIELDS.items():
        if spec["type"] == "secret":
            assert set(view[key]) == {"set", "hint"}
            assert len(view[key]["hint"]) <= 4


# ---- drafting prompt --------------------------------------------------------

def test_drafting_defaults_and_override(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DRAFTING_FILE", tmp_path / "drafting.json")
    d = config.load_drafting()
    assert d["is_default"] and d["system_prompt"] == DEFAULT_SYSTEM_PROMPT
    config.save_drafting({"system_prompt": "Write as {owner_name}. Style: {style_brief}", "revision_prompt": "", "banned_phrases": ["Synergy", "synergy", " "]})
    d = config.load_drafting()
    assert not d["is_default"]
    assert d["banned_phrases"] == ["synergy"]
    assert d["revision_prompt"].startswith("Your previous draft")
    config.reset_drafting()
    assert config.load_drafting()["is_default"]


def test_drafting_prompt_validation(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "DRAFTING_FILE", tmp_path / "drafting.json")
    with pytest.raises(ValueError, match="Unknown placeholder"):
        config.save_drafting({"system_prompt": "{style_brief} {recipient}", "banned_phrases": []})
    with pytest.raises(ValueError, match="Unbalanced"):
        config.save_drafting({"system_prompt": "{style_brief} and {", "banned_phrases": []})
    with pytest.raises(ValueError, match="style_brief"):
        config.save_drafting({"system_prompt": "Just write something nice.", "banned_phrases": []})
    with pytest.raises(ValueError, match="empty"):
        config.save_drafting({"system_prompt": "  ", "banned_phrases": []})


def test_lint_uses_configured_phrases(tmp_path, monkeypatch):
    from emailtriage import drafter

    monkeypatch.setattr(config, "DRAFTING_FILE", tmp_path / "drafting.json")
    config.save_drafting({"system_prompt": "{style_brief}", "banned_phrases": ["per my last email"]})
    assert drafter._lint("Per my last email, see attached.") == ["per my last email"]
    assert drafter._lint("I hope this finds you well.") == []  # default list no longer applies


# ---- handled state + today selection -----------------------------------------

def _email(id_, subject, received, sender="sam@partner.com"):
    return Email(id=id_, subject=subject, sender_name="Sam", sender_email=sender, to=["me@example.com"], cc=[],
                 received=received, body_text="Can you confirm?")


def _assessment(priority, ts=2.0, review=False, thread=None):
    return Assessment(priority=priority, confidence=0.9, probabilities={priority: 0.9}, signals={"needs_reply": 0.9},
                      time_sensitivity=ts, tags=[], tag_scores={}, jev_priority=priority, needs_review=review, thread=thread)


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DB_FILE", tmp_path / "t.db")
    monkeypatch.setattr(store, "_conn", None)
    yield
    if store._conn is not None:
        store._conn.close()
    store._conn = None


def test_handled_survives_reassess_and_filters(fresh_db):
    now = datetime.now(timezone.utc)
    store.save_email(_email("a", "Urgent thing", now))
    store.save_assessment("a", _assessment("urgent", 3.0))
    store.save_email(_email("b", "High thing", now))
    store.save_assessment("b", _assessment("high", 2.0))
    store.set_handled("a", True)
    assert store.get_email("a")["handled_at"]
    store.save_assessment("a", _assessment("urgent", 3.0))  # re-assess keeps the handled mark
    assert store.get_email("a")["handled_at"]
    open_items = store.list_emails(priorities=["urgent", "high"], handled=False)
    assert [e["id"] for e in open_items] == ["b"]
    assert store.handled_count_since((now.replace(hour=0, minute=0)).isoformat()) == 1
    store.set_handled("a", False)
    assert store.get_email("a")["handled_at"] is None


def test_today_excludes_replied_threads_and_ranks_urgent_first(fresh_db):
    from emailtriage import server

    now = datetime.now(timezone.utc)
    store.save_email(_email("h1", "High, older", now.replace(microsecond=0)))
    store.save_assessment("h1", _assessment("high", 2.0))
    store.save_email(_email("u1", "Urgent", now.replace(microsecond=0)))
    store.save_assessment("u1", _assessment("urgent", 3.0))
    store.save_email(_email("h2", "High but I replied", now.replace(microsecond=0)))
    store.save_assessment("h2", _assessment("high", 2.0, thread=ThreadInfo(messages=2, owner_replied_after=now.isoformat())))
    store.save_email(_email("r1", "Low confidence", now.replace(microsecond=0)))
    store.save_assessment("r1", _assessment("medium", 1.0, review=True))
    t = server.today(hours=72)
    assert [e["id"] for e in t["needs_you"]] == ["u1", "h1"]
    assert [e["id"] for e in t["review"]] == ["r1"]
    assert t["later_total"] == 1 and t["handled_today"] == 0
