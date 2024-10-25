import json
from datetime import date
from typing import Any

import aiosqlite
import asyncpg


def _is_postgres(db_path: str) -> bool:
    p = (db_path or "").strip().lower()
    return p.startswith("postgres://") or p.startswith("postgresql://")


_PG_POOLS: dict[str, asyncpg.Pool] = {}


async def _get_pg_pool(dsn: str) -> asyncpg.Pool:
    d = dsn.strip()
    pool = _PG_POOLS.get(d)
    if pool is None:
        pool = await asyncpg.create_pool(dsn=d, min_size=1, max_size=5)
        _PG_POOLS[d] = pool
    return pool


def _pgify_sql(sql: str) -> str:
    out: list[str] = []
    i = 0
    for ch in sql:
        if ch == "?":
            i += 1
            out.append(f"${i}")
        else:
            out.append(ch)
    return "".join(out)


async def _fetchone(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        row = await pool.fetchrow(_pgify_sql(sql), *params)
        return dict(row) if row else None

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            row = await cur.fetchone()
            return dict(row) if row else None


async def _fetchall(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        rows = await pool.fetch(_pgify_sql(sql), *params)
        return [dict(r) for r in rows]

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(sql, params) as cur:
            rows = await cur.fetchall()
            return [dict(r) for r in rows]


async def _execute(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> None:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            await conn.execute(_pgify_sql(sql), *params)
        return

    async with aiosqlite.connect(db_path) as db:
        await db.execute(sql, params)
        await db.commit()


async def create_broadcast(db_path: str, text: str, created_by_admin_telegram_id: int | None) -> int:
    return await _execute_returning_id(
        db_path,
        "INSERT INTO broadcasts (text, created_by_admin_telegram_id) VALUES (?, ?)",
        (text, int(created_by_admin_telegram_id) if created_by_admin_telegram_id is not None else None),
    )


async def add_broadcast_delivery(
    db_path: str,
    broadcast_id: int,
    user_telegram_id: int,
    message_id: int | None,
    status: str,
    error: str | None,
) -> None:
    await _execute(
        db_path,
        """
        INSERT INTO broadcast_deliveries (broadcast_id, user_telegram_id, message_id, status, error)
        VALUES (?, ?, ?, ?, ?)
        """,
        (int(broadcast_id), int(user_telegram_id), int(message_id) if message_id is not None else None, status, error),
    )


async def list_broadcasts(db_path: str, limit: int = 25) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, text, created_by_admin_telegram_id, created_at
        FROM broadcasts
        ORDER BY id DESC
        LIMIT ?
        """,
        (int(limit),),
    )


async def list_broadcast_deliveries(db_path: str, broadcast_id: int) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, broadcast_id, user_telegram_id, message_id, status, error, created_at
        FROM broadcast_deliveries
        WHERE broadcast_id = ?
        ORDER BY id ASC
        """,
        (int(broadcast_id),),
    )


async def delete_broadcast_records(db_path: str, broadcast_id: int) -> None:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute("DELETE FROM broadcast_deliveries WHERE broadcast_id = $1", int(broadcast_id))
                await conn.execute("DELETE FROM broadcasts WHERE id = $1", int(broadcast_id))
        return

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")
        await db.execute("BEGIN")
        try:
            await db.execute("DELETE FROM broadcast_deliveries WHERE broadcast_id = ?", (int(broadcast_id),))
            await db.execute("DELETE FROM broadcasts WHERE id = ?", (int(broadcast_id),))
            await db.commit()
        except Exception:
            await db.rollback()
            raise


async def _get_or_create_default_category_id(db_path: str, vendor_id: int) -> int | None:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                """
                SELECT id
                FROM menu_categories
                WHERE vendor_id = $1
                ORDER BY sort_order ASC, id ASC
                LIMIT 1
                """,
                int(vendor_id),
            )
            if row:
                return int(row["id"])
            return None

    row = await _fetchone(
        db_path,
        """
        SELECT id
        FROM menu_categories
        WHERE vendor_id = ?
        ORDER BY sort_order ASC, id ASC
        LIMIT 1
        """,
        (int(vendor_id),),
    )
    if row:
        return int(row["id"])
    return None


async def _execute_returning_id(db_path: str, sql: str, params: tuple[Any, ...] = ()) -> int:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(_pgify_sql(sql) + " RETURNING id", *params)
        if not row:
            raise RuntimeError("Insert failed")
        return int(row["id"])

    async with aiosqlite.connect(db_path) as db:
        cur = await db.execute(sql, params)
        await db.commit()
        return int(cur.lastrowid)


async def init_db(db_path: str) -> None:
    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id SERIAL PRIMARY KEY,
                    telegram_id BIGINT UNIQUE NOT NULL,
                    username TEXT,
                    full_name TEXT,
                    role TEXT NOT NULL DEFAULT 'customer',
                    is_restricted INTEGER NOT NULL DEFAULT 0,
                    restriction_reason TEXT,
                    is_active INTEGER NOT NULL DEFAULT 1,
                    last_seen_at TIMESTAMPTZ,
                    total_orders INTEGER NOT NULL DEFAULT 0,
                    last_order_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS full_name TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_restricted INTEGER NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS restriction_reason TEXT")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS is_active INTEGER NOT NULL DEFAULT 1")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_seen_at TIMESTAMPTZ")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS total_orders INTEGER NOT NULL DEFAULT 0")
            await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS last_order_at TIMESTAMPTZ")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS user_notes (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    note TEXT NOT NULL,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS vendors (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute("ALTER TABLE vendors ADD COLUMN IF NOT EXISTS sort_order INTEGER")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS menu_items (
                    id SERIAL PRIMARY KEY,
                    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                    category_id INTEGER,
                    name TEXT NOT NULL,
                    price_etb INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS menu_categories (
                    id SERIAL PRIMARY KEY,
                    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                    name TEXT NOT NULL,
                    sort_order INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute("ALTER TABLE menu_items ADD COLUMN IF NOT EXISTS category_id INTEGER")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS blocks (
                    id SERIAL PRIMARY KEY,
                    name TEXT NOT NULL,
                    delivery_fee_etb INTEGER,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS orders (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                    items_json TEXT NOT NULL,
                    vendor_name_snapshot TEXT,
                    items_snapshot_json TEXT,
                    block_id INTEGER NOT NULL REFERENCES blocks(id),
                    phone_number TEXT NOT NULL,
                    delivery_fee_etb INTEGER NOT NULL,
                    total_amount_etb INTEGER NOT NULL,
                    promo_code TEXT,
                    discount_amount_etb INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    payment_screenshot_file_id TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
            await conn.execute(
                """
                DO $$
                DECLARE r RECORD;
                BEGIN
                    FOR r IN (
                        SELECT c.conname
                        FROM pg_constraint c
                        JOIN pg_class t ON t.oid = c.conrelid
                        JOIN pg_class rt ON rt.oid = c.confrelid
                        WHERE c.contype = 'f'
                          AND t.relname = 'orders'
                          AND rt.relname = 'vendors'
                    ) LOOP
                        EXECUTE format('ALTER TABLE orders DROP CONSTRAINT IF EXISTS %I', r.conname);
                    END LOOP;
                END $$;
                """
            )

            await conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS vendor_name_snapshot TEXT")
            await conn.execute("ALTER TABLE orders ADD COLUMN IF NOT EXISTS items_snapshot_json TEXT")

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS payments (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id),
                    proof_file_id TEXT NOT NULL,
                    confirmed INTEGER NOT NULL DEFAULT 0,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL,
                    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS promo_codes (
                    id SERIAL PRIMARY KEY,
                    code TEXT UNIQUE NOT NULL,
                    discount_type TEXT NOT NULL,
                    discount_value INTEGER NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    max_uses INTEGER,
                    used_count INTEGER NOT NULL DEFAULT 0,
                    starts_at TIMESTAMPTZ,
                    ends_at TIMESTAMPTZ,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS favorites (
                    id SERIAL PRIMARY KEY,
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                    items_json TEXT NOT NULL,
                    title TEXT,
                    active INTEGER NOT NULL DEFAULT 1,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                    UNIQUE(user_id, vendor_id, items_json)
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS ratings (
                    id SERIAL PRIMARY KEY,
                    order_id INTEGER UNIQUE NOT NULL REFERENCES orders(id),
                    user_id INTEGER NOT NULL REFERENCES users(id),
                    vendor_id INTEGER NOT NULL REFERENCES vendors(id),
                    rating INTEGER NOT NULL,
                    comment TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcasts (
                    id SERIAL PRIMARY KEY,
                    text TEXT NOT NULL,
                    created_by_admin_telegram_id BIGINT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )

            await conn.execute(
                """
                CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                    id SERIAL PRIMARY KEY,
                    broadcast_id INTEGER NOT NULL REFERENCES broadcasts(id),
                    user_telegram_id BIGINT NOT NULL,
                    message_id BIGINT,
                    status TEXT NOT NULL,
                    error TEXT,
                    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
                )
                """
            )
        return

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA journal_mode=WAL")
        await db.execute("PRAGMA foreign_keys=ON")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                telegram_id INTEGER UNIQUE NOT NULL,
                username TEXT,
                full_name TEXT,
                role TEXT NOT NULL DEFAULT 'customer',
                is_restricted INTEGER NOT NULL DEFAULT 0,
                restriction_reason TEXT,
                is_active INTEGER NOT NULL DEFAULT 1,
                last_seen_at TIMESTAMP,
                total_orders INTEGER NOT NULL DEFAULT 0,
                last_order_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        async with db.execute("PRAGMA table_info(users)") as cur:
            existing_user_columns = {row[1] async for row in cur}
        if "full_name" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN full_name TEXT")
        if "is_restricted" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN is_restricted INTEGER NOT NULL DEFAULT 0")
        if "restriction_reason" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN restriction_reason TEXT")
        if "is_active" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN is_active INTEGER NOT NULL DEFAULT 1")
        if "last_seen_at" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN last_seen_at TIMESTAMP")
        if "total_orders" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN total_orders INTEGER NOT NULL DEFAULT 0")
        if "last_order_at" not in existing_user_columns:
            await db.execute("ALTER TABLE users ADD COLUMN last_order_at TIMESTAMP")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS user_notes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                note TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS vendors (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        async with db.execute("PRAGMA table_info(vendors)") as cur:
            existing_vendor_columns = {row[1] async for row in cur}
        if "sort_order" not in existing_vendor_columns:
            await db.execute("ALTER TABLE vendors ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 0")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS menu_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL,
                category_id INTEGER,
                name TEXT NOT NULL,
                price_etb INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS menu_categories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                vendor_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                sort_order INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            )
            """
        )

        async with db.execute("PRAGMA table_info(menu_items)") as cur:
            existing_menu_item_columns = {row[1] async for row in cur}
        if "category_id" not in existing_menu_item_columns:
            await db.execute("ALTER TABLE menu_items ADD COLUMN category_id INTEGER")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS blocks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                delivery_fee_etb INTEGER,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        async with db.execute("PRAGMA table_info(blocks)") as cur:
            existing_columns = {row[1] async for row in cur}
        if "delivery_fee_etb" not in existing_columns:
            await db.execute("ALTER TABLE blocks ADD COLUMN delivery_fee_etb INTEGER")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS orders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                items_json TEXT NOT NULL,
                vendor_name_snapshot TEXT,
                items_snapshot_json TEXT,
                block_id INTEGER NOT NULL,
                phone_number TEXT NOT NULL,
                delivery_fee_etb INTEGER NOT NULL,
                total_amount_etb INTEGER NOT NULL,
                promo_code TEXT,
                discount_amount_etb INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL,
                payment_screenshot_file_id TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (vendor_id) REFERENCES vendors(id),
                FOREIGN KEY (block_id) REFERENCES blocks(id)
            )
            """
        )

        async with db.execute("PRAGMA table_info(orders)") as cur:
            existing_order_columns = {row[1] async for row in cur}
        if "promo_code" not in existing_order_columns:
            await db.execute("ALTER TABLE orders ADD COLUMN promo_code TEXT")
        if "discount_amount_etb" not in existing_order_columns:
            await db.execute("ALTER TABLE orders ADD COLUMN discount_amount_etb INTEGER NOT NULL DEFAULT 0")
        if "vendor_name_snapshot" not in existing_order_columns:
            await db.execute("ALTER TABLE orders ADD COLUMN vendor_name_snapshot TEXT")
        if "items_snapshot_json" not in existing_order_columns:
            await db.execute("ALTER TABLE orders ADD COLUMN items_snapshot_json TEXT")


        if "points_redeemed_etb" in existing_order_columns:
            await db.execute("PRAGMA foreign_keys=OFF")
            await db.execute("DROP TABLE IF EXISTS orders_new")
            await db.execute(
                """
                CREATE TABLE IF NOT EXISTS orders_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER NOT NULL,
                    vendor_id INTEGER NOT NULL,
                    items_json TEXT NOT NULL,
                    block_id INTEGER NOT NULL,
                    phone_number TEXT NOT NULL,
                    delivery_fee_etb INTEGER NOT NULL,
                    total_amount_etb INTEGER NOT NULL,
                    promo_code TEXT,
                    discount_amount_etb INTEGER NOT NULL DEFAULT 0,
                    status TEXT NOT NULL,
                    payment_screenshot_file_id TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    FOREIGN KEY (user_id) REFERENCES users(id),
                    FOREIGN KEY (vendor_id) REFERENCES vendors(id),
                    FOREIGN KEY (block_id) REFERENCES blocks(id)
                )
                """
            )
            await db.execute(
                """
                INSERT INTO orders_new (
                    id, user_id, vendor_id, items_json, block_id, phone_number,
                    delivery_fee_etb, total_amount_etb, promo_code, discount_amount_etb,
                    status, payment_screenshot_file_id, created_at, updated_at
                )
                SELECT
                    id, user_id, vendor_id, items_json, block_id, phone_number,
                    delivery_fee_etb, total_amount_etb, promo_code, discount_amount_etb,
                    status, payment_screenshot_file_id, created_at, updated_at
                FROM orders
                """
            )
            await db.execute("DROP TABLE orders")
            await db.execute("ALTER TABLE orders_new RENAME TO orders")
            await db.execute("PRAGMA foreign_keys=ON")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS payments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER UNIQUE NOT NULL,
                proof_file_id TEXT NOT NULL,
                confirmed INTEGER NOT NULL DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS settings (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS promo_codes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                code TEXT UNIQUE NOT NULL,
                discount_type TEXT NOT NULL,
                discount_value INTEGER NOT NULL,
                active INTEGER NOT NULL DEFAULT 1,
                max_uses INTEGER,
                used_count INTEGER NOT NULL DEFAULT 0,
                starts_at TIMESTAMP,
                ends_at TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await db.execute("DROP TABLE IF EXISTS points_ledger")
        await db.execute("DROP TABLE IF EXISTS user_points")

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS favorites (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                items_json TEXT NOT NULL,
                title TEXT,
                active INTEGER NOT NULL DEFAULT 1,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(user_id, vendor_id, items_json),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS ratings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_id INTEGER UNIQUE NOT NULL,
                user_id INTEGER NOT NULL,
                vendor_id INTEGER NOT NULL,
                rating INTEGER NOT NULL,
                comment TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (order_id) REFERENCES orders(id),
                FOREIGN KEY (user_id) REFERENCES users(id),
                FOREIGN KEY (vendor_id) REFERENCES vendors(id)
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcasts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                created_by_admin_telegram_id INTEGER,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        await db.execute(
            """
            CREATE TABLE IF NOT EXISTS broadcast_deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                broadcast_id INTEGER NOT NULL,
                user_telegram_id INTEGER NOT NULL,
                message_id INTEGER,
                status TEXT NOT NULL,
                error TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (broadcast_id) REFERENCES broadcasts(id)
            )
            """
        )

        await db.commit()


async def upsert_user(db_path: str, telegram_id: int, username: str | None, role: str) -> None:
    await _execute(
        db_path,
        """
        INSERT INTO users (telegram_id, username, full_name, role)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            role = excluded.role
        """,
        (telegram_id, username, None, role),
    )


async def upsert_user_profile(db_path: str, telegram_id: int, username: str | None, full_name: str | None, role: str) -> None:
    await _execute(
        db_path,
        """
        INSERT INTO users (telegram_id, username, full_name, role)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(telegram_id) DO UPDATE SET
            username = excluded.username,
            full_name = excluded.full_name,
            role = excluded.role
        """,
        (telegram_id, username, full_name, role),
    )


async def touch_user_activity(db_path: str, telegram_id: int) -> None:
    try:
        await _execute(
            db_path,
            """
            UPDATE users
            SET is_active = 1,
                last_seen_at = CURRENT_TIMESTAMP
            WHERE telegram_id = ?
            """,
            (int(telegram_id),),
        )
    except Exception:
        return


async def mark_user_inactive(db_path: str, telegram_id: int) -> None:
    try:
        await _execute(
            db_path,
            "UPDATE users SET is_active = 0 WHERE telegram_id = ?",
            (int(telegram_id),),
        )
    except Exception:
        return


async def get_user_role(db_path: str, telegram_id: int) -> str | None:
    row = await _fetchone(db_path, "SELECT role FROM users WHERE telegram_id = ?", (telegram_id,))
    return str(row["role"]) if row else None


async def get_user_id_by_telegram_id(db_path: str, telegram_id: int) -> int | None:
    row = await _fetchone(db_path, "SELECT id FROM users WHERE telegram_id = ?", (telegram_id,))
    return int(row["id"]) if row else None


async def is_user_restricted(db_path: str, telegram_id: int) -> tuple[bool, str | None]:
    try:
        row = await _fetchone(
            db_path,
            "SELECT is_restricted, restriction_reason FROM users WHERE telegram_id = ?",
            (int(telegram_id),),
        )
        if not row:
            return False, None
        restricted = int(row.get("is_restricted") or 0) == 1
        reason = row.get("restriction_reason")
        return restricted, (str(reason) if reason is not None else None)
    except Exception:
        return False, None


async def list_active_vendors(db_path: str) -> list[dict[str, Any]]:
    return await _fetchall(db_path, "SELECT id, name FROM vendors WHERE active = 1 ORDER BY sort_order ASC, id ASC")


async def list_favorites_for_user(db_path: str, telegram_id: int, limit: int = 20) -> list[dict[str, Any]]:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        return []

    return await _fetchall(
        db_path,
        """
        SELECT f.id, f.vendor_id, f.items_json, f.title, f.created_at, v.name AS vendor_name
        FROM favorites f
        JOIN vendors v ON v.id = f.vendor_id
        WHERE f.user_id = ? AND f.active = 1
        ORDER BY f.id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )


async def get_favorite_for_user(db_path: str, telegram_id: int, favorite_id: int) -> dict[str, Any] | None:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        return None

    return await _fetchone(
        db_path,
        """
        SELECT f.id, f.vendor_id, f.items_json, f.title, f.active, f.created_at, v.name AS vendor_name
        FROM favorites f
        JOIN vendors v ON v.id = f.vendor_id
        WHERE f.id = ? AND f.user_id = ?
        """,
        (int(favorite_id), user_id),
    )


async def upsert_favorite(
    db_path: str,
    telegram_id: int,
    vendor_id: int,
    items: dict[int, int],
    title: str | None = None,
) -> None:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        raise RuntimeError("User not found. Call upsert_user first.")

    items_json = json.dumps({str(int(k)): int(v) for k, v in items.items()}, sort_keys=True)

    await _execute(
        db_path,
        """
        INSERT INTO favorites (user_id, vendor_id, items_json, title, active)
        VALUES (?, ?, ?, ?, 1)
        ON CONFLICT(user_id, vendor_id, items_json) DO UPDATE SET
            title = excluded.title,
            active = 1,
            created_at = CURRENT_TIMESTAMP
        """,
        (int(user_id), int(vendor_id), items_json, title),
    )


async def deactivate_favorite(db_path: str, telegram_id: int, favorite_id: int) -> None:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        return

    await _execute(
        db_path,
        "UPDATE favorites SET active = 0 WHERE id = ? AND user_id = ?",
        (int(favorite_id), int(user_id)),
    )


async def list_delivered_orders_pending_rating(db_path: str, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        return []

    return await _fetchall(
        db_path,
        """
        SELECT o.id, o.created_at, o.total_amount_etb,
               COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        LEFT JOIN ratings r ON r.order_id = o.id
        WHERE o.user_id = ?
          AND LOWER(o.status) LIKE '%deliver%'
          AND r.id IS NULL
        ORDER BY o.id DESC
        LIMIT ?
        """,
        (user_id, limit),
    )


async def upsert_order_rating(
    db_path: str,
    telegram_id: int,
    order_id: int,
    rating: int,
    comment: str | None,
) -> None:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        raise RuntimeError("User not found. Call upsert_user first.")

    row = await _fetchone(db_path, "SELECT vendor_id, user_id FROM orders WHERE id = ?", (int(order_id),))
    if not row:
        raise RuntimeError("Order not found")
    if int(row["user_id"]) != int(user_id):
        raise RuntimeError("Not allowed")
    vendor_id = int(row["vendor_id"])

    r = int(rating)
    if r < 1:
        r = 1
    if r > 5:
        r = 5

    await _execute(
        db_path,
        """
        INSERT INTO ratings (order_id, user_id, vendor_id, rating, comment)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(order_id) DO UPDATE SET
            rating = excluded.rating,
            comment = excluded.comment,
            created_at = CURRENT_TIMESTAMP
        """,
        (int(order_id), int(user_id), vendor_id, r, comment),
    )


async def get_order_rating(db_path: str, order_id: int) -> dict[str, Any] | None:
    return await _fetchone(
        db_path,
        """
        SELECT rating, comment, created_at
        FROM ratings
        WHERE order_id = ?
        """,
        (int(order_id),),
    )


async def list_menu_categories_for_vendor(db_path: str, vendor_id: int) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, vendor_id, name, sort_order
        FROM menu_categories
        WHERE vendor_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(vendor_id),),
    )


async def list_active_menu_items_by_category(db_path: str, vendor_id: int, category_id: int) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, name, price_etb
        FROM menu_items
        WHERE vendor_id = ? AND category_id = ? AND active = 1
        ORDER BY name ASC
        """,
        (int(vendor_id), int(category_id)),
    )


async def list_active_menu_items(db_path: str, vendor_id: int) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, name, price_etb
        FROM menu_items
        WHERE vendor_id = ? AND active = 1
        ORDER BY name ASC
        """,
        (vendor_id,),
    )


async def get_vendor(db_path: str, vendor_id: int) -> dict[str, Any] | None:
    return await _fetchone(db_path, "SELECT id, name FROM vendors WHERE id = ?", (vendor_id,))


async def get_menu_item(db_path: str, item_id: int) -> dict[str, Any] | None:
    return await _fetchone(
        db_path,
        "SELECT id, vendor_id, name, price_etb FROM menu_items WHERE id = ?",
        (item_id,),
    )


async def list_active_blocks(db_path: str) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        "SELECT id, name, delivery_fee_etb FROM blocks WHERE active = 1 ORDER BY name ASC",
    )


async def get_block(db_path: str, block_id: int) -> dict[str, Any] | None:
    return await _fetchone(
        db_path,
        "SELECT id, name, delivery_fee_etb FROM blocks WHERE id = ?",
        (int(block_id),),
    )


async def create_order(
    db_path: str,
    telegram_id: int,
    vendor_id: int,
    items: dict[int, int],
    block_id: int,
    phone_number: str,
    delivery_fee_etb: int,
    total_amount_etb: int,
    promo_code: str | None,
    discount_amount_etb: int,
    status: str,
) -> int:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        raise RuntimeError("User not found. Call upsert_user first.")

    items_json = json.dumps(items, ensure_ascii=False)
    vendor = await get_vendor(db_path, int(vendor_id))
    vendor_name_snapshot = str((vendor or {}).get("name") or "")
    items_snapshot: dict[str, dict[str, Any]] = {}
    for item_id in (items or {}).keys():
        it = await get_menu_item(db_path, int(item_id))
        if not it:
            continue
        items_snapshot[str(int(item_id))] = {
            "name": str(it.get("name") or ""),
            "price_etb": int(it.get("price_etb") or 0),
        }
    items_snapshot_json = json.dumps(items_snapshot, ensure_ascii=False)
    promo_norm = promo_code.strip().upper() if promo_code else None

    if _is_postgres(db_path):
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            async with conn.transaction():
                if promo_norm:
                    await conn.execute(
                        "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = $1",
                        promo_norm,
                    )
                row = await conn.fetchrow(
                    """
                    INSERT INTO orders (
                        user_id, vendor_id, items_json, vendor_name_snapshot, items_snapshot_json,
                        block_id, phone_number, delivery_fee_etb, total_amount_etb,
                        promo_code, discount_amount_etb, status
                    ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12)
                    RETURNING id
                    """,
                    int(user_id),
                    int(vendor_id),
                    items_json,
                    vendor_name_snapshot,
                    items_snapshot_json,
                    int(block_id),
                    phone_number,
                    int(delivery_fee_etb),
                    int(total_amount_etb),
                    promo_norm,
                    int(discount_amount_etb),
                    status,
                )

                await conn.execute(
                    """
                    UPDATE users
                    SET total_orders = COALESCE(total_orders, 0) + 1,
                        last_order_at = NOW()
                    WHERE id = $1
                    """,
                    int(user_id),
                )
        if not row:
            raise RuntimeError("Insert failed")
        return int(row["id"])

    if promo_norm:
        await _execute(
            db_path,
            "UPDATE promo_codes SET used_count = used_count + 1 WHERE code = ?",
            (promo_norm,),
        )
    order_id = await _execute_returning_id(
        db_path,
        """
        INSERT INTO orders (
            user_id, vendor_id, items_json, vendor_name_snapshot, items_snapshot_json,
            block_id, phone_number, delivery_fee_etb, total_amount_etb,
            promo_code, discount_amount_etb, status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            int(user_id),
            int(vendor_id),
            items_json,
            vendor_name_snapshot,
            items_snapshot_json,
            int(block_id),
            phone_number,
            int(delivery_fee_etb),
            int(total_amount_etb),
            promo_norm,
            int(discount_amount_etb),
            status,
        ),
    )

    await _execute(
        db_path,
        """
        UPDATE users
        SET total_orders = COALESCE(total_orders, 0) + 1,
            last_order_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (int(user_id),),
    )

    return int(order_id)


async def get_active_promo_code(db_path: str, code: str) -> dict[str, Any] | None:
    c = code.strip().upper()
    if not c:
        return None
    row = await _fetchone(
        db_path,
        """
        SELECT id, code, discount_type, discount_value, active, max_uses, used_count
        FROM promo_codes
        WHERE UPPER(code) = ?
        """,
        (c,),
    )
    if not row:
        return None
    if int(row.get("active", 0) or 0) != 1:
        return None
    max_uses = row.get("max_uses")
    if max_uses is not None and int(row.get("used_count", 0) or 0) >= int(max_uses):
        return None
    return row


async def admin_list_promo_codes(db_path: str) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, code, discount_type, discount_value, active, max_uses, used_count
        FROM promo_codes
        ORDER BY id DESC
        """,
    )


async def admin_create_promo_code(
    db_path: str,
    code: str,
    discount_type: str,
    discount_value: int,
    max_uses: int | None,
) -> int:
    c = code.strip().upper()
    return await _execute_returning_id(
        db_path,
        """
        INSERT INTO promo_codes (code, discount_type, discount_value, active, max_uses, used_count)
        VALUES (?, ?, ?, 1, ?, 0)
        """,
        (c, discount_type, int(discount_value), max_uses),
    )


async def admin_set_promo_active(db_path: str, promo_id: int, active: bool) -> None:
    await _execute(
        db_path,
        "UPDATE promo_codes SET active = ? WHERE id = ?",
        (1 if active else 0, int(promo_id)),
    )


async def admin_list_menu_items(db_path: str, vendor_id: int | None = None) -> list[dict[str, Any]]:
    if vendor_id is None:
        query = """
        SELECT mi.id, mi.vendor_id, v.name AS vendor_name, mi.name, mi.price_etb, mi.active
        FROM menu_items mi
        JOIN vendors v ON v.id = mi.vendor_id
        ORDER BY mi.id DESC
        """
        params: tuple[Any, ...] = ()
    else:
        query = """
        SELECT mi.id, mi.vendor_id, v.name AS vendor_name, mi.name, mi.price_etb, mi.active
        FROM menu_items mi
        JOIN vendors v ON v.id = mi.vendor_id
        WHERE mi.vendor_id = ?
        ORDER BY mi.id DESC
        """
        params = (int(vendor_id),)
    return await _fetchall(db_path, query, params)


async def admin_list_orders_for_date(db_path: str, date_yyyy_mm_dd: str, limit: int = 200) -> list[dict[str, Any]]:
    if _is_postgres(db_path):
        d: date | None = None
        try:
            d = date.fromisoformat((date_yyyy_mm_dd or "").strip())
        except Exception:
            d = None
        if d is None:
            return []
        return await _fetchall(
            db_path,
            """
            SELECT o.id, o.status, o.total_amount_etb, o.created_at,
                   COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
                   u.telegram_id AS customer_telegram_id,
                   COALESCE(u.username, '') AS customer_username
            FROM orders o
            LEFT JOIN vendors v ON v.id = o.vendor_id
            JOIN users u ON u.id = o.user_id
            WHERE o.created_at::date = ($1::date)
            ORDER BY o.id DESC
            LIMIT $2
            """,
            (d, int(limit)),
        )

    return await _fetchall(
        db_path,
        """
        SELECT o.id, o.status, o.total_amount_etb, o.created_at,
               COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
               u.telegram_id AS customer_telegram_id,
               COALESCE(u.username, '') AS customer_username
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        JOIN users u ON u.id = o.user_id
        WHERE date(o.created_at) = date(?)
        ORDER BY o.id DESC
        LIMIT ?
        """,
        (date_yyyy_mm_dd, int(limit)),
    )


async def admin_delete_orders_for_date(db_path: str, date_yyyy_mm_dd: str) -> int:
    if _is_postgres(db_path):
        d: date | None = None
        try:
            d = date.fromisoformat((date_yyyy_mm_dd or "").strip())
        except Exception:
            d = None
        if d is None:
            return 0
        pool = await _get_pg_pool(db_path)
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT id FROM orders WHERE created_at::date = ($1::date)",
                d,
            )
            order_ids = [int(r["id"]) for r in rows]
            if not order_ids:
                return 0
            async with conn.transaction():
                await conn.execute("DELETE FROM payments WHERE order_id = ANY($1::int[])", order_ids)
                await conn.execute("DELETE FROM ratings WHERE order_id = ANY($1::int[])", order_ids)
                await conn.execute("DELETE FROM orders WHERE id = ANY($1::int[])", order_ids)
        return len(order_ids)

    async with aiosqlite.connect(db_path) as db:
        await db.execute("PRAGMA foreign_keys=ON")

        async with db.execute(
            "SELECT id FROM orders WHERE date(created_at) = date(?)",
            (date_yyyy_mm_dd,),
        ) as cur:
            rows = await cur.fetchall()
            order_ids = [int(r[0]) for r in rows]

        if not order_ids:
            return 0

        placeholders = ",".join(["?"] * len(order_ids))
        await db.execute("BEGIN")
        try:
            await db.execute(f"DELETE FROM payments WHERE order_id IN ({placeholders})", order_ids)
            await db.execute(f"DELETE FROM ratings WHERE order_id IN ({placeholders})", order_ids)
            await db.execute(f"DELETE FROM orders WHERE id IN ({placeholders})", order_ids)
            await db.commit()
            return len(order_ids)
        except Exception:
            await db.rollback()
            raise


async def admin_set_menu_item_active(db_path: str, item_id: int, active: bool) -> None:
    await _execute(
        db_path,
        "UPDATE menu_items SET active = ? WHERE id = ?",
        (1 if active else 0, int(item_id)),
    )


async def admin_update_menu_item_price(db_path: str, item_id: int, price_etb: int) -> None:
    await _execute(
        db_path,
        "UPDATE menu_items SET price_etb = ? WHERE id = ?",
        (int(price_etb), int(item_id)),
    )


async def attach_payment_proof(db_path: str, order_id: int, proof_file_id: str) -> None:
    await _execute(
        db_path,
        "UPDATE orders SET payment_screenshot_file_id = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (proof_file_id, int(order_id)),
    )
    await _execute(
        db_path,
        """
        INSERT INTO payments (order_id, proof_file_id, confirmed)
        VALUES (?, ?, 0)
        ON CONFLICT(order_id) DO UPDATE SET
            proof_file_id = excluded.proof_file_id,
            confirmed = 0,
            created_at = CURRENT_TIMESTAMP
        """,
        (int(order_id), proof_file_id),
    )


async def update_order_status(db_path: str, order_id: int, status: str) -> None:
    await _execute(
        db_path,
        "UPDATE orders SET status = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
        (status, int(order_id)),
    )


async def list_orders_for_user(db_path: str, telegram_id: int, limit: int = 10) -> list[dict[str, Any]]:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        return []

    return await _fetchall(
        db_path,
        """
        SELECT o.id, o.vendor_id, o.status, o.total_amount_etb, o.created_at,
               COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        WHERE o.user_id = ?
        ORDER BY o.id DESC
        LIMIT ?
        """,
        (user_id, int(limit)),
    )


async def get_order_details(db_path: str, telegram_id: int, order_id: int) -> dict[str, Any] | None:
    user_id = await get_user_id_by_telegram_id(db_path, telegram_id)
    if user_id is None:
        return None

    row = await _fetchone(
        db_path,
        """
        SELECT
            o.id, o.vendor_id, o.status, o.items_json, o.phone_number,
            o.delivery_fee_etb, o.total_amount_etb, o.promo_code, o.discount_amount_etb, o.payment_screenshot_file_id,
            o.created_at,
            COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
            b.name AS block_name
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        JOIN blocks b ON b.id = o.block_id
        JOIN users u ON u.id = o.user_id
        WHERE o.id = ? AND u.telegram_id = ?
        """,
        (int(order_id), int(telegram_id)),
    )
    if not row:
        return None
    try:
        row["items"] = {int(k): int(v) for k, v in json.loads(row["items_json"]).items()}
    except Exception:
        row["items"] = {}
    return row


async def admin_get_analytics_dashboard(db_path: str) -> dict[str, Any]:
    if _is_postgres(db_path):
        today = await _fetchone(
            db_path,
            """
            SELECT
                COUNT(*) AS orders_today,
                COALESCE(SUM(total_amount_etb), 0) AS revenue_today
            FROM orders
            WHERE created_at::date = CURRENT_DATE
            """,
        )
        seven = await _fetchone(
            db_path,
            """
            SELECT
                COUNT(*) AS orders_7d,
                COALESCE(SUM(total_amount_etb), 0) AS revenue_7d
            FROM orders
            WHERE created_at::date >= (CURRENT_DATE - INTERVAL '6 days')
            """,
        )
        status_counts = await _fetchall(
            db_path,
            """
            SELECT LOWER(status) AS status, COUNT(*) AS cnt
            FROM orders
            WHERE created_at::date >= (CURRENT_DATE - INTERVAL '6 days')
            GROUP BY LOWER(status)
            ORDER BY cnt DESC
            """,
        )
        daily = await _fetchall(
            db_path,
            """
            SELECT created_at::date AS day,
                   COUNT(*) AS orders,
                   COALESCE(SUM(total_amount_etb), 0) AS revenue
            FROM orders
            WHERE created_at::date >= (CURRENT_DATE - INTERVAL '6 days')
            GROUP BY created_at::date
            ORDER BY day ASC
            """,
        )
        top_vendors = await _fetchall(
            db_path,
            """
            SELECT v.name AS vendor_name,
                   COUNT(*) AS orders,
                   COALESCE(SUM(o.total_amount_etb), 0) AS revenue
            FROM orders o
            JOIN vendors v ON v.id = o.vendor_id
            WHERE o.created_at::date >= (CURRENT_DATE - INTERVAL '6 days')
            GROUP BY o.vendor_id, v.name
            ORDER BY orders DESC
            LIMIT 5
            """,
        )
        return {
            "today": today or {"orders_today": 0, "revenue_today": 0},
            "seven_days": seven or {"orders_7d": 0, "revenue_7d": 0},
            "status_counts": status_counts,
            "daily": daily,
            "top_vendors": top_vendors,
        }

    today = await _fetchone(
        db_path,
        """
        SELECT
            COUNT(*) AS orders_today,
            COALESCE(SUM(total_amount_etb), 0) AS revenue_today
        FROM orders
        WHERE date(created_at) = date('now')
        """,
    )
    seven = await _fetchone(
        db_path,
        """
        SELECT
            COUNT(*) AS orders_7d,
            COALESCE(SUM(total_amount_etb), 0) AS revenue_7d
        FROM orders
        WHERE date(created_at) >= date('now', '-6 day')
        """,
    )
    status_counts = await _fetchall(
        db_path,
        """
        SELECT LOWER(status) AS status, COUNT(*) AS cnt
        FROM orders
        WHERE date(created_at) >= date('now', '-6 day')
        GROUP BY LOWER(status)
        ORDER BY cnt DESC
        """,
    )
    daily = await _fetchall(
        db_path,
        """
        SELECT date(created_at) AS day,
               COUNT(*) AS orders,
               COALESCE(SUM(total_amount_etb), 0) AS revenue
        FROM orders
        WHERE date(created_at) >= date('now', '-6 day')
        GROUP BY date(created_at)
        ORDER BY day ASC
        """,
    )
    top_vendors = await _fetchall(
        db_path,
        """
        SELECT v.name AS vendor_name,
               COUNT(*) AS orders,
               COALESCE(SUM(o.total_amount_etb), 0) AS revenue
        FROM orders o
        JOIN vendors v ON v.id = o.vendor_id
        WHERE date(o.created_at) >= date('now', '-6 day')
        GROUP BY o.vendor_id
        ORDER BY orders DESC
        LIMIT 5
        """,
    )
    return {
        "today": today or {"orders_today": 0, "revenue_today": 0},
        "seven_days": seven or {"orders_7d": 0, "revenue_7d": 0},
        "status_counts": status_counts,
        "daily": daily,
        "top_vendors": top_vendors,
    }


async def admin_get_daily_vendor_report(db_path: str, day_offset: int = 0) -> list[dict[str, Any]]:
    if _is_postgres(db_path):
        return await _fetchall(
            db_path,
            """
            SELECT
                v.id AS vendor_id,
                COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
                COUNT(*) AS orders,
                COALESCE(SUM(o.total_amount_etb), 0) AS total_amount_etb,
                COALESCE(SUM(o.delivery_fee_etb), 0) AS delivery_fee_etb,
                COALESCE(SUM(o.discount_amount_etb), 0) AS discount_amount_etb
            FROM orders o
            LEFT JOIN vendors v ON v.id = o.vendor_id
            WHERE o.created_at::date = (CURRENT_DATE + (? * INTERVAL '1 day'))::date
            GROUP BY o.vendor_id, v.id, COALESCE(v.name, o.vendor_name_snapshot, '')
            ORDER BY total_amount_etb DESC
            """,
            (int(day_offset),),
        )

    modifier = f"{int(day_offset)} day"
    return await _fetchall(
        db_path,
        """
        SELECT
            v.id AS vendor_id,
            COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
            COUNT(*) AS orders,
            COALESCE(SUM(o.total_amount_etb), 0) AS total_amount_etb,
            COALESCE(SUM(o.delivery_fee_etb), 0) AS delivery_fee_etb,
            COALESCE(SUM(o.discount_amount_etb), 0) AS discount_amount_etb
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        WHERE date(o.created_at) = date('now', ?)
        GROUP BY o.vendor_id, COALESCE(v.name, o.vendor_name_snapshot, '')
        ORDER BY total_amount_etb DESC
        """,
        (modifier,),
    )


async def admin_list_orders(db_path: str, status: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
    if status:
        query = """
        SELECT o.id, o.status, o.total_amount_etb, o.created_at,
               COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
               u.telegram_id AS customer_telegram_id,
               COALESCE(u.username, '') AS customer_username
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        JOIN users u ON u.id = o.user_id
        WHERE o.status = ?
        ORDER BY o.id DESC
        LIMIT ?
        """
        params = (status, int(limit))
    else:
        query = """
        SELECT o.id, o.status, o.total_amount_etb, o.created_at,
               COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
               u.telegram_id AS customer_telegram_id,
               COALESCE(u.username, '') AS customer_username
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        JOIN users u ON u.id = o.user_id
        ORDER BY o.id DESC
        LIMIT ?
        """
        params = (int(limit),)
    return await _fetchall(db_path, query, params)


async def admin_get_order_details(db_path: str, order_id: int) -> dict[str, Any] | None:
    row = await _fetchone(
        db_path,
        """
        SELECT
            o.id, o.status, o.items_json, o.phone_number,
            o.delivery_fee_etb, o.total_amount_etb, o.promo_code, o.discount_amount_etb, o.payment_screenshot_file_id,
            o.created_at,
            COALESCE(v.name, o.vendor_name_snapshot, '') AS vendor_name,
            b.name AS block_name,
            u.telegram_id AS customer_telegram_id,
            COALESCE(u.username, '') AS customer_username,
            p.confirmed AS payment_confirmed
        FROM orders o
        LEFT JOIN vendors v ON v.id = o.vendor_id
        JOIN blocks b ON b.id = o.block_id
        JOIN users u ON u.id = o.user_id
        LEFT JOIN payments p ON p.order_id = o.id
        WHERE o.id = ?
        """,
        (int(order_id),),
    )
    if not row:
        return None
    try:
        row["items"] = {int(k): int(v) for k, v in json.loads(row["items_json"]).items()}
    except Exception:
        row["items"] = {}
    return row


async def admin_set_payment_confirmed(db_path: str, order_id: int, confirmed: bool) -> None:
    await _execute(
        db_path,
        "UPDATE payments SET confirmed = ? WHERE order_id = ?",
        (1 if confirmed else 0, int(order_id)),
    )


async def list_all_user_telegram_ids(db_path: str) -> list[int]:
    rows = await _fetchall(db_path, "SELECT telegram_id FROM users")
    return [int(r["telegram_id"]) for r in rows]


async def user_has_used_any_promo_code(db_path: str, telegram_id: int) -> bool:
    row = await _fetchone(
        db_path,
        """
        SELECT 1
        FROM orders o
        JOIN users u ON u.id = o.user_id
        WHERE u.telegram_id = ?
          AND o.promo_code IS NOT NULL
          AND TRIM(o.promo_code) <> ''
        LIMIT 1
        """,
        (int(telegram_id),),
    )
    return row is not None


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------
async def get_setting(db_path: str, key: str) -> str | None:
    row = await _fetchone(
        db_path,
        "SELECT value FROM settings WHERE key = ?",
        (str(key),),
    )
    if not row:
        return None
    value = row.get("value")
    return None if value is None else str(value)


async def set_setting(db_path: str, key: str, value: str) -> None:
    if _is_postgres(db_path):
        await _execute(
            db_path,
            """
            INSERT INTO settings (key, value, updated_at)
            VALUES (?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT (key) DO UPDATE
            SET value = EXCLUDED.value, updated_at = CURRENT_TIMESTAMP
            """,
            (str(key), str(value)),
        )
        return

    await _execute(
        db_path,
        """
        INSERT INTO settings (key, value, updated_at)
        VALUES (?, ?, CURRENT_TIMESTAMP)
        ON CONFLICT(key) DO UPDATE
        SET value = excluded.value, updated_at = CURRENT_TIMESTAMP
        """,
        (str(key), str(value)),
    )


# ---------------------------------------------------------------------------
# Admin: vendors (food houses)
# ---------------------------------------------------------------------------
async def admin_list_vendors(db_path: str) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        "SELECT id, name, active, sort_order FROM vendors ORDER BY sort_order ASC, id ASC",
    )


async def admin_seed_vendor(db_path: str, name: str) -> int:
    row = await _fetchone(
        db_path,
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM vendors",
    )
    next_order = int((row or {}).get("next_order") or 1)
    return await _execute_returning_id(
        db_path,
        "INSERT INTO vendors (name, active, sort_order) VALUES (?, 1, ?)",
        (str(name).strip(), next_order),
    )


async def admin_set_vendor_active(db_path: str, vendor_id: int, active: bool) -> None:
    await _execute(
        db_path,
        "UPDATE vendors SET active = ? WHERE id = ?",
        (1 if active else 0, int(vendor_id)),
    )


async def admin_move_vendor(db_path: str, vendor_id: int, direction: str) -> None:
    await _move_by_sort_order(db_path, "vendors", None, int(vendor_id), direction)


async def admin_hard_delete_vendor_and_related(db_path: str, vendor_id: int) -> None:
    vid = int(vendor_id)
    # Orders are intentionally preserved (they keep vendor name snapshots).
    await _execute(db_path, "DELETE FROM favorites WHERE vendor_id = ?", (vid,))
    await _execute(db_path, "DELETE FROM menu_items WHERE vendor_id = ?", (vid,))
    await _execute(db_path, "DELETE FROM menu_categories WHERE vendor_id = ?", (vid,))
    await _execute(db_path, "DELETE FROM vendors WHERE id = ?", (vid,))


# ---------------------------------------------------------------------------
# Admin: menu categories
# ---------------------------------------------------------------------------
async def admin_list_menu_categories(db_path: str, vendor_id: int) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        """
        SELECT id, vendor_id, name, sort_order
        FROM menu_categories
        WHERE vendor_id = ?
        ORDER BY sort_order ASC, id ASC
        """,
        (int(vendor_id),),
    )


async def admin_create_menu_category(db_path: str, vendor_id: int, name: str) -> int:
    row = await _fetchone(
        db_path,
        "SELECT COALESCE(MAX(sort_order), 0) + 1 AS next_order FROM menu_categories WHERE vendor_id = ?",
        (int(vendor_id),),
    )
    next_order = int((row or {}).get("next_order") or 1)
    return await _execute_returning_id(
        db_path,
        "INSERT INTO menu_categories (vendor_id, name, sort_order) VALUES (?, ?, ?)",
        (int(vendor_id), str(name).strip(), next_order),
    )


async def admin_update_menu_category_name(db_path: str, category_id: int, name: str) -> None:
    await _execute(
        db_path,
        "UPDATE menu_categories SET name = ? WHERE id = ?",
        (str(name).strip(), int(category_id)),
    )


async def admin_move_menu_category(db_path: str, vendor_id: int, category_id: int, direction: str) -> None:
    await _move_by_sort_order(db_path, "menu_categories", int(vendor_id), int(category_id), direction)


async def admin_delete_menu_category_and_items(db_path: str, category_id: int) -> None:
    cid = int(category_id)
    await _execute(db_path, "DELETE FROM menu_items WHERE category_id = ?", (cid,))
    await _execute(db_path, "DELETE FROM menu_categories WHERE id = ?", (cid,))


# ---------------------------------------------------------------------------
# Admin: menu items
# ---------------------------------------------------------------------------
async def admin_seed_menu_item(db_path: str, vendor_id: int, category_id: int, name: str, price_etb: int) -> int:
    return await _execute_returning_id(
        db_path,
        "INSERT INTO menu_items (vendor_id, category_id, name, price_etb, active) VALUES (?, ?, ?, ?, 1)",
        (int(vendor_id), int(category_id), str(name).strip(), int(price_etb)),
    )


async def admin_update_menu_item_name(db_path: str, item_id: int, name: str) -> None:
    await _execute(
        db_path,
        "UPDATE menu_items SET name = ? WHERE id = ?",
        (str(name).strip(), int(item_id)),
    )


async def admin_hard_delete_menu_item(db_path: str, item_id: int) -> None:
    await _execute(db_path, "DELETE FROM menu_items WHERE id = ?", (int(item_id),))


# ---------------------------------------------------------------------------
# Admin: blocks
# ---------------------------------------------------------------------------
async def admin_list_blocks(db_path: str) -> list[dict[str, Any]]:
    return await _fetchall(
        db_path,
        "SELECT id, name, delivery_fee_etb, active FROM blocks ORDER BY id ASC",
    )


async def admin_seed_block(db_path: str, name: str, delivery_fee_etb: int) -> int:
    return await _execute_returning_id(
        db_path,
        "INSERT INTO blocks (name, delivery_fee_etb, active) VALUES (?, ?, 1)",
        (str(name).strip(), int(delivery_fee_etb)),
    )


async def admin_set_block_active(db_path: str, block_id: int, active: bool) -> None:
    await _execute(
        db_path,
        "UPDATE blocks SET active = ? WHERE id = ?",
        (1 if active else 0, int(block_id)),
    )


async def admin_update_block_fee(db_path: str, block_id: int, delivery_fee_etb: int) -> None:
    await _execute(
        db_path,
        "UPDATE blocks SET delivery_fee_etb = ? WHERE id = ?",
        (int(delivery_fee_etb), int(block_id)),
    )


# ---------------------------------------------------------------------------
# Shared helper: reorder rows by sort_order (swap with neighbour)
# ---------------------------------------------------------------------------
async def _move_by_sort_order(
    db_path: str,
    table: str,
    vendor_id: int | None,
    row_id: int,
    direction: str,
) -> None:
    if table not in {"vendors", "menu_categories"}:
        raise ValueError("Unsupported table for reordering")
    direction = (direction or "").strip().lower()
    if direction not in {"up", "down"}:
        return

    scope_sql = ""
    scope_params: tuple[Any, ...] = ()
    if vendor_id is not None:
        scope_sql = " WHERE vendor_id = ?"
        scope_params = (int(vendor_id),)

    rows = await _fetchall(
        db_path,
        f"SELECT id, sort_order FROM {table}{scope_sql} ORDER BY sort_order ASC, id ASC",
        scope_params,
    )
    ids = [int(r["id"]) for r in rows]
    if row_id not in ids:
        return

    idx = ids.index(row_id)
    swap_idx = idx - 1 if direction == "up" else idx + 1
    if swap_idx < 0 or swap_idx >= len(ids):
        return

    a_id = ids[idx]
    b_id = ids[swap_idx]
    a_order = int(rows[idx]["sort_order"])
    b_order = int(rows[swap_idx]["sort_order"])
    if a_order == b_order:
        a_order, b_order = idx, swap_idx

    await _execute(db_path, f"UPDATE {table} SET sort_order = ? WHERE id = ?", (b_order, a_id))
    await _execute(db_path, f"UPDATE {table} SET sort_order = ? WHERE id = ?", (a_order, b_id))
