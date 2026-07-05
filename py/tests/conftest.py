"""Session-wide env hygiene: cache redirect + host-git isolation.

Every subprocess started by a test inherits `os.environ`, so pointing
TACKBOX_CACHE_HOME at a session tmp dir here guarantees no test can write
into the developer's real `~/.cache/tackbox`. Individual tests that want
per-test cache isolation override the env var in their subprocess env
(see test_cli_cache.py).

A git hook (pre-commit runs this suite) exports GIT_DIR/GIT_INDEX_FILE for
the host repo; inherited by nested git in tests, they redirect every git
call into the host repo instead of the test's tmp repo.
"""

from __future__ import annotations

import atexit
import os
import shutil
import tempfile

_HOST_GIT_ENV = (
    "GIT_DIR",
    "GIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_COMMON_DIR",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
)


def pytest_configure(config):
    for var in _HOST_GIT_ENV:
        os.environ.pop(var, None)
    if os.environ.get("TACKBOX_CACHE_HOME"):
        return
    session_cache = tempfile.mkdtemp(prefix="tackbox-test-cache-")
    os.environ["TACKBOX_CACHE_HOME"] = session_cache
    atexit.register(shutil.rmtree, session_cache, ignore_errors=True)
