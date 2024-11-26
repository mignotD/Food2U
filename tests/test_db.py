"""Tests for the async data layer (against a temporary SQLite DB)."""

import pytest

from food2u import db


async def test_settings_roundtrip(db_path):
    assert await db.get_setting(db_path, "missing") is None
    await db.set_setting(db_path, "ordering_open", "1")
    assert await db.get_setting(db_path, "ordering_open") == "1"
    # upsert overwrites
    await db.set_setting(db_path, "ordering_open", "0")
    assert await db.get_setting(db_path, "ordering_open") == "0"


async def test_vendor_crud_and_ordering(db_path):
    v1 = await db.admin_seed_vendor(db_path, "Burger House")
    v2 = await db.admin_seed_vendor(db_path, "Pizza Place")

    vendors = await db.admin_list_vendors(db_path)
    assert [v["name"] for v in vendors] == ["Burger House", "Pizza Place"]

    # move pizza up -> becomes first
    await db.admin_move_vendor(db_path, v2, "up")
    vendors = await db.admin_list_vendors(db_path)
    assert vendors[0]["name"] == "Pizza Place"

    # disable v1 -> not in active list
    await db.admin_set_vendor_active(db_path, v1, False)
    active = await db.list_active_vendors(db_path)
    assert all(int(v["id"]) != v1 for v in active)

    # hard delete v2 cascades (no items/cats), vendor gone
    await db.admin_hard_delete_vendor_and_related(db_path, v2)
    vendors = await db.admin_list_vendors(db_path)
    assert all(int(v["id"]) != v2 for v in vendors)


async def test_category_and_item_flow(db_path):
    vid = await db.admin_seed_vendor(db_path, "Burger House")
    cid = await db.admin_create_menu_category(db_path, vid, "Mains")
    cats = await db.admin_list_menu_categories(db_path, vid)
    assert len(cats) == 1 and cats[0]["name"] == "Mains"

    iid = await db.admin_seed_menu_item(db_path, vid, cid, "Cheeseburger", 250)
    await db.admin_update_menu_item_name(db_path, iid, "Big Cheeseburger")
    await db.admin_update_menu_item_price(db_path, iid, 300)
    items = await db.admin_list_menu_items(db_path, vid)
    assert items[0]["name"] == "Big Cheeseburger"
    assert int(items[0]["price_etb"]) == 300

    # disable item -> not in active-by-category
    await db.admin_set_menu_item_active(db_path, iid, False)
    active = await db.list_active_menu_items_by_category(db_path, vid, cid)
    assert active == []

    # deleting category removes its items
    await db.admin_set_menu_item_active(db_path, iid, True)
    await db.admin_delete_menu_category_and_items(db_path, cid)
    assert await db.admin_list_menu_categories(db_path, vid) == []
    assert await db.get_menu_item(db_path, iid) is None


async def test_block_crud(db_path):
    bid = await db.admin_seed_block(db_path, "Block A", 30)
    await db.admin_update_block_fee(db_path, bid, 40)
    blocks = await db.admin_list_blocks(db_path)
    assert int(blocks[0]["delivery_fee_etb"]) == 40

    await db.admin_set_block_active(db_path, bid, False)
    assert await db.list_active_blocks(db_path) == []


async def test_promo_codes(db_path):
    pid = await db.admin_create_promo_code(db_path, "WSU10", "percent", 10, max_uses=5)
    promos = await db.admin_list_promo_codes(db_path)
    assert promos[0]["code"] == "WSU10"

    active = await db.get_active_promo_code(db_path, "WSU10")
    assert active is not None and int(active["discount_value"]) == 10

    await db.admin_set_promo_active(db_path, pid, False)
    assert await db.get_active_promo_code(db_path, "WSU10") is None


async def test_favorites(db_path):
    await db.upsert_user(db_path, telegram_id=100, username="alice", role="customer")
    vid = await db.admin_seed_vendor(db_path, "Burger House")
    cid = await db.admin_create_menu_category(db_path, vid, "Mains")
    iid = await db.admin_seed_menu_item(db_path, vid, cid, "Cheeseburger", 250)

    await db.upsert_favorite(db_path, 100, vid, {iid: 2}, title="Burger House")
    favs = await db.list_favorites_for_user(db_path, 100)
    assert len(favs) == 1
    fav = await db.get_favorite_for_user(db_path, 100, int(favs[0]["id"]))
    assert fav is not None

    await db.deactivate_favorite(db_path, 100, int(favs[0]["id"]))
    assert await db.list_favorites_for_user(db_path, 100) == []


async def test_order_creation_and_rating(db_path):
    await db.upsert_user(db_path, telegram_id=200, username="bob", role="customer")
    vid = await db.admin_seed_vendor(db_path, "Burger House")
    cid = await db.admin_create_menu_category(db_path, vid, "Mains")
    iid = await db.admin_seed_menu_item(db_path, vid, cid, "Cheeseburger", 250)
    bid = await db.admin_seed_block(db_path, "Block A", 30)

    order_id = await db.create_order(
        db_path,
        telegram_id=200,
        vendor_id=vid,
        items={iid: 2},
        block_id=bid,
        phone_number="0911223344",
        delivery_fee_etb=30,
        total_amount_etb=530,
        promo_code=None,
        discount_amount_etb=0,
        status="Awaiting Payment",
    )
    assert order_id > 0

    details = await db.get_order_details(db_path, 200, order_id)
    assert details is not None
    assert details["items"] == {iid: 2}
    assert int(details["total_amount_etb"]) == 530

    await db.upsert_order_rating(db_path, 200, order_id, 5, "Great!")
    rating = await db.get_order_rating(db_path, order_id)
    assert rating is not None and int(rating["rating"]) == 5


async def test_rating_rejects_other_users_order(db_path):
    await db.upsert_user(db_path, telegram_id=300, username="a", role="customer")
    await db.upsert_user(db_path, telegram_id=301, username="b", role="customer")
    vid = await db.admin_seed_vendor(db_path, "V")
    cid = await db.admin_create_menu_category(db_path, vid, "C")
    iid = await db.admin_seed_menu_item(db_path, vid, cid, "Item", 100)
    bid = await db.admin_seed_block(db_path, "B", 10)
    order_id = await db.create_order(
        db_path,
        telegram_id=300,
        vendor_id=vid,
        items={iid: 1},
        block_id=bid,
        phone_number="0911223344",
        delivery_fee_etb=10,
        total_amount_etb=110,
        promo_code=None,
        discount_amount_etb=0,
        status="Awaiting Payment",
    )
    with pytest.raises(RuntimeError):
        await db.upsert_order_rating(db_path, 301, order_id, 4, None)
