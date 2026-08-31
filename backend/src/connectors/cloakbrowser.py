"""Optional CloakBrowser transport for source pages behind a challenge.

The integration uses the standard Chrome DevTools Protocol endpoint exposed by
a running CloakBrowser profile. It does not solve CAPTCHAs automatically: a
user or an explicitly configured browser profile must complete the challenge.
"""
from __future__ import annotations

import asyncio
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from threading import Lock
from urllib.parse import urlparse


class CloakBrowserError(RuntimeError):
    """The browser transport is unavailable or the challenge remains."""


class CloakBrowserHttpError(CloakBrowserError):
    """The browser reached a source but received an HTTP/error page."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class BrowserState(StrEnum):
    """Typed result states shared by source workers and diagnostics."""

    READY = "ready"
    CHALLENGE = "challenge"
    ROUTE_BLOCKED = "route_blocked"
    UNAVAILABLE = "unavailable"
    HTTP_ERROR = "http_error"
    NOT_FOUND = "not_found"


@dataclass(frozen=True)
class BrowserResult:
    """A bounded browser result; body is present only for a successful fetch."""

    state: BrowserState
    status_code: int | None = None
    url: str | None = None
    body: str = ""
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.state is BrowserState.READY


@dataclass
class BrowserMetrics:
    """Process-local counters for the browser transport."""

    requests: int = 0
    ready: int = 0
    challenge: int = 0
    route_blocked: int = 0
    unavailable: int = 0
    http_error: int = 0
    not_found: int = 0
    last_state: str | None = None
    last_source: str | None = None
    last_error: str | None = None

    def snapshot(self) -> dict[str, object]:
        return {
            "requests": self.requests,
            "states": {
                "ready": self.ready,
                "challenge": self.challenge,
                "route_blocked": self.route_blocked,
                "unavailable": self.unavailable,
                "http_error": self.http_error,
                "not_found": self.not_found,
            },
            "last_state": self.last_state,
            "last_source": self.last_source,
            "last_error": self.last_error,
        }


CHALLENGE_MARKERS = (
    "captcha",
    "cloudflare",
    "verify you are human",
    "проверка безопасности",
    "проверка что вы не робот",
)

ERROR_PAGE_MARKERS = (
    "страница не найдена",
    "page not found",
    "404 - not found",
    "404 not found",
)

_METRICS = BrowserMetrics()
_METRICS_LOCK = Lock()


def _record_metric(source: str, result: BrowserResult) -> None:
    with _METRICS_LOCK:
        _METRICS.requests += 1
        field = result.state.value
        if hasattr(_METRICS, field):
            setattr(_METRICS, field, int(getattr(_METRICS, field)) + 1)
        _METRICS.last_state = result.state.value
        _METRICS.last_source = source
        _METRICS.last_error = result.error


def browser_metrics_snapshot() -> dict[str, object]:
    """Return a JSON-serializable snapshot without exposing cookies or URLs."""
    with _METRICS_LOCK:
        return _METRICS.snapshot()


def reset_browser_metrics() -> None:
    """Reset counters for a test or an explicit operator diagnostic run."""
    with _METRICS_LOCK:
        _METRICS.requests = 0
        _METRICS.ready = 0
        _METRICS.challenge = 0
        _METRICS.route_blocked = 0
        _METRICS.unavailable = 0
        _METRICS.http_error = 0
        _METRICS.not_found = 0
        _METRICS.last_state = None
        _METRICS.last_source = None
        _METRICS.last_error = None


def _state_for_response(status_code: int | None, body: str = "") -> BrowserState:
    if status_code == 451:
        return BrowserState.ROUTE_BLOCKED
    if status_code in (401, 403, 429):
        return BrowserState.CHALLENGE
    if status_code == 404:
        return BrowserState.NOT_FOUND
    if isinstance(status_code, int) and status_code >= 400:
        return BrowserState.HTTP_ERROR
    lowered = body[:5000].lower()
    if any(marker in lowered for marker in CHALLENGE_MARKERS):
        return BrowserState.CHALLENGE
    return BrowserState.READY


class BrowserSessionBroker:
    """Serialize work per source and keep source pressure predictable.

    A broker does not launch browsers and does not solve challenges. It protects
    the already running persistent profile from concurrent pages and turns
    transport exceptions into typed results for the worker and diagnostics.
    """

    def __init__(self) -> None:
        self._locks: dict[str, asyncio.Lock] = {}
        self._last_request: dict[str, float] = {}

    def _lock_for(self, source: str) -> asyncio.Lock:
        lock = self._locks.get(source)
        if lock is None:
            lock = asyncio.Lock()
            self._locks[source] = lock
        return lock

    async def execute(
        self,
        source: str,
        operation: Callable[[], Awaitable[BrowserResult | str]],
        *,
        min_interval_seconds: float = 0.0,
    ) -> BrowserResult:
        """Run one operation with per-source serialization and a small delay."""
        async with self._lock_for(source):
            delay = max(0.0, min_interval_seconds)
            elapsed = time.monotonic() - self._last_request.get(source, 0.0)
            if elapsed < delay:
                await asyncio.sleep(delay - elapsed)
            self._last_request[source] = time.monotonic()
            try:
                value = await operation()
                result = (
                    value
                    if isinstance(value, BrowserResult)
                    else BrowserResult(BrowserState.READY, status_code=200, body=value)
                )
            except CloakBrowserHttpError as exc:
                status = _state_for_response(exc.status_code)
                result = BrowserResult(status, exc.status_code, exc.url, error=str(exc))
            except CloakBrowserError as exc:
                result = BrowserResult(BrowserState.UNAVAILABLE, error=str(exc))
            except Exception as exc:  # pragma: no cover - defensive boundary
                result = BrowserResult(
                    BrowserState.HTTP_ERROR,
                    error=f"{type(exc).__name__}: {exc}"[:500],
                )
            _record_metric(source, result)
            return result


_BROKER = BrowserSessionBroker()


def get_browser_session_broker() -> BrowserSessionBroker:
    return _BROKER


async def _body_text(page, *, timeout_ms: int = 5000) -> str:
    try:
        return await page.locator("body").inner_text(timeout=timeout_ms)
    except Exception:
        return ""


async def _safe_close(handle) -> None:
    """Close a Playwright handle without masking the source result."""
    try:
        await handle.close()
    except Exception:
        pass


async def _safe_close_page(page) -> None:
    """Stop an in-flight navigation before closing a short-lived page."""
    try:
        await page.goto("about:blank", wait_until="commit", timeout=2000)
    except Exception:
        pass
    await _safe_close(page)


async def _wait_for_challenge_clear(page, wait_seconds: int) -> str:
    """Wait for a human to complete a visible challenge in the headed profile."""
    deadline = time.monotonic() + max(0, wait_seconds)
    body = await _body_text(page)
    while any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
        if time.monotonic() >= deadline:
            break
        await asyncio.sleep(2)
        body = await _body_text(page)
    return body


def _host_is_allowed(url: str, allowed_hosts: Iterable[str]) -> bool:
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    allowed = {item.lower() for item in allowed_hosts}
    return parsed.scheme in {"http", "https"} and bool(host) and host in allowed


async def fetch_html_via_cloakbrowser(
    url: str,
    *,
    cdp_url: str,
    timeout_seconds: int = 90,
    wait_seconds: int = 8,
    manual_challenge_wait_seconds: int = 0,
    allowed_hosts: Iterable[str] = (),
) -> str:
    """Load a page through a running CloakBrowser CDP endpoint.

    ``playwright`` is intentionally optional. Production can install it only
    on the worker that has access to CloakBrowser; regular API deployments do
    not download a browser runtime.
    """
    allowed_hosts = tuple(allowed_hosts)
    if not cdp_url:
        raise CloakBrowserError("CLOAKBROWSER_CDP_URL is not configured")
    if allowed_hosts and not _host_is_allowed(url, allowed_hosts):
        raise CloakBrowserError("CloakBrowser URL host is not allowlisted")

    page = None
    browser = None
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CloakBrowserError(
            "CloakBrowser fallback requires the optional playwright package"
        ) from exc

    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                response = await page.goto(
                    url,
                    wait_until="domcontentloaded",
                    timeout=max(1, timeout_seconds) * 1000,
                )
                response_status = getattr(response, "status", None)
                response_url = getattr(response, "url", None) or getattr(page, "url", url)
                if (
                    isinstance(response_status, int)
                    and response_status >= 400
                    and not (
                        response_status in (401, 403, 429)
                        and manual_challenge_wait_seconds > 0
                    )
                ):
                    raise CloakBrowserHttpError(
                        f"browser navigation status={response_status}",
                        status_code=response_status,
                        url=response_url,
                    )
                final_url = getattr(page, "url", url)
                if allowed_hosts and not _host_is_allowed(final_url, allowed_hosts):
                    raise CloakBrowserError("CloakBrowser navigation left the allowlist")
                if wait_seconds > 0:
                    await page.wait_for_timeout(min(wait_seconds, timeout_seconds) * 1000)
                final_url = getattr(page, "url", url)
                if allowed_hosts and not _host_is_allowed(final_url, allowed_hosts):
                    raise CloakBrowserError("CloakBrowser navigation left the allowlist")
                body_text = await _body_text(page)
                if manual_challenge_wait_seconds > 0 and (
                    isinstance(response_status, int)
                    and response_status in (401, 403, 429)
                    or any(marker in body_text[:5000].lower() for marker in CHALLENGE_MARKERS)
                ):
                    body_text = await _wait_for_challenge_clear(
                        page, manual_challenge_wait_seconds
                    )
                lowered = body_text[:5000].lower()
                if any(marker in lowered for marker in ERROR_PAGE_MARKERS):
                    raise CloakBrowserHttpError(
                        "browser navigation returned an error page",
                        status_code=response_status if isinstance(response_status, int) else None,
                        url=final_url,
                    )
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
                if page is not None and hasattr(page, "close"):
                    await _safe_close_page(page)
                if browser is not None and hasattr(browser, "close"):
                    await _safe_close(browser)
    except CloakBrowserError:
        raise
    except Exception as exc:
        raise CloakBrowserError(
            f"CloakBrowser connection failed: {type(exc).__name__}"
        ) from exc


async def fetch_json_via_cloakbrowser(
    base_url: str,
    *,
    path: str,
    payload: dict[str, object],
    cdp_url: str,
    timeout_seconds: int = 90,
    manual_challenge_wait_seconds: int = 0,
    allowed_hosts: Iterable[str] = (),
) -> BrowserResult:
    """Run a same-origin JSON request from a persistent browser page.

    Several public registries expose their actual search contract only to the
    frontend. Keeping the request inside the page preserves the browser's
    cookies and origin without pretending that a protected endpoint is a free
    public API.
    """
    allowed_hosts = tuple(allowed_hosts)
    if not cdp_url:
        return BrowserResult(BrowserState.UNAVAILABLE, error="CLOAKBROWSER_CDP_URL is not configured")
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    if allowed_hosts and host not in {item.lower() for item in allowed_hosts}:
        return BrowserResult(BrowserState.UNAVAILABLE, error="CloakBrowser base URL host is not allowlisted")
    if not path.startswith("/") or "//" in path:
        return BrowserResult(BrowserState.UNAVAILABLE, error="browser request path is invalid")

    page = None
    browser = None
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        return BrowserResult(BrowserState.UNAVAILABLE, error="CloakBrowser fallback requires the optional playwright package")

    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                landing = await page.goto(
                    base_url,
                    wait_until="domcontentloaded",
                    timeout=max(1, timeout_seconds) * 1000,
                )
                landing_status = getattr(landing, "status", None)
                if isinstance(landing_status, int) and landing_status == 451:
                    return BrowserResult(
                        BrowserState.ROUTE_BLOCKED,
                        status_code=landing_status,
                        url=getattr(page, "url", base_url),
                        error="browser landing route is blocked",
                    )
                if (
                    isinstance(landing_status, int)
                    and landing_status >= 400
                    and landing_status not in (401, 403, 429)
                ):
                    return BrowserResult(
                        _state_for_response(landing_status),
                        status_code=landing_status,
                        url=getattr(page, "url", base_url),
                        error=f"browser landing status={landing_status}",
                    )

                async def request() -> BrowserResult:
                    raw = await page.evaluate(
                        """async ({path, payload}) => {
                          const response = await fetch(path, {
                            method: 'POST',
                            credentials: 'include',
                            headers: {
                              'Content-Type': 'application/json',
                              'X-Requested-With': 'XMLHttpRequest'
                            },
                            body: JSON.stringify(payload)
                          });
                          return {
                            status: response.status,
                            url: response.url,
                            text: await response.text()
                          };
                        }""",
                        {"path": path, "payload": payload},
                    )
                    status = raw.get("status") if isinstance(raw, dict) else None
                    text = raw.get("text", "") if isinstance(raw, dict) else ""
                    response_url = raw.get("url") if isinstance(raw, dict) else None
                    if not isinstance(text, str):
                        text = ""
                    if response_url and allowed_hosts and not _host_is_allowed(
                        response_url, allowed_hosts
                    ):
                        return BrowserResult(
                            BrowserState.UNAVAILABLE,
                            url=response_url,
                            error="CloakBrowser JSON response left the allowlist",
                        )
                    if not isinstance(status, int):
                        return BrowserResult(BrowserState.HTTP_ERROR, url=response_url, error="browser response status is invalid")
                    return BrowserResult(
                        _state_for_response(status, text),
                        status_code=status,
                        url=response_url,
                        body=text if 200 <= status < 300 else "",
                        error=None if 200 <= status < 300 else f"browser request status={status}",
                    )

                result = await request()
                if result.state is BrowserState.CHALLENGE and manual_challenge_wait_seconds > 0:
                    await _wait_for_challenge_clear(page, manual_challenge_wait_seconds)
                    result = await request()
                return result
            except Exception as exc:
                return BrowserResult(
                    BrowserState.HTTP_ERROR,
                    error=f"CloakBrowser JSON request failed: {type(exc).__name__}",
                )
            finally:
                if page is not None and hasattr(page, "close"):
                    await _safe_close_page(page)
                if browser is not None and hasattr(browser, "close"):
                    await _safe_close(browser)
    except Exception as exc:
        return BrowserResult(
            BrowserState.UNAVAILABLE,
            error=f"CloakBrowser connection failed: {type(exc).__name__}",
        )


async def fetch_fssp_via_cloakbrowser(
    inn: str,
    *,
    cdp_url: str,
    timeout_seconds: int = 90,
    manual_challenge_wait_seconds: int = 300,
) -> BrowserResult:
    """Submit the public FSSP form and wait for an operator CAPTCHA solve."""
    if not inn:
        return BrowserResult(BrowserState.UNAVAILABLE, error="FSSP INN is empty")
    if not cdp_url:
        return BrowserResult(BrowserState.UNAVAILABLE, error="CLOAKBROWSER_CDP_URL is not configured")

    page = None
    browser = None
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError:
        return BrowserResult(BrowserState.UNAVAILABLE, error="CloakBrowser fallback requires the optional playwright package")

    try:
        async with async_playwright() as playwright:
            try:
                browser = await playwright.chromium.connect_over_cdp(cdp_url)
                context = browser.contexts[0] if browser.contexts else await browser.new_context()
                page = await context.new_page()
                response = await page.goto(
                    "https://fssp.gov.ru/iss/ip/",
                    wait_until="domcontentloaded",
                    timeout=max(1, timeout_seconds) * 1000,
                )
                status = getattr(response, "status", None)
                if isinstance(status, int) and status >= 400:
                    return BrowserResult(
                        _state_for_response(status),
                        status_code=status,
                        url=getattr(page, "url", None),
                        error=f"FSSP form status={status}",
                    )
                if not _host_is_allowed(
                    getattr(page, "url", ""), {"fssp.gov.ru", "is-go.fssp.gov.ru"}
                ):
                    return BrowserResult(
                        BrowserState.UNAVAILABLE,
                        url=getattr(page, "url", None),
                        error="FSSP form navigation left the allowlist",
                    )

                selectors = (
                    "#input10",
                    "input[name='inn']",
                    "input[name*='INN' i]",
                    "input[placeholder*='ИНН' i]",
                )
                input_locator = None
                for selector in selectors:
                    candidate = page.locator(selector)
                    if await candidate.count() > 0:
                        input_locator = candidate.first
                        break
                if input_locator is None:
                    return BrowserResult(BrowserState.UNAVAILABLE, error="FSSP INN input selector was not found")
                await input_locator.fill(inn)

                button_locator = None
                for selector in ("#btn-sbm", "button[type='submit']", "input[type='submit']"):
                    candidate = page.locator(selector)
                    if await candidate.count() > 0:
                        button_locator = candidate.first
                        break
                if button_locator is None:
                    return BrowserResult(BrowserState.UNAVAILABLE, error="FSSP submit selector was not found")
                await button_locator.click(timeout=max(1, timeout_seconds) * 1000)
                await page.wait_for_timeout(1000)
                if not _host_is_allowed(
                    getattr(page, "url", ""), {"fssp.gov.ru", "is-go.fssp.gov.ru"}
                ):
                    return BrowserResult(
                        BrowserState.UNAVAILABLE,
                        url=getattr(page, "url", None),
                        error="FSSP result navigation left the allowlist",
                    )
                body = await _body_text(page)
                if any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
                    body = await _wait_for_challenge_clear(page, manual_challenge_wait_seconds)
                    if any(marker in body[:5000].lower() for marker in CHALLENGE_MARKERS):
                        return BrowserResult(
                            BrowserState.CHALLENGE,
                            status_code=200,
                            url=getattr(page, "url", None),
                            error="FSSP CAPTCHA is still visible",
                        )
                return BrowserResult(
                    _state_for_response(200, body),
                    status_code=200,
                    url=getattr(page, "url", None),
                    body=body,
                )
            except Exception as exc:
                return BrowserResult(
                    BrowserState.HTTP_ERROR,
                    error=f"FSSP browser workflow failed: {type(exc).__name__}",
                )
            finally:
                if page is not None and hasattr(page, "close"):
                    await _safe_close_page(page)
                if browser is not None and hasattr(browser, "close"):
                    await _safe_close(browser)
    except Exception as exc:
        return BrowserResult(
            BrowserState.UNAVAILABLE,
            error=f"CloakBrowser connection failed: {type(exc).__name__}",
        )


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

    context = None
    browser = None
    try:
        from playwright.async_api import async_playwright  # type: ignore[import-not-found]
    except ImportError as exc:
        raise CloakBrowserError(
            "CloakBrowser fallback requires the optional playwright package"
        ) from exc

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
                response_url = getattr(response, "url", url)
                if allowed_hosts and not _host_is_allowed(response_url, allowed_hosts):
                    raise CloakBrowserError("browser document redirect left the allowlist")
                content_length = None
                headers = getattr(response, "headers", {}) or {}
                for header_name, header_value in headers.items():
                    if header_name.lower() == "content-length":
                        try:
                            content_length = int(header_value)
                        except ValueError as exc:
                            raise CloakBrowserError("invalid browser document size") from exc
                        break
                if max_bytes is not None and content_length is not None and content_length > max_bytes:
                    raise CloakBrowserError("browser document exceeds configured size limit")
                if max_bytes is not None and content_length is None:
                    # Playwright's response.body() buffers the complete body,
                    # so there is no safe way to enforce a hard limit after a
                    # chunked response has already been received.
                    raise CloakBrowserError("browser document size is unknown")
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
                # ``context.request`` belongs to the persistent CDP browser
                # context.  Disposing it here breaks subsequent downloads that
                # reuse the same authenticated CloakBrowser profile.
                if browser is not None and hasattr(browser, "close"):
                    await _safe_close(browser)
    except CloakBrowserError:
        raise
    except Exception as exc:
        raise CloakBrowserError(
            f"CloakBrowser connection failed: {type(exc).__name__}"
        ) from exc
