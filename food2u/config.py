"""Configuration loading and helpers."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


def parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    result: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        result.add(int(part))
    return result


@dataclass(frozen=True)
class Config:
    bot_token: str
    admin_ids: set[int] = field(default_factory=set)
    db_path: str = "food2u.sqlite3"
    official_channel: str | None = None
    official_channel_join_url: str | None = None


def load_config() -> Config:
    """Load configuration from the environment (and a local .env if present)."""
    load_dotenv()

    bot_token = os.getenv("BOT_TOKEN")
    if not bot_token:
        raise RuntimeError(
            "BOT_TOKEN is not set. Copy .env.example to .env and add your token."
        )

    return Config(
        bot_token=bot_token,
        admin_ids=parse_admin_ids(os.getenv("ADMIN_IDS")),
        db_path=os.getenv("DB_PATH", "food2u.sqlite3"),
        official_channel=(os.getenv("OFFICIAL_CHANNEL") or "").strip() or None,
        official_channel_join_url=(os.getenv("OFFICIAL_CHANNEL_JOIN_URL") or "").strip()
        or None,
    )
