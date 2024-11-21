"""Application entrypoint: build the bot, register handlers, start polling."""

from __future__ import annotations

import asyncio

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.memory import MemoryStorage

from .config import Config, load_config
from .db import init_db
from .handlers import build_handlers
from .logging_config import get_logger, setup_logging
from .middleware import ContextMiddleware

logger = get_logger(__name__)


def build_dispatcher(config: Config) -> Dispatcher:
    """Create a Dispatcher with handlers registered and context injected."""
    dp = Dispatcher(storage=MemoryStorage())

    # Dependency injection: make config values available to every handler
    # via aiogram's workflow data and a middleware (handlers that declare
    # `db_path` / `admin_ids` parameters receive them automatically).
    dp["db_path"] = config.db_path
    dp["admin_ids"] = config.admin_ids
    ctx = ContextMiddleware(config.db_path, config.admin_ids)
    dp.message.middleware(ctx)
    dp.callback_query.middleware(ctx)

    register = build_handlers(config.db_path, config.admin_ids)
    register(dp)
    return dp


async def run() -> None:
    setup_logging()
    config = load_config()

    logger.info("Initializing database at %s", config.db_path)
    await init_db(config.db_path)

    bot = Bot(token=config.bot_token)
    dp = build_dispatcher(config)

    # Drop updates accumulated while the bot was offline to avoid a burst of
    # stale messages (and duplicate processing) on restart.
    await bot.delete_webhook(drop_pending_updates=True)

    logger.info("Food2U - WSU bot is running. Press Ctrl+C to stop.")
    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        logger.info("Bot session closed.")


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        print("\nFood2U bot stopped.")


if __name__ == "__main__":
    main()
