"""Database layer — SQLite with optional PostgreSQL support."""
import logging
from pathlib import Path

from sqlalchemy import Column, DateTime, Float, Integer, String, Text, create_engine
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
    product_name = Column(String(255), nullable=False)
    brand = Column(String(128), nullable=True)
    serving_size = Column(String(64), nullable=True)
    calories = Column(Float, nullable=True)
    fat = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    protein = Column(Float, nullable=True)
    raw_json = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=None, nullable=True)


class MealLog(Base):
    """User meal log entries."""
    __tablename__ = "meal_log"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, nullable=False, index=True)
    product_name = Column(String(255), nullable=False)
    quantity = Column(Float, nullable=False, default=1.0)
    unit = Column(String(32), nullable=True, default="serving")
    calories = Column(Float, nullable=True)
    fat = Column(Float, nullable=True)
    carbs = Column(Float, nullable=True)
    protein = Column(Float, nullable=True)
    logged_at = Column(DateTime, server_default=None, nullable=True)


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
        _SessionLocal = sessionmaker(bind=get_engine())
    return _SessionLocal()


def init_db():
    """Create all tables."""
    logger.info("Initialising database schema ...")
    from sqlalchemy import func

    # Re-import for server_default time functions
    engine = get_engine()
    Base.metadata.create_all(engine)
    logger.info("Database schema ready.")
