"""
API configuration. Environment-driven, with local-development defaults only.

Reuses `aventum_ingest.config` for the database URL rather than introducing a second
way to find the database -- one connection string, one place it is decided.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from aventum_ingest.config import DEFAULT_DATABASE_URL

# The Vite dev server's default origin, plus the preview server. Deliberately a closed
# list rather than "*": the browser is the untrusted edge of this system and CORS is one
# of the few places the backend gets to say who may talk to it.
DEFAULT_CORS_ORIGINS = (
    "http://localhost:5173",
    "http://127.0.0.1:5173",
    "http://localhost:4173",
    "http://127.0.0.1:4173",
)


@dataclass(frozen=True)
class ApiConfig:
    database_url: str
    cors_origins: tuple[str, ...] = field(default=DEFAULT_CORS_ORIGINS)
    # Demo reset rebuilds workflow state. It is enabled by default for local demo use
    # and can be switched off wherever that would be inappropriate.
    demo_reset_enabled: bool = True
    agent_enabled: bool = True


def load_api_config() -> ApiConfig:
    origins = os.getenv("AVENTUM_CORS_ORIGINS")
    return ApiConfig(
        database_url=os.getenv("AVENTUM_DATABASE_URL", DEFAULT_DATABASE_URL),
        cors_origins=(
            tuple(o.strip() for o in origins.split(",") if o.strip())
            if origins
            else DEFAULT_CORS_ORIGINS
        ),
        demo_reset_enabled=os.getenv("AVENTUM_DEMO_RESET", "1") not in ("0", "false", "False"),
        agent_enabled=os.getenv("AVENTUM_AGENT_ENABLED", "1") not in ("0", "false", "False"),
    )
