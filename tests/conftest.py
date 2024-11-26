"""Shared pytest fixtures."""

from __future__ import annotations

import os
import tempfile

import pytest_asyncio

from food2u import db as db_module


@pytest_asyncio.fixture
async def db_path():
    """Provide a fresh, initialized SQLite database for each test."""
    fd, path = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    os.remove(path)  # init_db will create it
    await db_module.init_db(path)
    try:
        yield path
    finally:
        if os.path.exists(path):
            os.remove(path)
