"""
Engine, session lifecycle, and the error vocabulary the API speaks.

One engine per process, one session per request, committed on success and rolled back on
any exception. A half-applied approval or a partially written action is exactly the kind
of state the rest of this system spent four days making impossible, and it would be
careless to reintroduce it at the HTTP boundary.
"""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import load_api_config

_config = load_api_config()

# pool_pre_ping: the demo runs against a container that may be restarted underneath a
# long-lived process. A stale pooled connection should be replaced, not surfaced to the
# operator as an incident in their own product.
_engine = create_engine(_config.database_url, pool_pre_ping=True, future=True)
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def get_config():
    return _config


def get_engine():
    return _engine


def get_session() -> Iterator[Session]:
    """One transaction per request. Commit on success, roll back on anything else."""
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class ApiError(HTTPException):
    """
    An error the browser is allowed to see.

    Carries a stable machine-readable `code` alongside the human sentence, so the
    frontend can branch on the condition without string-matching prose. Nothing from a
    stack trace, a SQL statement, or a connection string ever reaches this class -- §29
    forbids it, and the global handler in `app.py` enforces it for everything else.
    """

    def __init__(self, status_code: int, code: str, message: str, detail: dict | None = None):
        super().__init__(status_code=status_code, detail={
            "code": code,
            "message": message,
            **({"detail": detail} if detail else {}),
        })
        self.code = code


def not_found(what: str, ident) -> ApiError:
    return ApiError(404, "NOT_FOUND", f"No {what} {ident}.")


def conflict(code: str, message: str, detail: dict | None = None) -> ApiError:
    return ApiError(409, code, message, detail)


def bad_request(code: str, message: str, detail: dict | None = None) -> ApiError:
    return ApiError(400, code, message, detail)
