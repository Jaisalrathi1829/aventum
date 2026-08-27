"""Environment-driven configuration. No secrets are hard-coded outside local dev defaults."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from .constants import SOURCE_RELATIVE_PATH

# backend/aventum_ingest/config.py -> backend/ -> aventum/
BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent

# Load backend/.env if present. Real environment variables always win over the file.
load_dotenv(BACKEND_DIR / ".env", override=False)

# Local-development default, matching backend/docker-compose.yml. Overridden by
# AVENTUM_DATABASE_URL in any non-local environment.
DEFAULT_DATABASE_URL = (
    "postgresql+psycopg://aventum:aventum_local_dev@localhost:5433/aventum"
)


@dataclass(frozen=True)
class Config:
    database_url: str
    source_path: Path
    project_root: Path

    @property
    def source_display_path(self) -> str:
        """Repo-relative POSIX path, stable across machines for audit records."""
        try:
            return self.source_path.resolve().relative_to(self.project_root).as_posix()
        except ValueError:
            return self.source_path.resolve().as_posix()


def load_config(source_path: str | Path | None = None) -> Config:
    """Build config from the environment, with an optional source-path override."""
    database_url = os.getenv("AVENTUM_DATABASE_URL", DEFAULT_DATABASE_URL)

    if source_path is not None:
        resolved_source = Path(source_path)
    else:
        env_source = os.getenv("AVENTUM_SOURCE_PATH")
        resolved_source = (
            Path(env_source) if env_source else PROJECT_ROOT / SOURCE_RELATIVE_PATH
        )

    return Config(
        database_url=database_url,
        source_path=resolved_source,
        project_root=PROJECT_ROOT,
    )
