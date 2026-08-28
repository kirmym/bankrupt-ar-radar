# AR Radar backend

FastAPI backend for the Bankrupt AR Radar project. The deployable package is
built from `backend/src`; the repository level README contains the product
overview and local development instructions.


## Optional CloakBrowser transport

Install `playwright` only in a worker that can reach a running CloakBrowser profile. Set `CLOAKBROWSER_CDP_URL` to that profile's Chrome DevTools Protocol endpoint. The HTTP transport remains primary; a browser retry is attempted only for `401`, `403`, or `429`. CAPTCHAs are completed manually in the profile.
