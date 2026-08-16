from __future__ import annotations

import time
from dataclasses import dataclass

import requests
from django.conf import settings


class GraphConfigurationError(RuntimeError):
    pass


@dataclass
class CachedToken:
    access_token: str
    expires_at: float


_cached_token: CachedToken | None = None


def get_app_token() -> str:
    global _cached_token

    if _cached_token and _cached_token.expires_at > time.time() + 60:
        return _cached_token.access_token

    tenant_id = str(getattr(settings, "GRAPH_TENANT_ID", "")).strip()
    client_id = str(getattr(settings, "GRAPH_CLIENT_ID", "")).strip()
    client_secret = str(getattr(settings, "GRAPH_CLIENT_SECRET", "")).strip()
    if not tenant_id or not client_id or not client_secret:
        raise GraphConfigurationError(
            "GRAPH_TENANT_ID, GRAPH_CLIENT_ID, and GRAPH_CLIENT_SECRET are required."
        )

    response = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": "https://graph.microsoft.com/.default",
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    access_token = str(payload["access_token"])
    expires_in = int(payload.get("expires_in", 3600))
    _cached_token = CachedToken(access_token=access_token, expires_at=time.time() + expires_in)
    return access_token
