from emailtriage.config import load_priority_config
from emailtriage.jev import _apply_rules


def _probs(**kw):
    base = {"junk": 0, "low": 0, "medium": 0, "high": 0, "urgent": 0}
    base.update(kw)
    return base


def test_confident_answer_is_kept():
    conf = load_priority_config()
    p, note, review = _apply_rules("high", 0.9, _probs(high=0.9), {"automated": 0.1, "needs_reply": 0.9}, 2.0, conf)
    assert p == "high" and not review and note == ""


def test_low_confidence_automated_falls_back_to_low():
    conf = load_priority_config()
    p, note, review = _apply_rules("medium", 0.3, _probs(medium=0.4, low=0.35), {"automated": 0.9, "needs_reply": 0.1}, 0.5, conf)
    assert p == "low" and review and "signals suggest" in note


def test_low_confidence_blocked_person_becomes_urgent():
    conf = load_priority_config()
    p, _, review = _apply_rules("high", 0.4, _probs(high=0.45, urgent=0.4), {"needs_reply": 0.9, "escalation": 0.8, "automated": 0.0}, 2.6, conf)
    assert p == "urgent" and review


def test_weak_urgent_is_demoted_to_high():
    conf = load_priority_config()
    p, note, review = _apply_rules("urgent", 0.5, _probs(urgent=0.5, high=0.45), {"needs_reply": 0.8, "escalation": 0.2, "automated": 0.0}, 1.8, conf)
    assert p == "high" and "Demoted" in note and review


def test_automated_high_is_capped():
    conf = load_priority_config()
    p, note, _ = _apply_rules("high", 0.8, _probs(high=0.8), {"automated": 0.95, "needs_reply": 0.05, "escalation": 0.1}, 1.0, conf)
    assert p == "medium" and "capped" in note
