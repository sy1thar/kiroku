from collections.abc import Generator
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, Session, sessionmaker

from app.config import get_settings


class Base(DeclarativeBase):
    pass


def normalize_database_url(url: str) -> str:
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    if url.startswith("postgresql://") and "+psycopg" not in url.split("://", 1)[0]:
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    return url


def _create_engine():
    settings = get_settings()
    db_url = normalize_database_url(settings.database_url)
    connect_args = {}
    if db_url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    return create_engine(db_url, connect_args=connect_args, pool_pre_ping=True, future=True)


engine = _create_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, expire_on_commit=False)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    _ensure_user_columns()
    _ensure_media_columns()
    _ensure_text_columns()


def _ensure_text_columns() -> None:
    """Widen title/name VARCHAR columns to TEXT on PostgreSQL so long manga/LN titles never truncate."""
    if engine.dialect.name != "postgresql":
        return
    alters = [
        "ALTER TABLE media ALTER COLUMN canonical_title TYPE TEXT",
        "ALTER TABLE media ALTER COLUMN native_title TYPE TEXT",
        "ALTER TABLE media_translations ALTER COLUMN title TYPE TEXT",
        "ALTER TABLE characters ALTER COLUMN canonical_name TYPE TEXT",
        "ALTER TABLE characters ALTER COLUMN native_name TYPE TEXT",
        "ALTER TABLE character_translations ALTER COLUMN name TYPE TEXT",
    ]
    with engine.begin() as conn:
        for stmt in alters:
            try:
                conn.execute(text(stmt))
            except Exception:
                pass


def _ensure_media_columns() -> None:
    """Add new columns to existing media tables without requiring manual migrations."""
    inspector = inspect(engine)
    if "media" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("media")}
    if "characters_exhausted" not in existing_cols:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE media ADD COLUMN characters_exhausted BOOLEAN DEFAULT FALSE"))


def _ensure_user_columns() -> None:
    """Add profile columns to existing users tables without requiring manual migrations."""
    inspector = inspect(engine)
    if "users" not in inspector.get_table_names():
        return
    existing_cols = {col["name"] for col in inspector.get_columns("users")}
    alterations = {
        "is_moderator": "BOOLEAN DEFAULT FALSE",
        "avatar_url": "TEXT DEFAULT ''",
        "bio": "TEXT DEFAULT ''",
        "preferred_locale": "VARCHAR(10) DEFAULT 'ru'",
    }
    with engine.begin() as conn:
        for col_name, col_def in alterations.items():
            if col_name not in existing_cols:
                conn.execute(text(f"ALTER TABLE users ADD COLUMN {col_name} {col_def}"))
