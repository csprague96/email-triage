from emailtriage.text import body_only, first_greeting, html_to_text, last_signoff, strip_quoted, strip_signature


def test_html_to_text_basic():
    html = "<html><body><p>Hi Stacy,</p><p>All good &amp; thanks!</p><ul><li>one</li><li>two</li></ul></body></html>"
    text = html_to_text(html)
    assert "Hi Stacy," in text
    assert "All good & thanks!" in text
    assert "- one" in text and "- two" in text


def test_strip_quoted_outlook_style():
    text = "Thanks, will do.\n\nFrom: Someone <a@b.com>\nSent: Monday\nTo: Me\nSubject: RE: x\n\nOriginal text here"
    assert strip_quoted(text) == "Thanks, will do."


def test_strip_quoted_gmail_style():
    text = "Sounds good.\n\nOn Wed, Aug 12, 2026 at 8:52 AM Andrew <a@b.com> wrote:\n> hello"
    assert strip_quoted(text) == "Sounds good."


def test_strip_quoted_original_message_marker():
    text = "Yep\n-----Original Message-----\nFrom: x"
    assert strip_quoted(text) == "Yep"


def test_strip_signature_by_name_and_noise():
    text = "Hi,\n\nSee below.\n\nThanks,\n\nJane Doe\nProduct Manager - Example Co\nEmail: c@x.com\n[EXTERNAL EMAIL]"
    out = strip_signature(text, owner_name="Jane Doe")
    assert out.endswith("Thanks,")
    assert "Product Manager" not in out


def test_strip_signature_learned_lines():
    text = "Quick one.\n\nThanks!\n\nJane Doe\nProduct Manager - Example Co"
    out = strip_signature(text, signature_lines=["Jane Doe", "Product Manager - Example Co"])
    assert out == "Quick one.\n\nThanks!"


def test_greeting_and_signoff_detection():
    text = "Hi Sam,\n\nExpecting release by end of month.\n\nThanks,"
    assert first_greeting(text) == "Hi Sam,"
    assert last_signoff(text) == "Thanks,"
    assert body_only(text) == "Expecting release by end of month."
