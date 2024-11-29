# 🍔 Food2U – WSU

A Telegram bot for campus food delivery at Wolaita Sodo University (WSU).
Customers browse food houses, build a cart, choose their block, pay, and track
their order — all inside Telegram. Admins manage menus, food houses, blocks,
payments, orders, and broadcasts from a built-in admin panel.

Built with [aiogram 3](https://docs.aiogram.dev/) and SQLite (PostgreSQL optional).

---

## ✨ Features

**For customers**
- 🍽️ Browse food houses and menus by category
- 🛒 Build a cart with per-item quantities
- 🏫 Pick a delivery block (with dynamic delivery fees)
- 🎟️ Apply promo codes
- 💳 Pay and upload payment proof
- 📦 Track active orders in real time
- ⭐ Save favorites and reorder in one tap
- ⭐ Rate orders after delivery (1–5 stars)

**For admins**
- 📦 View and manage orders (confirm payment, update status)
- 🍲 Manage menus, categories, and items (add / edit / reorder / enable / delete)
- 🏠 Manage food houses (add, reorder, enable/disable, delete)
- 🏫 Manage delivery blocks and fees
- 🎟️ Create and toggle promo codes
- 💳 Configure CBE / Telebirr payment accounts
- 📢 Broadcast text or photo announcements (rate-limited, auto-skips blocked users)
- 🔘 Open / close ordering globally

---

## 🚀 Getting started

### 1. Requirements
- Python 3.10+
- A Telegram bot token from [@BotFather](https://t.me/BotFather)

### 2. Install dependencies

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

### 3. Configure

Copy the example environment file and fill in your own values:

```bash
cp .env.example .env
```

| Variable                    | Required | Description                                              |
| --------------------------- | -------- | -------------------------------------------------------- |
| `BOT_TOKEN`                 | ✅       | Bot token from @BotFather.                               |
| `ADMIN_IDS`                 | ✅       | Comma-separated Telegram user IDs with admin access.     |
| `DB_PATH`                   | ➖       | SQLite file path (default `food2u.sqlite3`), or a `postgres://` DSN. |
| `OFFICIAL_CHANNEL`          | ➖       | Channel `@username` or ID for an optional join gate.     |
| `OFFICIAL_CHANNEL_JOIN_URL` | ➖       | Public link shown when the channel is an ID.             |

> 💡 Don't know your Telegram ID? Start the bot and send `/myid`.

### 4. Run

```bash
python -m food2u
# or, equivalently:
python main.py
```

The database is created automatically on first launch. You should see:

```
Food2U – WSU bot is running. Press Ctrl+C to stop.
```

---

## 🗂️ Project structure

```
Food2U/
├── food2u/                # Application package
│   ├── __init__.py
│   ├── __main__.py        # `python -m food2u` entrypoint
│   ├── app.py             # Bot + Dispatcher setup, middleware, polling loop
│   ├── config.py          # Environment / .env loading (typed Config)
│   ├── logging_config.py  # Centralized logging setup
│   ├── middleware.py      # DI middleware (injects db_path / admin_ids)
│   ├── states.py          # FSM state groups (OrderFlow, AdminFlow)
│   ├── utils.py           # Pure helpers (no aiogram/DB deps)
│   ├── texts.py           # User-facing message strings
│   ├── keyboards.py       # Inline & reply keyboard builders
│   ├── handlers.py        # Handlers + build_handlers() registration
│   └── db.py              # Async data layer (SQLite + optional PostgreSQL)
├── tests/                 # pytest suite (utils + db layer)
├── main.py                # Thin shim → food2u.app:main
├── pyproject.toml         # Packaging, ruff & pytest config
├── requirements.txt       # Python dependencies
├── .env.example           # Configuration template (copy to .env)
└── README.md
```

**Module layering** (each layer only imports from the ones above it):
`config` · `states` · `db` · `logging_config` → `utils` · `texts` → `keyboards` → `middleware` → `handlers` → `app`

---

## 🧪 Development

```bash
pip install -e ".[dev]"   # install with dev tools (pytest, ruff)
pytest                    # run the test suite
ruff check food2u tests   # lint
```

---

## 💬 Commands

| Command  | Description              |
| -------- | ------------------------ |
| `/start` | Open the main menu       |
| `/help`  | Show help                |
| `/myid`  | Show your Telegram ID    |

Everything else is driven by on-screen buttons — no typing required.

---

## 🔒 Security notes

- **Never commit your real `.env`** — it is git-ignored by default.
- If a bot token is ever exposed, **revoke it** in @BotFather and generate a new one.
- Admin actions are gated by the `ADMIN_IDS` list; keep it accurate.

---

## ⌨️ Admin quick-seed commands

Besides the full inline admin menus, you can seed data quickly via commands:

```
/seed_vendor "Burger House"
/seed_block "Block A" 30
/seed_item <vendor_id> <category_id> "Cheeseburger" 250
/set_cbe <account_number>
/set_telebirr <phone_number>
```

## ⚠️ Notes

- User-management and analytics admin screens remain disabled in this build
  (they show a "not available" notice).
- Messages are sent as plain text (no HTML/Markdown formatting) to keep
  user-supplied content safe without escaping.
