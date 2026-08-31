# AR Radar backend

FastAPI backend for the Bankrupt AR Radar project. The deployable package is
built from `backend/src`; the repository level README contains the product
overview and local development instructions.


## Optional CloakBrowser transport

The Docker image installs the `playwright` client automatically. For local runs, install it in the worker environment that can reach a running CloakBrowser profile. Set `CLOAKBROWSER_CDP_URL` to that profile's Chrome DevTools Protocol endpoint. Set `CLOAKBROWSER_PROXY_URL` before launching the persistent profile when the browser itself must use a Russian route. The HTTP transport remains primary; a browser retry is attempted for `401`, `403`, `429` and explicit CAPTCHA/challenge pages with HTTP 200. `SOURCE_PROXY_URL` is an optional HTTP(S) proxy for public source clients only; Telegram and OpenAI are intentionally not routed through it. CAPTCHAs are completed manually in the profile, and the final browser URL is checked against the source allowlist after navigation settles.
