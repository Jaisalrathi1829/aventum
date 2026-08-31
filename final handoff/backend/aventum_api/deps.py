"""
Engine, session lifecycle, and the error vocabulary the API speaks.

One engine per process, one session per request, committed on success and rolled back on
any exception. A half-applied approval or a partially written action is exactly the kind
of state the rest of this system spent four days making impossible, and it would be
careless to reintroduce it at the HTTP boundary.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator

from fastapi import HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from .config import load_api_config

log = logging.getLogger("aventum.api.deps")

_config = load_api_config()

# How long a request may spend trying to reach a database that is not answering.
#
# Without this the engine inherits libpq's default, which against a stopped container is
# effectively unbounded: measured, EVERY endpoint hung for over 120 seconds with
# PostgreSQL down -- including `/api/health`, whose entire job is to report that the
# database is down. It could not, because it blocked trying to ask it. The operator got
# thirty seconds of empty skeletons and was then told the API was unreachable, which was
# false: the API was fine and its dependency was not.
#
# Two seconds is libpq's FLOOR, not a preference: it silently promotes any smaller
# value, so writing 1 here would be a comment that lies. It applies per address, and
# psycopg tries IPv6 then IPv4, so a dead database costs ~4s to discover. That is why
# `/api/health` probes the database and the agent CONCURRENTLY rather than in series --
# see `_health_probes` in app.py.
DB_CONNECT_TIMEOUT_S = 2
# And how long to wait for a pooled connection when every one is busy. Unbounded here
# turns pool exhaustion into the same silent hang.
DB_POOL_TIMEOUT_S = 5

# pool_pre_ping: the demo runs against a container that may be restarted underneath a
# long-lived process. A stale pooled connection should be replaced, not surfaced to the
# operator as an incident in their own product.
_engine = create_engine(
    _config.database_url,
    pool_pre_ping=True,
    future=True,
    pool_timeout=DB_POOL_TIMEOUT_S,
    connect_args={"connect_timeout": DB_CONNECT_TIMEOUT_S},
)
_SessionFactory = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def get_config():
    return _config


def get_engine():
    return _engine


def get_session() -> Iterator[Session]:
    """
    One transaction per request. Commit on success, roll back on anything else.

    The rollback and close are themselves guarded, because they run against the same
    database that may be the thing which failed. When PostgreSQL was down, the handler
    raised, `rollback()` raised again while unwinding, and the second exception escaped
    during dependency cleanup -- after the response had begun. The browser saw the
    connection abort ("Failed to fetch") rather than the clean JSON error the API had
    already produced, so the UI reported the BACKEND as unreachable when the backend was
    fine and its database was not.

    Swallowing a cleanup failure is safe here in a way that swallowing a request failure
    would not be: there is no transaction left to protect. Either the work committed or
    it did not, and a connection that cannot roll back is one the pool will discard.
    """
    session = _SessionFactory()
    try:
        yield session
        session.commit()
    except Exception:
        try:
            session.rollback()
        except Exception:
            log.warning("rollback failed during request cleanup", exc_info=True)
        raise
    finally:
        try:
            session.close()
        except Exception:
            log.warning("session close failed during request cleanup", exc_info=True)


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
