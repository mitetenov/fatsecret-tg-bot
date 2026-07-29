"""Database layer — SQLite with optional PostgreSQL support.

Provides:
- Product caching (avoids repeated FatSecret API calls)
- Meal log CRUD (create, read, update, delete)
"""

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Float,
    Integer,
    String,
    Text,
    create_engine,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from config import get_config

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    pass


# ── Models ──────────────────────────────────────────────────────────


class CachedProduct(Base):
    """FatSecret product lookup cache."""

    __tablename__ = "cached_products"

    id = Column(Integer, primary_key=True, autoincrement=True)
    barcode = Column(String(64), unique=True, nullable=True, index=True)
    query = Column(String(255), nullable=True, index=True)
    food_id = Column(String(64), nullable=True, index=True)
    product_name = Column(String(255), nullable=False)
    brand = Column(String(128), nullable=True)
    serving_size = Column(String(64), nullable=True)
    serving_description = Column(String(255), nullable=True)
    calories = Column(Float, nullable=True)
    fat = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    protein = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())


class MealLog(Base):
    """User meal log entries."""

    __tablename__ = "meal_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    brand = Column(String(128), nullable=True)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(32), nullable=True, default="serving")
    calories = Column(Float, nullable=True)
    fat = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    protein = Column(Float, nullable=True)
    logged_at = Column(DateTime, server_default=func.now())


# ── Engine & session factory ────────────────────────────────────────

_engine = None
_SessionLocal: sessionmaker | None = None


def get_engine():
    global _engine
    if _engine is None:
        cfg = get_config()
        db_url = cfg.database_url
        if db_url.startswith("sqlite"):
            # Ensure the data directory exists
            if "///" in db_url:
                db_path = db_url.split("///", 1)[1]
            else:
                db_path = db_url.split(":///", 1)[1]
            Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        _engine = create_engine(db_url, echo=cfg.debug)
    return _engine


def get_session() -> Session:
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), expire_on_commit=False)
    return _SessionLocal()


def init_db():
    """Create all tables."""
    logger.info("Initialising database schema ...")
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database schema ready.")


# ── Product cache operations ────────────────────────────────────────


def cache_product(
    *,
    barcode: Optional[str] = None,
    query: Optional[str] = None,
    food_id: Optional[str] = None,
    product_name: str,
    brand: Optional[str] = None,
    serving_size: Optional[str] = None,
    serving_description: Optional[str] = None,
    calories: Optional[float] = None,
    fat: Optional[float] = None,
    carbs: Optional[float] = None,
    protein: Optional[float] = None,
    raw_json: Optional[str] = None,
) -> CachedProduct:
    """Insert or update a cached product entry."""
    session = get_session()
    try:
        if barcode:
            existing = (
                session.query(CachedProduct)
                .filter_by(barcode=barcode)
                .first()
            )
            if existing:
                # Update existing
                for k, v in dict(
                    query=query,
                    food_id=food_id,
                    product_name=product_name,
                    brand=brand,
                    serving_size=serving_size,
                    serving_description=serving_description,
                    calories=calories,
                    fat=fat,
                    carbs=carbs,
                    protein=protein,
                    raw_json=raw_json,
                ).items():
                    if v is not None:
                        setattr(existing, k, v)
                session.commit()
                return existing

        entry = CachedProduct(
            barcode=barcode,
            query=query,
            food_id=food_id,
            product_name=product_name,
            brand=brand,
            serving_size=serving_size,
            serving_description=serving_description,
            calories=calories,
            fat=fat,
            carbs=carbs,
            protein=protein,
            raw_json=raw_json,
        )
        session.add(entry)
        session.commit()
        return entry
    finally:
        session.close()


def get_cached_by_barcode(barcode: str) -> Optional[CachedProduct]:
    """Look up a cached product by barcode."""
    session = get_session()
    try:
        return session.query(CachedProduct).filter_by(barcode=barcode).first()
    finally:
        session.close()


def get_cached_by_query(query: str) -> Optional[CachedProduct]:
    """Look up a cached product by search query (case-insensitive)."""
    session = get_session()
    try:
        return (
            session.query(CachedProduct)
            .filter(CachedProduct.query.ilike(query))
            .first()
        )
    finally:
        session.close()


def get_cached_by_food_id(food_id: str) -> Optional[CachedProduct]:
    """Look up a cached product by FatSecret food_id."""
    session = get_session()
    try:
        return session.query(CachedProduct).filter_by(food_id=food_id).first()
    finally:
        session.close()


# ── Meal log operations ─────────────────────────────────────────────


def log_meal(
    *,
    user_id: int,
    product_name: str,
    brand: Optional[str] = None,
    quantity: float = 1.0,
    unit: str = "serving",
    calories: Optional[float] = None,
    fat: Optional[float] = None,
    carbs: Optional[float] = None,
    protein: Optional[float] = None,
) -> MealLog:
    """Insert a meal log entry. Returns the new MealLog row."""
    session = get_session()
    try:
        entry = MealLog(
            user_id=user_id,
            product_name=product_name,
            brand=brand,
            quantity=quantity,
            unit=unit,
            calories=(
                round(calories * quantity, 1) if calories is not None else None
            ),
            fat=round(fat * quantity, 1) if fat is not None else None,
            carbs=round(carbs * quantity, 1) if carbs is not None else None,
            protein=round(protein * quantity, 1) if protein is not None else None,
        )
        session.add(entry)
        session.commit()
        return entry
    finally:
        session.close()


def get_today_logs(user_id: int) -> list[MealLog]:
    """Return today's meal log entries for *user_id*, newest first."""
    session = get_session()
    try:
        today = datetime.now(timezone.utc).date()
        return (
            session.query(MealLog)
            .filter(
                MealLog.user_id == user_id,
                func.date(MealLog.logged_at) == today,
            )
            .order_by(MealLog.logged_at.desc())
            .all()
        )
    finally:
        session.close()


def get_log_entry(entry_id: int) -> Optional[MealLog]:
    """Return a single log entry by its id."""
    session = get_session()
    try:
        return session.query(MealLog).filter_by(id=entry_id).first()
    finally:
        session.close()


def update_log_entry(
    entry_id: int,
    *,
    quantity: Optional[float] = None,
    unit: Optional[str] = None,
    calories: Optional[float] = None,
    fat: Optional[float] = None,
    carbs: Optional[float] = None,
    protein: Optional[float] = None,
) -> Optional[MealLog]:
    """Update a meal log entry's quantity/unit/nutrition.

    If *quantity* changes and per-serving nutrition is not provided,
    existing values are scaled from the old quantity.
    """
    session = get_session()
    try:
        entry = session.query(MealLog).filter_by(id=entry_id).first()
        if entry is None:
            return None

        old_quantity = entry.quantity or 1.0
        new_quantity = quantity if quantity is not None else old_quantity

        if quantity is not None:
            entry.quantity = quantity
        if unit is not None:
            entry.unit = unit

        # Recalculate nutrition: use explicit per-serving if provided,
        # otherwise scale existing totals by the quantity ratio
        ratio = new_quantity / old_quantity if old_quantity > 0 else 1.0

        if calories is not None:
            entry.calories = round(calories * new_quantity, 1)
        elif quantity is not None and entry.calories is not None:
            entry.calories = round(entry.calories * ratio, 1)

        if fat is not None:
            entry.fat = round(fat * new_quantity, 1)
        elif quantity is not None and entry.fat is not None:
            entry.fat = round(entry.fat * ratio, 1)

        if carbs is not None:
            entry.carbs = round(carbs * new_quantity, 1)
        elif quantity is not None and entry.carbs is not None:
            entry.carbs = round(entry.carbs * ratio, 1)

        if protein is not None:
            entry.protein = round(protein * new_quantity, 1)
        elif quantity is not None and entry.protein is not None:
            entry.protein = round(entry.protein * ratio, 1)

        session.commit()
        return entry
    finally:
        session.close()


def delete_log_entry(entry_id: int) -> bool:
    """Delete a meal log entry. Returns True if deleted."""
    session = get_session()
    try:
        entry = session.query(MealLog).filter_by(id=entry_id).first()
        if entry is None:
            return False
        session.delete(entry)
        session.commit()
        return True
    finally:
        session.close()


def get_daily_totals(user_id: int) -> dict:
    """Return macro totals for today."""
    logs = get_today_logs(user_id)
    return {
        "calories": sum(l.calories or 0 for l in logs),
        "fat": sum(l.fat or 0 for l in logs),
        "carbs": sum(l.carbs or 0 for l in logs),
        "protein": sum(l.protein or 0 for l in logs),
        "entries": len(logs),
    }
