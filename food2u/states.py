"""Finite-state-machine state groups for conversation flows."""

from __future__ import annotations

from aiogram.fsm.state import State, StatesGroup


class OrderFlow(StatesGroup):
    choosing_vendor = State()
    choosing_items = State()
    choosing_block = State()
    entering_phone = State()
    confirming = State()
    entering_promo = State()
    waiting_payment_proof = State()


class AdminFlow(StatesGroup):
    adding_vendor_name = State()

    choosing_vendor_for_item = State()
    adding_item_name = State()
    adding_item_price = State()
    editing_item_price = State()
    editing_item_name = State()

    adding_category_name = State()
    editing_category_name = State()

    adding_block_name = State()
    adding_block_fee = State()
    editing_block_fee = State()

    setting_cbe_name = State()
    setting_cbe_number = State()
    setting_telebirr_name = State()
    setting_telebirr_number = State()

    broadcasting_message = State()

    creating_promo_code = State()
    creating_promo_type = State()
    creating_promo_value = State()
    creating_promo_max_uses = State()

    setting_ordering_closed_reason = State()
