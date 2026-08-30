"""Explicit policy for public data sources and their allowed transports."""
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse


class SourceTransport(StrEnum):
    HTML = "html"
    FREE_API = "free_api"


@dataclass(frozen=True)
class SourceProvider:
    name: str
    official_hosts: frozenset[str]
    api_hosts: frozenset[str] = frozenset()
    api_is_free: bool = False


PROVIDERS: dict[str, SourceProvider] = {
    "egrul": SourceProvider("egrul", frozenset({"egrul.nalog.ru"})),
    # The direct FSSP endpoint is disabled until an operator explicitly
    # confirms that its account and quota are free for this deployment.
    "fssp": SourceProvider(
        "fssp",
        frozenset({"fssp.gov.ru"}),
        frozenset({"api-ip.fssprus.ru"}),
        api_is_free=True,
    ),
    # KAD exposes an internal JSON endpoint, not a documented free API.  It
    # therefore remains on the public HTML/CloakBrowser path.
    "kad": SourceProvider("kad", frozenset({"kad.arbitr.ru"})),
}


def provider_api_enabled(name: str, configured_sources: set[str], endpoint: str) -> bool:
    """Allow direct API use only for a declared free provider and exact host."""
    provider = PROVIDERS[name]
    host = (urlparse(endpoint).hostname or "").lower()
    return (
        name in configured_sources
        and provider.api_is_free
        and host in provider.api_hosts
    )
