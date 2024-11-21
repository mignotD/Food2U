"""Middleware for dependency injection of shared context into handlers."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.types import TelegramObject


class ContextMiddleware(BaseMiddleware):
    """Inject ``db_path`` and ``admin_ids`` into every handler's data.

    Handlers that declare ``db_path`` / ``admin_ids`` parameters will receive
    these automatically, removing the need to capture them via closures.
    """

    def __init__(self, db_path: str, admin_ids: set[int]) -> None:
        self.db_path = db_path
        self.admin_ids = admin_ids

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data.setdefault("db_path", self.db_path)
        data.setdefault("admin_ids", self.admin_ids)
        return await handler(event, data)
