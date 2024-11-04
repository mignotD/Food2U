"""Pure helper functions with no aiogram or database dependencies."""

from __future__ import annotations

import re


def is_admin_user(user_id: int, admin_ids: set[int]) -> bool:
    return user_id in admin_ids


def admin_only_message() -> str:
    return "⛔ Admin only."


def cart_item_count(cart: dict[int, int]) -> int:
    return sum(int(qty) for qty in (cart or {}).values())


def calculate_delivery_fee(base_fee: int, item_count: int) -> tuple[int, bool]:
    if item_count <= 0:
        return base_fee, True
    if item_count <= 3:
        return base_fee, True
    if item_count <= 6:
        return int(round(base_fee * 1.5)), True
    if item_count <= 8:
        return int(round(base_fee * 2.0)), True
    return base_fee, False


def parse_seed_command(text: str) -> list[str]:
    # Split by spaces, keep quoted strings together if user uses quotes
    return re.findall(r'"[^"]+"|\S+', text)


def strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and (
        (s.startswith('"') and s.endswith('"'))
        or (s.startswith("'") and s.endswith("'"))
    ):
        return s[1:-1]
    return s


def safe_label(value: object, fallback: str = "-") -> str:
    s = "" if value is None else str(value)
    s2 = s.strip()
    return s2 if s2 else fallback


def format_payment_value(value: str | None) -> str | None:
    if not value:
        return None
    v = value.strip()
    if not v:
        return None
    if "\n" in v:
        parts = [p.strip() for p in v.splitlines() if p.strip()]
        if len(parts) >= 2:
            return f"{parts[0]} - {parts[1]}"
    if "|" in v:
        parts = [p.strip() for p in v.split("|") if p.strip()]
        if len(parts) >= 2:
            return f"{parts[0]} - {parts[1]}"
    return v


def compose_payment_value(name: str, number: str) -> str:
    return f"{name.strip()}\n{number.strip()}"


def order_code(vendor_name: str | None, vendor_id: int | None, order_id: int) -> str:
    base = (vendor_name or "").strip().upper()
    base = re.sub(r"[^A-Z0-9]+", "", base)
    prefix = base[:4] if base else (f"V{int(vendor_id)}" if vendor_id is not None else "ORDER")
    return f"{prefix}-{int(order_id)}"


def status_emoji(status: str) -> str:
    s = status.lower()
    if "pending" in s:
        return "⏳"
    if "await" in s:
        return "💳"
    if "confirm" in s:
        return "✅"
    if "prepar" in s:
        return "👨‍🍳"
    if "way" in s:
        return "🚀"
    if "deliver" in s:
        return "🎉"
    if "reject" in s:
        return "❌"
    return "📦"


def is_active_order_status(status: str | None) -> bool:
    if not status:
        return True
    s = status.strip().lower()
    return ("deliver" not in s) and ("cancel" not in s) and ("archiv" not in s)


def valid_phone(phone: str) -> bool:
    return bool(re.fullmatch(r"09\d{8}", phone.strip()))
