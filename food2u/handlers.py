"""Telegram handlers and the build_handlers() registration factory."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime

from aiogram import Bot, Dispatcher, F
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)

from .db import (
    add_broadcast_delivery,
    admin_create_menu_category,
    admin_create_promo_code,
    admin_delete_menu_category_and_items,
    admin_delete_orders_for_date,
    admin_get_daily_vendor_report,
    admin_get_order_details,
    admin_hard_delete_menu_item,
    admin_hard_delete_vendor_and_related,
    admin_list_blocks,
    admin_list_menu_categories,
    admin_list_menu_items,
    admin_list_orders,
    admin_list_orders_for_date,
    admin_list_promo_codes,
    admin_list_vendors,
    admin_move_menu_category,
    admin_move_vendor,
    admin_seed_block,
    admin_seed_menu_item,
    admin_seed_vendor,
    admin_set_block_active,
    admin_set_menu_item_active,
    admin_set_payment_confirmed,
    admin_set_promo_active,
    admin_set_vendor_active,
    admin_update_block_fee,
    admin_update_menu_category_name,
    admin_update_menu_item_name,
    admin_update_menu_item_price,
    attach_payment_proof,
    create_broadcast,
    create_order,
    deactivate_favorite,
    delete_broadcast_records,
    get_active_promo_code,
    get_block,
    get_favorite_for_user,
    get_menu_item,
    get_order_details,
    get_order_rating,
    get_setting,
    get_user_role,
    get_vendor,
    is_user_restricted,
    list_active_blocks,
    list_active_menu_items_by_category,
    list_active_vendors,
    list_all_user_telegram_ids,
    list_broadcast_deliveries,
    list_broadcasts,
    list_favorites_for_user,
    list_menu_categories_for_vendor,
    list_orders_for_user,
    mark_user_inactive,
    set_setting,
    touch_user_activity,
    update_order_status,
    upsert_favorite,
    upsert_order_rating,
    upsert_user,
    upsert_user_profile,
    user_has_used_any_promo_code,
)
from .keyboards import *  # noqa: F401,F403 (keyboard builders)
from .keyboards import ikb
from .logging_config import get_logger
from .states import AdminFlow, OrderFlow
from .texts import (
    delivery_fee_info_note,
    ordering_closed_message,
    ordering_disabled_message,
)
from .utils import (
    admin_only_message,
    calculate_delivery_fee,
    cart_item_count,
    is_active_order_status,
    is_admin_user,
    order_code,
    parse_seed_command,
    status_emoji,
    strip_quotes,
    valid_phone,
)
from .utils import (
    compose_payment_value as _compose_payment_value,
)
from .utils import (
    format_payment_value as _format_payment_value,
)
from .utils import (
    safe_label as _safe_label,
)

logger = get_logger(__name__)


# Telegram allows roughly 30 messages/second to different users for bulk
# sending. We stay comfortably under that to avoid flood limits.
BROADCAST_DELAY_SECONDS = 0.05


async def _broadcast_to_users(
    bot: Bot,
    db_path: str,
    user_ids: list[int],
    *,
    send,
):
    """Send a message to many users with throttling and flood handling.

    ``send`` is an async callable ``send(uid) -> Message`` performing the
    actual delivery. Returns ``(sent, failed, blocked_ids)``.
    """
    sent = 0
    failed = 0
    blocked: list[int] = []

    for uid in user_ids:
        try:
            await send(int(uid))
            sent += 1
        except TelegramRetryAfter as e:
            # Honor Telegram's requested back-off, then retry once.
            logger.warning("Broadcast hit flood limit; sleeping %ss", e.retry_after)
            await asyncio.sleep(e.retry_after)
            try:
                await send(int(uid))
                sent += 1
            except Exception:
                failed += 1
                logger.exception("Broadcast retry failed for user %s", uid)
        except TelegramForbiddenError:
            # User blocked the bot or deleted their account.
            failed += 1
            blocked.append(int(uid))
        except Exception:
            failed += 1
            logger.exception("Broadcast failed for user %s", uid)

        await asyncio.sleep(BROADCAST_DELAY_SECONDS)

    # Mark users who blocked the bot as inactive so future broadcasts skip them.
    for uid in blocked:
        try:
            await mark_user_inactive(db_path, uid)
        except Exception:
            logger.exception("Failed to mark user %s inactive", uid)

    if blocked:
        logger.info("Marked %d blocked users inactive", len(blocked))

    return sent, failed, blocked


async def render_admin_daily_vendor_report(message: Message, db_path: str) -> None:
    rows = await admin_get_daily_vendor_report(db_path, day_offset=0)
    if not rows:
        await message.answer("📝 Daily Report (Today)\n\nNo orders today yet.")
        return

    lines: list[str] = ["📝 Daily Food House Report (Today)", ""]
    total_orders = 0
    total_delivery_fees = 0
    total_discount = 0
    total_revenue = 0

    for r in rows:
        name = r.get("vendor_name") or "-"
        orders = int(r.get("orders") or 0)
        total = int(r.get("total_amount_etb") or 0)
        fees = int(r.get("delivery_fee_etb") or 0)
        discount = int(r.get("discount_amount_etb") or 0)

        total_orders += orders
        total_delivery_fees += fees
        total_discount += discount
        total_revenue += total

        items_subtotal = max(0, total - fees)
        lines.append(f"- {name}: {orders} orders")
        lines.append(f"  Items subtotal: {items_subtotal} ETB")
        if discount > 0:
            lines.append(f"  Promo discounts: -{discount} ETB")
        lines.append(f"  Delivery fees: {fees} ETB")
        lines.append(f"  Total collected: {total} ETB")

    lines.extend(
        [
            "",
            "🚚 Delivery Fee Report (Today)",
            
            f"- Total delivery fees: {total_delivery_fees} ETB",
            "",
            "📌 Totals (Today)",
            f"- Orders: {total_orders}",
            f"- Promo discounts: -{total_discount} ETB",
            f"- Total collected: {total_revenue} ETB",
        ]
    )
    await message.answer("\n".join(lines))


async def render_admin_order_detail(query: CallbackQuery, db_path: str, order_id: int, state: FSMContext) -> None:
    details = await admin_get_order_details(db_path, order_id)
    if not details:
        await query.answer("Order not found", show_alert=True)
        return

    items: dict[int, int] = details.get("items", {})
    items_text, _ = await format_cart_lines(db_path, items)

    promo_code = details.get("promo_code")
    discount_amount = int(details.get("discount_amount_etb") or 0)
    promo_line = f"Promo: {promo_code} (-{discount_amount} ETB on items)\n" if promo_code and discount_amount > 0 else ""

    has_proof = bool(details.get("payment_screenshot_file_id"))
    payment_confirmed = details.get("payment_confirmed")
    pay_line = "✅" if has_proof else "❌"
    if has_proof and payment_confirmed is not None:
        pay_line = f"{pay_line} ({'confirmed' if int(payment_confirmed) == 1 else 'not confirmed'})"

    username = (details.get("customer_username") or "").strip()
    customer_label = f"@{username}" if username else str(details.get("customer_telegram_id") or "-")

    text = (
        "📦 Admin Order Details\n\n"
        f"Order: #{details['id']}\n"
        f"Customer: {customer_label}\n"
        f"Food House: {details.get('vendor_name')}\n"
        f"Status: {details.get('status')} {status_emoji(details.get('status'))}\n"
        f"Block: {details.get('block_name')}\n"
        f"Phone: {details.get('phone_number')}\n"
        f"Created: {details.get('created_at')}\n\n"
        "🍴 Items:\n"
        f"{items_text}\n\n"
        f"{promo_line}"
        f"Delivery Fee: {details.get('delivery_fee_etb')} ETB\n"
        f"Total: {details.get('total_amount_etb')} ETB\n"
        f"Payment Proof: {pay_line}"
    )

    rating = await get_order_rating(db_path, int(details["id"]))
    if rating:
        r = rating.get("rating")
        c = (rating.get("comment") or "").strip()
        text += "\n\n📝 Customer Rating\n"
        text += f"Rating: {r}/5\n"
        if c:
            text += f"Comment: {c}"

    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
    kb = admin_order_actions_keyboard(int(details["id"]), str(details.get("status") or ""), has_proof)
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:
        msg = await query.message.answer(text, reply_markup=kb)
        await track_step_message(state, msg.message_id)
    await query.answer()


async def notify_customer_order_update(bot: Bot, db_path: str, order_id: int, text: str) -> None:
    try:
        details = await admin_get_order_details(db_path, int(order_id))
        customer_tid = int(details.get("customer_telegram_id") or 0) if details else 0
        if not customer_tid:
            return
        await bot.send_message(customer_tid, text)
    except TelegramForbiddenError:
        try:
            details = await admin_get_order_details(db_path, int(order_id))
            customer_tid = int(details.get("customer_telegram_id") or 0) if details else 0
            if customer_tid:
                await mark_user_inactive(db_path, customer_tid)
        except Exception:
            pass
    except Exception:
        return


async def continue_after_join_gate(message: Message, db_path: str, admin_ids: set[int], user) -> None:
    is_admin = int(user.id) in admin_ids
    role = "admin" if is_admin else "customer"
    await upsert_user_profile(
        db_path=db_path,
        telegram_id=user.id,
        username=user.username,
        full_name=getattr(user, "full_name", None),
        role=role,
    )

    if not is_admin:
        ordering_flag = await get_setting(db_path, "ordering_open")
        if ordering_flag is not None and ordering_flag.strip() == "0":
            reason = await get_setting(db_path, "ordering_closed_reason")
            await message.answer(ordering_closed_message(reason))
            return

    if is_admin:
        await message.answer(
            "👋 Welcome back, Admin!\n\n"
            "🧠 You're at the controls of Food2U – WSU.\n\n"
            "From the menu below you can manage:\n"
            "📦 Orders   🍲 Menus   🏠 Food Houses\n"
            "🏫 Blocks   💳 Payments   📢 Broadcasts\n"
            "🔘 Ordering ON/OFF",
            reply_markup=admin_menu_keyboard(),
        )
        return

    await message.answer(
        "✨ Welcome to Food2U – WSU! ✨\n"
        "🍔🍕 Your campus food delivery companion.\n\n"
        "Here's how it works:\n"
        "1️⃣ Pick a food house 🏠\n"
        "2️⃣ Add your favorites to the cart 🛒\n"
        "3️⃣ Choose your block & pay 💳\n"
        "4️⃣ We deliver right to you 🚚\n\n"
        "👇 Tap a button below to get started:",
        reply_markup=customer_menu_keyboard(),
    )


async def enforce_ordering_open_message(message: Message, db_path: str, admin_ids: set[int]) -> bool:
    if is_admin_user(message.from_user.id, admin_ids):
        return True
    ordering_flag = await get_setting(db_path, "ordering_open")
    if ordering_flag is not None and ordering_flag.strip() == "0":
        reason = await get_setting(db_path, "ordering_closed_reason")
        await message.answer(ordering_closed_message(reason))
        return False
    return True


async def enforce_ordering_open_callback(query: CallbackQuery, db_path: str, admin_ids: set[int]) -> bool:
    if is_admin_user(query.from_user.id, admin_ids):
        return True
    ordering_flag = await get_setting(db_path, "ordering_open")
    if ordering_flag is not None and ordering_flag.strip() == "0":
        reason = await get_setting(db_path, "ordering_closed_reason")
        await query.message.answer(ordering_closed_message(reason))
        await query.answer("Ordering closed", show_alert=True)
        return False
    return True


async def enforce_not_restricted_message(message: Message, db_path: str, admin_ids: set[int]) -> bool:
    if is_admin_user(message.from_user.id, admin_ids):
        return True
    restricted, reason = await is_user_restricted(db_path, message.from_user.id)
    if restricted:
        await message.answer(ordering_disabled_message(reason))
        return False
    return True


async def enforce_not_restricted_callback(query: CallbackQuery, db_path: str, admin_ids: set[int], state: FSMContext) -> bool:
    if is_admin_user(query.from_user.id, admin_ids):
        return True
    restricted, reason = await is_user_restricted(db_path, query.from_user.id)
    if restricted:
        try:
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
        except Exception:
            pass
        await state.clear()
        await query.message.answer(ordering_disabled_message(reason))
        await query.answer("Ordering disabled", show_alert=True)
        return False
    return True


async def cart_controls_keyboard(db_path: str, cart_item_ids: list[int]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for item_id in cart_item_ids:
        item = await get_menu_item(db_path, int(item_id))
        name = _safe_label(item.get("name") if item else None, f"Item {item_id}")
        rows.append([InlineKeyboardButton(text=f"➖ {name}", callback_data=f"rem:{item_id}")])
    rows.append([InlineKeyboardButton(text="✅ Done", callback_data="done")])
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="oback:menu")])
    rows.append([InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")])
    return ikb(rows)


async def format_cart_lines(db_path: str, cart: dict[int, int]) -> tuple[str, int]:
    lines: list[str] = []
    subtotal = 0
    for item_id, qty in cart.items():
        item = await get_menu_item(db_path, item_id)
        if not item:
            continue
        price = int(item["price_etb"])
        subtotal += price * int(qty)
        lines.append(f"• {item['name']} x{qty} ({price} ETB)")
    if not lines:
        return "(empty)", 0
    return "\n".join(lines), subtotal


async def mini_cart_preview_lines(db_path: str, cart: dict[int, int], limit: int = 3) -> tuple[str, int, int]:
    full_text, subtotal = await format_cart_lines(db_path, cart)
    count = cart_item_count(cart)
    if full_text == "(empty)":
        return "(empty)", subtotal, count
    lines = full_text.splitlines()
    if len(lines) > limit:
        lines = lines[:limit] + [f"… and {len(lines) - limit} more"]
    return "\n".join(lines), subtotal, count


async def safe_delete_message(bot: Bot, chat_id: int, message_id: int) -> None:
    try:
        await bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception:
        return


async def cleanup_step_messages(bot: Bot, chat_id: int, state: FSMContext, keep_message_ids: set[int] | None = None) -> None:
    data = await state.get_data()
    msg_ids: list[int] = data.get("step_message_ids", [])
    keep = keep_message_ids or set()
    tracked = set(int(mid) for mid in msg_ids if isinstance(mid, int) or (isinstance(mid, str) and str(mid).isdigit()))
    keep_tracked = tracked.intersection(set(int(x) for x in keep if isinstance(x, int) or (isinstance(x, str) and str(x).isdigit())))
    for mid in msg_ids:
        if int(mid) in keep_tracked:
            continue
        await safe_delete_message(bot, chat_id, int(mid))
    await state.update_data(step_message_ids=list(keep_tracked))


async def track_step_message(state: FSMContext, message_id: int) -> None:
    data = await state.get_data()
    msg_ids: list[int] = data.get("step_message_ids", [])
    if message_id not in msg_ids:
        msg_ids.append(message_id)
    await state.update_data(step_message_ids=msg_ids)


async def replace_callback_message(
    query: CallbackQuery,
    state: FSMContext,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    keep_current: bool = True,
) -> None:
    keep = {query.message.message_id} if keep_current else set()
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=keep)
    try:
        await query.message.edit_text(text, reply_markup=reply_markup)
    except Exception:
        msg = await query.message.answer(text, reply_markup=reply_markup)
        await track_step_message(state, msg.message_id)


async def cmd_myid(message: Message):
    await message.answer(f"Your Telegram ID is: {message.from_user.id}")


async def cmd_help(message: Message):
    await message.answer(
        "❓ Food2U – WSU Help\n\n"
        "Commands:\n"
        "🚀 /start – Open the main menu\n"
        "❓ /help – Show this help\n"
        "🆔 /myid – Show your Telegram ID\n\n"
        "💡 Tip: Use the buttons in the menu to order food, "
        "track your orders, and more — no typing needed!"
    )


async def cmd_start(message: Message, db_path: str, admin_ids: set[int]):
    user = message.from_user
    is_admin = user.id in admin_ids
    role = "admin" if is_admin else "customer"

    await upsert_user(db_path=db_path, telegram_id=user.id, username=user.username, role=role)

    await continue_after_join_gate(message, db_path, admin_ids, user)


async def handle_browse_vendors(message: Message, db_path: str, admin_ids: set[int], state: FSMContext):
    if not await enforce_not_restricted_message(message, db_path, admin_ids):
        return
    if not await enforce_ordering_open_message(message, db_path, admin_ids):
        return

    vendors = await list_active_vendors(db_path)
    if not vendors:
        await message.answer("😕 No food houses are available right now.\n\nPlease try again later ⏳")
        return

    await state.clear()
    await state.set_state(OrderFlow.choosing_vendor)
    await message.answer(
        "🍴 Choose a food house\n\nSelect where you want to order from 👇",
        reply_markup=vendor_list_keyboard(vendors),
    )


async def handle_view_orders(message: Message, db_path: str, state: FSMContext):
    await state.clear()
    orders = await list_orders_for_user(db_path, message.from_user.id, limit=10)
    orders = [o for o in orders if is_active_order_status(o.get("status"))]
    if not orders:
        await message.answer("📦 No active orders right now.\n\nStart a new order from 🍽️ Order Food!")
        return

    lines = ["📦 Your Active Orders"]
    for o in orders:
        code = order_code(o.get("vendor_name"), o.get("vendor_id"), int(o["id"]))
        lines.append(f"• {code} – {o['vendor_name']} – {o['status']} {status_emoji(o['status'])}")
    await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
    msg = await message.answer("\n".join(lines), reply_markup=orders_list_keyboard(orders))
    await track_step_message(state, msg.message_id)


async def handle_order_history(message: Message, db_path: str, state: FSMContext):
    await state.clear()
    orders = await list_orders_for_user(db_path, message.from_user.id, limit=10)
    if not orders:
        await message.answer("🧾 No order history yet.\n\nOnce you place an order, you’ll see it here ✅")
        return

    lines = ["🧾 Your Order History (Recent)"]
    for o in orders:
        status = o.get("status") or "Unknown"
        code = order_code(o.get("vendor_name"), o.get("vendor_id"), int(o["id"]))
        lines.append(f"• {code} – {o['vendor_name']} – {status} {status_emoji(status)}")
    await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
    msg = await message.answer("\n".join(lines), reply_markup=orders_list_keyboard(orders))
    await track_step_message(state, msg.message_id)


async def handle_admin_view_orders(message: Message, db_path: str, state: FSMContext):
    await state.clear()
    orders = await admin_list_orders(db_path, status="Pending Confirmation", limit=30)
    if not orders:
        orders = await admin_list_orders(db_path, limit=30)
        orders = [o for o in orders if is_active_order_status(o.get("status"))]
    if not orders:
        await message.answer("📦 No orders yet.")
        return

    title = "📦 Orders (Pending Confirmation)" if any(o.get("status") == "Pending Confirmation" for o in orders) else "📦 Recent Orders"
    await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
    msg = await message.answer(title, reply_markup=admin_orders_keyboard(orders))
    await track_step_message(state, msg.message_id)


async def handle_admin_order_history(message: Message, db_path: str, state: FSMContext):
    await state.clear()
    today = datetime.now().date().isoformat()
    orders = await admin_list_orders_for_date(db_path, today, limit=200)
    if not orders:
        await message.answer("🧾 No orders for today yet.")
        return

    title = f"🧾 Today's Orders ({today})"
    await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
    msg = await message.answer(title, reply_markup=admin_daily_orders_keyboard(orders))
    await track_step_message(state, msg.message_id)


async def handle_help_guide(message: Message, state: FSMContext):
    await state.clear()
    await message.answer(
        "❓ Food2U Help\n\n"
        "1️⃣ 🍽️ Order Food\n"
        "2️⃣ Add items to your cart 🛒\n"
        "3️⃣ Choose your block 🏫\n"
        "4️⃣ Enter your phone number 📞\n"
        "5️⃣ Pay & upload proof 💳📸\n"
        "6️⃣ Track your order from 📦 My Orders 🚚\n\n"
        "Need support? Contact admin/support in your channel or group."
    )


async def handle_placeholder(message: Message, state: FSMContext):
    logger.debug(
        "Unhandled message from=%s text=%r",
        getattr(message.from_user, "id", None),
        message.text,
    )

    current_state = await state.get_state()
    text = (message.text or "").strip()
    if text.startswith("/"):
        msg = (
            "❌ Unknown command.\n\n"
            "✅ Please use the menu buttons, or type /start to return to the main menu."
        )
    else:
        msg = (
            "❌ I didn’t understand that message.\n\n"
            "✅ Please use the buttons on the screen, or type /start to return to the main menu."
        )
    if current_state:
        msg += "\n\n💡 You can continue from where you left off."
    await message.answer(msg)


async def admin_cmd_seed_vendor(message: Message, db_path: str, admin_ids: set[int]):
    if not is_admin_user(message.from_user.id, admin_ids):
        await message.answer(admin_only_message())
        return

    parts = parse_seed_command(message.text or "")
    if len(parts) < 2:
        await message.answer('Usage: /seed_vendor "Vendor Name"')
        return
    name = strip_quotes(" ".join(parts[1:]))
    vid = await admin_seed_vendor(db_path, name)
    await message.answer(f"✅ Vendor added: {name} (id={vid})")


async def admin_cmd_seed_item(message: Message, db_path: str, admin_ids: set[int]):
    if not is_admin_user(message.from_user.id, admin_ids):
        await message.answer(admin_only_message())
        return

    parts = parse_seed_command(message.text or "")
    if len(parts) < 5:
        await message.answer('Usage: /seed_item <vendor_id> <category_id> "Item Name" <price_etb>')
        return
    vendor_id = int(parts[1])
    category_id = int(parts[2])
    price_etb = int(parts[-1])
    name = strip_quotes(" ".join(parts[3:-1]))
    item_id = await admin_seed_menu_item(db_path, vendor_id, category_id, name, price_etb)
    await message.answer(f"✅ Menu item added: {name} (id={item_id})")


async def admin_cmd_seed_block(message: Message, db_path: str, admin_ids: set[int]):
    if not is_admin_user(message.from_user.id, admin_ids):
        await message.answer(admin_only_message())
        return

    parts = parse_seed_command(message.text or "")
    if len(parts) < 3:
        await message.answer('Usage: /seed_block "Block Name" <delivery_fee_etb>')
        return
    fee = int(parts[-1])
    name = strip_quotes(" ".join(parts[1:-1]))
    bid = await admin_seed_block(db_path, name, fee)
    await message.answer(f"✅ Block added: {name} – {fee} ETB (id={bid})")


async def admin_cmd_set_cbe(message: Message, db_path: str, admin_ids: set[int]):
    if not is_admin_user(message.from_user.id, admin_ids):
        await message.answer(admin_only_message())
        return
    parts = parse_seed_command(message.text or "")
    if len(parts) < 2:
        await message.answer("Usage: /set_cbe <account_number>")
        return
    value = strip_quotes(" ".join(parts[1:]))
    await set_setting(db_path, "cbe_account", value)
    await message.answer("✅ CBE account saved.")


async def admin_cmd_set_telebirr(message: Message, db_path: str, admin_ids: set[int]):
    if not is_admin_user(message.from_user.id, admin_ids):
        await message.answer(admin_only_message())
        return
    parts = parse_seed_command(message.text or "")
    if len(parts) < 2:
        await message.answer("Usage: /set_telebirr <phone_number>")
        return
    value = strip_quotes(" ".join(parts[1:]))
    await set_setting(db_path, "telebirr_number", value)
    await message.answer("✅ Telebirr number saved.")


async def on_vendor_callback(query: CallbackQuery, db_path: str, state: FSMContext):
    vendor_id = int(query.data.split(":", 1)[1])
    vendor = await get_vendor(db_path, vendor_id)
    if not vendor:
        await query.answer("Vendor not found", show_alert=True)
        return

    categories = await list_menu_categories_for_vendor(db_path, vendor_id)
    if not categories:
        await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
        try:
            await query.message.edit_text(
                "😕 Menu not available right now.\n\n"
                "This food house hasn’t added categories yet. Please try again later ⏳",
            )
        except Exception:
            msg = await query.message.answer(
                "😕 Menu not available right now.\n\n"
                "This food house hasn’t added categories yet. Please try again later ⏳",
            )
            await track_step_message(state, msg.message_id)
        await query.answer()

        return

    await state.update_data(vendor_id=vendor_id, cart={}, cart_message_id=None)
    await state.set_state(OrderFlow.choosing_items)
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
    try:
        await query.message.edit_text(
            f"📂 {vendor['name']} Categories\n\nSelect a category 👇",
            reply_markup=vendor_categories_keyboard(vendor_id, categories),
        )
    except Exception:
        msg = await query.message.answer(
            f"📂 {vendor['name']} Categories\n\nSelect a category 👇",
            reply_markup=vendor_categories_keyboard(vendor_id, categories),
        )
        await track_step_message(state, msg.message_id)
    await query.answer()


async def on_category_callback(query: CallbackQuery, db_path: str, state: FSMContext):
    parts = (query.data or "").split(":")
    if len(parts) < 3:
        await query.answer()
        return
    vendor_id = int(parts[1])
    category_id = int(parts[2])

    vendor = await get_vendor(db_path, vendor_id)
    if not vendor:
        await query.answer("Vendor not found", show_alert=True)
        return

    items = await list_active_menu_items_by_category(db_path, vendor_id, category_id)
    if not items:
        await query.answer("😕 No items in this category yet.", show_alert=True)
        return

    data = await state.get_data()
    cart = data.get("cart")
    cart_message_id = data.get("cart_message_id")
    if not isinstance(cart, dict):
        cart = {}
    await state.update_data(vendor_id=vendor_id, last_category_id=category_id, cart=cart, cart_message_id=cart_message_id)
    await state.set_state(OrderFlow.choosing_items)

    keep: set[int] = set()
    if cart_message_id:
        keep.add(int(cart_message_id))
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=keep)
    mini_lines, mini_subtotal, mini_count = await mini_cart_preview_lines(db_path, cart)
    try:
        await query.message.edit_text(
            f"🍲 {vendor['name']} Menu\n\n"
            "Tap items to add to your cart 🛒\n\n"
            "🛒 Live Cart\n"
            f"Items: {mini_count}\n"
            f"Subtotal: {mini_subtotal} ETB\n"
            f"{mini_lines}",
            reply_markup=menu_keyboard(items, mini_count),
        )
    except Exception:
        msg = await query.message.answer(
            f"🍲 {vendor['name']} Menu\n\n"
            "Tap items to add to your cart 🛒\n\n"
            "🛒 Live Cart\n"
            f"Items: {mini_count}\n"
            f"Subtotal: {mini_subtotal} ETB\n"
            f"{mini_lines}",
            reply_markup=menu_keyboard(items, mini_count),
        )
        await track_step_message(state, msg.message_id)
    await query.answer()


async def on_add_item_callback(query: CallbackQuery, db_path: str, state: FSMContext):
    item_id = int(query.data.split(":", 1)[1])
    data = await state.get_data()
    cart: dict[int, int] = data.get("cart", {})
    cart[item_id] = int(cart.get(item_id, 0)) + 1
    await state.update_data(cart=cart)
    vendor_id = int(data.get("vendor_id") or 0)
    category_id = int(data.get("last_category_id") or 0)
    vendor = await get_vendor(db_path, vendor_id) if vendor_id else None
    items = await list_active_menu_items_by_category(db_path, vendor_id, category_id) if vendor_id and category_id else []
    mini_lines, mini_subtotal, mini_count = await mini_cart_preview_lines(db_path, cart)
    warning = "\n\n⚠️ More than 8 items. Remove some items before checkout." if mini_count > 8 else ""
    text = (
        f"🍲 {(vendor.get('name') if vendor else 'Menu')} Menu\n\n"
        "Tap items to add to your cart 🛒\n\n"
        "🛒 Live Cart\n"
        f"Items: {mini_count}\n"
        f"Subtotal: {mini_subtotal} ETB\n"
        f"{mini_lines}{warning}"
    )
    kb = menu_keyboard(items, mini_count)
    try:
        await query.message.edit_text(text, reply_markup=kb)
    except Exception:
        await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
        msg = await query.message.answer(text, reply_markup=kb)
        await track_step_message(state, msg.message_id)

    await query.answer("Added")


async def on_remove_item_callback(query: CallbackQuery, db_path: str, state: FSMContext):
    item_id = int(query.data.split(":", 1)[1])
    data = await state.get_data()
    cart: dict[int, int] = data.get("cart", {})
    if item_id in cart:
        cart[item_id] = int(cart[item_id]) - 1
        if cart[item_id] <= 0:
            cart.pop(item_id, None)
    cart_msg_id = data.get("cart_message_id")
    await state.update_data(cart=cart)

    cart_text, subtotal = await format_cart_lines(db_path, cart)
    text = f"🛒 Your Cart\n\n💰 Current total: {subtotal} ETB\n\n{cart_text}\n\n➕ Add more items or tap ✅ Done"
    kb = await cart_controls_keyboard(db_path, list(cart.keys()))

    if cart_msg_id:
        try:
            await query.bot.edit_message_text(
                chat_id=query.message.chat.id,
                message_id=int(cart_msg_id),
                text=text,
                reply_markup=kb,
            )
            await query.answer("✅ Updated")
            return
        except Exception:
            pass

    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
    msg = await query.message.answer(text, reply_markup=kb)
    await state.update_data(cart_message_id=msg.message_id)
    await track_step_message(state, msg.message_id)
    await query.answer("✅ Updated")


async def on_done_callback(query: CallbackQuery, db_path: str, state: FSMContext):
    data = await state.get_data()
    cart: dict[int, int] = data.get("cart", {})
    if not cart:
        await query.answer("🛒 Your cart is empty. Add at least one item!", show_alert=True)
        return

    count = cart_item_count(cart)
    if count > 8:
        await query.answer(
            "⚠️ Too many items (max 8). Please remove some items to continue.",
            show_alert=True,
        )
        return

    blocks = await list_active_blocks(db_path)
    if not blocks:
        await query.answer("🏫 No delivery blocks are available right now.", show_alert=True)
        return

    cart_msg_id = data.get("cart_message_id")
    await state.set_state(OrderFlow.choosing_block)
    keep: set[int] = set()
    if cart_msg_id:
        keep.add(int(cart_msg_id))
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=keep)
    try:
        await query.message.edit_text(
            "🏫 Choose your delivery block\n\nDelivery fee is included 👇",
            reply_markup=blocks_keyboard(blocks),
        )
    except Exception:
        msg = await query.message.answer(
            "🏫 Choose your delivery block\n\nDelivery fee is included 👇",
            reply_markup=blocks_keyboard(blocks),
        )
        await track_step_message(state, msg.message_id)
    await query.answer()


async def on_block_callback(query: CallbackQuery, db_path: str, state: FSMContext):
    block_id = int(query.data.split(":", 1)[1])
    block = await get_block(db_path, block_id)
    if not block:
        await query.answer("❌ Block not found. Please try again.", show_alert=True)
        return

    fee = block.get("delivery_fee_etb")
    if fee is None:
        await query.answer("⚠️ Delivery fee not set for this block yet.", show_alert=True)
        return

    await state.update_data(block_id=block_id, base_delivery_fee_etb=int(fee), delivery_fee_etb=int(fee))
    await state.set_state(OrderFlow.entering_phone)

    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
    try:
        await query.message.edit_text(
            "📞 Enter your phone number\n\n"
            "We’ll use it only to contact you for delivery 🚚\n\n"
            "Example: 09XXXXXXXX"
        )
    except Exception:
        msg = await query.message.answer(
            "📞 Enter your phone number\n\n"
            "We’ll use it only to contact you for delivery 🚚\n\n"
            "Example: 09XXXXXXXX"
        )
        await track_step_message(state, msg.message_id)
    await query.answer()


async def on_phone_message(message: Message, db_path: str, state: FSMContext):
    phone = (message.text or "").strip()
    if not valid_phone(phone):
        await message.answer(
            "❌ Invalid phone number format.\n\n"
            "✅ Please send like: 09XXXXXXXX"
        )
        return

    data = await state.get_data()
    vendor_id = int(data["vendor_id"])
    block_id = int(data["block_id"])
    base_fee = int(data.get("base_delivery_fee_etb") or data["delivery_fee_etb"])
    cart: dict[int, int] = data.get("cart", {})

    vendor = await get_vendor(db_path, vendor_id)
    block = await get_block(db_path, block_id)
    items_text, subtotal = await format_cart_lines(db_path, cart)
    count = cart_item_count(cart)
    fee, can_confirm = calculate_delivery_fee(base_fee, count)
    total = subtotal + fee

    await state.update_data(
        phone_number=phone,
        subtotal=subtotal,
        delivery_fee_etb=fee,
        total=total,
        promo_code=None,
        discount_amount_etb=0,
    )
    await state.set_state(OrderFlow.confirming)

    await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
    msg = await message.answer(
        "🧾 Order Summary\n\n"
        f"🏠 Vendor: {vendor['name'] if vendor else vendor_id}\n"
        "🍴 Items:\n"
        f"{items_text}\n\n"
        f"🏫 Delivery Block: {block['name'] if block else block_id}\n"
        f"📞 Phone: {phone}\n"
        f"🚚 Delivery Fee: {fee} ETB\n"
        f"💰 Total: {total} ETB\n\n"
        f"{delivery_fee_info_note()}\n\n"
        "✅ If everything looks correct, confirm to proceed to payment 👇",
        reply_markup=confirm_keyboard_limited(can_confirm),
    )
    await track_step_message(state, msg.message_id)


async def on_confirm_callback(query: CallbackQuery, db_path: str, state: FSMContext, admin_ids: set[int]):
    data = await state.get_data()
    vendor_id = int(data["vendor_id"])
    block_id = int(data["block_id"])
    base_fee = int(data.get("base_delivery_fee_etb") or data["delivery_fee_etb"])
    cart: dict[int, int] = data.get("cart", {})
    phone = data["phone_number"]
    promo_code = data.get("promo_code")
    discount_amount_etb = int(data.get("discount_amount_etb") or 0)

    items_text, subtotal = await format_cart_lines(db_path, cart)
    count = cart_item_count(cart)
    fee, can_confirm = calculate_delivery_fee(base_fee, count)
    if not can_confirm:
        vendor = await get_vendor(db_path, vendor_id) if vendor_id else None
        block = await get_block(db_path, block_id) if block_id else None
        promo_line = (
            f"💸 Promo: {promo_code} (-{discount_amount_etb} ETB on items)\n" if promo_code and discount_amount_etb > 0 else ""
        )
        total = (subtotal - discount_amount_etb) + fee
        if total < 0:
            total = 0
        await state.update_data(subtotal=subtotal, delivery_fee_etb=fee, total=total)
        await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
        try:
            await query.message.edit_text(
                "🧾 Order Summary\n\n"
                f"🏠 Vendor: {vendor['name'] if vendor else vendor_id}\n"
                "🍴 Items:\n"
                f"{items_text}\n\n"
                f"🏫 Delivery Block: {block['name'] if block else block_id}\n"
                f"📞 Phone: {phone}\n"
                f"{promo_line}"
                f"🚚 Delivery Fee: {fee} ETB\n"
                f"💰 Total: {total} ETB\n\n"
                f"{delivery_fee_info_note()}\n\n"
                "⚠️ Please remove some items to continue.",
                reply_markup=confirm_keyboard_limited(False),
            )
        except Exception:
            msg = await query.message.answer(
                "🧾 Order Summary\n\n"
                f"🏠 Vendor: {vendor['name'] if vendor else vendor_id}\n"
                "🍴 Items:\n"
                f"{items_text}\n\n"
                f"🏫 Delivery Block: {block['name'] if block else block_id}\n"
                f"📞 Phone: {phone}\n"
                f"{promo_line}"
                f"🚚 Delivery Fee: {fee} ETB\n"
                f"💰 Total: {total} ETB\n\n"
                f"{delivery_fee_info_note()}\n\n"
                "⚠️ Please remove some items to continue.",
                reply_markup=confirm_keyboard_limited(False),
            )
            await track_step_message(state, msg.message_id)
        await query.answer("Too many items", show_alert=True)
        return

    total = (subtotal - discount_amount_etb) + fee
    if total < 0:
        total = 0

    await state.update_data(subtotal=subtotal, delivery_fee_etb=fee, total=total)

    order_id = await create_order(
        db_path=db_path,
        telegram_id=query.from_user.id,
        vendor_id=vendor_id,
        items=cart,
        block_id=block_id,
        phone_number=phone,
        delivery_fee_etb=fee,
        total_amount_etb=total,
        promo_code=promo_code,
        discount_amount_etb=discount_amount_etb,
        status="Awaiting Payment",
    )
    await state.update_data(order_id=order_id)

    cbe = _format_payment_value(await get_setting(db_path, "cbe_account"))
    tele = _format_payment_value(await get_setting(db_path, "telebirr_number"))

    if not cbe and not tele:
        await query.message.answer(
            "⚠️ Payment accounts are not configured yet.\n\nPlease contact admin to set up payments."
        )
        await state.clear()
        await query.answer()

    pay_lines = ["💳 Payment Instructions", "", "Please pay the exact amount 👇", ""]
    if cbe:
        pay_lines.append(f"🏦 CBE Account: {cbe}")
    if tele:
        pay_lines.append(f"📱 Telebirr: {tele}")
    pay_lines.extend(["", "📸 After payment, upload your screenshot as proof."])

    await state.set_state(OrderFlow.waiting_payment_proof)
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
    try:
        await query.message.edit_text("\n".join(pay_lines), reply_markup=payment_keyboard())
    except Exception:
        msg = await query.message.answer("\n".join(pay_lines), reply_markup=payment_keyboard())
        await track_step_message(state, msg.message_id)
    await query.answer()


async def on_paid_callback(query: CallbackQuery, state: FSMContext):
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
    try:
        await query.message.edit_text(
            "📸 Upload your payment screenshot here\n\n"
            "✅ Make sure the amount and account details are visible."
        )
    except Exception:
        msg = await query.message.answer(
            "📸 Upload your payment screenshot here\n\n"
            "✅ Make sure the amount and account details are visible."
        )
        await track_step_message(state, msg.message_id)
    await query.answer()


async def on_payment_photo(message: Message, db_path: str, state: FSMContext, admin_ids: set[int]):
    if not message.photo:
        await message.answer("❌ Please upload a screenshot/photo image only 📸")
        return

    data = await state.get_data()
    order_id = data.get("order_id")
    if not order_id:
        await message.answer("⚠️ No active order found.\n\nPlease start a new order from the menu 🛒")
        await state.clear()
        return

    file_id = message.photo[-1].file_id
    await attach_payment_proof(db_path, int(order_id), file_id)
    await update_order_status(db_path, int(order_id), "Pending Confirmation")

    await message.answer(
        "✅ Payment proof received!\n\n"
        "⏳ Your order is now under verification.\n"
        "We’ll update you soon — thank you 🙏"
    )

    details = await get_order_details(db_path, message.from_user.id, int(order_id))
    notify_text = f"💳 New payment proof\nOrder #{order_id} {status_emoji('Pending')}\n"
    if details:
        notify_text += f"Vendor: {details['vendor_name']}\nBlock: {details['block_name']}\nPhone: {details['phone_number']}\nTotal: {details['total_amount_etb']} ETB"

    for admin_id in admin_ids:
        try:
            await message.bot.send_photo(admin_id, file_id, caption=notify_text)
        except Exception:
            try:
                await message.bot.send_message(admin_id, notify_text)
            except Exception:
                logger.exception("Failed to notify admin %s of payment", admin_id)

    # Offer to save this order as a favorite for quick reordering later.
    vendor_id = data.get("vendor_id")
    cart = data.get("cart") or {}
    if vendor_id and cart:
        try:
            await message.bot.send_message(
                message.chat.id,
                "⭐ Liked this order? Save it as a favorite for one-tap reordering next time.",
                reply_markup=ikb(
                    [[InlineKeyboardButton(text="⭐ Save as Favorite", callback_data=f"fav:save:{order_id}")]]
                ),
            )
        except Exception:
            logger.exception("Failed to offer favorite save for order %s", order_id)

    await state.clear()


async def on_order_detail_callback(query: CallbackQuery, db_path: str):
    order_id = int(query.data.split(":", 1)[1])
    details = await get_order_details(db_path, query.from_user.id, order_id)
    if not details:
        await query.answer("Order not found", show_alert=True)
        return

    promo_code = details.get("promo_code")
    discount_amount = int(details.get("discount_amount_etb") or 0)
    promo_line = f"Promo: {promo_code} (-{discount_amount} ETB on items)\n" if promo_code and discount_amount > 0 else ""

    await query.message.answer(
        "📦 Order Details\n\n"
        f"Order: {order_code(details.get('vendor_name'), details.get('vendor_id'), int(details['id']))}\n"
        f"Vendor: {details['vendor_name']}\n"
        f"Status: {details['status']} {status_emoji(details['status'])}\n"
        f"Block: {details['block_name']}\n"
        f"Phone: {details['phone_number']}\n"
        f"{promo_line}"
        f"Delivery Fee: {details['delivery_fee_etb']} ETB\n"
        f"Total: {details['total_amount_etb']} ETB\n"
        f"Payment Proof: {'✅' if details.get('payment_screenshot_file_id') else '❌'}",
        reply_markup=customer_order_detail_keyboard(order_id),
    )
    await query.answer()


async def render_customer_order_detail(query: CallbackQuery, db_path: str, order_id: int, state: FSMContext) -> None:
    details = await get_order_details(db_path, query.from_user.id, int(order_id))
    if not details:
        await query.answer("Order not found", show_alert=True)
        return

    cart: dict[int, int] = details.get("items", {})
    items_text, _ = await format_cart_lines(db_path, cart)

    promo_code = details.get("promo_code")
    promo_discount = int(details.get("discount_amount_etb") or 0)
    promo_line = (
        f"💸 Promo: {promo_code} (-{promo_discount} ETB on items)\n" if promo_code and promo_discount > 0 else ""
    )

    text = (
        "📦 Order Details\n\n"
        f"Order: {order_code(details.get('vendor_name'), details.get('vendor_id'), int(details['id']))}\n"
        f"Vendor: {details['vendor_name']}\n"
        f"Status: {details['status']} {status_emoji(details['status'])}\n"
        f"Block: {details['block_name']}\n"
        f"Phone: {details['phone_number']}\n\n"
        "🍴 Items:\n"
        f"{items_text}\n\n"
        f"{promo_line}"
        f"Delivery Fee: {details['delivery_fee_etb']} ETB\n"
        f"Total: {details['total_amount_etb']} ETB\n"
        f"Payment Proof: {'✅' if details.get('payment_screenshot_file_id') else '❌'}"
    )

    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
    try:
        await query.message.edit_text(text, reply_markup=customer_order_detail_keyboard(int(order_id)))
    except Exception:
        msg = await query.message.answer(text, reply_markup=customer_order_detail_keyboard(int(order_id)))
        await track_step_message(state, msg.message_id)
    await query.answer()


async def render_customer_orders_list(message: Message, db_path: str, state: FSMContext, telegram_id: int) -> None:
    orders = await list_orders_for_user(db_path, int(telegram_id), limit=10)
    orders = [o for o in orders if is_active_order_status(o.get("status"))]
    if not orders:
        msg = await message.answer(
            "📦 No active orders right now.\n\nStart a new order from 🍽️ Order Food!",
            reply_markup=customer_menu_keyboard(),
        )
        await track_step_message(state, msg.message_id)
        return

    lines = ["📦 Your Active Orders"]
    for o in orders:
        status = o.get("status") or ""
        code = order_code(o.get("vendor_name"), o.get("vendor_id"), int(o["id"]))
        lines.append(f"• {code} – {o['vendor_name']} – {status} {status_emoji(status)}")

    msg = await message.answer("\n".join(lines), reply_markup=orders_list_keyboard(orders))
    await track_step_message(state, msg.message_id)


async def on_cancel_callback(query: CallbackQuery, state: FSMContext):
    await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
    try:
        await query.message.delete()
    except Exception:
        pass
    await state.clear()
    await query.message.answer("Choose an option:", reply_markup=customer_menu_keyboard())
    await query.answer()


def build_handlers(db_path: str, admin_ids: set[int]):
    async def start_handler(message: Message, state: FSMContext):
        await touch_user_activity(db_path, message.from_user.id)
        await cmd_start(message, db_path, admin_ids)
        await state.clear()

    async def broadcast_photo_handler(message: Message, state: FSMContext):
        await touch_user_activity(db_path, message.from_user.id)
        if not is_admin_user(message.from_user.id, admin_ids):
            return
        if await state.get_state() != AdminFlow.broadcasting_message.state:
            return

        if not message.photo:
            await message.answer("❌ Please send a photo.")
            return

        photo = message.photo[-1]
        file_id = photo.file_id
        caption = (message.caption or "").strip()
        title = caption if caption else "(Photo broadcast)"

        try:
            broadcast_id = await create_broadcast(db_path, title, message.from_user.id)
        except Exception:
            await state.clear()
            await message.answer("❌ Failed to create broadcast record.")
            return

        user_ids = await list_all_user_telegram_ids(db_path)

        async def _send_photo(uid: int):
            m = await message.bot.send_photo(
                chat_id=uid,
                photo=file_id,
                caption=caption if caption else None,
            )
            try:
                await add_broadcast_delivery(
                    db_path, broadcast_id, uid, int(m.message_id), "sent", None
                )
            except Exception:
                logger.exception("Failed to record broadcast delivery for %s", uid)
            return m

        sent, failed, _blocked = await _broadcast_to_users(
            message.bot, db_path, user_ids, send=_send_photo
        )

        await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
        await state.clear()
        msg = await message.answer(
            f"✅ Broadcast sent.\n\nDelivered: {sent}\nFailed: {failed}",
            reply_markup=admin_broadcast_keyboard(),
        )
        await track_step_message(state, msg.message_id)

    async def text_handler(message: Message, state: FSMContext):
        await touch_user_activity(db_path, message.from_user.id)
        role = await get_user_role(db_path, message.from_user.id)
        if role is None:
            await cmd_start(message, db_path, admin_ids)
            await state.clear()
            return

        text = (message.text or "").strip()

        if is_admin_user(message.from_user.id, admin_ids):
            norm = ((message.text or "").strip()).lower()
            if text in {"📦 Orders", "Manage Food Houses 🏠", "Manage Menu 🍲", "Manage Blocks 🏫", "Set Payments 💳"}:
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                await state.clear()
                if text == "📦 Orders":
                    await handle_admin_view_orders(message, db_path, state)
                    return
                if text == "Manage Food Houses 🏠":
                    vendors = await admin_list_vendors(db_path)
                    msg = await message.answer("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
                    await track_step_message(state, msg.message_id)
                    return
                if text == "Manage Menu 🍲":
                    msg = await message.answer("🍲 Menu Management", reply_markup=admin_menu_root_keyboard())
                    await track_step_message(state, msg.message_id)
                    return
                if text == "Manage Blocks 🏫":
                    blocks = await admin_list_blocks(db_path)
                    msg = await message.answer("🏫 Blocks Management", reply_markup=admin_blocks_keyboard(blocks))
                    await track_step_message(state, msg.message_id)
                    return
                if text == "Set Payments 💳":
                    cbe = _format_payment_value(await get_setting(db_path, "cbe_account"))
                    tele = _format_payment_value(await get_setting(db_path, "telebirr_number"))
                    msg = await message.answer(
                        "💳 Payment Accounts\n\n"
                        f"CBE: {cbe or '-'}\n"
                        f"Telebirr: {tele or '-'}",
                        reply_markup=admin_payments_keyboard(cbe, tele),
                    )
                    await track_step_message(state, msg.message_id)
                    return

            if text == "📢 Broadcast":
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                await state.clear()
                msg = await message.answer(
                    "📢 Broadcast\n\nSend an announcement to all users.",
                    reply_markup=admin_broadcast_keyboard(),
                )
                await track_step_message(state, msg.message_id)
                return

            if text in {"🎟️ Promo Codes", "Promo Codes 🎟️"}:
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                await state.clear()
                promos = await admin_list_promo_codes(db_path)
                msg = await message.answer("🎟️ Promo Codes", reply_markup=admin_promos_keyboard(promos))
                await track_step_message(state, msg.message_id)
                return

            if text in {"User Management 👤", "🧾 Order History", "Reports 📝"}:
                await state.clear()
                await message.answer(
                    "ℹ️ This option is not available in the current minimal version.\n\n"
                    "Use core options: Orders, Menu, Food Houses, Blocks, Payments, Promos, or Ordering ON/OFF."
                )
                return

            if "payment" in norm or "payments" in norm:
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                await state.clear()
                cbe = _format_payment_value(await get_setting(db_path, "cbe_account"))
                tele = _format_payment_value(await get_setting(db_path, "telebirr_number"))
                msg = await message.answer(
                    "💳 Payment Accounts\n\n"
                    f"CBE: {cbe or '-'}\n"
                    f"Telebirr: {tele or '-'}",
                    reply_markup=admin_payments_keyboard(cbe, tele),
                )
                await track_step_message(state, msg.message_id)
                return

            if text == "Set Delivery Fee 💵":
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                await state.clear()
                blocks = await admin_list_blocks(db_path)
                msg = await message.answer("💵 Set Delivery Fee\n\nChoose a block to edit its fee:", reply_markup=admin_blocks_keyboard(blocks))
                await track_step_message(state, msg.message_id)
                return

            if text in {"Update Status 🔄", "Update Status"} or "update status" in norm:
                await state.clear()
                orders = await admin_list_orders(db_path, status="Pending Confirmation", limit=30)
                if not orders:
                    orders = await admin_list_orders(db_path, limit=30)
                    orders = [o for o in orders if is_active_order_status(o.get("status"))]
                if not orders:
                    await message.answer("No active orders to update.")
                    return
                msg = await message.answer("Select an order to update:", reply_markup=admin_orders_keyboard(orders))
                await track_step_message(state, msg.message_id)
                return

            if text == "Ordering ON/OFF 🔘":
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                await state.clear()
                ordering_flag = await get_setting(db_path, "ordering_open")
                ordering_open = ordering_flag is None or ordering_flag.strip() != "0"
                msg = await message.answer(
                    "🔘 Ordering ON/OFF\n\nUse this to open/close ordering instantly.",
                    reply_markup=admin_service_keyboard(ordering_open),
                )
                await track_step_message(state, msg.message_id)
                return

            if text == "📦 Orders":
                await handle_admin_view_orders(message, db_path, state)
                return

        # Admin typed input states
        if is_admin_user(message.from_user.id, admin_ids):
            current = await state.get_state()
            if current == AdminFlow.creating_promo_code.state:
                code = (message.text or "").strip().upper()
                if not code or len(code) < 3:
                    await message.answer("❌ Promo code too short.")
                    return
                await state.update_data(promo_code=code)
                await state.set_state(AdminFlow.creating_promo_type)
                await message.answer("Choose discount type:", reply_markup=admin_promo_type_keyboard())
                return

            if current == AdminFlow.creating_promo_value.state:
                try:
                    val = int((message.text or "").strip())
                except ValueError:
                    await message.answer("❌ Enter a number.")
                    return
                await state.update_data(promo_value=val)
                await state.set_state(AdminFlow.creating_promo_max_uses)
                await message.answer("Enter max uses (number) or 0 for unlimited:")
                return

            if current == AdminFlow.creating_promo_max_uses.state:
                try:
                    mu = int((message.text or "").strip())
                except ValueError:
                    await message.answer("❌ Enter a number.")
                    return
                data2 = await state.get_data()
                code = data2.get("promo_code")
                dtype = data2.get("promo_type")
                val = int(data2.get("promo_value") or 0)
                max_uses = None if mu <= 0 else int(mu)
                await admin_create_promo_code(db_path, code, dtype, val, max_uses)
                await state.clear()
                promos = await admin_list_promo_codes(db_path)
                await message.answer("✅ Promo created.", reply_markup=admin_promos_keyboard(promos))
                return

            if current == AdminFlow.broadcasting_message.state:
                body = (message.text or "").strip()
                if not body:
                    await message.answer("❌ Message text required. You can also send a photo with caption.")
                    return

                try:
                    broadcast_id = await create_broadcast(db_path, body, message.from_user.id)
                except Exception:
                    await state.clear()
                    await message.answer("❌ Failed to create broadcast record.")
                    return

                user_ids = await list_all_user_telegram_ids(db_path)

                async def _send_text(uid: int):
                    m = await message.bot.send_message(chat_id=uid, text=body)
                    try:
                        await add_broadcast_delivery(
                            db_path, broadcast_id, uid, int(m.message_id), "sent", None
                        )
                    except Exception:
                        logger.exception(
                            "Failed to record broadcast delivery for %s", uid
                        )
                    return m

                sent, failed, _blocked = await _broadcast_to_users(
                    message.bot, db_path, user_ids, send=_send_text
                )

                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids={message.message_id})
                msg = await message.answer(
                    f"✅ Broadcast sent.\n\nDelivered: {sent}\nFailed: {failed}",
                    reply_markup=admin_broadcast_keyboard(),
                )
                await track_step_message(state, msg.message_id)
                return

            if current == AdminFlow.adding_vendor_name.state:
                name = (message.text or "").strip()
                if not name:
                    await message.answer("❌ Food house name required.")
                    return
                await admin_seed_vendor(db_path, name)
                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer(
                    "✅ Food house added.\n\nWhat next?",
                    reply_markup=admin_vendor_saved_keyboard(),
                )
                return

            if current == AdminFlow.adding_item_name.state:
                name = (message.text or "").strip()
                if not name:
                    await message.answer("❌ Item name required.")
                    return
                await state.update_data(item_name=name)
                await state.set_state(AdminFlow.adding_item_price)
                await message.answer("Enter item price (ETB):")
                return

            if current == AdminFlow.adding_item_price.state:
                try:
                    price = int((message.text or "").strip())
                except ValueError:
                    await message.answer("❌ Enter a number (ETB).")
                    return
                data = await state.get_data()
                vendor_id = int(data["vendor_id"])
                category_id = int(data.get("category_id") or 0)
                if not category_id:
                    await state.clear()
                    await message.answer("❌ Missing category. Please start again.")
                    return
                item_name = data["item_name"]
                await admin_seed_menu_item(db_path, vendor_id, category_id, item_name, price)
                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer(
                    "✅ Item added.\n\nWhat next?",
                    reply_markup=admin_item_saved_keyboard(vendor_id, category_id),
                )
                return

            if current == AdminFlow.editing_item_name.state:
                name = (message.text or "").strip()
                if not name:
                    await message.answer("❌ Item name required.")
                    return
                data = await state.get_data()
                vendor_id = int(data.get("vendor_id") or 0)
                item_id = int(data.get("item_id") or 0)
                if not vendor_id or not item_id:
                    await state.clear()
                    await message.answer("❌ Missing item. Please start again.")
                    return
                await admin_update_menu_item_name(db_path, item_id, name)
                items = await admin_list_menu_items(db_path, vendor_id)
                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer("✅ Item name updated.", reply_markup=admin_items_keyboard(items, "edit_name"))
                return

            if current == AdminFlow.adding_category_name.state:
                name = (message.text or "").strip()
                data = await state.get_data()
                vendor_id = int(data.get("vendor_id") or 0)
                if not vendor_id:
                    await state.clear()
                    await message.answer("❌ Missing vendor.")
                    return
                if not name:
                    await message.answer("❌ Category name required.")
                    return
                await admin_create_menu_category(db_path, vendor_id, name)
                cats = await admin_list_menu_categories(db_path, vendor_id)
                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer(
                    f"📂 Categories (vendor {vendor_id})",
                    reply_markup=admin_categories_keyboard(vendor_id, cats),
                )
                return

            if current == AdminFlow.editing_category_name.state:
                name = (message.text or "").strip()
                data = await state.get_data()
                vendor_id = int(data.get("vendor_id") or 0)
                category_id = int(data.get("category_id") or 0)
                if not vendor_id or not category_id:
                    await state.clear()
                    await message.answer("❌ Missing category.")
                    return
                if not name:
                    await message.answer("❌ Category name required.")
                    return
                await admin_update_menu_category_name(db_path, category_id, name)
                cats = await admin_list_menu_categories(db_path, vendor_id)
                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer(
                    f"📂 Categories (vendor {vendor_id})",
                    reply_markup=admin_categories_keyboard(vendor_id, cats),
                )
                return

            if current == AdminFlow.editing_item_price.state:
                try:
                    price = int((message.text or "").strip())
                except ValueError:
                    await message.answer("❌ Enter a number (ETB).")
                    return
                data = await state.get_data()
                item_id = int(data["item_id"])
                await admin_update_menu_item_price(db_path, item_id, price)
                vendor_id = int(data.get("vendor_id") or 0)
                if not vendor_id:
                    item = await get_menu_item(db_path, item_id)
                    vendor_id = int(item.get("vendor_id") or 0) if item else 0
                items = await admin_list_menu_items(db_path, vendor_id) if vendor_id else []
                await state.clear()
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                if items:
                    await message.answer("✅ Price updated.", reply_markup=admin_items_keyboard(items, "edit_price"))
                else:
                    await message.answer("✅ Price updated.", reply_markup=admin_menu_root_keyboard())
                return

            if current == AdminFlow.adding_block_name.state:
                name = (message.text or "").strip()
                if not name:
                    await message.answer("❌ Block name required.")
                    return
                await state.update_data(block_name=name)
                await state.set_state(AdminFlow.adding_block_fee)
                await message.answer("Enter delivery fee for this block (ETB):")
                return

            if current == AdminFlow.adding_block_fee.state:
                try:
                    fee = int((message.text or "").strip())
                except ValueError:
                    await message.answer("❌ Enter a number (ETB).")
                    return
                data = await state.get_data()
                name = data["block_name"]
                await admin_seed_block(db_path, name, fee)
                await state.clear()
                blocks = await admin_list_blocks(db_path)
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer("✅ Block added.", reply_markup=admin_blocks_keyboard(blocks))
                return

            if current == AdminFlow.editing_block_fee.state:
                try:
                    fee = int((message.text or "").strip())
                except ValueError:
                    await message.answer("❌ Enter a number (ETB).")
                    return
                data = await state.get_data()
                block_id = int(data["block_id"])
                await admin_update_block_fee(db_path, block_id, fee)
                await state.clear()
                blocks = await admin_list_blocks(db_path)
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer("✅ Fee updated.", reply_markup=admin_blocks_keyboard(blocks))
                return

            if current == AdminFlow.setting_cbe_name.state:
                name = (message.text or "").strip()
                if not name:
                    await message.answer("❌ Name required.")
                    return
                await state.update_data(payment_name=name)
                await state.set_state(AdminFlow.setting_cbe_number)
                await message.answer("Enter CBE account number:")
                return

            if current == AdminFlow.setting_cbe_number.state:
                number = (message.text or "").strip()
                if not number:
                    await message.answer("❌ Account number required.")
                    return
                data = await state.get_data()
                name = (data.get("payment_name") or "").strip()
                if not name:
                    await message.answer("❌ Name required. Please start again.")
                    await state.clear()
                    return
                await set_setting(db_path, "cbe_account", _compose_payment_value(name, number))
                await state.clear()
                cbe = _format_payment_value(await get_setting(db_path, "cbe_account"))
                tele = _format_payment_value(await get_setting(db_path, "telebirr_number"))
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer("✅ Saved.", reply_markup=admin_payments_keyboard(cbe, tele))
                return

            if current == AdminFlow.setting_telebirr_name.state:
                name = (message.text or "").strip()
                if not name:
                    await message.answer("❌ Name required.")
                    return
                await state.update_data(payment_name=name)
                await state.set_state(AdminFlow.setting_telebirr_number)
                await message.answer("Enter Telebirr number:")
                return

            if current == AdminFlow.setting_telebirr_number.state:
                number = (message.text or "").strip()
                if not number:
                    await message.answer("❌ Number required.")
                    return
                data = await state.get_data()
                name = (data.get("payment_name") or "").strip()
                if not name:
                    await message.answer("❌ Name required. Please start again.")
                    await state.clear()
                    return
                await set_setting(db_path, "telebirr_number", _compose_payment_value(name, number))
                await state.clear()
                cbe = _format_payment_value(await get_setting(db_path, "cbe_account"))
                tele = _format_payment_value(await get_setting(db_path, "telebirr_number"))
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer("✅ Saved.", reply_markup=admin_payments_keyboard(cbe, tele))
                return
        if is_admin_user(message.from_user.id, admin_ids):
            current = await state.get_state()
            if current == AdminFlow.setting_ordering_closed_reason.state:
                reason = (message.text or "").strip()
                if reason == "-":
                    await set_setting(db_path, "ordering_closed_reason", "")
                elif reason:
                    await set_setting(db_path, "ordering_closed_reason", reason)
                await state.clear()
                ordering_flag = await get_setting(db_path, "ordering_open")
                ordering_open = ordering_flag is None or ordering_flag.strip() != "0"
                await cleanup_step_messages(message.bot, message.chat.id, state, keep_message_ids=set())
                await message.answer(
                    "✅ Ordering closed reason saved.",
                    reply_markup=admin_service_keyboard(ordering_open),
                )
                return

        if text in {"🍽️ Order Food", "Order Food 🍽️"}:
            await handle_browse_vendors(message, db_path, admin_ids, state)
            return
        if text in {"📦 View Orders", "View Orders 📦", "📦 My Orders"}:
            if not is_admin_user(message.from_user.id, admin_ids):
                await handle_view_orders(message, db_path, state)
            return
        if text == "⭐ Favorites":
            if not is_admin_user(message.from_user.id, admin_ids):
                await handle_favorites(message, state)
            return
        if text in {"❓ Help", "📃Guide"}:
            await handle_help_guide(message, state)
            return
        if text in {"📞 Contact Us", "📝 Feedback"}:
            await state.clear()
            await message.answer(
                "ℹ️ This option has been simplified.\n\n"
                "Please use ❓ Help for the current ordering guide and support path."
            )
            return

        if text.startswith("/seed_vendor"):
            await admin_cmd_seed_vendor(message, db_path, admin_ids)
            return
        if text.startswith("/seed_item"):
            await admin_cmd_seed_item(message, db_path, admin_ids)
            return
        if text.startswith("/seed_block"):
            await admin_cmd_seed_block(message, db_path, admin_ids)
            return
        if text.startswith("/set_cbe"):
            await admin_cmd_set_cbe(message, db_path, admin_ids)
            return
        if text.startswith("/set_telebirr"):
            await admin_cmd_set_telebirr(message, db_path, admin_ids)
            return

        await handle_placeholder(message, state)

    async def phone_handler(message: Message, state: FSMContext):
        await touch_user_activity(db_path, message.from_user.id)
        await on_phone_message(message, db_path, state)

    async def payment_photo_handler(message: Message, state: FSMContext):
        await touch_user_activity(db_path, message.from_user.id)
        await on_payment_photo(message, db_path, state, admin_ids)

    async def vendor_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_vendor_callback(query, db_path, state)

    async def category_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_category_callback(query, db_path, state)

    async def add_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_add_item_callback(query, db_path, state)

    async def rem_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_remove_item_callback(query, db_path, state)

    async def done_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_done_callback(query, db_path, state)

    async def block_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_block_callback(query, db_path, state)

    async def confirm_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        await on_confirm_callback(query, db_path, state, admin_ids)

    async def promo_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        if not await enforce_ordering_open_callback(query, db_path, admin_ids):
            return
        if await state.get_state() != OrderFlow.confirming.state:
            await query.answer()
            return
        await state.set_state(OrderFlow.entering_promo)
        await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
        msg = await query.message.answer(
            "🎟️ Enter promo code now (e.g. WSU10) or type 0 to cancel:",
            reply_markup=checkout_cancel_keyboard(),
        )
        await track_step_message(state, msg.message_id)
        await query.answer()

    async def paid_cb(query: CallbackQuery, state: FSMContext):
        await on_paid_callback(query, state)

    async def cancel_cb(query: CallbackQuery, state: FSMContext):
        await on_cancel_callback(query, state)

    async def order_cb(query: CallbackQuery):
        await touch_user_activity(db_path, query.from_user.id)
        await on_order_detail_callback(query, db_path)

    async def admin_payments_cb(query: CallbackQuery, state: FSMContext):
        if not is_admin_user(query.from_user.id, admin_ids):
            await query.answer("Admin only", show_alert=True)
            return
        if (query.data or "") != "adm:payments":
            await query.answer()
            return
        try:
            await state.clear()
            cbe = _format_payment_value(await get_setting(db_path, "cbe_account"))
            tele = _format_payment_value(await get_setting(db_path, "telebirr_number"))
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            text = (
                "💳 Payment Accounts\n\n"
                f"CBE: {cbe or '-'}\n"
                f"Telebirr: {tele or '-'}"
            )
            try:
                await query.message.edit_text(text, reply_markup=admin_payments_keyboard(cbe, tele))
            except Exception:
                msg = await query.message.answer(text, reply_markup=admin_payments_keyboard(cbe, tele))
                await track_step_message(state, msg.message_id)
            await query.answer()
        except Exception:
            await query.answer("❌ Failed to open Payments.", show_alert=True)

    async def admin_set_cbe_cb(query: CallbackQuery, state: FSMContext):
        if not is_admin_user(query.from_user.id, admin_ids):
            await query.answer("Admin only", show_alert=True)
            return
        if (query.data or "") != "adm:pay:set_cbe":
            await query.answer()
            return
        try:
            await state.clear()
            await state.set_state(AdminFlow.setting_cbe_name)
            await query.answer("✍️ Send the CBE account name in chat")
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            msg = await query.message.answer("Enter CBE account name:")
            await track_step_message(state, msg.message_id)
        except Exception:
            await query.answer("❌ Failed to start CBE setup.", show_alert=True)

    async def admin_set_tele_cb(query: CallbackQuery, state: FSMContext):
        if not is_admin_user(query.from_user.id, admin_ids):
            await query.answer("Admin only", show_alert=True)
            return
        if (query.data or "") != "adm:pay:set_tele":
            await query.answer()
            return
        try:
            await state.clear()
            await state.set_state(AdminFlow.setting_telebirr_name)
            await query.answer("✍️ Send the Telebirr account name in chat")
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            msg = await query.message.answer("Enter Telebirr account name:")
            await track_step_message(state, msg.message_id)
        except Exception:
            await query.answer("❌ Failed to start Telebirr setup.", show_alert=True)

    async def oback_cb(query: CallbackQuery, state: FSMContext):
        data = (query.data or "").strip()
        parts = data.split(":")
        if len(parts) < 2:
            await query.answer()
            return
        target = parts[1]

        d = await state.get_data()
        vendor_id = int(d.get("vendor_id") or 0)
        last_category_id = int(d.get("last_category_id") or 0)
        cart: dict[int, int] = d.get("cart", {})
        cart_msg_id = d.get("cart_message_id")

        if target == "vendors":
            await state.clear()
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
            vendors = await list_active_vendors(db_path)
            msg = await query.message.answer(
                "🍴 Choose a food house\n\nSelect where you want to order from 👇",
                reply_markup=vendor_list_keyboard(vendors),
            )
            await track_step_message(state, msg.message_id)
            await state.set_state(OrderFlow.choosing_vendor)
            await query.answer()
            return

        if target == "categories":
            if not vendor_id:
                await query.answer()
                return
            vendor = await get_vendor(db_path, vendor_id)
            categories = await list_menu_categories_for_vendor(db_path, vendor_id)
            keep: set[int] = set()
            if cart_msg_id:
                keep.add(int(cart_msg_id))
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=keep)
            msg = await query.message.answer(
                f"📂 {vendor['name'] if vendor else vendor_id} Categories\n\nSelect a category 👇",
                reply_markup=vendor_categories_keyboard(vendor_id, categories),
            )
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if target == "menu":
            if not vendor_id or not last_category_id:
                await query.answer()
                return
            vendor = await get_vendor(db_path, vendor_id)
            items = await list_active_menu_items_by_category(db_path, vendor_id, last_category_id)
            mini_lines, mini_subtotal, mini_count = await mini_cart_preview_lines(db_path, cart)
            keep: set[int] = set()
            if cart_msg_id:
                keep.add(int(cart_msg_id))
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=keep)
            msg = await query.message.answer(
                f"🍲 {vendor['name'] if vendor else vendor_id} Menu\n\n"
                "Tap items to add to your cart 🛒\n\n"
                "🛒 Live Cart\n"
                f"Items: {mini_count}\n"
                f"Subtotal: {mini_subtotal} ETB\n"
                f"{mini_lines}",
                reply_markup=menu_keyboard(items, mini_count),
            )
            await track_step_message(state, msg.message_id)
            await state.set_state(OrderFlow.choosing_items)
            await query.answer()
            return

        if target == "cart":
            cart_text, subtotal = await format_cart_lines(db_path, cart)
            count = cart_item_count(cart)
            text = (
                f"🛒 Your Cart\n\n"
                f"🧺 Items: {count}\n"
                f"💰 Current total: {subtotal} ETB\n\n"
                f"{cart_text}\n\n"
                f"{delivery_fee_info_note()}\n\n"
                "➕ Add more items or tap ✅ Done"
            )
            kb = await cart_controls_keyboard(db_path, list(cart.keys()))
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
            msg = await query.message.answer(text, reply_markup=kb)
            await state.update_data(cart_message_id=msg.message_id)
            await track_step_message(state, msg.message_id)
            await state.set_state(OrderFlow.choosing_items)
            await query.answer()
            return

        if target == "summary":
            if not vendor_id or not int(d.get("block_id") or 0) or not d.get("phone_number"):
                await query.answer()
                return
            vendor = await get_vendor(db_path, vendor_id)
            block = await get_block(db_path, int(d.get("block_id") or 0))
            items_text, subtotal = await format_cart_lines(db_path, cart)
            count = cart_item_count(cart)
            base_fee = int(d.get("base_delivery_fee_etb") or d.get("delivery_fee_etb") or 0)
            fee, can_confirm = calculate_delivery_fee(base_fee, count)
            total = int(d.get("total") or (subtotal + fee))
            await state.update_data(subtotal=subtotal, delivery_fee_etb=fee, total=total)
            await state.set_state(OrderFlow.confirming)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids=set())
            msg = await query.message.answer(
                "🧾 Order Summary\n\n"
                f"🏠 Vendor: {vendor['name'] if vendor else vendor_id}\n"
                "🍴 Items:\n"
                f"{items_text}\n\n"
                f"🏫 Delivery Block: {block['name'] if block else d.get('block_id')}\n"
                f"📞 Phone: {d.get('phone_number')}\n"
                f"🚚 Delivery Fee: {fee} ETB\n"
                f"💰 Total: {total} ETB\n\n"
                f"{delivery_fee_info_note()}\n\n"
                "✅ If everything looks correct, confirm to proceed to payment 👇",
                reply_markup=confirm_keyboard_limited(can_confirm),
            )
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        await query.answer()

    async def checkout_back_cb(query: CallbackQuery, state: FSMContext):
        current = await state.get_state()
        if current != OrderFlow.entering_promo.state:
            await query.answer()
            return
        data = await state.get_data()
        vendor_id = int(data.get("vendor_id") or 0)
        block_id = int(data.get("block_id") or 0)
        cart: dict[int, int] = data.get("cart", {})
        phone = data.get("phone_number")
        fee = int(data.get("delivery_fee_etb") or 0)
        subtotal = int(data.get("subtotal") or 0)
        promo_code = data.get("promo_code")
        promo_discount = int(data.get("discount_amount_etb") or 0)
        if promo_discount < 0:
            promo_discount = 0
        if promo_discount > subtotal:
            promo_discount = subtotal
        total = int(data.get("total") or ((subtotal - promo_discount) + fee))
        if total < 0:
            total = 0

        vendor = await get_vendor(db_path, vendor_id) if vendor_id else None
        block = await get_block(db_path, block_id) if block_id else None
        items_text, _ = await format_cart_lines(db_path, cart)
        promo_line = (
            f"💸 Promo: {promo_code} (-{promo_discount} ETB on items)\n" if promo_code and promo_discount > 0 else ""
        )

        await state.set_state(OrderFlow.confirming)
        await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
        msg = await query.message.answer(
            "🧾 Order Summary\n\n"
            f"🏠 Vendor: {vendor['name'] if vendor else vendor_id}\n"
            "🍴 Items:\n"
            f"{items_text}\n\n"
            f"🏫 Delivery Block: {block['name'] if block else block_id}\n"
            f"📞 Phone: {phone}\n"
            f"{promo_line}"
            f"🚚 Delivery Fee: {fee} ETB\n"
            f"💰 Total: {total} ETB\n\n"
            "Please confirm before payment 👇",
            reply_markup=confirm_keyboard(),
        )
        await track_step_message(state, msg.message_id)
        await query.answer()

    async def promo_text_handler(message: Message, state: FSMContext):
        if await state.get_state() != OrderFlow.entering_promo.state:
            return
        code = (message.text or "").strip()

        data = await state.get_data()
        vendor_id = int(data.get("vendor_id") or 0)
        block_id = int(data.get("block_id") or 0)
        cart: dict[int, int] = data.get("cart", {})
        phone = data.get("phone_number")
        fee = int(data.get("delivery_fee_etb") or 0)
        subtotal = int(data.get("subtotal") or 0)

        async def render_summary(promo_code: str | None, discount: int, total: int) -> None:
            vendor = await get_vendor(db_path, vendor_id) if vendor_id else None
            block = await get_block(db_path, block_id) if block_id else None
            items_text, _ = await format_cart_lines(db_path, cart)
            promo_line = (
                f"💸 Promo: {promo_code} (-{discount} ETB on items)\n" if promo_code and discount > 0 else ""
            )
            await message.answer(
                "🧾 Order Summary\n\n"
                f"🏠 Vendor: {vendor['name'] if vendor else vendor_id}\n"
                "🍴 Items:\n"
                f"{items_text}\n\n"
                f"🏫 Delivery Block: {block['name'] if block else block_id}\n"
                f"📞 Phone: {phone}\n"
                f"{promo_line}"
                f"🚚 Delivery Fee: {fee} ETB\n"
                f"💰 Total: {total} ETB\n\n"
                "Please confirm before payment 👇",
                reply_markup=confirm_keyboard(),
            )

        if code == "0":
            await state.set_state(OrderFlow.confirming)
            existing_promo = data.get("promo_code")
            existing_discount = int(data.get("discount_amount_etb") or 0)
            existing_total = int(data.get("total") or (subtotal + fee))
            await render_summary(existing_promo, existing_discount, existing_total)
            return

        existing_promo = (data.get("promo_code") or "").strip() or None
        if existing_promo:
            await message.answer(
                "⚠️ You already applied a promo code for this order.\n\n"
                "✅ You can only use one promo code."
            )
            existing_discount = int(data.get("discount_amount_etb") or 0)
            existing_total = int(data.get("total") or ((subtotal - max(existing_discount, 0)) + fee))
            await state.set_state(OrderFlow.confirming)
            await render_summary(existing_promo, existing_discount, existing_total)
            return

        if await user_has_used_any_promo_code(db_path, message.from_user.id):
            await message.answer(
                "⚠️ Promo code can only be used once per user.\n\n"
                "✅ You already used a promo code before, so you can’t apply another one."
            )
            await state.set_state(OrderFlow.confirming)
            existing_total = int(data.get("total") or (subtotal + fee))
            await render_summary(None, 0, existing_total)
            return

        promo = await get_active_promo_code(db_path, code)
        if not promo:
            await message.answer("❌ Invalid or expired promo code.")
            return

        dtype = (promo.get("discount_type") or "").strip().lower()
        dval = int(promo.get("discount_value") or 0)
        if dval < 0:
            dval = 0
        if dtype == "percent":
            if dval > 100:
                dval = 100
            discount = int(round(subtotal * (dval / 100.0)))
        else:
            # treat any non-percent type as fixed ETB
            discount = dval
        if discount > subtotal:
            discount = subtotal
        if discount < 0:
            discount = 0
        total = (subtotal - discount) + fee
        if total < 0:
            total = 0
        await state.update_data(promo_code=promo.get("code"), discount_amount_etb=discount, total=total)
        await state.set_state(OrderFlow.confirming)

        await render_summary(promo.get("code"), discount, total)

    async def admin_cb(query: CallbackQuery, state: FSMContext):
        """Catch-all dispatcher for every ``adm:*`` callback.

        Dispatched by callback-data prefix. Sections in order:
          - users (disabled), home/back navigation
          - daily orders, promos, service (ordering on/off)
          - broadcast, orders, blocks, vendors
          - menu, categories, items (add/edit/toggle/delete)
          - payments handled by dedicated callbacks above
        """
        await touch_user_activity(db_path, query.from_user.id)
        if not is_admin_user(query.from_user.id, admin_ids):
            await query.answer("Admin only", show_alert=True)
            return
        data = query.data or ""

        if data.startswith("adm:users"):
            await state.clear()
            await query.answer("This feature is not available in the current minimal version.", show_alert=True)
            return

        async def _show_inline(text: str, reply_markup: InlineKeyboardMarkup | None = None) -> None:
            await replace_callback_message(query, state, text, reply_markup=reply_markup, keep_current=True)

        if data == "noop":
            await query.answer()
            return

        if data in {"adm:home", "adm:back"}:
            await state.clear()
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.delete()
            except Exception:
                pass
            await query.message.answer(
                "👋 Welcome back, Admin!\n\nChoose an option from the menu below:",
                reply_markup=admin_menu_keyboard(),
            )
            await query.answer()
            return

        if data == "adm:daily_orders:clear":
            today = datetime.now().date().isoformat()
            await _show_inline(
                "🧾 Daily Audit: Clear Today's Orders\n\n"
                "⚠️ This will permanently delete ALL orders created today (including payment proof + rating).\n\n"
                f"📅 Date: {today}\n\n"
                "Are you sure?",
                reply_markup=ikb(
                    [
                        [InlineKeyboardButton(text="🗑 Yes, Delete Today", callback_data="adm:daily_orders:clear:confirm")],
                        [InlineKeyboardButton(text="Cancel ❌", callback_data="adm:home")],
                    ]
                ),
            )
            await query.answer()
            return

        if data == "adm:daily_orders:clear:confirm":
            today = datetime.now().date().isoformat()
            deleted = await admin_delete_orders_for_date(db_path, today)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            msg = await query.message.answer(
                "✅ Daily cleanup completed!\n\n"
                f"🧾 Deleted orders: {deleted}\n"
                f"📅 Date: {today}",
                reply_markup=admin_menu_keyboard(),
            )
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:promos":
            await state.clear()
            promos = await admin_list_promo_codes(db_path)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🎟️ Promo Codes", reply_markup=admin_promos_keyboard(promos))
            except Exception:
                msg = await query.message.answer("🎟️ Promo Codes", reply_markup=admin_promos_keyboard(promos))
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:promo:create":
            await state.clear()
            await state.set_state(AdminFlow.creating_promo_code)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            msg = await query.message.answer("Enter promo code (e.g. WSU10):")
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data.startswith("adm:promo:toggle:"):
            promo_id = int(data.split(":")[-1])
            promos = await admin_list_promo_codes(db_path)
            current = next((p for p in promos if int(p.get("id")) == promo_id), None)
            if current:
                active = int(current.get("active", 1)) == 1
                await admin_set_promo_active(db_path, promo_id, not active)
            promos = await admin_list_promo_codes(db_path)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🎟️ Promo Codes", reply_markup=admin_promos_keyboard(promos))
            except Exception:
                msg = await query.message.answer("🎟️ Promo Codes", reply_markup=admin_promos_keyboard(promos))
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:promo:type:percent":
            await state.update_data(promo_type="percent")
            await state.set_state(AdminFlow.creating_promo_value)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            msg = await query.message.answer("Enter percent value (e.g. 10):")
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:promo:type:etb":
            await state.update_data(promo_type="etb")
            await state.set_state(AdminFlow.creating_promo_value)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            msg = await query.message.answer("Enter ETB discount (e.g. 20):")
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:service":
            await state.clear()
            ordering_flag = await get_setting(db_path, "ordering_open")
            ordering_open = ordering_flag is None or ordering_flag.strip() != "0"
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text(
                    "🔘 Ordering ON/OFF\n\nUse this to open/close ordering instantly.",
                    reply_markup=admin_service_keyboard(ordering_open),
                )
            except Exception:
                msg = await query.message.answer(
                    "🔘 Ordering ON/OFF\n\nUse this to open/close ordering instantly.",
                    reply_markup=admin_service_keyboard(ordering_open),
                )
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:service:toggle":
            ordering_flag = await get_setting(db_path, "ordering_open")
            ordering_open = ordering_flag is None or ordering_flag.strip() != "0"
            if ordering_open:
                await set_setting(db_path, "ordering_open", "0")
                await state.set_state(AdminFlow.setting_ordering_closed_reason)
                await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
                msg = await query.message.answer(
                    "✍️ Ordering is now CLOSED. Send the reason message to show users (or send '-' to keep default):"
                )
                await track_step_message(state, msg.message_id)
                await query.answer("Closed")
                return
            await set_setting(db_path, "ordering_open", "1")
            await set_setting(db_path, "ordering_closed_reason", "")
            ordering_open = True
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text(
                    "🔘 Ordering ON/OFF\n\nUse this to open/close ordering instantly.",
                    reply_markup=admin_service_keyboard(ordering_open),
                )
            except Exception:
                msg = await query.message.answer(
                    "🔘 Ordering ON/OFF\n\nUse this to open/close ordering instantly.",
                    reply_markup=admin_service_keyboard(ordering_open),
                )
                await track_step_message(state, msg.message_id)
            await query.answer("Updated")
            return

        if data == "adm:broadcast":
            await state.clear()
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("📢 Broadcast\n\nSend an announcement to all users.", reply_markup=admin_broadcast_keyboard())
            except Exception:
                msg = await query.message.answer("📢 Broadcast\n\nSend an announcement to all users.", reply_markup=admin_broadcast_keyboard())
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data == "adm:broadcast:write":
            await state.set_state(AdminFlow.broadcasting_message)
            await _show_inline("✍️ Send the broadcast now (text or photo):")
            await query.answer()
            return

        if data == "adm:broadcast:history":
            await state.clear()
            broadcasts = await list_broadcasts(db_path, limit=25)
            await _show_inline("🕘 Broadcast History", reply_markup=admin_broadcast_history_keyboard(broadcasts))
            await query.answer()
            return

        if data.startswith("adm:broadcast:open:"):
            bid = int(data.split(":")[-1])
            broadcasts = await list_broadcasts(db_path, limit=200)
            b = next((x for x in broadcasts if int(x.get("id") or 0) == bid), None)
            if not b:
                await query.answer("Not found", show_alert=True)
                return
            text = str(b.get("text") or "")
            await _show_inline(f"📢 Broadcast #{bid}\n\n{text}", reply_markup=admin_broadcast_detail_keyboard(bid))
            await query.answer()
            return

        if data.startswith("adm:broadcast:del:"):
            bid = int(data.split(":")[-1])
            await _show_inline(
                "⚠️ Delete Broadcast\n\n"
                "This will try to delete the broadcast message from users’ chats (best effort).\n"
                "Some deletions may fail if users blocked the bot or deleted the chat.\n\nProceed?",
                reply_markup=admin_confirm_keyboard(
                    f"adm:broadcast:del_confirm:{bid}",
                    f"adm:broadcast:open:{bid}",
                ),
            )
            await query.answer()
            return

        if data.startswith("adm:broadcast:del_confirm:"):
            bid = int(data.split(":")[-1])
            deliveries = await list_broadcast_deliveries(db_path, bid)
            for d in deliveries:
                mid = d.get("message_id")
                tid = d.get("user_telegram_id")
                if not mid or not tid:
                    continue
                try:
                    await query.bot.delete_message(int(tid), int(mid))
                except Exception:
                    pass
            await delete_broadcast_records(db_path, bid)
            broadcasts = await list_broadcasts(db_path, limit=25)
            await _show_inline("✅ Broadcast deleted.", reply_markup=admin_broadcast_history_keyboard(broadcasts))
            await query.answer("Deleted")
            return

        if data == "adm:orders":
            await state.clear()
            orders = await admin_list_orders(db_path, status="Pending Confirmation", limit=30)
            if not orders:
                orders = await admin_list_orders(db_path, limit=30)
            if not orders:
                await query.answer("No orders", show_alert=True)
                return
            title = "📦 Orders (Pending Confirmation)" if any(o.get("status") == "Pending Confirmation" for o in orders) else "📦 Recent Orders"
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text(title, reply_markup=admin_orders_keyboard(orders))
            except Exception:
                msg = await query.message.answer(title, reply_markup=admin_orders_keyboard(orders))
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data.startswith("adm:order:"):
            order_id = int(data.split(":")[-1])
            await render_admin_order_detail(query, db_path, order_id, state)
            return

        if data.startswith("adm:pay:confirm:"):
            order_id = int(data.split(":")[-1])
            await admin_set_payment_confirmed(db_path, order_id, True)
            await update_order_status(db_path, order_id, "Confirmed")

            try:
                details = await admin_get_order_details(db_path, order_id)
                vendor_name = (details.get("vendor_name") if details else None) or "the vendor"
                total = int(details.get("total_amount_etb") or 0) if details else 0
                await notify_customer_order_update(
                    query.bot,
                    db_path,
                    order_id,
                    "✅ Payment Confirmed!\n\n"
                    f"📦 Order #{order_id} ({vendor_name})\n"
                    f"💰 Total: {total} ETB\n\n"
                    "👨‍🍳 The vendor will start preparing your order soon.\n"
                    "⏱️ Thanks for using Food2U!",
                )
            except Exception:
                pass
            await render_admin_order_detail(query, db_path, order_id, state)
            return

        if data.startswith("adm:pay:reject:"):
            order_id = int(data.split(":")[-1])
            await admin_set_payment_confirmed(db_path, order_id, False)
            await update_order_status(db_path, order_id, "Rejected")

            try:
                details = await admin_get_order_details(db_path, order_id)
                vendor_name = (details.get("vendor_name") if details else None) or "the vendor"
                await notify_customer_order_update(
                    query.bot,
                    db_path,
                    order_id,
                    "❌ Payment Not Confirmed\n\n"
                    f"📦 Order #{order_id} ({vendor_name})\n\n"
                    "⚠️ Your payment proof was rejected.\n"
                    "If you think this is a mistake, please contact support/admin and send the correct screenshot 📸.",
                )
            except Exception:
                pass
            await render_admin_order_detail(query, db_path, order_id, state)
            return

        if data.startswith("adm:status:"):
            parts = data.split(":", 3)
            if len(parts) < 4:
                await query.answer()
                return
            new_status = parts[2]
            order_id = int(parts[3])
            await update_order_status(db_path, order_id, new_status)

            try:
                details = await admin_get_order_details(db_path, order_id)
                vendor_name = (details.get("vendor_name") if details else None) or "the vendor"
                status_norm = str(new_status).strip().lower()

                if status_norm == "preparing":
                    text = (
                        "👨‍🍳 Order is now being prepared!\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n\n"
                        "🍴 The kitchen is working on it right now.\n"
                        "⏱️ We’ll notify you when it’s on the way 🚀"
                    )
                elif status_norm in {"on the way"}:
                    text = (
                        "🚀 Your order is on the way!\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n\n"
                        "🏫 Please be ready at your block.\n"
                        "📞 Keep your phone reachable for the rider."
                    )
                elif status_norm == "delivered":
                    text = (
                        "🎉 Delivered successfully!\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n\n"
                        "⭐ Enjoy your meal!\n"
                        "🙏 Thank you for ordering with Food2U."
                    )
                elif status_norm == "cancelled":
                    text = (
                        "❌ Your order has been cancelled.\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n\n"
                        "🙏 Sorry for the inconvenience. If you already paid, please contact admin/support."
                    )
                elif status_norm == "confirmed":
                    text = (
                        "✅ Order confirmed!\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n\n"
                        "👨‍🍳 Next: preparing your order."
                    )
                elif status_norm == "rejected":
                    text = (
                        "⚠️ Order update\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n\n"
                        "❌ Payment was rejected. Please contact admin/support."
                    )
                else:
                    text = (
                        "🔔 Order status updated\n\n"
                        f"📦 Order #{order_id} ({vendor_name})\n"
                        f"📌 New status: {new_status}"
                    )

                await notify_customer_order_update(query.bot, db_path, order_id, text)

                # Invite the customer to rate the order once it's delivered.
                if status_norm == "delivered" and details:
                    customer_tid = int(details.get("customer_telegram_id") or 0)
                    if customer_tid and not await get_order_rating(db_path, order_id):
                        try:
                            await query.bot.send_message(
                                customer_tid,
                                "⭐ How was your order?\n\n"
                                f"📦 Order #{order_id} ({vendor_name})\n\n"
                                "Tap the stars to rate your experience:",
                                reply_markup=rating_keyboard(order_id),
                            )
                        except Exception:
                            logger.exception("Failed to send rating prompt for order %s", order_id)
            except Exception:
                logger.exception("Failed to notify customer for order %s", order_id)
            await render_admin_order_detail(query, db_path, order_id, state)
            return

        if data.startswith("adm:archive:"):
            order_id = int(data.split(":")[-1])
            await update_order_status(db_path, order_id, "Archived")
            try:
                details = await admin_get_order_details(db_path, order_id)
                vendor_name = (details.get("vendor_name") if details else None) or "the vendor"
                await notify_customer_order_update(
                    query.bot,
                    db_path,
                    order_id,
                    "🗄️ Order Archived\n\n"
                    f"📦 Order #{order_id} ({vendor_name})\n\n"
                    "✅ This order is now closed and archived in our system.",
                )
            except Exception:
                pass
            await query.answer("🗄️ Archived")
            await render_admin_order_detail(query, db_path, order_id, state)
            return

        if data == "adm:blocks":
            await state.clear()
            blocks = await admin_list_blocks(db_path)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🏫 Blocks Management", reply_markup=admin_blocks_keyboard(blocks))
            except Exception:
                msg = await query.message.answer("🏫 Blocks Management", reply_markup=admin_blocks_keyboard(blocks))
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data.startswith("adm:vendors"):
            await state.clear()
            vendors = await admin_list_vendors(db_path)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
            except Exception:
                msg = await query.message.answer("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data.startswith("adm:vendor:move:"):
            parts = data.split(":")
            if len(parts) < 5:
                await query.answer()
                return
            vid = int(parts[3])
            direction = parts[4]
            await admin_move_vendor(db_path, vid, direction)
            vendors = await admin_list_vendors(db_path)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
            except Exception:
                msg = await query.message.answer("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
                await track_step_message(state, msg.message_id)
            await query.answer("Updated")
            return

        if data == "adm:vendor:add":
            await state.clear()
            await state.set_state(AdminFlow.adding_vendor_name)
            await _show_inline("Enter food house name:")
            await query.answer()
            return

        if data.startswith("adm:vendor:toggle:"):
            vid = int(data.split(":")[-1])
            vendors = await admin_list_vendors(db_path)
            current = next((v for v in vendors if int(v["id"]) == vid), None)
            if current:
                active = int(current.get("active", 1)) == 1
                await admin_set_vendor_active(db_path, vid, not active)
            vendors = await admin_list_vendors(db_path)
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
            except Exception:
                msg = await query.message.answer("🏠 Food House Management", reply_markup=admin_vendors_keyboard(vendors))
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        if data.startswith("adm:vendor:del:"):
            vid = int(data.split(":")[-1])
            await _show_inline(
                "⚠️ Delete Food House (Permanent)\n\n"
                "This will permanently delete the vendor AND:\n"
                "- All categories\n"
                "- All items\n"
                "- All favorites\n\n"
                "Orders will stay readable using snapshots.\n\nProceed?",
                reply_markup=admin_confirm_keyboard(
                    f"adm:vendor:del_confirm:{vid}",
                    "adm:vendors",
                ),
            )
            await query.answer()
            return

        if data.startswith("adm:vendor:del_confirm:"):
            vid = int(data.split(":")[-1])
            await admin_hard_delete_vendor_and_related(db_path, vid)
            vendors = await admin_list_vendors(db_path)
            await replace_callback_message(
                query,
                state,
                "✅ Food house deleted.",
                reply_markup=admin_vendors_keyboard(vendors),
                keep_current=False,
            )
            await query.answer("Deleted")
            return

        if data == "adm:menu":
            await state.clear()
            await cleanup_step_messages(query.bot, query.message.chat.id, state, keep_message_ids={query.message.message_id})
            try:
                await query.message.edit_text("🍲 Menu Management", reply_markup=admin_menu_root_keyboard())
            except Exception:
                msg = await query.message.answer("🍲 Menu Management", reply_markup=admin_menu_root_keyboard())
                await track_step_message(state, msg.message_id)
            await query.answer()
            return

        # ---------------- Category management ----------------
        if data == "adm:cats":
            await state.clear()
            vendors = await admin_list_vendors(db_path)
            if not vendors:
                await _show_inline(
                    "📂 Manage Categories\n\nNo food houses yet. Add one first.",
                    reply_markup=admin_menu_root_keyboard(),
                )
                await query.answer()
                return
            await _show_inline(
                "📂 Manage Categories\n\nChoose a food house:",
                reply_markup=admin_categories_vendor_keyboard(vendors),
            )
            await query.answer()
            return

        if data.startswith("adm:cats:open:"):
            vid = int(data.split(":")[-1])
            cats = await admin_list_menu_categories(db_path, vid)
            await _show_inline(
                f"📂 Categories (vendor {vid})",
                reply_markup=admin_categories_keyboard(vid, cats),
            )
            await query.answer()
            return

        if data.startswith("adm:cats:add:"):
            vid = int(data.split(":")[-1])
            await state.clear()
            await state.update_data(vendor_id=vid)
            await state.set_state(AdminFlow.adding_category_name)
            await _show_inline("Enter new category name:")
            await query.answer()
            return

        if data.startswith("adm:cats:move:"):
            parts = data.split(":")
            if len(parts) < 6:
                await query.answer()
                return
            vid = int(parts[3])
            cid = int(parts[4])
            direction = parts[5]
            await admin_move_menu_category(db_path, vid, cid, direction)
            cats = await admin_list_menu_categories(db_path, vid)
            await _show_inline(
                f"📂 Categories (vendor {vid})",
                reply_markup=admin_categories_keyboard(vid, cats),
            )
            await query.answer("Updated")
            return

        if data.startswith("adm:cats:edit:"):
            parts = data.split(":")
            vid = int(parts[3])
            cid = int(parts[4])
            await state.clear()
            await state.update_data(vendor_id=vid, category_id=cid)
            await state.set_state(AdminFlow.editing_category_name)
            await _show_inline("Enter the new category name:")
            await query.answer()
            return

        if data.startswith("adm:cats:del:"):
            parts = data.split(":")
            vid = int(parts[3])
            cid = int(parts[4])
            await _show_inline(
                "⚠️ Delete Category (Permanent)\n\n"
                "This deletes the category AND all of its items.\n\nProceed?",
                reply_markup=admin_confirm_keyboard(
                    f"adm:cats:del_confirm:{vid}:{cid}",
                    f"adm:cats:open:{vid}",
                ),
            )
            await query.answer()
            return

        if data.startswith("adm:cats:del_confirm:"):
            parts = data.split(":")
            vid = int(parts[3])
            cid = int(parts[4])
            await admin_delete_menu_category_and_items(db_path, cid)
            cats = await admin_list_menu_categories(db_path, vid)
            await replace_callback_message(
                query,
                state,
                "✅ Category deleted.",
                reply_markup=admin_categories_keyboard(vid, cats),
                keep_current=False,
            )
            await query.answer("Deleted")
            return

        # ---------------- Item management ----------------
        if data == "adm:item:add":
            await state.clear()
            vendors = await admin_list_vendors(db_path)
            if not vendors:
                await _show_inline(
                    "➕ Add Item\n\nNo food houses yet. Add one first.",
                    reply_markup=admin_menu_root_keyboard(),
                )
                await query.answer()
                return
            await _show_inline(
                "➕ Add Item\n\nChoose a food house:",
                reply_markup=admin_choose_vendor_keyboard(vendors, "add_item"),
            )
            await query.answer()
            return

        if data in {"adm:item:edit_name", "adm:item:edit_price", "adm:item:toggle", "adm:item:del"}:
            action = {
                "adm:item:edit_name": "edit_name",
                "adm:item:edit_price": "edit_price",
                "adm:item:toggle": "toggle",
                "adm:item:del": "delete",
            }[data]
            await state.clear()
            vendors = await admin_list_vendors(db_path)
            if not vendors:
                await _show_inline(
                    "No food houses yet. Add one first.",
                    reply_markup=admin_menu_root_keyboard(),
                )
                await query.answer()
                return
            await state.update_data(item_action=action)
            await _show_inline(
                "Choose a food house:",
                reply_markup=admin_choose_vendor_keyboard(vendors, f"items_{action}"),
            )
            await query.answer()
            return

        if data.startswith("adm:choose_vendor:"):
            parts = data.split(":")
            # adm:choose_vendor:<action>:<vendor_id>
            action = parts[2]
            vid = int(parts[3])
            if action == "add_item":
                cats = await admin_list_menu_categories(db_path, vid)
                if not cats:
                    await _show_inline(
                        "This food house has no categories yet. Add one from 📂 Manage Categories first.",
                        reply_markup=admin_menu_root_keyboard(),
                    )
                    await query.answer()
                    return
                await _show_inline(
                    "Choose a category for the new item:",
                    reply_markup=admin_choose_category_keyboard(vid, cats, "add_item"),
                )
                await query.answer()
                return
            if action.startswith("items_"):
                mode = action.split("items_", 1)[1]  # edit_name / edit_price / toggle / delete
                items = await admin_list_menu_items(db_path, vid)
                if not items:
                    await _show_inline(
                        "This food house has no items yet.",
                        reply_markup=admin_menu_root_keyboard(),
                    )
                    await query.answer()
                    return
                await _show_inline(
                    "Select an item:",
                    reply_markup=admin_items_keyboard(items, mode),
                )
                await query.answer()
                return
            await query.answer()
            return

        if data.startswith("adm:catpick:"):
            parts = data.split(":")
            # adm:catpick:<action>:<vendor_id>:<category_id>
            action = parts[2]
            vid = int(parts[3])
            cid = int(parts[4])
            if action == "add_item":
                await state.clear()
                await state.update_data(vendor_id=vid, category_id=cid)
                await state.set_state(AdminFlow.adding_item_name)
                await _show_inline("Enter the item name:")
                await query.answer()
                return
            await query.answer()
            return

        if data.startswith("adm:quick:add_item:"):
            parts = data.split(":")
            vid = int(parts[3])
            cid = int(parts[4])
            await state.clear()
            await state.update_data(vendor_id=vid, category_id=cid)
            await state.set_state(AdminFlow.adding_item_name)
            await _show_inline("Enter the item name:")
            await query.answer()
            return

        if data.startswith("adm:item:edit_name_do:"):
            parts = data.split(":")
            vid = int(parts[3])
            iid = int(parts[4])
            await state.clear()
            await state.update_data(vendor_id=vid, item_id=iid)
            await state.set_state(AdminFlow.editing_item_name)
            await _show_inline("Enter the new item name:")
            await query.answer()
            return

        if data.startswith("adm:item:edit_price_do:"):
            iid = int(data.split(":")[-1])
            item = await get_menu_item(db_path, iid)
            vid = int(item.get("vendor_id") or 0) if item else 0
            await state.clear()
            await state.update_data(item_id=iid, vendor_id=vid)
            await state.set_state(AdminFlow.editing_item_price)
            await _show_inline("Enter the new price (ETB):")
            await query.answer()
            return

        if data.startswith("adm:item:toggle_do:"):
            iid = int(data.split(":")[-1])
            item = await get_menu_item(db_path, iid)
            if item:
                active = int(item.get("active", 1)) == 1
                await admin_set_menu_item_active(db_path, iid, not active)
                vid = int(item.get("vendor_id") or 0)
                items = await admin_list_menu_items(db_path, vid)
                await _show_inline(
                    "Select an item:",
                    reply_markup=admin_items_keyboard(items, "toggle"),
                )
            await query.answer("Updated")
            return

        if data.startswith("adm:item:del_prompt:"):
            parts = data.split(":")
            vid = int(parts[3])
            iid = int(parts[4])
            await _show_inline(
                "⚠️ Delete Item (Permanent)\n\nThis cannot be undone. Proceed?",
                reply_markup=admin_confirm_keyboard(
                    f"adm:item:del_confirm:{vid}:{iid}",
                    "adm:menu",
                ),
            )
            await query.answer()
            return

        if data.startswith("adm:item:del_confirm:"):
            parts = data.split(":")
            vid = int(parts[3])
            iid = int(parts[4])
            await admin_hard_delete_menu_item(db_path, iid)
            items = await admin_list_menu_items(db_path, vid)
            await replace_callback_message(
                query,
                state,
                "✅ Item deleted.",
                reply_markup=admin_items_keyboard(items, "delete"),
                keep_current=False,
            )
            await query.answer("Deleted")
            return

        # ---------------- Block management ----------------
        if data == "adm:block:add":
            await state.clear()
            await state.set_state(AdminFlow.adding_block_name)
            await _show_inline("Enter the new block name:")
            await query.answer()
            return

        if data.startswith("adm:block:edit_fee:"):
            bid = int(data.split(":")[-1])
            await state.clear()
            await state.update_data(block_id=bid)
            await state.set_state(AdminFlow.editing_block_fee)
            await _show_inline("Enter the new delivery fee (ETB):")
            await query.answer()
            return

        if data.startswith("adm:block:toggle:"):
            bid = int(data.split(":")[-1])
            blocks = await admin_list_blocks(db_path)
            current = next((b for b in blocks if int(b["id"]) == bid), None)
            if current:
                active = int(current.get("active", 1)) == 1
                await admin_set_block_active(db_path, bid, not active)
            blocks = await admin_list_blocks(db_path)
            await _show_inline(
                "🏫 Blocks Management",
                reply_markup=admin_blocks_keyboard(blocks),
            )
            await query.answer("Updated")
            return

        # Unknown admin callback: clear the loading spinner instead of hanging.
        await query.answer()
        return

    async def handle_favorites(message: Message, state: FSMContext):
        await touch_user_activity(db_path, message.from_user.id)
        await state.clear()
        favorites = await list_favorites_for_user(db_path, message.from_user.id, limit=10)
        if not favorites:
            await message.answer(
                "⭐ You have no saved favorites yet.\n\n"
                "After placing an order, tap “⭐ Save as Favorite” to reorder it in one tap next time.",
                reply_markup=customer_menu_keyboard(),
            )
            return
        await message.answer(
            "⭐ Your Favorites\n\nTap to reorder or remove:",
            reply_markup=favorites_keyboard(favorites),
        )

    async def fav_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        if not await enforce_not_restricted_callback(query, db_path, admin_ids, state):
            return
        parts = (query.data or "").split(":")
        if len(parts) < 3:
            await query.answer()
            return
        action = parts[1]

        if action == "save":
            order_id = int(parts[2])
            details = await get_order_details(db_path, query.from_user.id, order_id)
            if not details:
                await query.answer("Order not found", show_alert=True)
                return
            items = details.get("items") or {}
            vendor_id = int(details.get("vendor_id") or 0)
            if not vendor_id or not items:
                await query.answer("Nothing to save for this order.", show_alert=True)
                return
            try:
                await upsert_favorite(
                    db_path,
                    query.from_user.id,
                    vendor_id,
                    items,
                    title=details.get("vendor_name"),
                )
            except Exception:
                logger.exception("Failed to save favorite for order %s", order_id)
                await query.answer("Couldn't save favorite.", show_alert=True)
                return
            try:
                await query.message.edit_text("⭐ Saved to your favorites!")
            except Exception:
                await query.message.answer("⭐ Saved to your favorites!")
            await query.answer("Saved")
            return

        if action == "del":
            fid = int(parts[2])
            await deactivate_favorite(db_path, query.from_user.id, fid)
            favorites = await list_favorites_for_user(db_path, query.from_user.id, limit=10)
            try:
                await query.message.edit_text(
                    "⭐ Your Favorites\n\nTap to reorder or remove:"
                    if favorites
                    else "⭐ You have no saved favorites yet.",
                    reply_markup=favorites_keyboard(favorites),
                )
            except Exception:
                pass
            await query.answer("Removed")
            return

        if action == "order":
            if not await enforce_ordering_open_callback(query, db_path, admin_ids):
                return
            fid = int(parts[2])
            fav = await get_favorite_for_user(db_path, query.from_user.id, fid)
            if not fav:
                await query.answer("Favorite not found", show_alert=True)
                return
            vendor_id = int(fav.get("vendor_id") or 0)
            vendor = await get_vendor(db_path, vendor_id)
            if not vendor or int(vendor.get("active", 1)) != 1:
                await query.answer(
                    "This food house is no longer available.", show_alert=True
                )
                return
            # Rebuild the cart, keeping only items that still exist and are active.
            try:
                raw = json.loads(fav.get("items_json") or "{}")
            except Exception:
                raw = {}
            cart: dict[int, int] = {}
            for k, v in raw.items():
                item = await get_menu_item(db_path, int(k))
                if item and int(item.get("active", 1)) == 1:
                    cart[int(k)] = int(v)
            if not cart:
                await query.answer(
                    "None of the saved items are available anymore.", show_alert=True
                )
                return

            await state.clear()
            await state.update_data(vendor_id=vendor_id, cart=cart, cart_message_id=None)
            await state.set_state(OrderFlow.choosing_items)

            cart_text, subtotal = await format_cart_lines(db_path, cart)
            count = cart_item_count(cart)
            text = (
                "🔁 Reordering your favorite\n\n"
                f"🍴 {vendor['name']}\n\n"
                f"🧺 Items: {count}\n"
                f"💰 Current total: {subtotal} ETB\n\n"
                f"{cart_text}\n\n"
                "➕ Add more items or tap ✅ Done to checkout."
            )
            kb = await cart_controls_keyboard(db_path, list(cart.keys()))
            await cleanup_step_messages(
                query.bot, query.message.chat.id, state, keep_message_ids=set()
            )
            try:
                await query.message.delete()
            except Exception:
                pass
            msg = await query.message.answer(text, reply_markup=kb)
            await state.update_data(cart_message_id=msg.message_id)
            await track_step_message(state, msg.message_id)
            await query.answer()
            return

        await query.answer()

    async def corder_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        data = query.data or ""
        if data.startswith("corder:refresh:"):
            order_id = int(data.split(":")[-1])
            await render_customer_order_detail(query, db_path, order_id, state)
            return
        if data == "corder:list":
            await cleanup_step_messages(
                query.bot, query.message.chat.id, state, keep_message_ids=set()
            )
            try:
                await query.message.delete()
            except Exception:
                pass
            await render_customer_orders_list(
                query.message, db_path, state, query.from_user.id
            )
            await query.answer()
            return
        if data == "corder:menu":
            await cleanup_step_messages(
                query.bot, query.message.chat.id, state, keep_message_ids=set()
            )
            try:
                await query.message.delete()
            except Exception:
                pass
            await state.clear()
            await query.message.answer(
                "Choose an option:", reply_markup=customer_menu_keyboard()
            )
            await query.answer()
            return
        await query.answer()

    async def rate_cb(query: CallbackQuery, state: FSMContext):
        await touch_user_activity(db_path, query.from_user.id)
        parts = (query.data or "").split(":")
        # rate:<order_id>:<stars>
        if len(parts) < 3:
            await query.answer()
            return
        try:
            order_id = int(parts[1])
            stars = int(parts[2])
        except ValueError:
            await query.answer()
            return

        try:
            await upsert_order_rating(db_path, query.from_user.id, order_id, stars, None)
        except Exception:
            logger.exception("Failed to save rating for order %s", order_id)
            await query.answer("Couldn't save your rating. Please try again.", show_alert=True)
            return

        try:
            await query.message.edit_text(
                f"🙏 Thanks for rating order #{order_id}!\n\n"
                f"Your rating: {'⭐' * stars}\n"
                "We appreciate your feedback. 💚"
            )
        except Exception:
            await query.message.answer(
                f"🙏 Thanks for rating order #{order_id}! {'⭐' * stars}"
            )
        await query.answer("Rating saved")

    # ------------------------------------------------------------------
    # Handler registration
    # ------------------------------------------------------------------
    def register(dp: Dispatcher) -> None:
        # Slash commands
        dp.message.register(start_handler, Command("start"))
        dp.message.register(cmd_help, Command("help"))
        dp.message.register(cmd_myid, Command("myid"))

        # FSM-state-specific message handlers (must come before generic text)
        dp.message.register(phone_handler, StateFilter(OrderFlow.entering_phone))
        dp.message.register(promo_text_handler, StateFilter(OrderFlow.entering_promo))

        # Photo handlers (disambiguated by FSM state)
        dp.message.register(
            payment_photo_handler,
            StateFilter(OrderFlow.waiting_payment_proof),
            F.photo,
        )
        dp.message.register(
            broadcast_photo_handler,
            StateFilter(AdminFlow.broadcasting_message),
            F.photo,
        )

        # Generic text handler (buttons, admin flows, seed commands, fallback)
        dp.message.register(text_handler, F.text)

        # Customer order-flow callbacks
        dp.callback_query.register(vendor_cb, F.data.startswith("vendor:"))
        dp.callback_query.register(category_cb, F.data.startswith("cat:"))
        dp.callback_query.register(add_cb, F.data.startswith("add:"))
        dp.callback_query.register(rem_cb, F.data.startswith("rem:"))
        dp.callback_query.register(done_cb, F.data == "done")
        dp.callback_query.register(block_cb, F.data.startswith("block:"))
        dp.callback_query.register(confirm_cb, F.data == "confirm")
        dp.callback_query.register(promo_cb, F.data == "promo")
        dp.callback_query.register(paid_cb, F.data == "paid")
        dp.callback_query.register(cancel_cb, F.data == "cancel")
        dp.callback_query.register(oback_cb, F.data.startswith("oback:"))
        dp.callback_query.register(checkout_back_cb, F.data == "checkout:back")
        dp.callback_query.register(order_cb, F.data.startswith("corder:open:"))
        dp.callback_query.register(
            corder_cb,
            F.data.startswith("corder:refresh:")
            | (F.data == "corder:list")
            | (F.data == "corder:menu"),
        )

        # Admin payment-account callbacks (exact match)
        dp.callback_query.register(admin_payments_cb, F.data == "adm:payments")
        dp.callback_query.register(admin_set_cbe_cb, F.data == "adm:pay:set_cbe")
        dp.callback_query.register(admin_set_tele_cb, F.data == "adm:pay:set_tele")

        # Giant admin catch-all (handles all remaining "adm:" callbacks)
        dp.callback_query.register(admin_cb, F.data.startswith("adm:"))

        # Order rating callbacks
        dp.callback_query.register(rate_cb, F.data.startswith("rate:"))

        # Favorites callbacks
        dp.callback_query.register(fav_cb, F.data.startswith("fav:"))

        # No-op buttons (labels rendered as buttons)
        dp.callback_query.register(_noop_cb, F.data == "noop")

    return register


async def _noop_cb(query: CallbackQuery) -> None:
    await query.answer()
