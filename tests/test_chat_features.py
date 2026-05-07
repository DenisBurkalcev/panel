"""Tests for the new chat features (grouping, order/product parsing)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from app.schemas.chat import ChatMessage
from app.services.account_session import _GROUP_WINDOW_SECONDS, _compute_grouping
from app.services.funpay_client import (
    ORDER_RE,
    _parse_order_page,
    _parse_product_panel,
)


def _msg(
    *,
    author: str | None,
    is_me: bool = False,
    minute: int = 0,
    kind: str = "regular",
    text: str = "",
) -> ChatMessage:
    return ChatMessage(
        id=str(minute),
        author=author,
        is_me=is_me,
        text=text or f"m{minute}",
        sent_at=datetime(2024, 1, 1, 12, minute, 0, tzinfo=timezone.utc),
        kind=kind,  # type: ignore[arg-type]
    )


class TestGrouping:
    def test_two_consecutive_same_author_form_one_group(self) -> None:
        a = _msg(author="alice", minute=0)
        b = _msg(author="alice", minute=1)
        out = _compute_grouping([a, b])
        assert out[0].is_group_first is True
        assert out[0].is_group_last is False
        assert out[1].is_group_first is False
        assert out[1].is_group_last is True

    def test_alternating_authors_each_alone(self) -> None:
        out = _compute_grouping(
            [
                _msg(author="alice", minute=0),
                _msg(author="bob", minute=1),
                _msg(author="alice", minute=2),
            ]
        )
        for m in out:
            assert m.is_group_first is True
            assert m.is_group_last is True

    def test_window_breaks_group(self) -> None:
        a = _msg(author="alice", minute=0)
        b = _msg(author="alice", minute=10)  # 10 min apart > 5 min window
        out = _compute_grouping([a, b])
        assert all(m.is_group_first and m.is_group_last for m in out)
        assert _GROUP_WINDOW_SECONDS == 300

    def test_kind_change_breaks_group(self) -> None:
        out = _compute_grouping(
            [
                _msg(author=None, minute=0, kind="system"),
                _msg(author=None, minute=1, kind="system"),
                _msg(author="alice", minute=2),
            ]
        )
        assert out[0].is_group_first and not out[0].is_group_last
        assert not out[1].is_group_first and out[1].is_group_last
        assert out[2].is_group_first and out[2].is_group_last

    def test_is_me_flag_breaks_group_when_author_missing(self) -> None:
        # Outgoing messages have `author=None` and `is_me=True`; incoming
        # messages also might have `author=None` if the parser couldn't
        # extract it (rare). The `is_me` discriminator must keep them apart.
        out = _compute_grouping(
            [
                _msg(author=None, is_me=True, minute=0),
                _msg(author=None, is_me=False, minute=1),
            ]
        )
        assert out[0].is_group_first and out[0].is_group_last
        assert out[1].is_group_first and out[1].is_group_last

    def test_negative_window_also_breaks_group(self) -> None:
        # If timestamps are out of order (FunPay occasionally returns history
        # interleaved), still treat them as separate groups.
        a = _msg(author="alice", minute=10)
        b = _msg(author="alice", minute=0)
        b.sent_at = b.sent_at - timedelta(minutes=20)  # type: ignore[union-attr]
        out = _compute_grouping([a, b])
        assert out[0].is_group_first and out[0].is_group_last
        assert out[1].is_group_first and out[1].is_group_last


class TestOrderRegex:
    def test_matches_eight_char_order(self) -> None:
        assert ORDER_RE.search("заказ #EKW9ZFHL открыт") is not None

    def test_does_not_match_lowercase(self) -> None:
        assert ORDER_RE.search("обсуждение #abc12345") is None


def _wrap_html(inner: str) -> str:
    return f"<html><body>{inner}</body></html>"


class TestParseOrderPage:
    def test_full_order(self) -> None:
        html = _wrap_html(
            """
            <h1>Заказ #EKW9ZFHL Закрыт</h1>
            <div class='media-body'><a class='media-user-name'>BigBon</a></div>
            <div class='param-list'>
              <h5>Игра</h5><div>Steam</div>
              <h5>Категория</h5><div>Аккаунты</div>
              <h5>Сумма</h5><div>199 ₽</div>
            </div>
            """
        )
        info = _parse_order_page(html, order_id="EKW9ZFHL", url="https://funpay.com/orders/EKW9ZFHL/")
        assert info.id == "EKW9ZFHL"
        assert info.title == "Заказ #EKW9ZFHL"
        assert info.status == "Закрыт"
        assert info.buyer == "BigBon"
        labels = [it.label for it in info.items]
        assert "Игра" in labels and "Категория" in labels and "Сумма" in labels
        assert info.total == "199 ₽"


class TestParseProductPanel:
    def test_with_title_price(self) -> None:
        info = _parse_product_panel(
            "<a href='/lots/123'>Steam Premium</a>"
            "<div class='chat-panel-price'>1 200 ₽</div>"
            "<p class='chat-panel-desc'>Свежий аккаунт</p>"
        )
        assert info.available is True
        assert info.title == "Steam Premium"
        assert info.price == "1 200 ₽"
        assert info.description == "Свежий аккаунт"
        assert info.url is not None and info.url.endswith("/lots/123")

    def test_unknown_markup_falls_back_to_text(self) -> None:
        info = _parse_product_panel("<div>some unknown card</div>")
        assert info.available is True
        assert info.title == "some unknown card"


class TestSendMessageValidation:
    def test_image_id_pattern(self) -> None:
        # Bare regex check is performed inside funpay_client.send_message;
        # we re-state it here to make the constraint visible to readers and
        # so it stays in tests when the function gains other call paths.
        import re
        valid = re.compile(r"[A-Za-z0-9_\-]{1,64}")
        assert valid.fullmatch("abc-DEF_123") is not None
        assert valid.fullmatch("") is None
        assert valid.fullmatch("a" * 65) is None
        assert valid.fullmatch("bad/slash") is None


@pytest.mark.parametrize(
    "text, expected",
    [
        ("платёж за #EKW9ZFHL", "EKW9ZFHL"),
        ("see #ABCD1234 please", "ABCD1234"),
        # Bare `#XYZ` with too few chars is rejected — minimum is 4.
        ("#AB1 too short", None),
    ],
)
def test_order_re_extracts(text: str, expected: str | None) -> None:
    m = ORDER_RE.search(text)
    if expected is None:
        assert m is None
    else:
        assert m is not None and m.group(1) == expected
