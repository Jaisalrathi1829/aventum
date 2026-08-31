"""Day 5 HTTP API. The only surface the browser is permitted to reach."""

from .app import API_VERSION, app

__all__ = ["app", "API_VERSION"]
