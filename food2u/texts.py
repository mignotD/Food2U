"""User-facing message strings (pure, no side effects)."""

from __future__ import annotations


def delivery_fee_info_note() -> str:
    return (
        "🚚 Delivery Fee Info:\n\n"
        "1–3 items → Normal fee\n"
        "4–6 items → +50% fee\n"
        "6–8 items → +100% fee\n"
        ">8 items → Cannot confirm order\n\n"
        "💡 Tip: Split large orders into multiple orders for fairness 😊"
    )


def ordering_disabled_message(reason: str | None) -> str:
    base = "🚫 Ordering Disabled\n\nYour account has been restricted from placing orders."
    if reason:
        return base + f"\n\nReason: {reason}"
    return base


def ordering_closed_message(reason: str | None = None) -> str:
    if reason and reason.strip():
        return reason.strip()
    return (
        "⛔ Ordering is currently closed.\n\n"
        "Please try again later, or contact admin for reopening updates."
    )


WELCOME_CUSTOMER = (
    "✨ Welcome to Food2U – WSU! ✨\n"
    "🍔🍕 Your campus food delivery companion.\n\n"
    "Here's how it works:\n"
    "1️⃣ Pick a food house 🏠\n"
    "2️⃣ Add your favorites to the cart 🛒\n"
    "3️⃣ Choose your block & pay 💳\n"
    "4️⃣ We deliver right to you 🚚\n\n"
    "👇 Tap a button below to get started:"
)

WELCOME_ADMIN = (
    "👋 Welcome back, Admin!\n\n"
    "🧠 You're at the controls of Food2U – WSU.\n\n"
    "From the menu below you can manage:\n"
    "📦 Orders   🍲 Menus   🏠 Food Houses\n"
    "🏫 Blocks   💳 Payments   📢 Broadcasts\n"
    "🔘 Ordering ON/OFF"
)

HELP_COMMANDS = (
    "❓ Food2U – WSU Help\n\n"
    "Commands:\n"
    "🚀 /start – Open the main menu\n"
    "❓ /help – Show this help\n"
    "🆔 /myid – Show your Telegram ID\n\n"
    "💡 Tip: Use the buttons in the menu to order food, "
    "track your orders, and more — no typing needed!"
)

HELP_GUIDE = (
    "❓ Food2U Help\n\n"
    "1️⃣ 🍽️ Order Food\n"
    "2️⃣ Add items to your cart 🛒\n"
    "3️⃣ Choose your block 🏫\n"
    "4️⃣ Enter your phone number 📞\n"
    "5️⃣ Pay & upload proof 💳📸\n"
    "6️⃣ Track your order from 📦 My Orders 🚚\n\n"
    "Need support? Contact admin/support in your channel or group."
)
