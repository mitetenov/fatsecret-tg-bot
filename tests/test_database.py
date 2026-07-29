"""Unit tests for database layer — product caching and meal log CRUD."""

import os
import tempfile

import pytest

import database


@pytest.fixture(autouse=True)
def _fresh_db(monkeypatch):
    """Create a fresh in-memory SQLite database for each test."""
    db_path = os.path.join(tempfile.gettempdir(), f"test_bot_{os.getpid()}.db")
    monkeypatch.setattr(database, "_engine", None)
    monkeypatch.setattr(database, "_SessionLocal", None)
    # Override config to use temp db
    from config import _config
    monkeypatch.setattr("config._config", None)
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_path}")
    database.init_db()
    yield
    # Clean up
    try:
        os.unlink(db_path)
    except OSError:
        pass


# ── Product caching ──────────────────────────────────────────────────


class TestCacheProduct:
    def test_insert_new_product(self):
        entry = database.cache_product(
            barcode="0078742075581",
            product_name="Protein Bar",
            brand="Quest",
            calories=190.0,
            protein=21.0,
            fat=8.0,
            carbs=21.0,
        )
        assert entry.id is not None
        assert entry.product_name == "Protein Bar"
        assert entry.brand == "Quest"
        assert entry.calories == 190.0

    def test_update_existing_product(self):
        """Second cache call with same barcode updates the record."""
        database.cache_product(
            barcode="0078742075581",
            product_name="Old Name",
            calories=100.0,
        )
        entry = database.cache_product(
            barcode="0078742075581",
            product_name="Updated Name",
            calories=200.0,
            protein=15.0,
        )
        assert entry.product_name == "Updated Name"
        assert entry.calories == 200.0
        assert entry.protein == 15.0

    def test_cache_by_query(self):
        database.cache_product(
            query="banana",
            product_name="Banana",
            food_id="123",
            calories=105.0,
        )
        entry = database.get_cached_by_query("banana")
        assert entry is not None
        assert entry.product_name == "Banana"

    def test_cache_by_food_id(self):
        database.cache_product(
            food_id="abc123",
            product_name="Test Food",
        )
        entry = database.get_cached_by_food_id("abc123")
        assert entry is not None
        assert entry.product_name == "Test Food"

    def test_case_insensitive_query_lookup(self):
        database.cache_product(
            query="Chicken Breast",
            product_name="Chicken Breast",
        )
        entry = database.get_cached_by_query("chicken breast")
        assert entry is not None

    def test_no_cache_hit(self):
        entry = database.get_cached_by_query("nonexistent_xyz")
        assert entry is None


# ── Meal logging ─────────────────────────────────────────────────────


class TestMealLog:
    def test_log_meal_basic(self):
        entry = database.log_meal(
            user_id=12345,
            product_name="Banana",
            quantity=1.0,
            unit="piece",
            calories=105.0,
            protein=1.3,
            fat=0.4,
            carbs=27.0,
        )
        assert entry.id is not None
        assert entry.user_id == 12345
        assert entry.product_name == "Banana"
        assert entry.quantity == 1.0
        assert entry.unit == "piece"
        assert entry.calories == 105.0

    def test_log_meal_scales_nutrition(self):
        """Nutrition values should be scaled by quantity."""
        entry = database.log_meal(
            user_id=1,
            product_name="Rice",
            quantity=2.5,
            unit="cup",
            calories=200.0,
            protein=4.0,
        )
        assert entry.calories == 500.0  # 200 * 2.5
        assert entry.protein == 10.0   # 4 * 2.5

    def test_get_today_logs(self):
        database.log_meal(user_id=1, product_name="Breakfast", calories=300.0, quantity=1.0)
        database.log_meal(user_id=1, product_name="Lunch", calories=500.0, quantity=1.0)
        database.log_meal(user_id=2, product_name="Other", calories=100.0, quantity=1.0)

        logs = database.get_today_logs(user_id=1)
        assert len(logs) == 2
        assert logs[0].product_name in ("Breakfast", "Lunch")
        assert logs[1].product_name in ("Breakfast", "Lunch")

    def test_get_today_logs_empty(self):
        logs = database.get_today_logs(user_id=99999)
        assert logs == []

    def test_get_log_entry(self):
        entry = database.log_meal(user_id=1, product_name="Test", calories=100.0, quantity=1.0)
        fetched = database.get_log_entry(entry.id)
        assert fetched is not None
        assert fetched.product_name == "Test"

    def test_get_log_entry_not_found(self):
        assert database.get_log_entry(99999) is None

    def test_update_log_entry_quantity(self):
        entry = database.log_meal(user_id=1, product_name="Apple", quantity=1.0, calories=95.0)
        updated = database.update_log_entry(entry.id, quantity=2.0)
        assert updated.quantity == 2.0
        assert updated.calories == 190.0  # scaled: 95 * 2.0

    def test_update_log_entry_unit(self):
        entry = database.log_meal(user_id=1, product_name="Milk", quantity=1.0, unit="cup")
        updated = database.update_log_entry(entry.id, unit="ml")
        assert updated.unit == "ml"

    def test_update_log_entry_not_found(self):
        assert database.update_log_entry(99999, quantity=2.0) is None

    def test_delete_log_entry(self):
        entry = database.log_meal(user_id=1, product_name="ToDelete", calories=50.0, quantity=1.0)
        assert database.delete_log_entry(entry.id) is True
        assert database.get_log_entry(entry.id) is None

    def test_delete_log_entry_not_found(self):
        assert database.delete_log_entry(99999) is False

    def test_daily_totals(self):
        database.log_meal(user_id=1, product_name="A", calories=200.0, protein=10.0, fat=5.0, carbs=30.0, quantity=1.0)
        database.log_meal(user_id=1, product_name="B", calories=300.0, protein=15.0, fat=10.0, carbs=40.0, quantity=1.0)

        totals = database.get_daily_totals(user_id=1)
        assert totals["calories"] == 500.0
        assert totals["protein"] == 25.0
        assert totals["fat"] == 15.0
        assert totals["carbs"] == 70.0
        assert totals["entries"] == 2

    def test_daily_totals_empty(self):
        totals = database.get_daily_totals(user_id=99999)
        assert totals["calories"] == 0
        assert totals["entries"] == 0
