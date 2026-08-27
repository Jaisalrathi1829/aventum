"""Engine/session construction and small DB helpers."""

from __future__ import annotations

from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session, sessionmaker


def build_engine(database_url: str, echo: bool = False) -> Engine:
    """
    Build an Engine sized for a bulk load.

    future=True keeps SQLAlchemy 2.x semantics explicit; pool_pre_ping avoids handing
    out a connection the container recycled between runs.
    """
    return create_engine(
        database_url,
        echo=echo,
        future=True,
        pool_pre_ping=True,
    )


def build_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, future=True)


@contextmanager
def session_scope(session_factory: sessionmaker[Session]) -> Iterator[Session]:
    """Transactional scope: commit on success, roll back on any exception."""
    session = session_factory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def database_is_reachable(engine: Engine) -> bool:
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


def table_exists(engine: Engine, table_name: str) -> bool:
    with engine.connect() as connection:
        result = connection.execute(
            text("SELECT to_regclass(:name) IS NOT NULL"), {"name": f"public.{table_name}"}
        )
        return bool(result.scalar())
