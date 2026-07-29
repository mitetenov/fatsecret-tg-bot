"""Tests for the SQLite caching layer (db.py)."""

import json
import os
import sqlite3
import tempfile
import time

import pytest

from db import CacheDB


class TestCacheDB:
    """SQLite caching for product lookups and search results."""

    def test_init_creates_database_file(self):
        """CacheDB() creates the database file on disk."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            assert os.path.exists(path)
            db.close()
        finally:
            os.unlink(path)

    def test_init_creates_tables(self):
        """CacheDB() creates the products and searches tables."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            conn = sqlite3.connect(path)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            conn.close()
            table_names = [t[0] for t in tables]
            assert "products" in table_names
            assert "searches" in table_names
            db.close()
        finally:
            os.unlink(path)

    def test_set_and_get_product(self):
        """set_product() stores and get_product() retrieves by barcode or food_id."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)

            data = {
                "food_id": "12345",
                "food_name": "Test Apple",
                "brand_name": "Test Farms",
                "servings": {"serving": []},
            }

            # Cache by barcode
            db.set_product("5901234123457", data)
            result = db.get_product("5901234123457")
            assert result is not None
            assert result["food_id"] == "12345"
            assert result["food_name"] == "Test Apple"

            # Cache by food_id
            db.set_product("food:12345", data)
            result2 = db.get_product("food:12345")
            assert result2 is not None
            assert result2["brand_name"] == "Test Farms"

            db.close()
        finally:
            os.unlink(path)

    def test_get_product_miss_returns_none(self):
        """get_product() returns None for uncached lookups."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            assert db.get_product("nonexistent") is None
            db.close()
        finally:
            os.unlink(path)

    def test_set_and_get_search(self):
        """set_search() stores and get_search() retrieves with TTL validation."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path, search_ttl=60)

            results = [
                {"food_id": "1", "food_name": "Apple"},
                {"food_id": "2", "food_name": "Banana"},
            ]

            db.set_search("apple", results)
            cached = db.get_search("apple")
            assert cached is not None
            assert len(cached) == 2
            assert cached[0]["food_name"] == "Apple"

            db.close()
        finally:
            os.unlink(path)

    def test_get_search_miss_returns_none(self):
        """get_search() returns None for uncached queries."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            assert db.get_search("no such query") is None
            db.close()
        finally:
            os.unlink(path)

    def test_search_cache_expires_after_ttl(self):
        """get_search() returns None when TTL has elapsed."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            # TTL of 0 seconds — already expired on insert
            db = CacheDB(path, search_ttl=0)
            db.set_search("pizza", [{"food_id": "3"}])
            assert db.get_search("pizza") is None
            db.close()
        finally:
            os.unlink(path)

    def test_set_product_overwrites_existing(self):
        """set_product() overwrites an existing cached entry."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)

            db.set_product("5901234123457", {"food_id": "1", "food_name": "First"})
            db.set_product("5901234123457", {"food_id": "2", "food_name": "Second"})

            result = db.get_product("5901234123457")
            assert result["food_name"] == "Second"
            db.close()
        finally:
            os.unlink(path)

    # -- food_log tests -------------------------------------------------------

    def test_init_creates_food_log_table(self):
        """CacheDB() creates the food_log table."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            conn = sqlite3.connect(path)
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
            conn.close()
            table_names = [t[0] for t in tables]
            assert "food_log" in table_names
            db.close()
        finally:
            os.unlink(path)

    def test_log_food_returns_row_id(self):
        """log_food() inserts a row and returns its id."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            lid = db.log_food(
                user_id=123,
                food_id="456",
                food_name="Test Apple",
                serving_description="100g",
                amount_raw="150g",
                amount_grams=150.0,
                servings_multiplier=1.5,
                calories=80.0,
                protein=2.0,
                fat=1.0,
                carbs=18.0,
            )
            assert lid == 1
            db.close()
        finally:
            os.unlink(path)

    def test_get_user_log_returns_entries(self):
        """get_user_log() returns recent entries for a user, newest first."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            db.log_food(user_id=1, food_id="a", food_name="A", calories=100)
            time.sleep(0.01)
            db.log_food(user_id=1, food_id="b", food_name="B", calories=200)
            time.sleep(0.01)
            db.log_food(user_id=2, food_id="c", food_name="C", calories=300)

            entries = db.get_user_log(1)
            assert len(entries) == 2
            assert entries[0]["food_name"] == "B"  # newest first
            assert entries[1]["food_name"] == "A"
            db.close()
        finally:
            os.unlink(path)

    def test_get_daily_totals_sums_correctly(self):
        """get_daily_totals() returns summed KBJU for today."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            db.log_food(
                user_id=1, food_id="x", food_name="X",
                calories=100, protein=10, fat=5, carbs=20,
            )
            db.log_food(
                user_id=1, food_id="y", food_name="Y",
                calories=200, protein=20, fat=10, carbs=40,
            )

            totals = db.get_daily_totals(1)
            assert totals["calories"] == 300.0
            assert totals["protein"] == 30.0
            assert totals["fat"] == 15.0
            assert totals["carbs"] == 60.0
            db.close()
        finally:
            os.unlink(path)

    def test_get_daily_totals_empty_returns_zeros(self):
        """get_daily_totals() for a user with no entries returns zeros."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
            path = f.name
        try:
            db = CacheDB(path)
            totals = db.get_daily_totals(999)
            assert totals == {"calories": 0.0, "protein": 0.0, "fat": 0.0, "carbs": 0.0}
            db.close()
        finally:
            os.unlink(path)
