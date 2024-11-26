"""Tests for pure helper functions."""

from food2u.utils import (
    calculate_delivery_fee,
    cart_item_count,
    compose_payment_value,
    format_payment_value,
    is_active_order_status,
    is_admin_user,
    order_code,
    parse_seed_command,
    status_emoji,
    strip_quotes,
    valid_phone,
)


def test_is_admin_user():
    assert is_admin_user(1, {1, 2})
    assert not is_admin_user(3, {1, 2})


def test_cart_item_count():
    assert cart_item_count({}) == 0
    assert cart_item_count({1: 2, 2: 3}) == 5


def test_calculate_delivery_fee_tiers():
    # 1-3 items: normal fee, can confirm
    assert calculate_delivery_fee(100, 2) == (100, True)
    # 4-6 items: +50%
    assert calculate_delivery_fee(100, 5) == (150, True)
    # 7-8 items: +100%
    assert calculate_delivery_fee(100, 8) == (200, True)
    # >8 items: cannot confirm
    fee, can_confirm = calculate_delivery_fee(100, 9)
    assert can_confirm is False


def test_valid_phone():
    assert valid_phone("0911223344")
    assert valid_phone("  0911223344 ")
    assert not valid_phone("911223344")     # missing leading 0
    assert not valid_phone("091122334")      # too short
    assert not valid_phone("09112233445")    # too long
    assert not valid_phone("0a11223344")     # non-digit


def test_status_emoji():
    assert status_emoji("Pending Confirmation") == "⏳"
    assert status_emoji("Delivered") == "🎉"
    assert status_emoji("Rejected") == "❌"
    assert status_emoji("Something else") == "📦"


def test_is_active_order_status():
    assert is_active_order_status(None) is True
    assert is_active_order_status("Preparing") is True
    assert is_active_order_status("Delivered") is False
    assert is_active_order_status("Cancelled") is False
    assert is_active_order_status("Archived") is False


def test_order_code():
    assert order_code("Burger House", 1, 42) == "BURG-42"
    assert order_code(None, 7, 9) == "V7-9"
    assert order_code("", None, 3) == "ORDER-3"


def test_parse_seed_command_keeps_quotes():
    assert parse_seed_command('/seed_vendor "Burger House"') == ['/seed_vendor', '"Burger House"']


def test_strip_quotes():
    assert strip_quotes('"hello"') == "hello"
    assert strip_quotes("'world'") == "world"
    assert strip_quotes("plain") == "plain"


def test_payment_value_roundtrip():
    composed = compose_payment_value("John Doe", "1000123456")
    assert format_payment_value(composed) == "John Doe - 1000123456"
    assert format_payment_value("") is None
    assert format_payment_value(None) is None
