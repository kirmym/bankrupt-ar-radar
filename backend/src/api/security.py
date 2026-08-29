"""Small server-side guard for private API operations."""
from __future__ import annotations

from secrets import compare_digest
from typing import Annotated

from fastapi import Header, HTTPException

from src.config import get_settings


def require_api_access(
    api_key: Annotated[str | None, Header(alias="X-API-Key")] = None,
) -> None:
    """Require the configured API key when the deployment enables one.

    Keeping the key server-side avoids putting a secret into the Vite bundle.
    Local development remains backward compatible while the setting is empty.
    """
    app_settings = get_settings()
    expected = app_settings.api_auth_token
    if getattr(app_settings, "app_env", "development").lower() in {"production", "prod"} and not expected:
        raise HTTPException(
            status_code=503,
            detail="API authentication is not configured",
        )
    if expected and (not api_key or not compare_digest(api_key, expected)):
        raise HTTPException(
            status_code=401,
            detail="API authentication required",
            headers={"WWW-Authenticate": "ApiKey"},
        )
