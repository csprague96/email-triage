from emailtriage.drafter import _clean_output, _lint


def test_lint_catches_ai_tells():
    hits = _lint("I hope this email finds you well — I wanted to reach out to delve into this. It's not just fast but also robust.")
    assert "em dash" in hits
    assert "i hope this email finds you well" in hits
    assert "delve" in hits
    assert "not X but Y framing" in hits


def test_lint_clean_text_passes():
    assert _lint("Hi Stacy,\n\nThat's jane@example.com. All Sola merchants is fine for this key.\n\nThanks,") == []


def test_clean_output_strips_fences_and_dashes():
    assert _clean_output("```\nHi Hutch — quick one.\n```") == "Hi Hutch - quick one."
