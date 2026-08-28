"""Optional CloakBrowser transport for source pages behind a challenge.

The integration uses the standard Chrome DevTools Protocol endpoint exposed by
a running CloakBrowser profile. It does not solve CAPTCHAs automatically: a
user or an explicitly configured browser profile must complete the challenge.
"""
from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse


class CloakBrowserError(RuntimeError):
    """The browser transport is unavailable or the challenge remains."""


CHALLENGE_MARKERS = (
    "captcha",
    "cloudflare",
    "verify you are human",
    "проверка безопасности",
    "проверка что вы не робот",
)


def _host_is_allowed(url: str, allowed_hosts: Iterable[str]) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and host in {item.lower() for item in allowed_hosts}


async def fetch_html_via_cloakbrowser(
    url: str,
    *,
    cdp_url: str,
    timeout_seconds: int = 90,
    wait_seconds: int = 8,
    allowed_hosts: Iterable[str] = (),
) -> str:
    """Load a page through a running CloakBrowser CDP endpoint.

    ``playwright`` is intentionally optional. Production can install it only
    on the worker that has access to CloakBrowser; regular API deployments do
    not download a browser runtime.
    """
    if not cdp_url:
        raise CloakBrowserError("CLOAKBROWSER_CDP_URL is not configured")
    if allowed_hosts and not _host_is_allowed(url, allowed_hosts):
        raise CloakBrowserError("CloakBrowser URL host is not allowlisted")

    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CloakBrowserError(
            "CloakBrowser fallback requires the optional playwright package"
        ) from exc

    browser = None
    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                if browser.contexts:
                    context = browser.contexts[0]
                else:
                    context = await browser.new_context()
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=max(1, timeout_seconds) * 1000,
                )
                if wait_seconds > 0:
                    await page.wait_for_timeout(min(wait_seconds, timeout_seconds) * 1000)
                body_text = ""
                try:
                    body_text = await page.locator("body").inner_text(timeout=5000)
                except Exception:
                    pass
                lowered = body_text[:5000].lower()
                if any(marker in lowered for marker in CHALLENGE_MARKERS):
                    raise CloakBrowserError("browser challenge is still visible")
                return await page.content()
            except CloakBrowserError:
                raise
            except Exception as exc:
                raise CloakBrowserError(
                    f"CloakBrowser navigation failed: {type(exc).__name__}"
                ) from exc
            finally:
                if browser is not None:
                    await browser.close()
    except CloakBrowserError:
        raise
    except Exception as exc:
        raise CloakBrowserError(
            f"CloakBrowser connection failed: {type(exc).__name__}"
        ) from exc


async def fetch_bytes_via_cloakbrowser(
    url: str,
    *,
    cdp_url: str,
    timeout_seconds: int = 90,
    allowed_hosts: Iterable[str] = (),
    max_bytes: int | None = None,
) -> bytes:
    """Download a document through the browser context after a challenge."""
    if not cdp_url:
        raise CloakBrowserError("CLOAKBROWSER_CDP_URL is not configured")
    if allowed_hosts and not _host_is_allowed(url, allowed_hosts):
        raise CloakBrowserError("CloakBrowser URL host is not allowlisted")

    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CloakBrowserError(
            "CloakBrowser fallback requires the optional playwright package"
        ) from exc

    browser = None
    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                response = await context.request.get(
                    url,
                    timeout=max(1, timeout_seconds) * 1000,
                )
                if not response.ok:
                    raise CloakBrowserError(
                        f"browser document status={response.status}"
                    )
                data = await response.body()
                if max_bytes is not None and len(data) > max_bytes:
                    raise CloakBrowserError("browser document exceeds configured size limit")
                return data
            except CloakBrowserError:
                raise
            except Exception as exc:
                raise CloakBrowserError(
                    f"CloakBrowser document download failed: {type(exc).__name__}"
                ) from exc
            finally:
                if browser is not None:
                    await browser.close()
    except CloakBrowserError:
        raise
    except Exception as exc:
        raise CloakBrowserError(
            f"CloakBrowser connection failed: {type(exc).__name__}"
        ) from exc
