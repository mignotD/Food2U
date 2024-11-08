"""Inline and reply keyboard builders (synchronous, no database access)."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from aiogram.utils.keyboard import ReplyKeyboardBuilder

from .utils import order_code, safe_label, status_emoji


def ikb(rows: list[list[InlineKeyboardButton]]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------------------------------------------------------------------
# Reply keyboards (main menus)
# ---------------------------------------------------------------------------
def customer_menu_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="🍽️ Order Food")
    kb.button(text="📦 My Orders")
    kb.button(text="⭐ Favorites")
    kb.button(text="❓ Help")
    kb.adjust(2, 2)
    return kb.as_markup(resize_keyboard=True)


def admin_menu_keyboard():
    kb = ReplyKeyboardBuilder()
    kb.button(text="📦 Orders")
    kb.button(text="Manage Menu 🍲")
    kb.button(text="Manage Food Houses 🏠")
    kb.button(text="Manage Blocks 🏫")
    kb.button(text="Set Payments 💳")
    kb.button(text="🎟️ Promo Codes")
    kb.button(text="📢 Broadcast")
    kb.button(text="Ordering ON/OFF 🔘")
    kb.adjust(2, 2, 2, 2)
    return kb.as_markup(resize_keyboard=True)


# ---------------------------------------------------------------------------
# Customer order flow
# ---------------------------------------------------------------------------
def vendor_list_keyboard(vendors: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for v in vendors:
        rows.append([InlineKeyboardButton(text=f"{v['name']} 🍴", callback_data=f"vendor:{v['id']}")])
    rows.append([InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")])
    return ikb(rows)


def vendor_categories_keyboard(vendor_id: int, categories: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for c in categories:
        rows.append(
            [
                InlineKeyboardButton(
                    text=safe_label(c.get("name"), "(Unnamed)"),
                    callback_data=f"cat:{vendor_id}:{c.get('id')}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="oback:vendors")])
    rows.append([InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")])
    return ikb(rows)


def menu_keyboard(items: list[dict], cart_count: int = 0) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for it in items:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{safe_label(it.get('name'), '(Unnamed)')} – {it['price_etb']} ETB ➕",
                    callback_data=f"add:{it['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text=f"🛒 View Cart ({cart_count})", callback_data="oback:cart")])
    rows.append([InlineKeyboardButton(text="✅ Done", callback_data="done")])
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="oback:categories")])
    rows.append([InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")])
    return ikb(rows)


def blocks_keyboard(blocks: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for b in blocks:
        fee = b.get("delivery_fee_etb")
        fee_str = "?" if fee is None else str(int(fee))
        rows.append([InlineKeyboardButton(text=f"{b['name']} – {fee_str} ETB", callback_data=f"block:{b['id']}")])
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="oback:cart")])
    rows.append([InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")])
    return ikb(rows)


def confirm_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="✅ Confirm", callback_data="confirm")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="oback:phone")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")],
        ]
    )


def confirm_keyboard_limited(can_confirm: bool) -> InlineKeyboardMarkup:
    if can_confirm:
        return confirm_keyboard()
    return ikb(
        [
            [InlineKeyboardButton(text="❌ Too many items (max 8)", callback_data="noop")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="oback:phone")],
            [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")],
        ]
    )


def checkout_cancel_keyboard() -> InlineKeyboardMarkup:
    return ikb([[InlineKeyboardButton(text="Cancel ❌", callback_data="checkout:back")]])


def promo_type_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="% Percent", callback_data="promo:type:percent")],
            [InlineKeyboardButton(text="ETB Fixed", callback_data="promo:type:etb")],
            [InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")],
        ]
    )


def payment_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="I Paid ✅", callback_data="paid")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="oback:summary")],
            [InlineKeyboardButton(text="Cancel ❌", callback_data="cancel")],
        ]
    )


# ---------------------------------------------------------------------------
# Customer orders list / detail
# ---------------------------------------------------------------------------
def customer_order_detail_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="🔄 Refresh", callback_data=f"corder:refresh:{order_id}")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="corder:list")],
        ]
    )


def customer_orders_footer_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="🔄 Refresh", callback_data="corder:list")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="corder:menu")],
        ]
    )


def favorites_keyboard(favorites: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for f in favorites:
        fid = int(f.get("id") or 0)
        vendor = safe_label(f.get("vendor_name"), "(vendor)")
        title = (f.get("title") or "").strip()
        label = f"🍴 {vendor}" + (f" – {title}" if title else "")
        rows.append([InlineKeyboardButton(text=f"🔁 Reorder {label}", callback_data=f"fav:order:{fid}")])
        rows.append([InlineKeyboardButton(text=f"🗑️ Remove {label}", callback_data=f"fav:del:{fid}")])
    if not favorites:
        rows.append([InlineKeyboardButton(text="(no favorites yet)", callback_data="noop")])
    return ikb(rows)


def rating_keyboard(order_id: int) -> InlineKeyboardMarkup:
    return ikb(
        [
            [
                InlineKeyboardButton(text="⭐", callback_data=f"rate:{order_id}:1"),
                InlineKeyboardButton(text="⭐⭐", callback_data=f"rate:{order_id}:2"),
                InlineKeyboardButton(text="⭐⭐⭐", callback_data=f"rate:{order_id}:3"),
            ],
            [
                InlineKeyboardButton(text="⭐⭐⭐⭐", callback_data=f"rate:{order_id}:4"),
                InlineKeyboardButton(text="⭐⭐⭐⭐⭐", callback_data=f"rate:{order_id}:5"),
            ],
        ]
    )


def orders_list_keyboard(orders: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        vid = o.get("vendor_id")
        vname = o.get("vendor_name")
        oid = int(o["id"])
        label = order_code(
            str(vname) if vname is not None else None,
            int(vid) if vid is not None else None,
            oid,
        )
        rows.append([InlineKeyboardButton(text=f"{label}", callback_data=f"corder:open:{oid}")])
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="corder:menu")])
    return ikb(rows)


# ---------------------------------------------------------------------------
# Admin: root / service / broadcast / users
# ---------------------------------------------------------------------------
def admin_root_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="📦 Orders", callback_data="adm:orders")],
            [InlineKeyboardButton(text="🍲 Menu", callback_data="adm:menu")],
            [InlineKeyboardButton(text="🏠 Food Houses", callback_data="adm:vendors")],
            [InlineKeyboardButton(text="🏫 Blocks", callback_data="adm:blocks")],
            [InlineKeyboardButton(text="💳 Payments", callback_data="adm:payments")],
            [InlineKeyboardButton(text="📢 Broadcast", callback_data="adm:broadcast")],
            [InlineKeyboardButton(text="🔘 Ordering ON/OFF", callback_data="adm:service")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:back")],
        ]
    )


def admin_promos_keyboard(promos: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="➕ Create Promo", callback_data="adm:promo:create")])
    for p in promos:
        pid = int(p.get("id") or 0)
        code = safe_label(p.get("code"), "(none)")
        active = int(p.get("active", 1)) == 1
        status = "✅" if active else "🚫"
        dtype = p.get("discount_type")
        value = int(p.get("discount_value") or 0)
        value_str = f"{value}%" if dtype == "percent" else f"{value} ETB"
        used = int(p.get("used_count") or 0)
        max_uses = p.get("max_uses")
        uses_str = f"{used}/{max_uses}" if max_uses else f"{used}/∞"
        toggle_text = "Disable" if active else "Enable"
        rows.append(
            [
                InlineKeyboardButton(text=f"{status} {code} – {value_str} ({uses_str})", callback_data="noop"),
                InlineKeyboardButton(text=toggle_text, callback_data=f"adm:promo:toggle:{pid}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")])
    return ikb(rows)


def admin_promo_type_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="% Percent", callback_data="adm:promo:type:percent")],
            [InlineKeyboardButton(text="ETB Fixed", callback_data="adm:promo:type:etb")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:promos")],
        ]
    )


def admin_broadcast_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="✍️ Write Broadcast", callback_data="adm:broadcast:write")],
            [InlineKeyboardButton(text="🕘 Broadcast History", callback_data="adm:broadcast:history")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")],
        ]
    )


def admin_broadcast_history_keyboard(broadcasts: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for b in broadcasts:
        bid = int(b.get("id") or 0)
        text = (str(b.get("text") or "").strip().replace("\n", " "))[:40]
        if not text:
            text = "(no text)"
        rows.append([InlineKeyboardButton(text=f"#{bid} {text}", callback_data=f"adm:broadcast:open:{bid}")])
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:broadcast")])
    return ikb(rows)


def admin_broadcast_detail_keyboard(broadcast_id: int) -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="🗑 Delete Broadcast", callback_data=f"adm:broadcast:del:{broadcast_id}")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:broadcast:history")],
        ]
    )


def admin_confirm_keyboard(confirm_cb: str, cancel_cb: str) -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="✅ Confirm", callback_data=confirm_cb)],
            [InlineKeyboardButton(text="Cancel ❌", callback_data=cancel_cb)],
        ]
    )


# ---------------------------------------------------------------------------
# Admin: orders
# ---------------------------------------------------------------------------
def admin_daily_orders_keyboard(orders: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="🗑 Clear Today's Orders", callback_data="adm:daily_orders:clear")])
    for o in orders:
        status = o.get("status") or ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{o['id']} {o.get('vendor_name','')} – {status} {status_emoji(status)}",
                    callback_data=f"adm:order:{o['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")])
    return ikb(rows)


def admin_service_keyboard(ordering_open: bool) -> InlineKeyboardMarkup:
    status = "🟢 OPEN" if ordering_open else "🔴 CLOSED"
    toggle_text = "Close Ordering 🔴" if ordering_open else "Open Ordering 🟢"
    return ikb(
        [
            [InlineKeyboardButton(text=f"Ordering Status: {status}", callback_data="noop")],
            [InlineKeyboardButton(text=toggle_text, callback_data="adm:service:toggle")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:back")],
        ]
    )


def admin_orders_keyboard(orders: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for o in orders:
        status = o.get("status") or ""
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"#{o['id']} {o.get('vendor_name','')} – {status} {status_emoji(status)}",
                    callback_data=f"adm:order:{o['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")])
    return ikb(rows)


def admin_order_actions_keyboard(order_id: int, status: str, has_proof: bool) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if has_proof and status in {"Pending Confirmation", "Awaiting Payment"}:
        rows.append(
            [
                InlineKeyboardButton(text="✅ Confirm Payment", callback_data=f"adm:pay:confirm:{order_id}"),
                InlineKeyboardButton(text="❌ Reject Payment", callback_data=f"adm:pay:reject:{order_id}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="🗄 Hide (Archive)", callback_data=f"adm:archive:{order_id}")])
    rows.append(
        [
            InlineKeyboardButton(text="👨‍🍳 Preparing", callback_data=f"adm:status:Preparing:{order_id}"),
            InlineKeyboardButton(text="🚀 On the way", callback_data=f"adm:status:On the way:{order_id}"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton(text="🎉 Delivered", callback_data=f"adm:status:Delivered:{order_id}"),
            InlineKeyboardButton(text="❌ Cancelled", callback_data=f"adm:status:Cancelled:{order_id}"),
        ]
    )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:orders")])
    return ikb(rows)


# ---------------------------------------------------------------------------
# Admin: vendors / menu / categories / items
# ---------------------------------------------------------------------------
def admin_vendors_keyboard(vendors: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="➕ Add Food House", callback_data="adm:vendor:add")])
    for idx, v in enumerate(vendors):
        active = int(v.get("active", 1)) == 1
        status = "✅" if active else "🚫"
        toggle_text = "Disable" if active else "Enable"
        controls: list[InlineKeyboardButton] = []
        if idx > 0:
            controls.append(InlineKeyboardButton(text="⬆️", callback_data=f"adm:vendor:move:{v['id']}:up"))
        if idx < (len(vendors) - 1):
            controls.append(InlineKeyboardButton(text="⬇️", callback_data=f"adm:vendor:move:{v['id']}:down"))
        rows.append(
            [
                InlineKeyboardButton(text=f"{status} {safe_label(v.get('name'), '(Unnamed)')} (id {v['id']})", callback_data="noop"),
                InlineKeyboardButton(text=toggle_text, callback_data=f"adm:vendor:toggle:{v['id']}"),
                *controls,
                InlineKeyboardButton(text="🗑️ Delete", callback_data=f"adm:vendor:del:{v['id']}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")])
    return ikb(rows)


def admin_menu_root_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="📂 Manage Categories", callback_data="adm:cats")],
            [InlineKeyboardButton(text="➕ Add Item", callback_data="adm:item:add")],
            [InlineKeyboardButton(text="✏️ Edit Item Name", callback_data="adm:item:edit_name")],
            [InlineKeyboardButton(text="✏️ Edit Item Price", callback_data="adm:item:edit_price")],
            [InlineKeyboardButton(text="🚫 Disable/Enable Item", callback_data="adm:item:toggle")],
            [InlineKeyboardButton(text="🗑️ Delete Item", callback_data="adm:item:del")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")],
        ]
    )


def admin_vendor_saved_keyboard() -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="➕ Add Another Food House", callback_data="adm:vendor:add")],
            [InlineKeyboardButton(text="🏠 Back to Food Houses", callback_data="adm:vendors")],
            [InlineKeyboardButton(text="🍲 Manage Menu", callback_data="adm:menu")],
        ]
    )


def admin_item_saved_keyboard(vendor_id: int, category_id: int) -> InlineKeyboardMarkup:
    return ikb(
        [
            [InlineKeyboardButton(text="➕ Add Another Item (Same Category)", callback_data=f"adm:quick:add_item:{vendor_id}:{category_id}")],
            [InlineKeyboardButton(text="📂 Add Item In Another Category", callback_data=f"adm:choose_vendor:add_item:{vendor_id}")],
            [InlineKeyboardButton(text="🍲 Back to Menu Management", callback_data="adm:menu")],
        ]
    )


def admin_categories_vendor_keyboard(vendors: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for v in vendors:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{safe_label(v.get('name'), '(Unnamed)')} (id {v['id']})",
                    callback_data=f"adm:cats:open:{v['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:menu")])
    return ikb(rows)


def admin_categories_keyboard(vendor_id: int, categories: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="➕ Add Category", callback_data=f"adm:cats:add:{vendor_id}")])
    for idx, c in enumerate(categories):
        cid = int(c.get("id"))
        name = safe_label(c.get("name"), "(Unnamed)")
        controls: list[InlineKeyboardButton] = []
        if idx > 0:
            controls.append(InlineKeyboardButton(text="⬆️", callback_data=f"adm:cats:move:{vendor_id}:{cid}:up"))
        if idx < (len(categories) - 1):
            controls.append(InlineKeyboardButton(text="⬇️", callback_data=f"adm:cats:move:{vendor_id}:{cid}:down"))
        controls.append(InlineKeyboardButton(text="✏️", callback_data=f"adm:cats:edit:{vendor_id}:{cid}"))
        controls.append(InlineKeyboardButton(text="🗑️", callback_data=f"adm:cats:del:{vendor_id}:{cid}"))
        rows.append([InlineKeyboardButton(text=name, callback_data="noop"), *controls])
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:cats")])
    return ikb(rows)


def admin_choose_category_keyboard(vendor_id: int, categories: list[dict], action: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for c in categories:
        rows.append(
            [
                InlineKeyboardButton(
                    text=safe_label(c.get("name"), "(Unnamed)"),
                    callback_data=f"adm:catpick:{action}:{vendor_id}:{int(c.get('id'))}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:item:add")])
    return ikb(rows)


def admin_choose_vendor_keyboard(vendors: list[dict], action: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for v in vendors:
        rows.append(
            [
                InlineKeyboardButton(
                    text=f"{safe_label(v.get('name'), '(Unnamed)')} (id {v['id']})",
                    callback_data=f"adm:choose_vendor:{action}:{v['id']}",
                )
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:menu")])
    return ikb(rows)


def admin_items_keyboard(items: list[dict], mode: str) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    for it in items:
        active = int(it.get("active", 1)) == 1
        status = "✅" if active else "🚫"
        if mode == "toggle":
            toggle_text = "Disable" if active else "Enable"
            cb = f"adm:item:toggle_do:{it['id']}"
            rows.append(
                [
                    InlineKeyboardButton(text=f"{status} {safe_label(it.get('name'), '(Unnamed)')} – {it['price_etb']} ETB", callback_data="noop"),
                    InlineKeyboardButton(text=toggle_text, callback_data=cb),
                ]
            )
        elif mode == "delete":
            rows.append(
                [
                    InlineKeyboardButton(text=f"{safe_label(it.get('name'), '(Unnamed)')} – {it['price_etb']} ETB", callback_data="noop"),
                    InlineKeyboardButton(text="🗑️", callback_data=f"adm:item:del_prompt:{it['vendor_id']}:{it['id']}"),
                ]
            )
        elif mode == "edit_name":
            rows.append(
                [
                    InlineKeyboardButton(text=f"{safe_label(it.get('name'), '(Unnamed)')} – {it['price_etb']} ETB", callback_data="noop"),
                    InlineKeyboardButton(text="✏️", callback_data=f"adm:item:edit_name_do:{it['vendor_id']}:{it['id']}"),
                ]
            )
        else:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=f"{safe_label(it.get('name'), '(Unnamed)')} – {it['price_etb']} ETB",
                        callback_data=f"adm:item:edit_price_do:{it['id']}",
                    )
                ]
            )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:menu")])
    return ikb(rows)


def admin_blocks_keyboard(blocks: list[dict]) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    rows.append([InlineKeyboardButton(text="➕ Add Block", callback_data="adm:block:add")])
    for b in blocks:
        active = int(b.get("active", 1)) == 1
        status = "✅" if active else "🚫"
        fee = b.get("delivery_fee_etb")
        fee_str = "?" if fee is None else str(int(fee))
        toggle_text = "Disable" if active else "Enable"
        rows.append(
            [
                InlineKeyboardButton(text=f"{status} {b['name']} – {fee_str} ETB", callback_data="noop"),
                InlineKeyboardButton(text="Edit Fee", callback_data=f"adm:block:edit_fee:{b['id']}"),
                InlineKeyboardButton(text=toggle_text, callback_data=f"adm:block:toggle:{b['id']}"),
            ]
        )
    rows.append([InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")])
    return ikb(rows)


def admin_payments_keyboard(cbe: str | None, tele: str | None) -> InlineKeyboardMarkup:
    cbe_status = "✅" if cbe else "❌"
    tele_status = "✅" if tele else "❌"
    return ikb(
        [
            [InlineKeyboardButton(text=f"{cbe_status} Set CBE Account", callback_data="adm:pay:set_cbe")],
            [InlineKeyboardButton(text=f"{tele_status} Set Telebirr", callback_data="adm:pay:set_tele")],
            [InlineKeyboardButton(text="Back 🔙", callback_data="adm:home")],
        ]
    )
