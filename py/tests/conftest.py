"""Session-wide TACKBOX_CACHE_HOME and XDG_DATA_HOME redirects.

Every subprocess started by a test inherits `os.environ`, so pointing these
at session tmp dirs here guarantees no test can write into the developer's
real `~/.cache/tackbox` or `~/.local/share/tackbox` (the engine store).
Individual tests that want tighter isolation override the env vars themselves
(see test_cli_cache.py, test_engines_store.py).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile


def _redirect(env_var: str, prefix: str) -> None:
    if os.environ.get(env_var):
        return
    tmp = tempfile.mkdtemp(prefix=prefix)
    os.environ[env_var] = tmp
    atexit.register(shutil.rmtree, tmp, ignore_errors=True)


def pytest_configure(config):
    _redirect("TACKBOX_CACHE_HOME", "tackbox-test-cache-")
    _redirect("XDG_DATA_HOME", "tackbox-test-xdg-")
