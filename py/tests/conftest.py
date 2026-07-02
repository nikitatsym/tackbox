"""Session-wide TACKBOX_CACHE_HOME redirect.

Every subprocess started by a test inherits `os.environ`, so pointing
TACKBOX_CACHE_HOME at a session tmp dir here guarantees no test can write
into the developer's real `~/.cache/tackbox`. Individual tests that want
per-test cache isolation override the env var in their subprocess env
(see test_cli_cache.py).
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile


def pytest_configure(config):
    if os.environ.get("TACKBOX_CACHE_HOME"):
        return
    session_cache = tempfile.mkdtemp(prefix="tackbox-test-cache-")
    os.environ["TACKBOX_CACHE_HOME"] = session_cache
    atexit.register(shutil.rmtree, session_cache, ignore_errors=True)
