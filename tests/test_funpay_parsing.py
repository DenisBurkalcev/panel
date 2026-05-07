"""Unit tests for FunPay HTML parsing helpers (no network)."""

from __future__ import annotations

from bs4 import BeautifulSoup


def _normalize(html: str) -> str:
    from app.services.funpay_client import _normalize_message_text

    soup = BeautifulSoup(html, "lxml")
    root = soup.body or soup
    return _normalize_message_text(root)


def test_br_tags_become_newlines() -> None:
    out = _normalize("<div>line one<br>line two<br>line three</div>")
    assert out == "line one\nline two\nline three"


def test_paragraphs_get_blank_line_between() -> None:
    out = _normalize("<div><p>first paragraph</p><p>second paragraph</p></div>")
    # Blocks insert a single \n; consecutive blocks therefore produce a blank
    # line which renders as a paragraph break with `whitespace-pre-wrap`.
    assert out == "first paragraph\n\nsecond paragraph"


def test_inline_tags_keep_words_separated() -> None:
    # Two adjacent <span>s without whitespace between them should still keep
    # their words apart in the rendered text.
    out = _normalize("<div><span>hello</span><span>world</span></div>")
    assert out == "hello world"


def test_collapses_runs_of_blank_lines() -> None:
    # Three explicit <br>s in a row would otherwise produce three blank lines;
    # we clamp to one (i.e. two consecutive newlines).
    out = _normalize("<div>top<br><br><br><br>bottom</div>")
    assert out == "top\n\nbottom"


def test_collapses_horizontal_whitespace_per_line() -> None:
    out = _normalize("<div>foo   \tbar\nbaz</div>")
    # Tabs and runs of spaces collapse; the explicit \n in the source text is
    # preserved by `get_text` and survives normalization.
    assert "foo bar" in out
    assert "baz" in out


def test_strips_leading_and_trailing_whitespace() -> None:
    out = _normalize("<div>\n   <br>hello<br>\n   </div>")
    assert out == "hello"


def test_realistic_buyer_message_with_newlines() -> None:
    """Mimic a real FunPay buyer message with explicit Enters."""
    html = (
        "<div class='chat-msg-text'>Hello,<br>"
        "We reopened the order.<br>"
        "It is now exactly the same as it was before.<br><br>"
        "After 48 hours, you can escalate.</div>"
    )
    out = _normalize(html)
    assert out == (
        "Hello,\n"
        "We reopened the order.\n"
        "It is now exactly the same as it was before.\n"
        "\n"
        "After 48 hours, you can escalate."
    )
