"""Run CloakBrowser with a persistent local profile and a private CDP endpoint."""
from __future__ import annotations

import os
import sys
import time
from pathlib import Path


try:
    from cloakbrowser import launch_persistent_context
except ModuleNotFoundError:
    legacy_site_packages = (
        Path.home()
        / "AppData/Local/Packages/PythonSoftwareFoundation.Python.3.11_qbz5n2kfra8p0"
        / "LocalCache/local-packages/Python311/site-packages"
    )
    if not legacy_site_packages.exists():
        raise
    sys.path.insert(0, str(legacy_site_packages))
    from cloakbrowser import launch_persistent_context


REPO_ROOT = Path(__file__).resolve().parents[1]
# The task layout keeps runtime/ next to repo/.  For a standalone clone the
# runtime directory may live inside the repository, so support both forms.
TASK_ROOT = (
    REPO_ROOT
    if (REPO_ROOT / "runtime").exists()
    else REPO_ROOT.parent
    if (REPO_ROOT.parent / "runtime").exists()
    else REPO_ROOT
)
PROFILE_DIR = Path(
    os.environ.get("CLOAKBROWSER_PROFILE_DIR", str(TASK_ROOT / "runtime" / "cloakbrowser-profile"))
)
CDP_PORT = os.environ.get("CLOAKBROWSER_CDP_PORT", "9222")


def main() -> None:
    PROFILE_DIR.mkdir(parents=True, exist_ok=True)
    context = launch_persistent_context(
        str(PROFILE_DIR),
        headless=False,
        args=[
            f"--remote-debugging-port={CDP_PORT}",
            "--remote-debugging-address=127.0.0.1",
        ],
    )
    if not context.pages:
        context.new_page()
    print(f"CloakBrowser CDP: http://127.0.0.1:{CDP_PORT}", flush=True)
    print(f"Profile: {PROFILE_DIR}", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        pass
    finally:
        context.close()


if __name__ == "__main__":
    main()
